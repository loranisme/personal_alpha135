import pandas as pd
import pytest
from us_equity_alpha.event_backtest import run_event_backtest


def prices(values):
    return pd.DataFrame({'A':values},index=pd.date_range('2025-01-01',periods=len(values),tz='UTC'))


def test_initial_inventory_split_without_fake_trades():
    p=prices([100.,50.]); r=run_event_backtest(p,initial_cash=100,initial_positions={'A':10},events=[{'event_id':'s','kind':'split','at':p.index[1],'security_id':'A','ratio':2}])
    assert r['snapshots'].equity.tolist()==[1100,1100]
    assert r['positions']['A'].tolist()==[10,20]
    assert r['audit']['traded_notional']==0 and r['engine_order_count']==0


def test_dividend_receivable_then_payment_and_no_double_profit():
    p=prices([100.,99.,99.]); events=[{'event_id':'d','kind':'dividend_ex','at':p.index[1],'security_id':'A','amount_per_share':1},{'event_id':'pay','kind':'dividend_pay','at':p.index[2],'entitlement_id':'d'}]
    r=run_event_backtest(p,initial_cash=0,initial_positions={'A':10},events=events)
    assert r['snapshots'].equity.tolist()==[1000,1000,1000]
    assert r['snapshots'].receivables.tolist()==[0,10,0]
    assert r['snapshots'].cash.tolist()==[0,0,10]
    assert r['audit']['fees']==0


def test_delisting_zero_terminal_loss_and_deposit_twr():
    p=prices([100.,0.,0.]); events=[{'event_id':'x','kind':'delisting','at':p.index[1],'security_id':'A','terminal_value':0},{'event_id':'f','kind':'external_cash_flow','at':p.index[2],'amount':1000}]
    r=run_event_backtest(p,initial_cash=1000,initial_positions={'A':10},events=events)
    assert r['snapshots'].equity.tolist()==[2000,1000,2000]
    assert r['snapshots'].twr_return.tolist()==[0,-.5,0]
    assert r['total_twr']==-.5 and r['engine_order_count']==0


def test_actual_orders_charge_fees_with_initial_inventory():
    p=prices([100.,100.]); orders=pd.DataFrame({'A':[0.,-2.]},index=p.index)
    r=run_event_backtest(p,initial_cash=100,initial_positions={'A':10},sizes=orders,fee_rate=.001)
    assert r['snapshots'].cash.iloc[-1]==pytest.approx(299.8)
    assert r['snapshots'].equity.iloc[-1]==pytest.approx(1099.8)
    assert r['audit']['traded_notional']==200 and r['audit']['fees']==pytest.approx(.2)


def test_dividend_unpaid_cannot_fund_orders():
    p=prices([100.,99.]); orders=pd.DataFrame({'A':[0.,1.]},index=p.index)
    with pytest.raises(Exception): run_event_backtest(p,initial_cash=0,initial_positions={'A':10},sizes=orders,events=[{'event_id':'d','kind':'dividend_ex','at':p.index[1],'security_id':'A','amount_per_share':100}])


def test_fractional_split_and_duplicate_events_rejected():
    p=prices([100.,200.]); e={'event_id':'s','kind':'split','at':p.index[1],'security_id':'A','ratio':.5}
    with pytest.raises(ValueError,match='FRACTIONAL'): run_event_backtest(p,initial_cash=0,initial_positions={'A':1},events=[e])
    with pytest.raises(ValueError,match='DUPLICATE'): run_event_backtest(p,initial_cash=0,initial_positions={'A':2},events=[e,e])


def test_external_flow_after_repricing_does_not_dilute_market_return():
    p=prices([100.,110.]); r=run_event_backtest(p,initial_cash=0,initial_positions={'A':10},events=[{'event_id':'f','kind':'external_cash_flow','at':p.index[1],'amount':1000}])
    assert r['snapshots'].equity.iloc[-1]==2100
    assert r['total_twr']==pytest.approx(.1)


def test_total_delisting_loss_is_recorded_even_with_zero_cash():
    p=prices([100.,0.]); r=run_event_backtest(p,initial_cash=0,initial_positions={'A':10},events=[{'event_id':'x','kind':'delisting','at':p.index[1],'security_id':'A','terminal_value':0}])
    assert r['snapshots'].equity.iloc[-1]==0
    assert r['total_twr']==-1
