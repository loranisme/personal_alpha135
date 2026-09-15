"""Event adapter over vectorbt simulation state, not synthetic action orders.

Canonical values are post-segment engine snapshots plus unpaid entitlements.
Native Portfolio reconstructions from order records omit these state changes and
are deliberately not returned. Cash flows occur immediately before row trading;
TWR separates market repricing before the cash flow from post-flow trading.
"""
from __future__ import annotations
import importlib.metadata
import numpy as np
import pandas as pd
from .backtest import audit_trade_totals


def run_event_backtest(close,*,initial_cash,initial_positions=None,sizes=None,fill_prices=None,fee_rate=0.,events=()):
    import vectorbt as vbt
    from vectorbt.portfolio.nb import order_nb
    from vectorbt.portfolio.enums import NoOrder,Direction
    p=close.astype(float).copy(); n,m=p.shape
    if not n or not m or not isinstance(p.index,pd.DatetimeIndex) or p.index.tz is None or p.index.has_duplicates or not p.index.is_monotonic_increasing or p.columns.has_duplicates: raise ValueError('INVALID_INDEX')
    if not np.isfinite(p.to_numpy()).all() or (p<0).any().any(): raise ValueError('INVALID_MARK_PRICE')
    if not np.isfinite([initial_cash,fee_rate]).all() or initial_cash<0 or fee_rate<0: raise ValueError('INVALID_CASH_COST')
    initial_positions=initial_positions or {}
    if set(initial_positions)-set(p.columns): raise ValueError('MISSING_INITIAL_MARK')
    inventory=np.array([float(initial_positions.get(s,0)) for s in p.columns])
    if not np.isfinite(inventory).all() or (inventory<0).any() or (inventory%1!=0).any(): raise ValueError('INVALID_INITIAL_INVENTORY')
    sizes=pd.DataFrame(0.,index=p.index,columns=p.columns) if sizes is None else sizes.astype(float)
    fill_prices=p if fill_prices is None else fill_prices.astype(float)
    for frame in (sizes,fill_prices):
        if not frame.index.equals(p.index) or not frame.columns.equals(p.columns): raise ValueError('INPUT_ALIGNMENT')
    if not np.isfinite(sizes.to_numpy()).all() or (sizes%1!=0).any().any(): raise ValueError('INTEGER_ORDERS_REQUIRED')
    active=sizes!=0
    if ((~np.isfinite(fill_prices))&active).any().any() or ((fill_prices<=0)&active).any().any(): raise ValueError('INVALID_FILL_PRICE')
    by_row={}; seen=set(); supported={'split','dividend_ex','dividend_pay','delisting','external_cash_flow'}
    for raw in events:
        e=dict(raw); eid=e.get('event_id')
        if not eid or eid in seen: raise ValueError('DUPLICATE_OR_MISSING_EVENT_ID')
        seen.add(eid); at=pd.Timestamp(e['at'])
        if at.tzinfo is None or at not in p.index: raise ValueError('EVENT_TIME_NOT_ON_GRID')
        if e['kind'] not in supported: raise ValueError('ENGINE_ACTIONS_UNSUPPORTED')
        if e['kind'] in {'split','dividend_ex','delisting'} and e.get('security_id') not in p.columns: raise ValueError('UNKNOWN_EVENT_SECURITY')
        by_row.setdefault(p.index.get_loc(at),[]).append(e)
    cash=np.empty(n); positions=np.empty((n,m)); receivable=np.empty(n); external=np.zeros(n); pretrade_equity=np.empty(n); entitlements={}; retired=set(); event_audit=[]
    def pre_sim(c):
        c.last_position[:]=inventory
        c.last_val_price[:]=p.iloc[0].to_numpy()
        c.last_value[0]=float(initial_cash)+float(np.dot(inventory,p.iloc[0]))
        return ()
    def pre_segment(c):
        for e in by_row.get(c.i,[]):
            kind=e['kind']; sid=e.get('security_id'); col=p.columns.get_loc(sid) if sid is not None else None; delta=0.
            if kind=='split':
                ratio=float(e['ratio']); qty=c.last_position[col]*ratio
                if not np.isfinite(ratio) or ratio<=0: raise ValueError('INVALID_SPLIT')
                if not np.isclose(qty,round(qty),rtol=0,atol=1e-10): raise ValueError('FRACTIONAL_SPLIT_CASH_IN_LIEU_REQUIRED')
                c.last_position[col]=round(qty)
            elif kind=='dividend_ex':
                amount=float(e['amount_per_share'])
                if not np.isfinite(amount) or amount<0: raise ValueError('INVALID_DIVIDEND')
                entitlements[e['event_id']]=float(c.last_position[col])*amount
            elif kind=='dividend_pay':
                key=e['entitlement_id']
                if key not in entitlements: raise ValueError('UNKNOWN_OR_PAID_ENTITLEMENT')
                delta=entitlements.pop(key)
            elif kind=='delisting':
                terminal=float(e['terminal_value'])
                if not np.isfinite(terminal) or terminal<0: raise ValueError('INVALID_TERMINAL_VALUE')
                delta=float(c.last_position[col])*terminal; c.last_position[col]=0.; retired.add(sid)
            elif kind=='external_cash_flow':
                delta=float(e['amount'])
                if not np.isfinite(delta): raise ValueError('INVALID_EXTERNAL_FLOW')
                external[c.i]+=delta
            if c.last_cash[0]+delta<0: raise ValueError('NEGATIVE_CASH_EVENT')
            c.last_cash[0]+=delta; c.last_free_cash[0]+=delta
            event_audit.append(dict(event_id=e['event_id'],kind=kind,at=p.index[c.i].isoformat(),cash_change=delta,active_trade_notional=0.))
        c.last_val_price[:]=p.iloc[c.i].to_numpy()
        # Receivables are excluded from engine buying power and added only to NAV.
        c.last_value[0]=c.last_cash[0]+np.dot(c.last_position,c.last_val_price)
        pretrade_equity[c.i]=c.last_value[0]+sum(entitlements.values())
        return ()
    def orders(c):
        qty=float(sizes.iloc[c.i,c.col])
        if qty==0: return NoOrder
        if p.columns[c.col] in retired: raise ValueError('TRADE_AFTER_DELISTING')
        return order_nb(size=qty,price=float(fill_prices.iloc[c.i,c.col]),fees=fee_rate,direction=Direction.LongOnly,allow_partial=False,raise_reject=True,lock_cash=True)
    def post_segment(c):
        cash[c.i]=c.last_cash[0]; positions[c.i,:]=c.last_position; receivable[c.i]=sum(entitlements.values())
    pf=vbt.Portfolio.from_order_func(p,orders,init_cash=float(initial_cash),cash_sharing=True,group_by=True,call_seq='default',pre_sim_func_nb=pre_sim,pre_segment_func_nb=pre_segment,post_segment_func_nb=post_segment,call_pre_segment=True,call_post_segment=True,use_numba=False,update_value=True,fill_pos_record=False,freq='1D')
    records=pf.orders.records.copy()
    trades=pd.DataFrame({'filled_quantity':records['size']*np.where(records['side']==0,1,-1),'fill_price':records['price'],'fee':records['fees']})
    equity=cash+(positions*p.to_numpy()).sum(axis=1)+receivable
    starting=float(initial_cash)+float(np.dot(inventory,p.iloc[0]))
    if starting<=0: raise ValueError('NONPOSITIVE_START_EQUITY')
    previous=np.r_[starting,equity[:-1]]
    if (previous<=0).any() or (pretrade_equity<0).any(): raise ValueError('NONPOSITIVE_TWR_BASE')
    post_factor=np.divide(equity,pretrade_equity,out=np.ones(n),where=pretrade_equity!=0)
    returns=((pretrade_equity-external)/previous)*post_factor-1
    snapshots=pd.DataFrame({'cash':cash,'receivables':receivable,'equity':equity,'external_flow':external,'twr_return':returns},index=p.index)
    return {'engine':'vectorbt.from_order_func','engine_version':importlib.metadata.version('vectorbt'),'snapshots':snapshots,'positions':pd.DataFrame(positions,index=p.index,columns=p.columns),'orders':records,'engine_order_count':len(records),'audit':audit_trade_totals(trades,starting),'events':event_audit,'total_twr':float(np.prod(1+returns)-1),'evidence_kind':'ENGINEERING_DIAGNOSTIC','supported_events':sorted(supported),'native_portfolio_reconstruction_allowed':False,'limitations':['Events must be source-verified and aligned to exact observation timestamps before trading.','Cash distributions are gross; tax, withholding, cash interest, mergers and cash-in-lieu are not implemented.','Explicit event ordering is preserved; snapshots rather than native order-only portfolio reconstruction are canonical.','Market and execution data eligibility remain external gates.']}
