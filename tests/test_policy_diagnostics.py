import pytest
from us_equity_alpha.policy_diagnostics import coverage_gate, quantile_weights, diagnostic_returns, evaluate_gates, turnover_summary


def test_family_coverage():
    assert coverage_gate(1000, 950, 'OHLCV')['status'] == 'PASS'
    assert coverage_gate(1000, 949, 'OHLCV')['status'] == 'FAIL'
    assert coverage_gate(1000, 800, 'NEWS')['status'] == 'COVERAGE_POLICY_UNREGISTERED'
    registration = dict(family_id='NEWS', version='1', threshold=.72, observation_range='audit', basis='missingness', approved_at='2026-09-09', hash='abc')
    assert coverage_gate(1000, 720, 'NEWS', registration)['status'] == 'PASS'
    assert coverage_gate(400, 400, 'OHLCV')['status'] == 'FAIL'
    with pytest.raises(ValueError): coverage_gate(100, 101, 'OHLCV')


def test_groups_ties_and_sizes():
    rows = quantile_weights({f's{i:04}': 1.0 for i in range(1001)})
    assert sum(r['long_weight'] > 0 for r in rows) == 100
    assert sum(r['short_weight'] < 0 for r in rows) == 100
    assert all(not (r['long_weight'] and r['short_weight']) for r in rows)
    sizes = [sum(r['quantile'] == q for r in rows) for q in range(1, 11)]
    assert max(sizes)-min(sizes) <= 1
    assert rows == quantile_weights(dict(reversed(list({f's{i:04}': 1.0 for i in range(1001)}.items()))))
    with pytest.raises(ValueError): quantile_weights({'x': float('nan')})


def test_return_normalization():
    rows = quantile_weights({str(i): i for i in range(20)})
    ret = {r['security_id']: .03 if r['long_weight'] else -.01 for r in rows}
    result = diagnostic_returns(rows, ret)
    assert result['gmb'] == pytest.approx(.04)
    assert result['ls_50_50'] == pytest.approx(.02)
    ret.pop(rows[0]['security_id'])
    assert diagnostic_returns(rows, ret)['status'] == 'NOT_EVALUABLE'


def good_metrics():
    return dict(mean_rank_ic=.02, gmb_mean=.01, gmb_ci_lower=.001, complete_years=3, positive_joint_years=2, halfyear_segments=6, positive_ic_halfyears=4, active_mean=.001, active_ci_lower=.0001, max_drawdown=.2, stress_active_return=.01, risk_audit_pass=True, execution_complete=True, corporate_actions_complete=True, benchmark='B2_risk_matched')


def test_gates_independent_and_missing():
    metrics = good_metrics()
    out = evaluate_gates('PASS', metrics, {'monotonicity': .72, 'icir': .01})
    assert out['alpha_gate'] == 'ALPHA_VALIDATION_PASS'
    assert out['portfolio_gate'] == 'PORTFOLIO_VALIDATION_PASS'
    metrics['execution_complete'] = False
    out = evaluate_gates('PASS', metrics)
    assert out['alpha_gate'] == 'ALPHA_VALIDATION_PASS'
    assert out['portfolio_gate'] == 'PORTFOLIO_VALIDATION_FAIL'
    del metrics['gmb_ci_lower']
    assert evaluate_gates('PASS', metrics)['alpha_gate'] == 'NOT_EVALUABLE'
    with pytest.raises(ValueError): evaluate_gates('PASS', dict(good_metrics(), mean_rank_ic=True))


def test_turnover_denominator_missing_and_initial():
    out = turnover_summary([dict(desired=.3, approved=.2, executed=.1, initial=True), dict(desired=None, approved=None, executed=None), dict(desired=.2, approved=.1, executed=0)])
    assert out['binding_rate'] == .5
    assert out['scheduled_batches'] == 3
    assert out['computable_batches'] == 2
    assert out['missing_batches'] == 1
    assert out['initial_batches'] == 1
    with pytest.raises(ValueError): turnover_summary([dict(desired=-1)])
