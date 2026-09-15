"""R2 pure target construction; no orders, execution approval or return inputs.

The B2 variants use eligible securities independently of factor availability.
Policy hashes include the entire supplied policy, not just target parameters.
"""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import hashlib
import json
import math


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _number(value):
    if isinstance(value, bool):
        raise ValueError('INVALID_POLICY_NUMBER')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('INVALID_POLICY_NUMBER') from exc
    if not number.is_finite():
        raise ValueError('INVALID_POLICY_NUMBER')
    return number


def _policy(policy):
    p = policy.get('portfolio', policy)
    values = {k: _number(p[k]) for k in ('long_fraction', 'min_cash', 'max_single_name_weight', 'max_sector_weight')}
    if not (0 < values['long_fraction'] <= 1 and 0 <= values['min_cash'] < 1
            and 0 < values['max_single_name_weight'] <= 1 and 0 < values['max_sector_weight'] <= 1):
        raise ValueError('INVALID_TARGET_POLICY')
    return values, _hash(policy)


def _eligible(rows):
    if hasattr(rows, 'to_dict'):
        rows = rows.to_dict('records')
    seen = set(); eligible = []
    for r in rows:
        sid = r.get('security_id')
        if not isinstance(sid, str) or not sid.strip() or sid == 'CASH' or sid in seen:
            raise ValueError('INVALID_OR_DUPLICATE_SECURITY_ID')
        seen.add(sid)
        if type(r.get('eligible')) is not bool:
            raise ValueError('ELIGIBILITY_MUST_BE_BOOLEAN')
        if r['eligible']:
            eligible.append(dict(r))
    return sorted(eligible, key=lambda r: r['security_id'])


def _sector(row):
    value = row.get('sector')
    return value if isinstance(value, str) and value.strip() else None


def _finish(weights, rows, policy_hash, construction, **metadata):
    targets = [dict(security_id=r['security_id'], sector=_sector(r), target_weight=str(weights[r['security_id']])) for r in rows]
    targets.append(dict(security_id='CASH', sector=None, target_weight=str(Decimal(1)-sum(weights.values(), Decimal(0)))))
    return dict(status='TARGETS_READY', construction=construction, policy_hash=policy_hash,
                targets=targets, target_only=True, **metadata)


def _blocked(policy_hash, reason, **metadata):
    return dict(status='NOT_EVALUABLE', policy_hash=policy_hash, reason=reason, targets=[], target_only=True, **metadata)


def select_alpha_targets(rows, policy):
    """Top fraction, equal base weights, sector scan with residual cash."""
    p, ph = _policy(policy); eligible = _eligible(rows)
    rankable = []
    for row in eligible:
        score = row.get('score')
        if isinstance(score, bool):
            continue
        try:
            valid = math.isfinite(float(score))
        except (ValueError, TypeError, OverflowError):
            valid = False
        if valid:
            row['score'] = float(score); rankable.append(row)
    rankable.sort(key=lambda r: (-r['score'], r['security_id']))
    n = int((len(rankable)*p['long_fraction']).to_integral_value(rounding=ROUND_FLOOR))
    meta = dict(eligible_count=len(eligible), rankable_count=len(rankable), n_long=n)
    if not n:
        return _blocked(ph, 'INSUFFICIENT_RANKABLE_UNIVERSE', **meta)
    if any(_sector(r) is None for r in rankable):
        return _blocked(ph, 'PIT_SECTOR_UNAVAILABLE', **meta)
    base = min((1-p['min_cash'])/n, p['max_single_name_weight'])
    selected = []; exposure = {}; exclusions = []
    for rank, row in enumerate(rankable, 1):
        sector = row['sector']
        if len(selected) == n:
            exclusions.append(dict(security_id=row['security_id'], reason='OUTSIDE_SELECTION')); continue
        if exposure.get(sector, Decimal(0))+base > p['max_sector_weight']:
            exclusions.append(dict(security_id=row['security_id'], reason='SECTOR_CAP')); continue
        exposure[sector] = exposure.get(sector, Decimal(0))+base
        selected.append(dict(row, rank=rank))
    return _finish({r['security_id']: base for r in selected}, selected, ph, 'ALPHA_RANK_SCAN',
                   base_weight=str(base), exclusions=exclusions,
                   selected_outside_raw_top_count=sum(r['rank']>n for r in selected), **meta)


def build_benchmarks(rows, policy):
    """Build independent B2 target universes; never inspect score or rank."""
    p, ph = _policy(policy)
    eligible = [dict(security_id=r['security_id'], sector=_sector(r)) for r in _eligible(rows)]
    if not eligible:
        return {name: _blocked(ph, 'EMPTY_ELIGIBLE_UNIVERSE') for name in ('B2_equal_weight', 'B2_risk_matched')}
    base = min((1-p['min_cash'])/len(eligible), p['max_single_name_weight'])
    weights = {r['security_id']: base for r in eligible}
    meta = dict(eligible_count=len(eligible), input_hash=_hash(eligible), alpha_rank_used=False)
    equal = _finish(weights, eligible, ph, 'ELIGIBLE_EQUAL_WEIGHT', sector_constraint='DISABLED_BY_BENCHMARK_DEFINITION', **meta)
    if any(r['sector'] is None for r in eligible):
        risk = _blocked(ph, 'PIT_SECTOR_UNAVAILABLE', **meta)
    else:
        totals = {}
        for r in eligible:
            totals[r['sector']] = totals.get(r['sector'], Decimal(0))+base
        for r in eligible:
            total = totals[r['sector']]
            if total > p['max_sector_weight']:
                weights[r['security_id']] = base*p['max_sector_weight']/total
        risk = _finish(weights, eligible, ph, 'ELIGIBLE_SECTOR_PROPORTIONAL', sector_constraint='APPLIED', **meta)
    return dict(B2_equal_weight=equal, B2_risk_matched=risk)
