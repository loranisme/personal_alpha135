"""Core target weights and an optional review-only share helper."""
from __future__ import annotations
import math
import pandas as pd

def build_personal_targets(ranking,universe,policy):
    required={"security_id","composite_score"}
    if not required<=set(ranking): raise ValueError("RANKING_FIELDS_MISSING")
    if not {"security_id","sector"}<=set(universe): raise ValueError("UNIVERSE_FIELDS_MISSING")
    ranked=ranking.merge(universe[["security_id","sector"]],on="security_id",how="inner",validate="one_to_one")
    ranked=ranked.dropna(subset=["composite_score"]).sort_values(["composite_score","security_id"],ascending=[False,True],kind="stable")
    k=math.ceil(len(ranked)*float(policy["top_fraction"])); weight=float(policy["invested_weight"])/k
    if weight>float(policy["max_single_name_weight"])+1e-12: raise ValueError("SINGLE_NAME_CAP_INCOMPATIBLE")
    selected=[]; sector_weight={}
    for row in ranked.to_dict("records"):
        sector=row.get("sector")
        if pd.isna(sector): continue
        if sector_weight.get(sector,0)+weight<=float(policy["max_sector_weight"])+1e-12:
            selected.append(row);sector_weight[sector]=sector_weight.get(sector,0)+weight
        if len(selected)==k: break
    if len(selected)<k: raise ValueError("SECTOR_CAP_CANNOT_FILL_TARGET_COUNT")
    targets=pd.DataFrame(selected).assign(target_weight=weight)
    targets["rank"]=range(1,len(targets)+1)
    targets=pd.concat([targets,pd.DataFrame([{"security_id":"CASH","composite_score":None,"sector":None,"target_weight":float(policy["minimum_cash"]),"rank":None}])],ignore_index=True)
    return {"status":"SELECTION_READY","stock_ranking":ranked.reset_index(drop=True),"target_portfolio":targets,"execution_helper_status":"NOT_REQUESTED","blockers":[]}

def build_manual_rebalance_helper(targets,positions,prices,nav,policy,*,now):
    now_ts=pd.Timestamp(now); 
    if now_ts.tzinfo is None: raise ValueError("NOW_TIMEZONE_REQUIRED")
    if nav<=0:return {"execution_helper_status":"BLOCKED_HELPER","blockers":["INVALID_NAV"]}
    if positions.empty or "account_as_of" not in positions:return {"execution_helper_status":"BLOCKED_HELPER","blockers":["ACCOUNT_SNAPSHOT_REQUIRED"]}
    times=pd.to_datetime(positions.account_as_of,utc=True,errors="raise")
    if (now_ts.tz_convert("UTC")-times.max()).total_seconds()>float(policy["max_account_age_seconds"]):
        return {"execution_helper_status":"BLOCKED_HELPER","blockers":["STALE_ACCOUNT_SNAPSHOT"]}
    if positions.security_id.duplicated().any() or prices.security_id.duplicated().any(): raise ValueError("DUPLICATE_HELPER_SECURITY")
    stocks=targets.loc[targets.security_id!="CASH",["security_id","target_weight"]].merge(prices,on="security_id",how="left",validate="one_to_one")
    if stocks.reference_price.isna().any() or (stocks.reference_price<=0).any(): return {"execution_helper_status":"BLOCKED_HELPER","blockers":["REFERENCE_PRICE_MISSING_OR_INVALID"]}
    stocks["target_shares"]=(stocks.target_weight*float(nav)/stocks.reference_price).apply(math.floor)
    current=positions[["security_id","current_shares"]]
    if (current.current_shares%1!=0).any(): return {"execution_helper_status":"BLOCKED_HELPER","blockers":["NON_INTEGER_POSITION"]}
    draft=stocks.merge(current,on="security_id",how="outer").fillna({"target_weight":0,"target_shares":0,"current_shares":0})
    draft["target_shares"]=draft.target_shares.astype(int);draft["current_shares"]=draft.current_shares.astype(int)
    draft["suggested_trade_shares"]=draft.target_shares-draft.current_shares
    draft["review_status"]="REVIEW_REQUIRED"
    return {"execution_helper_status":"EXECUTION_HELPER_READY","manual_rebalance_draft":draft,"blockers":[]}
