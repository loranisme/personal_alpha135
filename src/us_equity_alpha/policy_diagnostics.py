"""R2 deterministic diagnostics; no execution filters or evidence certification.

Gate outputs describe supplied statistics, not independent evidence maturity.
The caller must bind these inputs to its immutable dataset/protocol manifest.
"""
from __future__ import annotations
import math
from numbers import Real


def _number(value, name, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f'{name}: finite numeric value required')
    if low is not None and value < low or high is not None and value > high:
        raise ValueError(f'{name}: out of bounds')
    return float(value)


def _count(value, name):
    _number(value, name, 0)
    if not isinstance(value, int):
        raise ValueError(f'{name}: integer required')
    return value


def coverage_gate(eligible, rankable, family, registration=None):
    """Denominator is full eligible universe, never vendor-covered subset."""
    _count(eligible, 'eligible'); _count(rankable, 'rankable')
    if rankable > eligible or not isinstance(family, str) or not family:
        raise ValueError('invalid coverage inputs')
    threshold = .95 if family in {'PRICE', 'OHLCV'} else None
    if threshold is None:
        required = {'family_id', 'version', 'threshold', 'observation_range', 'basis', 'approved_at', 'hash'}
        if not isinstance(registration, dict) or not required.issubset(registration) or any(not registration[k] for k in required) or registration['family_id'] != family:
            return {'status': 'COVERAGE_POLICY_UNREGISTERED', 'family_id': family}
        threshold = _number(registration['threshold'], 'threshold', 0, 1)
    ratio = rankable / eligible if eligible else None
    return dict(status='PASS' if ratio is not None and ratio >= threshold and rankable >= 500 else 'FAIL', family_id=family, eligible=eligible, rankable=rankable, coverage=ratio, threshold=threshold, minimum_rankable=500)


