"""Thin vectorbt adapter; unsupported accounting events fail closed."""
from __future__ import annotations
import importlib.metadata
import math
import numpy as np
import pandas as pd


def audit_trade_totals(trades,equity_before):
    if not np.isfinite(equity_before) or equity_before<=0: raise ValueError('INVALID_EQUITY')
    x=trades[['filled_quantity','fill_price','fee']].astype(float)
    if not np.isfinite(x.to_numpy()).all() or (x.fill_price<=0).any() or (x.fee<0).any(): raise ValueError('INVALID_TRADE')
    notional=float((x.filled_quantity.abs()*x.fill_price).sum()); fees=float(x.fee.sum())
    return {'traded_notional':notional,'gross_turnover':notional/equity_before,'half_gross_turnover':.5*notional/equity_before,'fees':fees,'cash_change':float(-(x.filled_quantity*x.fill_price).sum()-fees),'basis':'actual_engine_fills'}


def check_engine_capabilities(events=()):
    kinds=sorted({e['kind'] for e in events})
    return {'status':'ENGINE_ACTIONS_UNSUPPORTED' if kinds else 'NO_EVENTS_DIAGNOSTIC_ONLY','formal_backtest_allowed':False,'unsupported_events':kinds,'required_unverified_capabilities':['split_share_adjustment','dividend_receivable_and_payment','delisting_terminal_value','external_cash_flow_TWR'],'reason':'Installed vectorbt order adapter does not implement corporate-action/receivable/cash-flow events. Formal raw-price integer-share acceptance remains blocked.'}


def run_vectorbt(close,sizes,fill_prices,*,initial_cash,fee_rate,fixed_fees=0,events=(),formal=False):
    import vectorbt as vbt
    capabilities=check_engine_capabilities(events)
    if events or formal: raise ValueError('ENGINE_ACTIONS_UNSUPPORTED')
    if not close.index.equals(sizes.index) or not close.index.equals(fill_prices.index) or not close.columns.equals(sizes.columns) or not close.columns.equals(fill_prices.columns): raise ValueError('ENGINE_ALIGNMENT_ERROR')
    if not isinstance(close.index,pd.DatetimeIndex) or close.index.tz is None or not close.index.is_monotonic_increasing or close.index.has_duplicates: raise ValueError('ENGINE_TIME_INDEX_INVALID')
    if not np.isfinite(close.to_numpy()).all() or (close<=0).any().any(): raise ValueError('MISSING_MARK_PRICE')
    active=sizes!=0
    if not np.isfinite(sizes.to_numpy()).all() or (sizes%1!=0).any().any(): raise ValueError('INTEGER_SHARES_REQUIRED')
    if ((~np.isfinite(fill_prices))&active).any().any() or ((fill_prices<=0)&active).any().any(): raise ValueError('MISSING_FILL_PRICE')
    if not np.isfinite([initial_cash,fee_rate,fixed_fees]).all() or initial_cash<=0 or min(fee_rate,fixed_fees)<0: raise ValueError('INVALID_CASH_OR_COST')
    # Explicit column order; caller supplies confirmed fills, never speculative sell proceeds.
    portfolio=vbt.Portfolio.from_orders(close=close,size=sizes.where(sizes.ne(0),np.nan),size_type='amount',price=fill_prices,init_cash=initial_cash,fees=fee_rate,fixed_fees=fixed_fees,slippage=0.,cash_sharing=True,group_by=True,call_seq='default',direction='longonly',allow_partial=False,raise_reject=True,lock_cash=True,update_value=True,freq='1D')
    records=portfolio.orders.records
    trades=pd.DataFrame({'filled_quantity':records['size']*np.where(records['side']==0,1,-1),'fill_price':records['price'],'fee':records['fees']})
    return {'engine':'vectorbt','engine_version':importlib.metadata.version('vectorbt'),'portfolio':portfolio,'audit':audit_trade_totals(trades,initial_cash),'capabilities':capabilities,'evidence_kind':'DEVELOPMENT_DIAGNOSTIC','cash_sharing':True,'order_sequence':'explicit_input_column_order','fee_basis':'one-sided traded notional; each buy/sell charged once'}


