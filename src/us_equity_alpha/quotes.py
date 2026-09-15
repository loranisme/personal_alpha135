"""Quote time/source qualification, independent of order submission."""
from datetime import datetime, timezone
from .account import D

def timestamp(value):
    x=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if x.tzinfo is None: raise ValueError('NAIVE_TIMESTAMP')
    return x.astimezone(timezone.utc)

def validate_quotes(quotes,policy,now):
    errors={}; times=[]
    for q in quotes.to_dict('records'):
        key=q['security_id']; reasons=[]
        if key in errors: reasons.append('DUPLICATE_QUOTE')
        try:
            bid=D(q['bid']); ask=D(q['ask'])
            if not 0<bid<=ask: reasons.append('CROSSED_OR_INVALID_QUOTE')
        except (KeyError,ValueError,ArithmeticError): reasons.append('INVALID_QUOTE')
        try:
            qt=timestamp(q['quote_time']); received=timestamp(q['received_at']); times.append(qt)
            if not qt<=received<=now: reasons.append('QUOTE_TIME_ORDER')
            if (now-qt).total_seconds()>policy['max_quote_age_seconds']: reasons.append('STALE_QUOTE')
        except (KeyError,ValueError,TypeError): reasons.append('UNKNOWN_QUOTE_TIME')
        if q.get('feed')!=policy.get('feed') or not q.get('feed'): reasons.append('FEED_MISMATCH')
        if q.get('delayed') is not False: reasons.append('DELAYED_OR_UNKNOWN_FEED')
        if q.get('currency')!='USD' or q.get('session')!='REGULAR': reasons.append('INVALID_CURRENCY_OR_SESSION')
        if q.get('halted') is not False: reasons.append('HALTED_OR_UNKNOWN')
        errors[key]=list(dict.fromkeys(errors.get(key,[])+reasons))
    if times and (max(times)-min(times)).total_seconds()>policy['max_cross_security_skew_seconds']:
        for key in errors: errors[key].append('QUOTE_SKEW')
    return errors
