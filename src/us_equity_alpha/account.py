"""Local account normalization and idempotent confirmed-fill import; no broker API."""
from copy import deepcopy
from decimal import Decimal

def D(value):
    result=Decimal(str(value))
    if not result.is_finite(): raise ValueError('NONFINITE_NUMBER')
    return result

def positions(account):
    raw=account.get('positions',[])
    if isinstance(raw,dict):
        raw=[dict(security_id=k,shares=v.get('shares',v.get('quantity',0)) if isinstance(v,dict) else v) for k,v in raw.items()]
    result={}
    for row in raw:
        key=row['security_id']
        if key in result: raise ValueError('DUPLICATE_POSITION')
        quantity=D(row.get('shares',row.get('quantity',0)))
        if quantity!=int(quantity) or quantity<0: raise ValueError('INVALID_POSITION')
        result[key]=int(quantity)
    return result

def apply_fills(account, fills):
    """Apply explicit confirmed fills once; open-order status is never inferred."""
    out=deepcopy(account); pos=positions(out); seen=set(out.get('imported_fill_ids',[]))
    cash=D(out['cash']); power=D(out.get('buying_power',out['cash']))
    for f in fills:
        if f['fill_id'] in seen: continue
        if f.get('status','CONFIRMED')!='CONFIRMED': raise ValueError('UNCONFIRMED_FILL')
        qty=D(f['quantity']); price=D(f['price']); fee=D(f['fee'])
        if qty<=0 or qty!=int(qty) or price<=0 or fee<0 or f['side'] not in ('BUY','SELL'): raise ValueError('INVALID_FILL')
        signed=int(qty)*(1 if f['side']=='BUY' else -1)
        key=f['security_id']; pos[key]=pos.get(key,0)+signed
        if pos[key]<0: raise ValueError('NEGATIVE_POSITION')
        change=-D(signed)*price-fee; cash+=change
        # Sale proceeds require a refreshed broker buying-power snapshot.
        if change<0: power+=change
        seen.add(f['fill_id'])
    out.update(positions=[dict(security_id=k,shares=v) for k,v in sorted(pos.items())],cash=str(cash),buying_power=str(power),imported_fill_ids=sorted(seen))
    return out

def reconcile_fills(account,fills,*,asof):
    out=apply_fills(account,fills)
    # Fill import time is not a broker account/buying-power observation.
    from .quotes import timestamp
    timestamp(asof)
    out['fills_imported_at']=asof
    out['reconciled']=False
    out['requires_broker_snapshot_refresh']=True
    return out
