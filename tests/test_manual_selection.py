import pandas as pd
from us_equity_alpha.manual_selection import build_personal_targets,build_manual_rebalance_helper

def _ranking(n=500): return pd.DataFrame({"security_id":[f"S{i:04d}" for i in range(n)],"composite_score":range(n,0,-1)})
def _universe(n=500): return pd.DataFrame({"security_id":[f"S{i:04d}" for i in range(n)],"sector":[f"sector-{i%10}" for i in range(n)]})
def _policy(): return {"top_fraction":.1,"invested_weight":.95,"minimum_cash":.05,"max_single_name_weight":.02,"max_sector_weight":.25,"max_account_age_seconds":60}

def test_core_targets_need_no_account_and_select_top_decile():
    bundle=build_personal_targets(_ranking(),_universe(),_policy())
    stocks=bundle["target_portfolio"].query("security_id!='CASH'")
    assert len(stocks)==50 and stocks.target_weight.nunique()==1
    assert stocks.target_weight.iloc[0]==.019
    assert bundle["execution_helper_status"]=="NOT_REQUESTED"
    assert "manual_rebalance_draft" not in bundle

def test_optional_helper_produces_review_only_share_differences():
    targets=build_personal_targets(_ranking(),_universe(),_policy())["target_portfolio"]
    positions=pd.DataFrame([{"security_id":"S0000","current_shares":5,"account_as_of":"2025-01-02T15:00:00Z"}])
    prices=pd.DataFrame([{"security_id":f"S{i:04d}","reference_price":100.0} for i in range(50)])
    helper=build_manual_rebalance_helper(targets,positions,prices,100000,_policy(),now="2025-01-02T15:00:30Z")
    draft=helper["manual_rebalance_draft"]
    assert helper["execution_helper_status"]=="EXECUTION_HELPER_READY"
    assert draft.loc[draft.security_id=="S0000","suggested_trade_shares"].item()==14
    assert set(draft.review_status)=={"REVIEW_REQUIRED"}
    assert "approved_trade_shares" not in draft and "order_id" not in draft

def test_stale_helper_is_blocked_without_changing_core_targets():
    core=build_personal_targets(_ranking(),_universe(),_policy())
    positions=pd.DataFrame([{"security_id":"S0000","current_shares":5,"account_as_of":"2025-01-02T14:00:00Z"}])
    prices=pd.DataFrame([{"security_id":f"S{i:04d}","reference_price":100.0} for i in range(50)])
    helper=build_manual_rebalance_helper(core["target_portfolio"],positions,prices,100000,_policy(),now="2025-01-02T15:00:30Z")
    assert helper["execution_helper_status"]=="BLOCKED_HELPER"
    assert helper["blockers"]==["STALE_ACCOUNT_SNAPSHOT"]
    assert len(core["target_portfolio"])==51

def test_missing_classification_keeps_paper_targets_with_warning():
    universe=_universe();universe.loc[0,"sector"]=None
    result=build_personal_targets(_ranking(),universe,_policy())
    assert len(result["target_portfolio"])==51
    assert result["warnings"]==["SECTOR_CONSTRAINT_UNAVAILABLE"]
    assert result["portfolio_status"]=="PAPER_ONLY"
