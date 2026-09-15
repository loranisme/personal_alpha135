from decimal import Decimal as D
from us_equity_alpha.execution import limit_price, build_execution_plan

def test_directional_limits():
    assert limit_price('BUY',D('100.9'),D('101'),D('98'),D('102'),D('.001'),D('.01'))==D('101.10')
    assert limit_price('SELL',D('99'),D('99.1'),D('98'),D('102'),D('.001'),D('.01'))==D('98.91')

import json
from pathlib import Path
from copy import deepcopy
import pandas as pd
import pytest
from us_equity_alpha.execution import signal_hash, policy_hash

NOW='2026-09-08T13:35:00+00:00'
def fixture():
    p=dict(feed='SYNTHETIC',max_quote_age_seconds=30,max_account_age_seconds=60,max_cross_security_skew_seconds=5,draft_ttl_seconds=20,gap_up={'BUY':.1,'SELL':.1},gap_down={'BUY':.1,'SELL':.1},epsilon_buy=0,epsilon_sell=0,tick='.01',cash_min=.1,single_name_cap=.4,sector_cap=.4,fee_rate='.001',max_refreshes=2,max_spread_ratio='.02')
    ranking=[dict(security_id=k,score=4-i,rank=i+1,sector='S'+str(i),valid=True) for i,k in enumerate(['AAA','BBB','CCC','DDD'])]
    s=dict(signal_id='synthetic-signal',signal_cutoff='2026-09-07T20:00:00+00:00',release_id='synthetic-only',ranking=ranking,target_weights=[dict(security_id=k,target_weight=.3,sector='S'+str(i),rank=i+1) for i,k in enumerate(['AAA','BBB','CCC'])]+[dict(security_id='CASH',target_weight=.1)],anchors={k:dict(prior_raw_close=v,effective_split_ratio=1) for k,v in zip(['AAA','BBB','CCC','DDD'],[100,60,70,50])},execution_window_start=NOW,execution_window_end='2026-09-08T14:00:00+00:00',valid_until='2026-09-08T14:00:00+00:00',rebalance_due=True,execution_policy_hash=policy_hash(p))
    s['signal_hash']=signal_hash(s)
    a=dict(asof=NOW,positions=[dict(security_id='AAA',shares=20),dict(security_id='BBB',shares=60),dict(security_id='DDD',shares=10)],cash='3900',buying_power='3900',receivables='0',open_orders_known=True,open_orders=[],reconciled=True)
    q=pd.DataFrame([dict(security_id=k,bid=v,ask=v,quote_time=NOW,received_at=NOW,feed='SYNTHETIC',delayed=False,currency='USD',session='REGULAR',halted=False) for k,v in zip(['AAA','BBB','CCC','DDD'],[100,60,70,50])])
    return s,a,q,p

def run(s,a,q,p): return build_execution_plan(s,a,q,p,now=NOW)

def test_initial_approved_funding_not_given_fills():
    s,a,q,p=fixture(); before=deepcopy(s); r=run(s,a,q,p)
    assert s==before
    assert r==run(s,a,q,p)
    assert r['run_status']=='REVIEW'
    assert {x['security_id']:x['approved_trade_shares'] for x in r['orders']}=={'AAA':10,'BBB':-10,'CCC':27,'DDD':-10}
    assert D(r['summary']['cash_after'])==D('1054.96')
    assert D(r['summary']['cash_without_unfilled_sales'])>=D(r['summary']['approved_equity_after'])*D('.1')

@pytest.mark.parametrize('mutation,reason',[
    ('old_quote','INVALID_HOLDING_QUOTE'),('crossed','INVALID_HOLDING_QUOTE'),('delayed','INVALID_HOLDING_QUOTE'),('skew','INVALID_HOLDING_QUOTE'),('old_account','STALE_ACCOUNT'),('unknown_orders','UNKNOWN_OPEN_ORDERS'),('cancel_pending','OPEN_ORDERS'),('missing_signal','MISSING_HOLDING_SIGNAL'),('hash','SIGNAL_HASH_MISMATCH'),('policy','EXECUTION_POLICY_HASH_MISMATCH'),('refresh','REFRESH_LIMIT'),('daily','EXECUTION_DATA_INSUFFICIENT')])
