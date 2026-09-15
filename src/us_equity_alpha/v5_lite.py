"""End-to-end V5 Lite personal selection orchestration."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
import pandas as pd
from .daily_selection import calculate_active_scores
from .manual_selection import build_personal_targets,build_manual_rebalance_helper
from .paper_log import PaperLog
from .reporting import write_selection_review
from .sample_workflow import weekly_rebalance_dates

def _hash(value):
    if isinstance(value,pd.DataFrame): value=value.to_json(orient="split",date_format="iso",double_precision=15)
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()

def _rebalance_due(data):
    if data["selection_policy"].get("rebalance") != "WEEKLY_FIRST_SESSION":
        raise ValueError("UNSUPPORTED_REBALANCE_POLICY")
    indices=[]
    for panel in data["inputs"].values():
        index=pd.DatetimeIndex(panel.index)
        index=index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
        indices.append(index)
    sessions=indices[0]
    for index in indices[1:]: sessions=sessions.intersection(index)
    sessions=sessions.sort_values()
    session=pd.Timestamp(data["session"])
    session=session.tz_localize("UTC") if session.tz is None else session.tz_convert("UTC")
    return any(execution_session==session for execution_session,_ in weekly_rebalance_dates(sessions))

def _signal_event(data, *, helper, target=None):
    return {
        "signal_id":f"{data['active_pool']['active_pool_id']}__{pd.Timestamp(data['session']).date()}",
        "signal_session":str(pd.Timestamp(data["session"])),
        "pool_hash":data["active_pool"]["content_sha256"],
        "universe_hash":_hash(data["universe"]),
        "config_hash":_hash(data["selection_policy"]),
        "input_hashes":{key:_hash(value) for key,value in data["inputs"].items()},
        "blockers":helper.get("blockers",[]),
        "target_hash":_hash(target) if target is not None else None,
        "execution_helper_status":helper["execution_helper_status"],
        "helper_output_hash":_hash(helper["manual_rebalance_draft"]) if "manual_rebalance_draft" in helper else None,
        "live_orders_submitted":0,
    }

def run_v5_lite(data,output_dir,paper_log_path):
    output=Path(output_dir)
    if output.exists(): raise FileExistsError("OUTPUT_DIRECTORY_EXISTS")
    scores=calculate_active_scores(data["library"],data["active_pool"],data["provider"],data["inputs"],data["universe"],data["session"])
    if scores.status!="PASS":
        output.mkdir(parents=True)
        pd.DataFrame([{"code":x} for x in scores.blockers]).to_csv(output/"blocked_items.csv",index=False)
        result={"status":"BLOCKED_DATA","blockers":scores.blockers,"execution_helper_status":"NOT_REQUESTED","live_orders_submitted":0}
        (output/"run_status.json").write_text(json.dumps(result,indent=2)+"\n")
        return result
    ranking=scores.composite.dropna().rename_axis("security_id").reset_index().sort_values(["composite_score","security_id"],ascending=[False,True],kind="stable")
    rebalance_due=_rebalance_due(data)
    if not rebalance_due:
        helper={"execution_helper_status":"NOT_DUE" if data.get("execution_helper") is True else "NOT_REQUESTED","blockers":["EXECUTION_HELPER_NOT_DUE"] if data.get("execution_helper") is True else []}
        bundle={"status":"RANKING_READY_NO_REBALANCE","rebalance_due":False,"execution_helper_status":helper["execution_helper_status"],"alpha_scores":scores.ranked_panels.rename_axis("security_id").reset_index(),"stock_ranking":ranking,"data_checks":scores.coverage,"blocked_items":pd.DataFrame([{"code":x} for x in helper["blockers"]],columns=["code"])}
        paths=write_selection_review(bundle,output)
        PaperLog(paper_log_path).record_signal(_signal_event(data,helper=helper))
        return {"status":"RANKING_READY_NO_REBALANCE","rebalance_due":False,"execution_helper_status":helper["execution_helper_status"],"live_orders_submitted":0,"output":str(output),"manifest":str(paths["manifest"])}
    core=build_personal_targets(ranking,data["universe"],data["selection_policy"])
    helper={"execution_helper_status":"NOT_REQUESTED","blockers":[]}
    if data.get("execution_helper") is True:
        helper=build_manual_rebalance_helper(core["target_portfolio"],data["positions"],data["prices"],data["nav"],data["selection_policy"],now=data["now"])
    bundle={**core,"rebalance_due":True,"execution_helper_status":helper["execution_helper_status"],"alpha_scores":scores.ranked_panels.rename_axis("security_id").reset_index(),"data_checks":scores.coverage,"blocked_items":pd.DataFrame([{"code":x} for x in helper.get("blockers",[])],columns=["code"])}
    if "manual_rebalance_draft" in helper: bundle["manual_rebalance_draft"]=helper["manual_rebalance_draft"]
    paths=write_selection_review(bundle,output)
    event=_signal_event(data,helper=helper,target=core["target_portfolio"])
    PaperLog(paper_log_path).record_signal(event)
    return {"status":"SELECTION_READY","execution_helper_status":helper["execution_helper_status"],"live_orders_submitted":0,"output":str(output),"manifest":str(paths["manifest"])}

def run_v5_lite_from_files(*,library,active_pool,universe,market_data,policy,session,output,paper_log,execution_helper=False,positions=None,account=None,reference_prices=None):
    root=Path(market_data);inputs={}
    for path in sorted(root.glob("*.parquet")):
        panel=pd.read_parquet(path);panel.index=pd.to_datetime(panel.index,utc=True);inputs[path.stem]=panel
    if not inputs: raise ValueError("MARKET_DATA_PANELS_REQUIRED")
    payload={"library":json.loads(Path(library).read_text()),"active_pool":json.loads(Path(active_pool).read_text()),"provider":json.loads(Path(active_pool).read_text())["provider"],"inputs":inputs,"universe":pd.read_csv(universe),"session":pd.Timestamp(session),"selection_policy":json.loads(Path(policy).read_text())}
    if execution_helper:
        if not all((positions,account,reference_prices)): raise ValueError("EXECUTION_HELPER_INPUTS_REQUIRED")
        account_data=json.loads(Path(account).read_text());payload.update(execution_helper=True,positions=pd.read_csv(positions),prices=pd.read_csv(reference_prices),nav=float(account_data["nav"]),now=account_data["as_of"])
    return run_v5_lite(payload,output,paper_log)
