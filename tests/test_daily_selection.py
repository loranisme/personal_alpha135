import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from us_equity_alpha.daily_selection import calculate_active_scores


def _factor(index):
    factor_id=f"F{index}"
    return dict(local_factor_id=factor_id, expression="rank(ts_delta(close, 1))",
                expression_hash=f"{index+1:064x}", family_id=f"family-{index}",
                economic_description={"mechanism_tags":[f"m{index}"]}, semantic_status="FULL_INTENT",
                field_bindings={"close":"close"}, allowed_providers=["tiingo_eod"],
                direction_basis="signed", build_status="COMPUTE_VERIFIED", usage_tier="DIAGNOSTIC_ONLY",
                provenance_kind="LOCAL_RECONSTRUCTION", source_alpha_ids=[str(index)],
                settings={"delay":0,"decay":0,"neutralization":"NONE"})


def _library(): return {"factors":[_factor(i) for i in range(7)]}


def _pool():
    payload={"schema_version":1,"status":"FROZEN","active_pool_id":"pool-1",
             "library_version":"reconstruction-v4","v4_manifest_sha256":"a"*64,
             "provider":"tiingo_eod","selected_factors":[
                 {"local_factor_id":f"F{i}","family_id":f"family-{i}","mechanism_tag":f"m{i}",
                  "direction":1,"comment":"chosen","weight":str(1/6)} for i in range(6)],
             "decision_list":[],"warnings":[],"selection_basis":"HUMAN_REVIEW",
             "data_exposure":{},"live_orders_submitted":0}
    payload["content_sha256"]=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    return payload


def _inputs(count=500, missing=0):
    dates=pd.bdate_range("2025-01-01",periods=4,tz="UTC")
    columns=[f"S{i:04d}" for i in range(count)]+["SPY"]
    values=np.arange(len(dates)*len(columns),dtype=float).reshape(len(dates),len(columns))+100
    close=pd.DataFrame(values,index=dates,columns=columns)
    if missing: close.loc[dates[-1],columns[:missing]]=np.nan
    universe=pd.DataFrame({"security_id":columns[:-1]})
    return {"close":close},universe,dates[-1]


def test_only_active_factors_are_computed_and_spy_is_never_ranked():
    inputs,universe,session=_inputs()
    result=calculate_active_scores(_library(),_pool(),"tiingo_eod",inputs,universe,session)
    assert set(result.factor_panels)=={f"F{i}" for i in range(6)}
    assert "F6" not in result.factor_panels
    assert "SPY" not in result.composite.index
    assert len(result.composite.dropna())==500
    assert result.status=="PASS"


def test_incomplete_active_factor_coverage_blocks_targets():
    inputs,universe,session=_inputs(missing=30)
    result=calculate_active_scores(_library(),_pool(),"tiingo_eod",inputs,universe,session)
    assert result.status=="BLOCKED_DATA"
    assert "ACTIVE_FACTOR_COVERAGE_BELOW_95_PERCENT" in result.blockers


def test_tampered_pool_hash_is_rejected():
    inputs,universe,session=_inputs()
    pool=_pool(); pool["provider"]="alpaca_sip"
    with pytest.raises(ValueError,match="ACTIVE_POOL_HASH_MISMATCH"):
        calculate_active_scores(_library(),pool,"alpaca_sip",inputs,universe,session)
