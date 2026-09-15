import pandas as pd
from decimal import Decimal as D
from us_equity_alpha.portfolio import select_targets, plan_rebalance
from us_equity_alpha.account import apply_fills

def test_sector_and_cash():
    x=select_targets(pd.DataFrame({'security_id':['B','A','C'],'score':[1,1,.5],'sector':['S','S','T'],'eligible':[True]*3}),dict(N=2,cash_min=.1,single_name_cap=.5,sector_cap=.5))
    assert list(x.security_id)==['A','C','CASH']
    assert x.target_weight.tolist()==[.45,.45,.1]

def test_actual_holdings_full_nav_and_intents():
    a={'positions':[{'security_id':'A','shares':20},{'security_id':'D','shares':10}],'cash':'7500','receivables':'0'}
    q=pd.DataFrame([dict(security_id='A',bid=100,ask=100),dict(security_id='D',bid=50,ask=50)])
    r=plan_rebalance(pd.DataFrame([dict(security_id='A',target_weight=.3)]),a,q,{'fee_rate':'.001'})
    assert D(r['summary']['equity_snapshot'])==10000
    assert r['orders'][0]['intended_trade_shares']==10
    assert all(x['order_status']=='INTENT_ONLY' for x in r['orders'])

def test_fills_deduplicate():
    a={'positions':[],'cash':'1000','buying_power':'1000','receivables':'0'}
    f=dict(fill_id='f',security_id='A',quantity=2,side='BUY',price='100',fee='1')
    b=apply_fills(a,[f,f]); assert b['positions'][0]['shares']==2
    assert D(b['cash'])==799
    assert apply_fills(b,[f])==b

def test_candidates_shortage_does_not_reallocate():
    x=select_targets(pd.DataFrame([dict(security_id='A',score=1,sector='S',eligible=True),dict(security_id='B',score=.5,sector=None,eligible=True)]),dict(N=3,cash_min=.1,single_name_cap=.5,sector_cap=.5))
    assert x.target_weight.tolist()==[.3,.7]
    assert x.attrs['exclusions'][0]['selection_reason']=='UNKNOWN_SECTOR'

def test_fractional_holding_rejected_in_both_formats():
    import pytest
    from us_equity_alpha.account import positions
    for raw in ({'A':1.5},[dict(security_id='A',shares=1.5)]):
        with pytest.raises(ValueError,match='INVALID_POSITION'): positions({'positions':raw})


def test_reconcile_does_not_refresh_broker_snapshot():
    from us_equity_alpha.account import reconcile_fills
    a=dict(positions=[],cash='1000',buying_power='1000',receivables='0',asof='2026-09-07T20:00:00+00:00',reconciled=True)
    result=reconcile_fills(a,[],asof='2026-09-08T13:35:00+00:00')
    assert result['asof']==a['asof']
    assert result['reconciled'] is False
    assert result['requires_broker_snapshot_refresh'] is True
    assert result['fills_imported_at']=='2026-09-08T13:35:00+00:00'
