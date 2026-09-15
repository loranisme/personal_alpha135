import pytest
from us_equity_alpha.economic_descriptions import describe_local_expression

@pytest.mark.parametrize('expression,tag', [
 ('-(close / ts_delay(close, 5) - 1)', 'mean_reversion'),
 ('close / ts_delay(close, 20) - 1', 'momentum'),
 ('ts_mean(close,20)/close', 'mean_reversion'),
 ('-ts_std_dev(close/ts_delay(close,1)-1,20)', 'realized_risk'),
 ('-ts_corr(open,volume,10)', 'price_volume_covariation'),
 ('volume/ts_mean(volume,20)', 'trading_activity'),
 ('close/open-1', 'intraday_return'),
 ('open/ts_delay(close,1)-1', 'overnight_return'),
 ('(close-low)/(high-low)', 'range_position'),
 ('(vwap-close)/vwap', 'vwap_deviation'),
 ('ts_mean(abs(close/ts_delay(close,1)-1)/(volume*(open+close)/2),20)', 'illiquidity_proxy'),
])
def test_relationships(expression, tag):
 assert tag in describe_local_expression(expression)['mechanism_tags']

def test_direction_and_nonmonotonicity():
 a=describe_local_expression('ts_corr(open,volume,10)')
 b=describe_local_expression('-ts_corr(open,volume,10)')
 assert a['measurement_text'] != b['measurement_text']
 c=describe_local_expression('rank(close/ts_delay(close,5)-1)*rank(ts_mean(close,20)/close)')
 assert c['interpretation_status']=='AMBIGUOUS'
 assert '排序' in c['hypothesis_text']

@pytest.mark.parametrize('expression',['cap','industry','cashflow_op/close','rank(close)','__import__("os")'])
def test_no_spurious_price_story(expression):
 result=describe_local_expression(expression)
 assert result['mechanism_tags']==['unclassified']
 assert result['interpretation_status']=='AMBIGUOUS'

def test_deterministic_and_schema():
 a=describe_local_expression('-ts_mean(returns,3)')
 assert a==describe_local_expression('-ts_mean(returns,3)')
 assert set(a)=={'mechanism_tags','hypothesis_text','measurement_text','interpretation_status','limitations'}
 assert isinstance(a['limitations'],list)

def test_absolute_returns_are_not_directional_momentum():
 result=describe_local_expression('abs(returns)')
 assert 'momentum' not in result['mechanism_tags']
 assert 'mean_reversion' not in result['mechanism_tags']

def test_functional_arithmetic():
 result=describe_local_expression('reverse(subtract(divide(close,ts_delay(close,5)),1))')
 assert 'mean_reversion' in result['mechanism_tags']

@pytest.mark.parametrize('expression',[
 '0-(close/ts_delay(close,5)-1)',
 '-1*(close/ts_delay(close,5)-1)',
 '(close/ts_delay(close,5)-1)*-1',
])
def test_equivalent_negative_direction(expression):
 result=describe_local_expression(expression)
 assert 'momentum' not in result['mechanism_tags']
 assert 'mean_reversion' in result['mechanism_tags'] or result['interpretation_status']=='AMBIGUOUS'

def test_benchmark_beta_change_structure():
 r='(close/ts_delay(close,1)-1)'
 beta=lambda w:f'ts_covariance({r},benchmark_returns,{w})/ts_covariance(benchmark_returns,benchmark_returns,{w})'
 result=describe_local_expression(f'-({beta(30)}-({beta(360)}))')
 assert 'benchmark_beta_change' in result['mechanism_tags']
 assert 'momentum' not in result['mechanism_tags']
 assert '基准' in result['measurement_text']

def test_benchmark_field_alone_does_not_prove_beta():
 assert 'benchmark_beta_change' not in describe_local_expression('benchmark_returns')['mechanism_tags']

def test_reciprocal_return_is_not_inferred_momentum():
 result=describe_local_expression('1/(close/ts_delay(close,5)-1)')
 assert result['interpretation_status']=='AMBIGUOUS'
 assert 'momentum' not in result['mechanism_tags']

def test_subtraction_of_signals_is_ambiguous():
 result=describe_local_expression('(close/ts_delay(close,5)-1)-(close/ts_delay(close,20)-1)')
 assert result['interpretation_status']=='AMBIGUOUS'


@pytest.mark.parametrize('expression', [
    '(close/ts_delay(close,5)-1)**2',
    '0*(close/ts_delay(close,5)-1)',
    '1-(close/ts_delay(close,5)-1)',
])
def test_nonmonotone_or_zero_transforms_do_not_claim_momentum(expression):
    result=describe_local_expression(expression)
    assert not (result['interpretation_status']=='INFERRED' and 'momentum' in result['mechanism_tags'])
