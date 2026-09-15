"""End-to-end V5 Lite personal selection orchestration."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
import pandas as pd
from .daily_selection import calculate_active_scores
from .manual_selection import build_personal_targets,build_manual_rebalance_helper
from .paper_log import PaperLog
from .reporting import write_selection_review

def _hash(value):
    if isinstance(value,pd.DataFrame): value=value.to_json(orient="split",date_format="iso",double_precision=15)
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()

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
    core=build_personal_targets(ranking,data["universe"],data["selection_policy"])
    helper={"execution_helper_status":"NOT_REQUESTED","blockers":[]}
    if data.get("execution_helper") is True:
        helper=build_manual_rebalance_helper(core["target_portfolio"],data["positions"],data["prices"],data["nav"],data["selection_policy"],now=data["now"])
    bundle={**core,"execution_helper_status":helper["execution_helper_status"],"alpha_scores":scores.ranked_panels.rename_axis("security_id").reset_index(),"data_checks":scores.coverage,"blocked_items":pd.DataFrame([{"code":x} for x in helper.get("blockers",[])],columns=["code"])}
    if "manual_rebalance_draft" in helper: bundle["manual_rebalance_draft"]=helper["manual_rebalance_draft"]
    paths=write_selection_review(bundle,output)
    event={"signal_id":f"{data['active_pool']['active_pool_id']}__{pd.Timestamp(data['session']).date()}","signal_session":str(pd.Timestamp(data["session"])),"pool_hash":data["active_pool"]["content_sha256"],"universe_hash":_hash(data["universe"]),"config_hash":_hash(data["selection_policy"]),"input_hashes":{k:_hash(v) for k,v in data["inputs"].items()},"blockers":helper.get("blockers",[]),"target_hash":_hash(core["target_portfolio"]),"execution_helper_status":helper["execution_helper_status"],"helper_output_hash":_hash(helper["manual_rebalance_draft"]) if "manual_rebalance_draft" in helper else None,"live_orders_submitted":0}
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
