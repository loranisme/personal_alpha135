"""Pure, fail-closed local execution drafts. No order or cancellation transport."""
from copy import deepcopy
from datetime import timedelta
from decimal import ROUND_FLOOR, ROUND_CEILING
import hashlib
import json
import pandas as pd
from .account import D, positions
from .portfolio import plan_rebalance
from .quotes import timestamp, validate_quotes

def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()).hexdigest()

def signal_hash(snapshot):
    return _hash({k:v for k,v in snapshot.items() if k!='signal_hash'})

def policy_hash(policy):
    return _hash({k:v for k,v in policy.items() if k not in {'refresh_count','expected_signal_hash','supersedes_execution_id','execution_id'}})

def limit_price(side,bid,ask,guard_low,guard_high,epsilon,tick):
    if side not in ('BUY','SELL') or tick<=0 or epsilon<0: raise ValueError('INVALID_LIMIT_ARGUMENT')
    raw=min(guard_high,ask*(1+epsilon)) if side=='BUY' else max(guard_low,bid*(1-epsilon))
    return (raw/tick).to_integral_value(rounding=ROUND_FLOOR if side=='BUY' else ROUND_CEILING)*tick

def build_execution_plan(signal_snapshot,account,quotes,policy,*,now=None):
    s=deepcopy(signal_snapshot); p=deepcopy(policy)
    now=timestamp(now if now is not None else p['snapshot_at'])
    checks=[]; status=None
    try:
        if timestamp(s['signal_cutoff'])>=now: raise ValueError('INVALID_SIGNAL_CUTOFF')
    except (KeyError,ValueError,TypeError): checks.append('INVALID_SIGNAL_CUTOFF')
    required=['feed','max_quote_age_seconds','max_account_age_seconds','max_cross_security_skew_seconds','draft_ttl_seconds','gap_up','gap_down','epsilon_buy','epsilon_sell','tick','cash_min','single_name_cap','sector_cap','fee_rate','max_refreshes']
    missing=[k for k in required if p.get(k) is None]
    if missing: checks+=['MISSING_POLICY:'+k for k in missing]
    try:
        if not 0<=D(p['cash_min'])<1 or not 0<D(p['single_name_cap'])<=1 or not 0<D(p['sector_cap'])<=1 or D(p['fee_rate'])<0 or D(p['tick'])<=0: raise ValueError('INVALID_POLICY')
        for side in ('BUY','SELL'):
            if not 0<=D(p['gap_down'][side])<1 or D(p['gap_up'][side])<0: raise ValueError('INVALID_POLICY')
        if any(D(p[k])<0 for k in ('epsilon_buy','epsilon_sell','max_quote_age_seconds','max_account_age_seconds','max_cross_security_skew_seconds','draft_ttl_seconds','max_refreshes')): raise ValueError('INVALID_POLICY')
    except (KeyError,ValueError,TypeError,ArithmeticError): checks.append('INVALID_POLICY')
    for field in ('cash','buying_power','receivables','positions','asof','open_orders_known','open_orders'):
        if field not in account: checks.append('MISSING_ACCOUNT_FIELD:'+field)
    if s.get('signal_hash')!=signal_hash(s): checks.append('SIGNAL_HASH_MISMATCH')
    if p.get('expected_signal_hash',s.get('signal_hash'))!=s.get('signal_hash'): checks.append('PARENT_SIGNAL_HASH_MISMATCH')
    if s.get('execution_policy_hash')!=policy_hash(p): checks.append('EXECUTION_POLICY_HASH_MISMATCH')
    if p.get('refresh_count',0)>p.get('max_refreshes',0): checks.append('REFRESH_LIMIT')
    if not account.get('open_orders_known',False): checks.append('UNKNOWN_OPEN_ORDERS')
    if any(o.get('status') not in ('FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED') for o in account.get('open_orders',[])): checks.append('OPEN_ORDERS')
    if account.get('reconciled') is not True: checks.append('ACCOUNT_NOT_RECONCILED')
    try:
        age=(now-timestamp(account['asof'])).total_seconds()
        if age<0 or age>p.get('max_account_age_seconds',0): checks.append('STALE_ACCOUNT')
    except (KeyError,ValueError,TypeError): checks.append('INVALID_ACCOUNT_TIME')
    valid_until=now
    try:
        start=timestamp(s['execution_window_start']); end=timestamp(s['execution_window_end']); expiry=timestamp(s['valid_until'])
        valid_until=min(now+timedelta(seconds=p.get('draft_ttl_seconds',0)),end,expiry)
        if now<start: checks.append('OUTSIDE_EXECUTION_WINDOW')
        if now>=min(end,expiry): status='EXPIRED'; checks.append('EXPIRED')
    except (KeyError,ValueError,TypeError): checks.append('INVALID_EXECUTION_WINDOW')
    if s.get('rebalance_due') is not True: status='NO_REBALANCE'; checks.append('NO_REBALANCE')
    if s.get('superseded_by_execution_id'): status='SUPERSEDED'; checks.append('SUPERSEDED')
    if p.get('mode','PAPER')=='MANUAL_LIVE' and not p.get('g6_enabled',False): checks.append('G6_NOT_ENABLED')
    if not {'bid','ask','security_id'}.issubset(quotes.columns):
        checks.append('EXECUTION_DATA_INSUFFICIENT'); quotes=pd.DataFrame(columns=['security_id','bid','ask'])
    qerrors=validate_quotes(quotes,p,now) if not missing else {}
    qmap={r['security_id']:r for r in quotes.to_dict('records')}
    try: pos=positions(account)
    except (ValueError,KeyError): pos={}; checks.append('INVALID_POSITIONS')
    ranking={r['security_id']:r for r in s.get('ranking',[])}
    for key,qty in pos.items():
        if qty and (key not in ranking or not ranking[key].get('valid',ranking[key].get('eligible',False)) or not ranking[key].get('sector')): checks.append('MISSING_HOLDING_SIGNAL_OR_CLASSIFICATION:'+key)
        if qty and (key not in qmap or qerrors.get(key)): checks.append('INVALID_HOLDING_QUOTE:'+key)
    validq=[]
    for key,q in qmap.items():
        try:
            if 0<D(q['bid'])<=D(q['ask']): validq.append(q)
        except (KeyError,ValueError,ArithmeticError): pass
    targets=pd.DataFrame(s.get('target_weights',s.get('targets',[])))
    if targets.empty: checks.append('MISSING_TARGETS'); targets=pd.DataFrame(columns=['security_id','target_weight'])
    try:
        weights=[D(x) for x in targets.target_weight]
        if targets.security_id.duplicated().any() or any(x<0 or x>1 for x in weights) or sum(weights)>D('1.000000000001'): checks.append('INVALID_TARGET_WEIGHTS')
    except (AttributeError,ValueError,TypeError): checks.append('INVALID_TARGET_WEIGHTS')
    try: result=plan_rebalance(targets,account,pd.DataFrame(validq,columns=quotes.columns),p)
    except (ValueError,KeyError,ArithmeticError) as exc: result=dict(targets=[],orders=[],checks=['INVALID_INPUT:'+str(exc)],summary={})
    checks+=result['checks']; rows=result['orders']; nav=D(result['summary'].get('equity_snapshot',0))
    for r in rows:
        key=r['security_id']; q=qmap.get(key); reasons=list(qerrors.get(key,[])); side=r['side']
        if q is None: reasons.append('MISSING_QUOTE')
        if not ranking.get(key,{}).get('sector'): reasons.append('UNKNOWN_SECTOR')
        r['sector']=ranking.get(key,{}).get('sector',r.get('sector'))
        if not reasons and not missing and side!='KEEP':
            try:
                a=s['anchors'][key]; anchor=D(a['prior_raw_close'])/D(a['effective_split_ratio'])
                if anchor<=0 or a.get('comparable',True) is not True: raise ValueError('INVALID_ANCHOR')
                bid=D(q['bid']); ask=D(q['ask']); low=anchor*(1-D(p['gap_down'][side])); high=anchor*(1+D(p['gap_up'][side])); action=ask if side=='BUY' else bid
                spread=(ask-bid)/((bid+ask)/2)
                r.update(bid=str(bid),ask=str(ask),anchor=str(anchor),gap_action=str(action/anchor-1),spread_ratio=str(spread),guard_low=str(low),guard_high=str(high))
                if not low<=action<=high: reasons.append('GAP_PROTECTION')
                if spread>D(p.get('max_spread_ratio',0)): reasons.append('SPREAD_PROTECTION')
                price=limit_price(side,bid,ask,low,high,D(p['epsilon_buy' if side=='BUY' else 'epsilon_sell']),D(q.get('tick',p['tick'])))
                r.update(limit_price=str(price),limit_semantics='MAXIMUM_BUY_PRICE' if side=='BUY' else 'MINIMUM_SELL_PRICE',time_in_force='DAY',marketable_at_snapshot=(price>=ask if side=='BUY' else price<=bid))
            except (KeyError,ValueError,ArithmeticError,TypeError): reasons.append('INVALID_ANCHOR_OR_POLICY')
        r.update(blocking_reasons=reasons,approved_trade_shares=r['intended_trade_shares'] if not reasons and not missing and r['intended_trade_shares'] is not None else 0)
        if q is not None:
            try: valid_until=min(valid_until,timestamp(q['quote_time'])+timedelta(seconds=p.get('max_quote_age_seconds',0)))
            except (KeyError,ValueError,TypeError): pass
    if now>=valid_until: checks.append('DRAFT_EXPIRED'); status='EXPIRED' if status is None else status
    fee=D(p.get('fee_rate',0)); cash=D(account.get('cash',0)); power=D(account.get('buying_power',0)); cmin=D(p.get('cash_min',1))
    def budget():
        buy=sell=fees=D(0); marks=D(0)
        for r in rows:
            qty=r['approved_trade_shares']; price=D(r.get('limit_price',r.get('mark_price') or 0)); amount=D(abs(qty))*price
            if qty>0: buy+=amount
            if qty<0: sell+=amount
            fees+=amount*fee
            marks+=D(r['actual_shares']+qty)*D(r.get('mark_price') or 0)
        after=cash+sell-buy-fees; equity=after+marks+D(account.get('receivables',0))
        # Reserve cash minimum before assuming any sell is filled.
        safe_cash=cash-buy-fees
        return buy,sell,fees,after,equity,safe_cash
    for r in sorted(rows,key=lambda x:(x.get('rank') is None,x.get('rank') or 0,x['security_id']),reverse=True):
        while r['approved_trade_shares']>0:
            buy,sell,fees,after,equity,safe=budget()
            if buy+fees<=power and safe>=cmin*equity: break
            r['approved_trade_shares']-=1
            if 'BLOCKED_FUNDING' not in r['blocking_reasons']: r['blocking_reasons'].append('BLOCKED_FUNDING')
    buy,sell,fees,after,equity,safe=budget()
    if rows and (equity<=0 or safe<cmin*equity or buy+fees>power): checks.append('BLOCKED_FUNDING')
    sectors={}
    for r in rows:
        value=D(r['actual_shares']+r['approved_trade_shares'])*D(r.get('mark_price') or 0)
        if equity>0 and value/equity>D(p.get('single_name_cap',1)): checks.append('POST_EXECUTION_SINGLE_NAME_CAP:'+r['security_id'])
        sectors[r['sector']]=sectors.get(r['sector'],D(0))+value
    if equity>0:
        for sector,value in sectors.items():
            if value/equity>D(p.get('sector_cap',1)): checks.append('POST_EXECUTION_SECTOR_CAP:'+str(sector))
    if checks and status is None: status='BLOCKED'
    if status is None: status='REVIEW' if any(r['blocking_reasons'] for r in rows) else 'READY_FOR_REVIEW'
    for r in rows:
        if status in ('BLOCKED','EXPIRED','SUPERSEDED','NO_REBALANCE'): r['approved_trade_shares']=0
        r['executable_target_shares']=r['actual_shares']+r['approved_trade_shares']
        r['order_status']='BLOCKED' if status in ('BLOCKED','EXPIRED','SUPERSEDED') else ('KEEP' if r['side']=='KEEP' else ('READY_FOR_REVIEW' if r['approved_trade_shares'] else 'REVIEW'))
        r['valid_until']=valid_until.isoformat()
    buy,sell,fees,after,equity,safe=budget()
    result['summary'].update(approved_buy_notional=str(buy),approved_sell_notional=str(sell),approved_estimated_fees=str(fees),approved_cash_after=str(after),approved_equity_after=str(equity),cash_without_unfilled_sales=str(safe),approved_gross_turnover=str((buy+sell)/nav) if nav else None)
    result.update(targets=[{k:v for k,v in r.items() if k not in ('blocking_reasons',)} for r in rows],ranking=deepcopy(s.get('ranking',[])),checks=list(dict.fromkeys(checks)),run_status=status,mode=p.get('mode','PAPER'),signal_id=s.get('signal_id'),release_id=s.get('release_id'),signal_hash=s.get('signal_hash'),snapshot_at=now.isoformat(),valid_until=valid_until.isoformat(),execution_policy_hash=policy_hash(p),quotes_snapshot_hash=_hash(quotes.to_dict('records')),account_snapshot_hash=_hash(account),supersedes_execution_id=p.get('supersedes_execution_id'),engineering_status='PASS',evidence_status=s.get('evidence_status','INSUFFICIENT_EVIDENCE'))
    result['targets'].append(dict(security_id='CASH',target_weight=str(next((x.get('target_weight') for x in s.get('target_weights',s.get('targets',[])) if x.get('security_id')=='CASH'),1-sum((D(x.get('weight_intended',0)) for x in rows),D(0)))),target_amount=str(after),executable_value=str(after),weight_after_rounding=str(after/equity) if equity else None))
    result['target_portfolio']=deepcopy(result['targets'])
    result['execution_id']=p.get('execution_id','execution-'+_hash(dict(signal=s,account=account,quotes=quotes.to_dict('records'),policy=p,now=now.isoformat()))[:20])
    return result
