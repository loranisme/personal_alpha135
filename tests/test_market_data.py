import pandas as pd
import pytest
from us_equity_alpha.market_data import load_asof
from us_equity_alpha.universe import build_universes, session_bounds
from us_equity_alpha.actions import normalize_actions


def frame():
    return pd.DataFrame(dict(security_id=['S1']*3,field=['x']*3,value=[100,None,120],event_time=['2025-03-31']*3,available_at=['2025-05-01','2025-06-01','2025-08-01'],revision_id=['a','b','c'],source=['synthetic']*3,unit=['USD']*3,adjustment=['none']*3,fetched_at=['2026-09-07']*3))

def test_revisions_no_future_no_fill(tmp_path):
    p=tmp_path/'x.parquet'; frame().to_parquet(p)
    assert load_asof(p,pd.Timestamp('2025-05-15',tz='UTC'),['x']).value.tolist()==[100]
    assert load_asof(p,pd.Timestamp('2025-07-01',tz='UTC'),['x']).value.isna().all()
    with pytest.raises(ValueError): load_asof(p,pd.Timestamp('2025-07-01'),['x'])

def test_conflicting_provenance_and_duplicates(tmp_path):
    d=frame(); d.loc[2,'source']='other'; p=tmp_path/'x.parquet'; d.to_parquet(p)
    with pytest.raises(ValueError): load_asof(p,pd.Timestamp('2026-01-01',tz='UTC'),['x'])
    pd.concat([frame(),frame()]).to_parquet(p)
    with pytest.raises(ValueError): load_asof(p,pd.Timestamp('2026-01-01',tz='UTC'),['x'])

def test_future_event(tmp_path):
    d=frame().iloc[:1].copy(); d['event_time']='2026-01-01'; p=tmp_path/'x.parquet'; d.to_parquet(p)
    assert load_asof(p,pd.Timestamp('2025-07-01',tz='UTC'),['x']).empty

def test_universe_rename_delisting_and_reasons():
    sec=pd.DataFrame([dict(security_id='S1',listed_at='2020-01-01',delisted_at='2025-07-01',history_count=30,price=10,volume=20),dict(security_id='S2',listed_at='2025-05-01',delisted_at=None,history_count=2,price=None,volume=20)])
    cls=pd.DataFrame([dict(security_id='S1',effective_at='2020-01-01',available_at='2020-01-01',industry='I',symbol='OLD'),dict(security_id='S1',effective_at='2025-06-01',available_at='2025-06-01',industry='I',symbol='NEW')])
    x=build_universes(sec,cls,pd.Timestamp('2025-05-15',tz='UTC'),20)
    assert x.iloc[0].symbol=='OLD' and x.iloc[0].trading_eligible
    assert 'INSUFFICIENT_HISTORY' in x.iloc[1].reasons and 'UNKNOWN_CLASSIFICATION' in x.iloc[1].reasons and 'MISSING_MARKET_DATA' in x.iloc[1].reasons
    assert not build_universes(sec,cls,pd.Timestamp('2025-08-01',tz='UTC'),20).iloc[0].calculation_eligible

def test_calendar_earlyclose_dst_holiday():
    assert session_bounds('2025-11-28')[1].hour==18
    assert session_bounds('2025-03-07')[0].hour==14
    assert session_bounds('2025-03-10')[0].hour==13
    with pytest.raises(ValueError): session_bounds('2025-12-25')

def test_actions_exclude_future_preserve_pay_date():
    d=pd.DataFrame([dict(security_id='S1',kind='split',effective_at='2025-05-01',available_at='2025-04-01',ratio=2,amount=None,pay_date=None),dict(security_id='S1',kind='distribution',effective_at='2025-07-01',available_at='2025-04-01',ratio=None,amount=1,pay_date='2025-07-10')])
    assert len(normalize_actions(d,pd.Timestamp('2025-06-01',tz='UTC')))==1
    assert normalize_actions(d,pd.Timestamp('2025-08-01',tz='UTC')).iloc[1].pay_date.day==10
