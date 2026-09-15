"""Deterministic frozen-weight selection and snapshot model intents."""
from decimal import ROUND_FLOOR
import pandas as pd
from .account import D, positions

def select_targets(scores,config):
    n=int(config['N']); cash=D(config['cash_min']); cap=D(config['single_name_cap']); sector_cap=D(config['sector_cap'])
    if n<1 or not 0<=cash<1 or not 0<cap<=1 or not 0<sector_cap<=1: raise ValueError('INVALID_PORTFOLIO_CONFIG')
    if scores.security_id.duplicated().any(): raise ValueError('DUPLICATE_SECURITY_ID')
    base=min((1-cash)/n,cap); exposure={}; rows=[]; exclusions=[]
    for rank,row in enumerate(scores.sort_values(['score','security_id'],ascending=[False,True],kind='stable').to_dict('records'),1):
        sector=row.get('sector'); reason=None
        if not row.get('eligible',False) or pd.isna(row['score']): reason='INVALID_CANDIDATE'
        elif sector is None or pd.isna(sector) or not str(sector).strip(): reason='UNKNOWN_SECTOR'
        elif len(rows)>=n: reason='OUTSIDE_TOP_N'
        elif exposure.get(sector,D(0))+base>sector_cap: reason='SECTOR_CAP'
        if reason: exclusions.append(dict(security_id=row['security_id'],selection_reason=reason)); continue
        exposure[sector]=exposure.get(sector,D(0))+base
        rows.append(dict(security_id=row['security_id'],target_weight=float(base),selection_reason='SELECTED',sector=sector,rank=rank))
    rows.append(dict(security_id='CASH',target_weight=float(1-base*len(rows)),selection_reason='RESIDUAL_CASH',sector=None,rank=None))
    frame=pd.DataFrame(rows); frame.attrs['exclusions']=exclusions
    return frame

def plan_rebalance(targets,account,quotes,config):
    pos=positions(account); prices={}
    for q in quotes.to_dict('records'):
        if q['security_id'] in prices: raise ValueError('DUPLICATE_QUOTE')
        bid=D(q['bid']); ask=D(q['ask'])
        if not 0<bid<=ask: raise ValueError('INVALID_QUOTE')
        prices[q['security_id']]=(bid+ask)/2
    missing=[k for k,v in pos.items() if v and k not in prices]
    if missing: return dict(targets=[],orders=[],checks=['MISSING_HOLDING_PRICE:'+k for k in missing],summary={})
    nav=D(account['cash'])+D(account.get('receivables',0))+sum((D(v)*prices[k] for k,v in pos.items() if v),D(0))
    if nav<=0: raise ValueError('NONPOSITIVE_EQUITY')
    target_map={r['security_id']:r for r in targets.to_dict('records') if r['security_id']!='CASH'}
    rows=[]; orders=[]; buy=D(0); sell=D(0)
    for key in list(target_map)+sorted(set(pos)-set(target_map)):
        t=target_map.get(key,{}); weight=D(t.get('target_weight',0)); actual=pos.get(key,0); price=prices.get(key)
        model=int((weight*nav/price).to_integral_value(rounding=ROUND_FLOOR)) if price else None
        diff=model-actual if model is not None else None
        row=dict(security_id=key,sector=t.get('sector'),rank=t.get('rank'),weight_intended=str(weight),target_weight=str(weight),target_amount=str(weight*nav),mark_price=str(price) if price else None,actual_shares=actual,model_target_shares=model,executable_target_shares=actual,weight_after_rounding=str(D(model)*price/nav) if model is not None else None)
        rows.append(row); orders.append(dict(**row,intended_trade_shares=diff,approved_trade_shares=0,side='KEEP' if not diff else ('BUY' if diff>0 else 'SELL'),order_status='INTENT_ONLY',blocking_reasons=[]))
        if diff and diff>0: buy+=D(diff)*price
        elif diff: sell-=D(diff)*price
    fees=(buy+sell)*D(config.get('fee_rate',0)); after=D(account['cash'])+sell-buy-fees
    summary=dict(equity_snapshot=str(nav),buy_notional=str(buy),sell_notional=str(sell),estimated_fees=str(fees),cash_after=str(after),equity_after=str(nav-fees),gross_turnover=str((buy+sell)/nav),calculation_basis='MODEL_INTENT_ESTIMATE_NOT_FILLS')
    return dict(targets=rows,orders=orders,checks=[],summary=summary)
