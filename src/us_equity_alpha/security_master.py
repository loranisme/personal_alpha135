"""As-of metadata and fixed-window liquidity primitives; no inferred listing dates."""
import numpy as np
import pandas as pd


def load_security_asof(rows,cutoff):
    t=pd.Timestamp(cutoff)
    if t.tzinfo is None:raise ValueError('CUTOFF_TIMEZONE_REQUIRED')
    needed={'security_id','effective_at','available_at'}
    if needed-set(rows):raise ValueError('MASTER_FIELDS_MISSING')
    frame=rows.copy()
    for key in ('effective_at','available_at'):
        frame[key]=pd.to_datetime(frame[key],utc=True,errors='raise')
        if frame[key].isna().any():raise ValueError('MASTER_TIME_MISSING')
    if frame.duplicated(['security_id','effective_at','available_at']).any():raise ValueError('AMBIGUOUS_MASTER_REVISION')
    visible=frame[(frame.effective_at<=t)&(frame.available_at<=t)]
    return visible.sort_values(['security_id','effective_at','available_at']).groupby('security_id',sort=False).tail(1).copy()


def liquidity_eligible(bars,listing_sessions,*,despac_sessions=None):
    reasons=[]
    if type(listing_sessions) is not int or listing_sessions<252:reasons.append('IPO_HISTORY')
    if despac_sessions is not None and (type(despac_sessions) is not int or despac_sessions<252):reasons.append('DESPAC_HISTORY')
    sample=bars.tail(60)
    if len(sample)!=60 or not {'close','volume'}<=set(sample):return {'eligible':False,'reasons':reasons+['ADV_WINDOW_MISSING'],'adv60':None}
    x=sample[['close','volume']].to_numpy(dtype=float)
    if not np.isfinite(x).all() or (x[:,0]<=0).any() or (x[:,1]<0).any():return {'eligible':False,'reasons':reasons+['INVALID_OR_MISSING_BAR'],'adv60':None}
    adv=float((x[:,0]*x[:,1]).mean())
    if x[-1,0]<5:reasons.append('PRICE_BELOW_5')
    if adv<10000000:reasons.append('ADV_BELOW_10M')
    return {'eligible':not reasons,'reasons':reasons,'adv60':adv}