def test_batch_blocks(mutation,reason):
    s,a,q,p=fixture()
    if mutation=='old_quote': q.loc[0,'quote_time']='2026-09-08T13:34:00+00:00'
    if mutation=='crossed': q.loc[0,'bid']=101
    if mutation=='delayed': q.loc[0,'delayed']=True
    if mutation=='skew': q.loc[0,'quote_time']='2026-09-08T13:34:50+00:00'
    if mutation=='old_account': a['asof']='2026-09-08T13:30:00+00:00'
    if mutation=='unknown_orders': a['open_orders_known']=False
    if mutation=='cancel_pending': a['open_orders']=[{'status':'CANCEL_PENDING'}]
    if mutation=='missing_signal': s['ranking']=s['ranking'][:-1]; s['signal_hash']=signal_hash(s)
    if mutation=='hash': s['ranking'][0]['score']=99
    if mutation=='policy': p['epsilon_buy']=.01
    if mutation=='refresh': p['refresh_count']=3
    if mutation=='daily': q=q.drop(columns=['bid','ask'])
    r=run(s,a,q,p)
    assert r['run_status'] in ('BLOCKED','EXPIRED')
    assert any(reason in c for c in r['checks'])
    assert all(x['approved_trade_shares']==0 for x in r['orders'])

def test_gap_skip_and_split_anchor():
    s,a,q,p=fixture(); q.loc[2,['bid','ask']]=80
    r=run(s,a,q,p); c=r['orders'][2]
    assert c['approved_trade_shares']==0 and c['model_target_shares']==37
    assert 'GAP_PROTECTION' in c['blocking_reasons']
    s,a,q,p=fixture(); s['anchors']['AAA']['effective_split_ratio']=2; s['signal_hash']=signal_hash(s)
    q.loc[0,['bid','ask']]=50; a['positions'][0]['shares']=40
    c=run(s,a,q,p)['orders'][0]
    assert D(c['anchor'])==50 and D(c['gap_action'])==0
    assert c['actual_shares']==40

def test_expired_and_superseded_do_not_cancel_orders():
    s,a,q,p=fixture(); a['open_orders']=[{'status':'PARTIALLY_FILLED'}]
    s['superseded_by_execution_id']='new'; s['signal_hash']=signal_hash(s)
    r=run(s,a,q,p); assert r['run_status']=='SUPERSEDED' and 'OPEN_ORDERS' in r['checks']
    assert a['open_orders'][0]['status']=='PARTIALLY_FILLED'
    s.pop('superseded_by_execution_id'); s['valid_until']=NOW; s['signal_hash']=signal_hash(s)
    assert run(s,a,q,p)['run_status']=='EXPIRED'

def test_frozen_weight_reprices_175_to_188():
    s,a,q,p=fixture(); p['single_name_cap']=1;p['sector_cap']=1
    s['execution_policy_hash']=policy_hash(p);s['target_weights']=[dict(security_id='AAA',target_weight=.05,sector='S0',rank=1)]
    s['anchors']['AAA']['prior_raw_close']=175;s['signal_hash']=signal_hash(s)
    models=[]
    for price in (175,188):
        a['positions']=[dict(security_id='AAA',shares=20)];a['cash']=str(100000-20*price);a['buying_power']=a['cash']
        q=q.iloc[:1].copy();q.loc[0,['bid','ask']]=price
        r=run(s,a,q,p);models.append(r['orders'][0]['model_target_shares'])
        assert r['signal_hash']==s['signal_hash'] and r['ranking']==s['ranking']
    assert models==[28,26]

