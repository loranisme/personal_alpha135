"""Approved policy artifact and conservative issuer representative selection."""
import hashlib
import json
import math
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def load_policy(path):
    p=json.loads(Path(path).read_text())
    if p.get('approval',{}).get('status')!='APPROVED_FOR_IMPLEMENTATION' or p.get('live_enabled') is not False:
        raise ValueError('POLICY_NOT_APPROVED_OR_LIVE_ENABLED')
    expected=p.pop('content_sha256',None)
    if not expected or expected!=digest(p):raise ValueError('POLICY_HASH_MISMATCH')
    p['content_sha256']=expected
    return p


def choose_representative(rows,current=None):
    indexed={r['security_id']:r for r in rows}
    if len(indexed)!=len(rows):raise ValueError('DUPLICATE_SECURITY')
    if current is not None:
        if current not in indexed or indexed[current].get('eligible') is None:raise ValueError('REPRESENTATIVE_STATUS_UNKNOWN')
        if indexed[current]['eligible'] is True:return current
        if indexed[current]['eligible'] is not False:raise ValueError('INVALID_ELIGIBILITY')
    candidates=[]
    for r in rows:
        if r.get('eligible') is True:
            adv=r.get('adv60')
            if isinstance(adv,bool) or not isinstance(adv,(int,float)) or not math.isfinite(adv) or adv<0:raise ValueError('INVALID_ADV')
            candidates.append(r)
    return sorted(candidates,key=lambda r:(-r['adv60'],r['security_id']))[0]['security_id'] if candidates else None