def eligible_bar_fill(*,side,quantity,limit_price,submitted_at,bar_start,bar_end,low,high,volume,participation):
    """Conservative bar approximation, never queue reconstruction or formal evidence."""
    start,end,submitted=map(pd.Timestamp,(bar_start,bar_end,submitted_at))
    if any(t.tzinfo is None for t in (start,end,submitted)) or end<=start: raise ValueError('INVALID_BAR_TIME')
    if side not in {'BUY','SELL'} or quantity<0 or int(quantity)!=quantity or not 0<participation<=1 or volume<0 or low<=0 or high<low or limit_price<=0: raise ValueError('INVALID_FILL_INPUT')
    reason='NO_TOUCH'; filled=0
    if start<submitted: reason='PRE_SUBMISSION_BAR_EXCLUDED'
    elif (side=='BUY' and low<limit_price) or (side=='SELL' and high>limit_price):
        filled=min(int(quantity),math.floor(volume*participation)); reason='PARTIAL' if filled<quantity else 'APPROXIMATED_FILL'
    return {'filled_quantity':filled,'fill_price':float(limit_price) if filled else None,'fill_available_at':end.isoformat() if filled else None,'status':reason,'evidence_kind':'CONSERVATIVE_BAR_APPROXIMATION','formal_execution_evidence':False}


def build_shared_execution_plan(signal_snapshot,account,quotes,policy,*,now=None):
    from .execution import build_execution_plan
    return build_execution_plan(signal_snapshot,account,quotes,policy,now=now)


def run_execution_diagnostic(signal_snapshot,account,quotes,policy,confirmed_fills,*,now):
    """Cash-start synthetic diagnostic; only explicit confirmed fills enter vectorbt.

    Current adapter cannot initialize existing holdings/receivables without a
    fabricated acquisition. Reject that scope rather than changing trade totals.
    """
    from .account import positions
    if any(positions(account).values()) or float(account.get('receivables',0)):
        raise ValueError('ENGINE_INITIAL_POSITIONS_UNSUPPORTED')
    plan=build_shared_execution_plan(signal_snapshot,account,quotes,policy,now=now)
    allowed={r['security_id']:r for r in plan['orders']}
    used={}; records=[]
    for fill in confirmed_fills:
        sid=fill['security_id']; qty=fill['filled_quantity']; price=float(fill['fill_price'])
        row=allowed.get(sid); submitted=pd.Timestamp(fill['submitted_at']); at=pd.Timestamp(fill['fill_at']); snapshot=pd.Timestamp(now); cutoff=pd.Timestamp(signal_snapshot['signal_cutoff'])
        if any(t.tzinfo is None for t in (submitted,at,snapshot,cutoff)) or not cutoff<snapshot<submitted<=at: raise ValueError('CAUSAL_EXECUTION_TIME_ERROR')
        if submitted>=pd.Timestamp(plan['valid_until']) or at>=pd.Timestamp(signal_snapshot['execution_window_end']): raise ValueError('EXPIRED_FILL')
        if not row or not qty or int(qty)!=qty or qty*row['approved_trade_shares']<=0: raise ValueError('UNAPPROVED_FILL')
        used[sid]=used.get(sid,0)+abs(qty)
        if used[sid]>abs(row['approved_trade_shares']): raise ValueError('FILL_EXCEEDS_APPROVED')
        limit=float(row['limit_price'])
        if price<=0 or not np.isfinite(price) or (qty>0 and price>limit) or (qty<0 and price<limit): raise ValueError('LIMIT_PRICE_VIOLATION')
        records.append((at,sid,qty,price))
    columns=pd.Index(sorted(quotes.security_id)); times=pd.DatetimeIndex(sorted({pd.Timestamp(now)}|{r[0] for r in records}))
    marks={r['security_id']:(float(r['bid'])+float(r['ask']))/2 for r in quotes.to_dict('records')}
    close=pd.DataFrame([[marks[s] for s in columns] for _ in times],index=times,columns=columns)
    sizes=close*0; prices=close.copy()
    for at,sid,qty,price in records:
        if sizes.loc[at,sid]!=0: raise ValueError('MULTIPLE_FILLS_PER_TIME_SECURITY_UNSUPPORTED')
        sizes.loc[at,sid]=qty; prices.loc[at,sid]=price
    result=run_vectorbt(close,sizes,prices,initial_cash=float(account['cash']),fee_rate=float(policy['fee_rate']))
    return {'execution_plan':plan,'engine':result,'evidence_kind':'SYNTHETIC_ENGINE_DIAGNOSTIC','limitations':['Cash-start only; marks held at snapshot for this order audit, not a historical performance series.','Unfilled approved quantities remain untraded; no forecast sell funding.']}
