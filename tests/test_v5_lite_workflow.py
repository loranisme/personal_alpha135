import hashlib,json
import numpy as np
import pandas as pd
from us_equity_alpha.v5_lite import run_v5_lite
from us_equity_alpha.cli import main

def _fixture(n=500,helper=False):
    dates=pd.bdate_range("2025-01-01",periods=4,tz="UTC");ids=[f"S{i:04d}" for i in range(n)]
    close=pd.DataFrame(np.arange(4*(n+1)).reshape(4,n+1)+100.,index=dates,columns=ids+["SPY"])
    factors=[]
    for i in range(6): factors.append({"local_factor_id":f"F{i}","expression":"rank(ts_delta(close, 1))","field_bindings":{"close":"close"},"allowed_providers":["tiingo_eod"],"build_status":"COMPUTE_VERIFIED","usage_tier":"DIAGNOSTIC_ONLY","provenance_kind":"LOCAL_RECONSTRUCTION","settings":{"delay":0,"decay":0,"neutralization":"NONE"}})
    pool={"schema_version":1,"status":"FROZEN","active_pool_id":"pool","library_version":"reconstruction-v4","v4_manifest_sha256":"a"*64,"provider":"tiingo_eod","selected_factors":[{"local_factor_id":f"F{i}","direction":1,"weight":str(1/6)} for i in range(6)],"decision_list":[],"warnings":[],"selection_basis":"HUMAN","data_exposure":{},"live_orders_submitted":0}
    pool["content_sha256"]=hashlib.sha256(json.dumps(pool,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    data={"library":{"factors":factors},"active_pool":pool,"provider":"tiingo_eod","inputs":{"close":close},"universe":pd.DataFrame({"security_id":ids,"sector":[f"sector-{i%10}" for i in range(n)]}),"session":dates[-1],"selection_policy":{"top_fraction":.1,"invested_weight":.95,"minimum_cash":.05,"max_single_name_weight":.02,"max_sector_weight":.25,"max_account_age_seconds":60,"rebalance":"WEEKLY_FIRST_SESSION"}}
    if helper:
        data.update(execution_helper=True,positions=pd.DataFrame([{"security_id":"S0000","current_shares":0,"account_as_of":"2025-01-06T15:00:00Z"}]),prices=pd.DataFrame([{"security_id":x,"reference_price":100.} for x in ids]),nav=100000,now="2025-01-06T15:00:30Z")
    return data

def test_v5_blocks_without_500_complete_scores_and_writes_no_targets(tmp_path):
    result=run_v5_lite(_fixture(499),tmp_path/"run",tmp_path/"paper.sqlite")
    assert result["status"]=="BLOCKED_DATA"
    assert not (tmp_path/"run/target_portfolio.csv").exists()

def test_v5_core_writes_targets_without_rebalance_draft(tmp_path):
    result=run_v5_lite(_fixture(),tmp_path/"run",tmp_path/"paper.sqlite")
    assert result["status"]=="SELECTION_READY" and result["live_orders_submitted"]==0
    assert result["execution_helper_status"]=="NOT_REQUESTED"
    assert (tmp_path/"run/stock_ranking.csv").is_file() and (tmp_path/"run/target_portfolio.csv").is_file()
    assert not (tmp_path/"run/manual_rebalance_draft.csv").exists()

def test_v5_non_rebalance_session_writes_ranking_without_targets(tmp_path):
    data=_fixture()
    data["session"]=pd.Timestamp("2025-01-03",tz="UTC")
    result=run_v5_lite(data,tmp_path/"run",tmp_path/"paper.sqlite")
    assert result["status"]=="RANKING_READY_NO_REBALANCE"
    assert (tmp_path/"run/stock_ranking.csv").is_file()
    assert not (tmp_path/"run/target_portfolio.csv").exists()
    manifest=json.loads((tmp_path/"run/manifest.json").read_text())
    assert manifest["rebalance_due"] is False

def test_v5_non_rebalance_session_rejects_helper_request(tmp_path):
    data=_fixture(helper=True)
    data["session"]=pd.Timestamp("2025-01-03",tz="UTC")
    result=run_v5_lite(data,tmp_path/"run",tmp_path/"paper.sqlite")
    assert result["status"]=="RANKING_READY_NO_REBALANCE"
    assert result["execution_helper_status"]=="NOT_DUE"
    assert not (tmp_path/"run/manual_rebalance_draft.csv").exists()

def test_v5_optional_helper_adds_review_only_draft(tmp_path):
    result=run_v5_lite(_fixture(helper=True),tmp_path/"run",tmp_path/"paper.sqlite")
    assert result["execution_helper_status"]=="EXECUTION_HELPER_READY"
    draft=pd.read_csv(tmp_path/"run/manual_rebalance_draft.csv")
    assert set(draft.review_status)=={"REVIEW_REQUIRED"} and "order_id" not in draft

def test_daily_select_cli_runs_core_file_workflow(tmp_path,capsys):
    data=_fixture(); market=tmp_path/"market";market.mkdir()
    (tmp_path/"library.json").write_text(json.dumps(data["library"]));(tmp_path/"pool.json").write_text(json.dumps(data["active_pool"]));data["universe"].to_csv(tmp_path/"universe.csv",index=False)
    data["inputs"]["close"].to_parquet(market/"close.parquet")
    (tmp_path/"policy.json").write_text(json.dumps(data["selection_policy"]))
    code=main(["daily-select","--library",str(tmp_path/"library.json"),"--active-pool",str(tmp_path/"pool.json"),"--universe",str(tmp_path/"universe.csv"),"--market-data",str(market),"--policy",str(tmp_path/"policy.json"),"--as-of",str(data["session"]),"--output",str(tmp_path/"run"),"--paper-log",str(tmp_path/"paper.sqlite")])
    assert code==0 and json.loads(capsys.readouterr().out)["status"]=="SELECTION_READY"
