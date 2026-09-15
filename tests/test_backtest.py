import numpy as np
import pandas as pd
import pytest
from us_equity_alpha.backtest import audit_trade_totals, run_vectorbt, check_engine_capabilities, eligible_bar_fill
from us_equity_alpha.validation import build_trade_labels, alphalens_diagnostics, validate_protocol


def test_turnover_counts_both_sides_once():
    trades=pd.DataFrame({'filled_quantity':[10,-10,42,-10],'fill_price':[100.,60.,70.,50.],'fee':[1.,.6,2.94,.5]})
    r=audit_trade_totals(trades,10000)
    assert r['traded_notional']==5040 and r['gross_turnover']==.504 and r['fees']==pytest.approx(5.04)
    assert 3900+r['cash_change']==pytest.approx(1054.96)


def test_real_engine_drift_fees_and_cash():
    idx=pd.date_range('2025-01-01',periods=3,tz='UTC')
    prices=pd.DataFrame({'A':[100.,150.,150.],'B':[100.,100.,100.]},index=idx)
    sizes=pd.DataFrame({'A':[5.,0.,-1.],'B':[5.,0.,1.]},index=idx)
    r=run_vectorbt(prices,sizes,prices,initial_cash=1010,fee_rate=.001)
    assert r['engine']=='vectorbt'
    assert r['portfolio'].value().iloc[1]==pytest.approx(1259)
    assert r['audit']['traded_notional']==1250
    assert r['portfolio'].cash().iloc[-1]==pytest.approx(58.75)


def test_missing_price_and_fee_shortfall_fail():
    p=pd.DataFrame({'A':[100.]},index=pd.date_range('2025-01-01',periods=1,tz='UTC'))
    with pytest.raises(ValueError,match='PRICE'): run_vectorbt(p,p*0+1,p*np.nan,initial_cash=100,fee_rate=0)
    with pytest.raises(Exception): run_vectorbt(p,p*0+1,p,initial_cash=100,fee_rate=.01)


@pytest.mark.parametrize('event',['split','dividend','delisting','external_cash_flow'])
def test_actions_fail_closed(event):
    r=check_engine_capabilities([{'kind':event}])
    assert r['status']=='ENGINE_ACTIONS_UNSUPPORTED' and not r['formal_backtest_allowed']


def test_trade_entry_excludes_overnight_gap():
    times=pd.date_range('2025-01-02 14:35',periods=3,freq='B',tz='UTC')
    prices=pd.DataFrame({'A':[110.,121.,121.]},index=times)
    signals=pd.DataFrame({'signal_cutoff':[pd.Timestamp('2025-01-01 21:00',tz='UTC')],'security_id':['A'],'score':[1.]})
    r=build_trade_labels(signals,prices,1)
    assert r.iloc[0]['forward_return']==pytest.approx(.1)
    assert r.iloc[0]['entry_time']==times[0] and r.iloc[0]['exit_time']==times[1]


def test_alphalens_uses_supplied_trade_labels():
    rows=[]
    for day in pd.date_range('2025-01-01',periods=3,tz='UTC'):
        for i in range(10): rows.append({'signal_cutoff':day,'security_id':str(i),'score':i,'forward_return':i/100,'sector':'S'})
    r=alphalens_diagnostics(pd.DataFrame(rows),horizon=1,quantiles=5)
    assert r['engine']=='alphalens-reloaded' and r['mean_rank_ic']==pytest.approx(1)
    assert r['observations']==30 and r['icir_annualized'] is False


def test_submitted_after_touch_cannot_fill_and_volume_caps_partial():
    base=dict(side='BUY',quantity=10,limit_price=101,submitted_at='2025-01-01T14:36:00Z',bar_start='2025-01-01T14:35:00Z',bar_end='2025-01-01T14:36:00Z',low=100,high=102,volume=100,participation=.05)
    assert eligible_bar_fill(**base)['filled_quantity']==0
    base.update(bar_start='2025-01-01T14:37:00Z',bar_end='2025-01-01T14:38:00Z')
    assert eligible_bar_fill(**base)['filled_quantity']==5
    assert validate_protocol({})['status']=='BLOCKED_CONFIG'


def test_protocol_empty_objects_rejected():
    from us_equity_alpha.validation import REQUIRED_PROTOCOL
    p={k:{} for k in REQUIRED_PROTOCOL}; p['controls']=[]
    assert validate_protocol(p)['status']=='BLOCKED_CONFIG'
    assert 'main_configuration' in validate_protocol(p)['missing']


def test_shared_plan_to_actual_vectorbt_with_unfilled_cash():
    from test_execution import fixture, NOW
    from us_equity_alpha.execution import signal_hash
    from us_equity_alpha.backtest import run_execution_diagnostic
    s,a,q,p=fixture(); a.update(positions=[],cash='10000',buying_power='10000')
    s['signal_cutoff']='2026-09-07T20:00:00Z'; s['signal_hash']=signal_hash(s)
    fills=[{'security_id':'AAA','filled_quantity':5,'fill_price':100,'submitted_at':'2026-09-08T13:35:05Z','fill_at':'2026-09-08T13:35:10Z'}]
    r=run_execution_diagnostic(s,a,q,p,fills,now=NOW)
    assert r['engine']['portfolio'].cash().iloc[-1]==pytest.approx(9499.5)
    assert r['engine']['audit']['traded_notional']==500
    assert r['execution_plan']['orders'][0]['approved_trade_shares']>5
    fills[0]['fill_at']='2026-09-08T13:34:00Z'
    with pytest.raises(ValueError,match='CAUSAL'): run_execution_diagnostic(s,a,q,p,fills,now=NOW)


def test_alphalens_counts_observed_sessions_not_empty_calendar_dates():
    from us_equity_alpha.validation import alphalens_diagnostics
    rows=pd.DataFrame([{'signal_cutoff':date,'security_id':s,'score':score,'forward_return':score*.01} for date in ['2024-01-05T22:00:00Z','2024-01-08T22:00:00Z'] for s,score in [('A',1.),('B',2.),('C',3.)]])
    result=alphalens_diagnostics(rows,quantiles=2)
    assert result['sessions']==2
