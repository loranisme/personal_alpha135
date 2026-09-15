from decimal import Decimal
import pytest
from us_equity_alpha.policy_portfolio import select_alpha_targets, build_benchmarks

POLICY = dict(long_fraction=0.10, min_cash=0.05, max_single_name_weight=0.02, max_sector_weight=0.25)

def rows(n=1000):
    return [dict(security_id=f'S{i:04}', eligible=True, score=1000-i, sector=f'SEC{i//100}') for i in range(n)]

def weight(result, sid):
    return Decimal(next(r['target_weight'] for r in result['targets'] if r['security_id']==sid))

def test_alpha_scan_sector_and_preserve_base():
    result=select_alpha_targets(rows(), POLICY)
    assert result['status']=='TARGETS_READY'
    assert result['n_long']==100
    assert weight(result,'S0000')==Decimal('0.0095')
    assert 'S0026' not in {r['security_id'] for r in result['targets']}
    assert weight(result,'CASH')==Decimal('0.05')
    assert result['selected_outside_raw_top_count']>0

def test_short_pool_cap_keeps_cash():
    result=select_alpha_targets(rows(50), POLICY)
    assert result['n_long']==5
    assert weight(result,'CASH')==Decimal('0.90')

def test_benchmarks_no_rank_dependency_and_shared_hash():
    data=rows()
    first=build_benchmarks(data,POLICY)
    for row in data:
        row['score']=float('nan'); row['rank']='IGNORE'
    second=build_benchmarks(list(reversed(data)),POLICY)
    assert first==second
    assert first['B2_equal_weight']['policy_hash']==first['B2_risk_matched']['policy_hash']==select_alpha_targets(rows(),POLICY)['policy_hash']
    assert len(first['B2_equal_weight']['targets'])==1001

def test_riskmatched_proportional_sector_reduction():
    data=rows(100)
    result=build_benchmarks(data,POLICY)
    assert weight(result['B2_equal_weight'],'CASH')==Decimal('0.05')
    assert weight(result['B2_risk_matched'],'CASH')==Decimal('0.75')
    assert weight(result['B2_risk_matched'],'S0000')==Decimal('0.0025')

def test_missing_sector_failclosed_without_suppressing_equal():
    data=rows(100); data[0]['sector']=None
    assert select_alpha_targets(data,POLICY)['status']=='NOT_EVALUABLE'
    result=build_benchmarks(data,POLICY)
    assert result['B2_risk_matched']['status']=='NOT_EVALUABLE'
    assert result['B2_equal_weight']['status']=='TARGETS_READY'

def test_unrankable_is_not_zero_and_eligible_denominator():
    data=rows(100); data[0]['score']=None; data[1]['eligible']=False
    result=select_alpha_targets(data,POLICY)
    assert result['eligible_count']==99
    assert result['rankable_count']==98
    assert result['n_long']==9

@pytest.mark.parametrize('mutation', ['duplicate','cash_id','non_boolean'])
def test_bad_identity_rejected(mutation):
    data=rows(100)
    if mutation=='duplicate': data[1]['security_id']=data[0]['security_id']
    elif mutation=='cash_id': data[0]['security_id']='CASH'
    else: data[0]['eligible']='false'
    with pytest.raises(ValueError): build_benchmarks(data,POLICY)

def test_invalid_policy_rejected():
    with pytest.raises(ValueError): select_alpha_targets(rows(),dict(POLICY,min_cash=float('nan')))