def quantile_weights(scores):
    """Stable ascending ranks: quantile 10 is highest; long/short gross 50% each."""
    if not isinstance(scores, dict):
        raise ValueError('scores must map stable security IDs to scores')
    for key, value in scores.items():
        if not isinstance(key, str) or not key:
            raise ValueError('security_id required')
        _number(value, 'score')
    size = len(scores)
    if size < 20:
        raise ValueError('at least 20 rankable securities required')
    ranked = sorted(scores, key=lambda key: (scores[key], key))
    k = size // 10
    return [dict(security_id=key, score=float(scores[key]), rank=i+1, quantile=min(10, i*10//size+1), long_weight=.5/k if i >= size-k else 0., short_weight=-.5/k if i < k else 0.) for i, key in enumerate(ranked)]


def diagnostic_returns(weights, forward_returns):
    """Missing labels block aggregate rather than renormalizing survivors."""
    missing = [r['security_id'] for r in weights if forward_returns.get(r['security_id']) is None]
    if missing:
        return dict(status='NOT_EVALUABLE', missing_security_ids=missing, gmb=None, ls_50_50=None)
    for r in weights:
        _number(forward_returns[r['security_id']], 'forward_return')
    ls = sum((r['long_weight']+r['short_weight'])*forward_returns[r['security_id']] for r in weights)
    return dict(status='DIAGNOSTIC_ONLY', gmb=2*ls, ls_50_50=ls, gmb_gross=2., ls_gross=1.)


def evaluate_gates(data_gate, metrics, supporting=None):
    """Frozen R2 metric thresholds; missing values never count as passing.

    This does not grant holdout, release, or real-world evidence status.
    """
    if data_gate not in {'PASS', 'FAIL', 'NOT_EVALUABLE', 'COVERAGE_POLICY_UNREGISTERED'}:
        raise ValueError('unrecognized data gate')
    if not isinstance(metrics, dict):
        raise ValueError('metrics mapping required')
    numeric = {'mean_rank_ic', 'gmb_mean', 'gmb_ci_lower', 'active_mean', 'active_ci_lower', 'max_drawdown', 'stress_active_return'}
    counts = {'complete_years', 'positive_joint_years', 'halfyear_segments', 'positive_ic_halfyears'}
    flags = {'risk_audit_pass', 'execution_complete', 'corporate_actions_complete'}
    for key in numeric & metrics.keys():
        if metrics[key] is not None:
            _number(metrics[key], key)
    for key in counts & metrics.keys():
        if metrics[key] is not None: _count(metrics[key], key)
    for key in flags & metrics.keys():
        if metrics[key] is not None and not isinstance(metrics[key], bool): raise ValueError(f'{key}: boolean required')
    for numerator, denominator in [('positive_joint_years', 'complete_years'), ('positive_ic_halfyears', 'halfyear_segments')]:
        if metrics.get(numerator) is not None and metrics.get(denominator) is not None and metrics[numerator] > metrics[denominator]: raise ValueError('positive count exceeds total')
    if metrics.get('max_drawdown') is not None: _number(metrics['max_drawdown'], 'max_drawdown', 0, 1)
    alpha_fields = counts | {'mean_rank_ic', 'gmb_mean', 'gmb_ci_lower'}
    portfolio_fields = flags | {'active_mean', 'active_ci_lower', 'max_drawdown', 'stress_active_return', 'benchmark'}
    missing_alpha = sorted(k for k in alpha_fields if metrics.get(k) is None)
    missing_portfolio = sorted(k for k in portfolio_fields if metrics.get(k) is None)
    alpha = 'NOT_EVALUABLE'
    if data_gate == 'PASS' and not missing_alpha and metrics['complete_years'] >= 3 and metrics['halfyear_segments'] > 0:
        passed = metrics['mean_rank_ic'] > .01 and metrics['gmb_mean'] > 0 and metrics['gmb_ci_lower'] > 0 and 3*metrics['positive_joint_years'] >= 2*metrics['complete_years'] and 3*metrics['positive_ic_halfyears'] >= 2*metrics['halfyear_segments']
        alpha = 'ALPHA_VALIDATION_PASS' if passed else 'ALPHA_VALIDATION_FAIL'
    portfolio = 'NOT_EVALUABLE'
    if alpha == 'ALPHA_VALIDATION_FAIL':
        portfolio = 'PORTFOLIO_VALIDATION_FAIL'
    elif alpha == 'ALPHA_VALIDATION_PASS' and not missing_portfolio and metrics['benchmark'] == 'B2_risk_matched':
        passed = metrics['active_mean'] > 0 and metrics['active_ci_lower'] > 0 and metrics['max_drawdown'] <= .25 and metrics['stress_active_return'] > 0 and all(metrics[k] for k in flags)
        portfolio = 'PORTFOLIO_VALIDATION_PASS' if passed else 'PORTFOLIO_VALIDATION_FAIL'
    return dict(data_gate=data_gate, alpha_gate=alpha, supporting_diagnostics=dict(supporting or {}), portfolio_gate=portfolio, execution_status='NOT_EVALUABLE' if metrics.get('execution_complete') is None else ('PASS' if metrics['execution_complete'] else 'FAIL'), missing_alpha=missing_alpha, missing_portfolio=missing_portfolio, evidence_certified=False)


def turnover_summary(batches, cap=.20):
    _number(cap, 'cap', 0, 1)
    rows = []
    for batch in batches:
        row = dict(batch)
        for key in ('desired', 'approved', 'executed'):
            if row.get(key) is not None: _number(row[key], key, 0)
        if 'initial' in row and not isinstance(row['initial'], bool): raise ValueError('initial must be boolean')
        row['turnover_cap_binding'] = None if row.get('desired') is None else row['desired'] > cap
        rows.append(row)
    available = [r for r in rows if r['turnover_cap_binding'] is not None]
    bound = sum(r['turnover_cap_binding'] for r in available)
    return dict(scheduled_batches=len(rows), computable_batches=len(available), missing_batches=len(rows)-len(available), initial_batches=sum(r.get('initial', False) for r in rows), binding_batches=bound, binding_rate=bound/len(available) if available else None, cap=cap, batches=rows)
