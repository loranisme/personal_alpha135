"""Calculate only the frozen Active Pool and create a complete composite rank."""
from __future__ import annotations
import hashlib,json
from dataclasses import dataclass
from typing import Any,Mapping
import pandas as pd
from .factor_library import compute_registry_factors

@dataclass(frozen=True)
class SelectionScores:
    status:str
    factor_panels:dict[str,pd.DataFrame]
    ranked_panels:pd.DataFrame
    composite:pd.Series
    coverage:pd.DataFrame
    blockers:list[str]

def _hash(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def validate_active_pool(pool:Mapping[str,Any],provider:str)->list[str]:
    raw=dict(pool); declared=raw.pop("content_sha256",None)
    if not declared or declared!=_hash(raw): raise ValueError("ACTIVE_POOL_HASH_MISMATCH")
    if pool.get("provider")!=provider: raise ValueError("ACTIVE_POOL_PROVIDER_MISMATCH")
    selected=pool.get("selected_factors") or []
    if not 6<=len(selected)<=8: raise ValueError("ACTIVE_POOL_SIZE")
    ids=[x.get("local_factor_id") for x in selected]
    if len(set(ids))!=len(ids): raise ValueError("ACTIVE_POOL_DUPLICATE_FACTOR")
    if any(int(x.get("direction",0))!=1 for x in selected): raise ValueError("DIRECTION_MUST_REMAIN_ONE")
    weights=[float(x.get("weight",0)) for x in selected]
    if abs(sum(weights)-1)>1e-9 or max(weights)-min(weights)>1e-9: raise ValueError("ACTIVE_POOL_WEIGHTS_NOT_EQUAL")
    return ids

def calculate_active_scores(library,active_pool,provider,inputs,universe,session)->SelectionScores:
    factor_ids=validate_active_pool(active_pool,provider)
    ids=universe.security_id.astype(str).tolist()
    if len(set(ids))!=len(ids): raise ValueError("DUPLICATE_UNIVERSE_SECURITY_ID")
    panels=compute_registry_factors(library,factor_ids,provider,inputs,usage="diagnostic")
    timestamp=pd.Timestamp(session)
    ranked={}
    coverage=[]
    blockers=[]
    for factor_id,panel in panels.items():
        if timestamp not in panel.index: raise ValueError(f"SIGNAL_SESSION_MISSING:{factor_id}")
        row=panel.loc[timestamp].reindex(ids).replace([float("inf"),float("-inf")],pd.NA)
        finite=row.notna()
        value=float(finite.mean()) if len(row) else 0.0
        coverage.append({"local_factor_id":factor_id,"eligible_count":len(ids),"finite_count":int(finite.sum()),"coverage":value})
        if value<0.95: blockers.append("ACTIVE_FACTOR_COVERAGE_BELOW_95_PERCENT")
        ranked[factor_id]=row.rank(pct=True,method="average")
    rank_frame=pd.DataFrame(ranked,index=ids)
    complete=rank_frame.notna().all(axis=1)
    composite=rank_frame.mean(axis=1).where(complete).rename("composite_score")
    if int(complete.sum())<500: blockers.append("COMPLETE_COMPOSITE_BELOW_500")
    blockers=list(dict.fromkeys(blockers))
    return SelectionScores("BLOCKED_DATA" if blockers else "PASS",panels,rank_frame,composite,pd.DataFrame(coverage),blockers)
