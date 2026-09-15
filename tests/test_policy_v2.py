import pytest
from us_equity_alpha.policy_v2 import load_policy, choose_representative

def test_policy_is_approved_but_not_a_trading_release():
    p=load_policy('config/policy_v2.json')
    assert p['approval']['status']=='APPROVED_FOR_IMPLEMENTATION'
    assert p['live_enabled'] is False

def test_representative_stays_when_adv_crosses():
    rows=[{'security_id':'A','eligible':True,'adv60':10},{'security_id':'B','eligible':True,'adv60':20}]
    assert choose_representative(rows,'A')=='A'
    rows[0]['eligible']=False
    assert choose_representative(rows,'A')=='B'

def test_unknown_representative_is_not_false():
    with pytest.raises(ValueError,match='UNKNOWN'):
        choose_representative([{'security_id':'A','eligible':None,'adv60':10}], 'A')