def test_limit_budget_reduces_buy_despite_midpoint():
    s,a,q,p=fixture(); p.update(cash_min=0,single_name_cap=1,sector_cap=1,epsilon_buy='.01',fee_rate='.001')
    s['execution_policy_hash']=policy_hash(p);s['target_weights']=[dict(security_id='AAA',target_weight=1,rank=1,sector='S0')];s['signal_hash']=signal_hash(s)
    a.update(positions=[],cash='1000',buying_power='1000');q=q.iloc[:1]
    row=run(s,a,q,p)['orders'][0]
    assert row['model_target_shares']==10 and row['approved_trade_shares']==9
    assert D(row['limit_price'])==101

def test_skipped_sell_rechecks_actual_exposure():
    s,a,q,p=fixture();p['single_name_cap']='.35';s['execution_policy_hash']=policy_hash(p);s['signal_hash']=signal_hash(s)
    s['anchors']['BBB']['prior_raw_close']=100;s['signal_hash']=signal_hash(s)
    r=run(s,a,q,p)
    assert r['run_status']=='BLOCKED'
    assert 'POST_EXECUTION_SINGLE_NAME_CAP:BBB' in r['checks']
    assert all(x['approved_trade_shares']==0 for x in r['orders'])

def test_independent_given_fills_cash_and_turnover():
    from us_equity_alpha.account import apply_fills
    s,a,q,p=fixture()
    fills=[dict(fill_id=str(i),security_id=k,quantity=abs(qty),side='BUY' if qty>0 else 'SELL',price=str(price),fee=str(D(abs(qty))*D(price)*D('.001'))) for i,(k,qty,price) in enumerate([('AAA',10,100),('BBB',-10,60),('CCC',42,70),('DDD',-10,50)])]
    actual=apply_fills(a,fills)
    assert D(actual['cash'])==D('1054.96')
    notional=sum(D(f['quantity'])*D(f['price']) for f in fills)
    assert notional/D(10000)==D('.504')


def test_non_target_holding_changes_complete_nav():
    s,a,q,p=fixture();q.loc[3,['bid','ask']]=100
    r=run(s,a,q,p)
    assert D(r['summary']['equity_snapshot'])==10500
    assert r['orders'][0]['model_target_shares']==31

def test_quote_time_contradiction_and_missing_new_quote():
    s,a,q,p=fixture(); q.loc[0,'received_at']='2026-09-08T13:34:59+00:00'
    assert run(s,a,q,p)['run_status']=='BLOCKED'
    s,a,q,p=fixture();q=q[q.security_id!='CCC']
    r=run(s,a,q,p);assert r['run_status']=='REVIEW'
    c=next(x for x in r['orders'] if x['security_id']=='CCC')
    assert c['model_target_shares'] is None and c['approved_trade_shares']==0
    assert c['weight_intended']=='0.3'

def test_non_cent_tick_and_non_rebalance():
    assert limit_price('BUY',D('100'),D('101.03'),D('98'),D('102'),D('0'),D('.05'))==D('101.00')
    s,a,q,p=fixture();s['rebalance_due']=False;s['signal_hash']=signal_hash(s)
    r=run(s,a,q,p);assert r['run_status']=='NO_REBALANCE'
    assert all(x['approved_trade_shares']==0 for x in r['orders'])

def test_fixture_approved_shares_are_reproducible():
    b=json.loads((Path(__file__).parent/'fixtures/daily_bundle.json').read_text())
    r=build_execution_plan(b['signal_snapshot'],b['account'],pd.DataFrame(b['quotes']),b['execution_policy'],now=NOW)
    assert r['orders']==b['execution_plan']['orders']


@pytest.mark.parametrize('cutoff',[None,NOW,'2026-09-08T13:35:01+00:00','2026-09-07T20:00:00'])
def test_signal_cutoff_must_precede_execution(cutoff):
    s,a,q,p=fixture()
    if cutoff is None: s.pop('signal_cutoff')
    else: s['signal_cutoff']=cutoff
    s['signal_hash']=signal_hash(s)
    r=run(s,a,q,p)
    assert r['run_status']=='BLOCKED'
    assert 'INVALID_SIGNAL_CUTOFF' in r['checks']
    assert all(x['approved_trade_shares']==0 for x in r['orders'])
