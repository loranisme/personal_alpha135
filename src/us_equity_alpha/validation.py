"""Trade-aligned diagnostics via installed alphalens-reloaded; no price fetching."""
from __future__ import annotations
import importlib.metadata
import numpy as np
import pandas as pd

REQUIRED_PROTOCOL=('development_range','validation_range','main_configuration','controls','primary_metric','benchmark','risk_limits','minimum_sessions','minimum_rebalances','minimum_calendar_days','base_cost','stress_cost','block_length','review_date','execution_policy','concentration_policy','coverage_policy','delay_sensitivity_policy','sources')


def validate_protocol(protocol):
    missing=[k for k in REQUIRED_PROTOCOL if k not in protocol or protocol[k] is None or protocol[k]=='' or (k!='controls' and protocol[k] in ({},[]))]
    errors=[]
    if len(protocol.get('controls') or [])>4: errors.append('CONTROL_BUDGET_EXCEEDED')
    for key in ('minimum_sessions','minimum_rebalances','minimum_calendar_days','block_length'):
        if key in protocol and protocol[key] is not None and (not isinstance(protocol[key],int) or protocol[key]<=0): errors.append('INVALID_'+key.upper())
    if isinstance(protocol.get('base_cost'),(int,float)) and isinstance(protocol.get('stress_cost'),(int,float)) and protocol['stress_cost']<protocol['base_cost']: errors.append('STRESS_COST_BELOW_BASE')
    return {'status':'BLOCKED_CONFIG' if missing or errors else 'CONFIG_COMPLETE','missing':missing,'errors':errors,'evidence_kind':'DEVELOPMENT_DIAGNOSTIC'}


def build_trade_labels(signals,trade_prices,horizon,available_at=None):
    if not isinstance(horizon,int) or horizon<1: raise ValueError('INVALID_HORIZON')
    p=trade_prices.copy()
    if not isinstance(p.index,pd.DatetimeIndex) or p.index.tz is None or not p.index.is_monotonic_increasing or p.index.has_duplicates: raise ValueError('TRADE_TIME_INDEX_INVALID')
    if signals.duplicated(['signal_cutoff','security_id']).any(): raise ValueError('DUPLICATE_SIGNAL')
    rows=[]
    for row in signals.to_dict('records'):
        cutoff=pd.Timestamp(row['signal_cutoff'])
        if cutoff.tzinfo is None: raise ValueError('SIGNAL_TIMEZONE_REQUIRED')
        entry=p.index.searchsorted(cutoff,side='right'); exit_=entry+horizon
        result=dict(row,entry_time=pd.NaT,exit_time=pd.NaT,label_available_at=pd.NaT,forward_return=np.nan,label_status='INSUFFICIENT_FUTURE_DATA')
        if exit_<len(p) and row['security_id'] in p:
            sid=row['security_id']; a=p.iloc[entry][sid]; b=p.iloc[exit_][sid]
            maturity=p.index[exit_] if available_at is None else available_at.iloc[exit_][sid]
            result.update(entry_time=p.index[entry],exit_time=p.index[exit_],label_available_at=maturity)
            if pd.notna(a) and pd.notna(b) and np.isfinite(a) and np.isfinite(b) and a>0 and b>=0 and pd.notna(maturity) and pd.Timestamp(maturity)>=p.index[exit_]:
                result.update(forward_return=float(b/a-1),label_status='AVAILABLE')
            else: result['label_status']='INVALID_PRICE_OR_AVAILABILITY'
        rows.append(result)
    return pd.DataFrame(rows)


def alphalens_diagnostics(labels,horizon=1,quantiles=5):
    from alphalens import performance,utils
    d=labels.copy(); total=len(d)
    d=d[np.isfinite(pd.to_numeric(d.score,errors='coerce')) & np.isfinite(pd.to_numeric(d.forward_return,errors='coerce'))]
    if d.empty: raise ValueError('NO_VALID_LABELS')
    d['date']=pd.to_datetime(d.signal_cutoff,utc=True); d['asset']=d.security_id.astype(str)
    if d.duplicated(['date','asset']).any(): raise ValueError('DUPLICATE_LABEL')
    column=f'{horizon}D'; d=d.set_index(['date','asset']).rename(columns={'score':'factor','forward_return':column})
    if 'sector' in d: d['group']=d['sector']
    d['factor_quantile']=utils.quantize_factor(d,quantiles=quantiles,no_raise=True)
    d=d.dropna(subset=['factor_quantile']); d['factor_quantile']=d.factor_quantile.astype(int)
    ic=performance.factor_information_coefficient(d).dropna(how="all")
    means,_=performance.mean_return_by_quantile(d,demeaned=False)
    sigma=float(ic[column].std()); mean=float(ic[column].mean())
    groups=performance.factor_information_coefficient(d,by_group=True) if 'group' in d else None
    return {'engine':'alphalens-reloaded','engine_version':importlib.metadata.version('alphalens-reloaded'),'evidence_kind':'DEVELOPMENT_DIAGNOSTIC','mean_rank_ic':mean if np.isfinite(mean) else None,'raw_icir':mean/sigma if sigma>0 and np.isfinite(sigma) else None,'icir_annualized':False,'observations':len(d),'input_observations':total,'excluded_observations':total-len(d),'sessions':len(ic),'quantile_returns':{str(k):float(v) for k,v in means[column].items()},'long_end_return':float(means[column].iloc[-1]),'group_mean_ic':{str(k):float(v) if np.isfinite(v) else None for k,v in groups.groupby(level='group')[column].mean().items()} if groups is not None else {},'return_definition':'provided entry-to-exit trade-reference returns; no implicit close-to-close labels','limitations':['Diagnostic reference returns require total-return-compatible inputs; no corporate-action inference.','Quantile means are diagnostics, not an executable portfolio.']}
