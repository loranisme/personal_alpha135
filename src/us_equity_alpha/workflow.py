"""Reproducible T0-T3 diagnostic; never certifies BRAIN parity or emits orders."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from .registry import import_alpha_files
from .mapping import map_library
from .market_data import load_asof
from .factors import evaluate_factor
from .signals import combine_equal_weight
from .universe import session_bounds


def run_workflow(export, catalog, alpha_id, output, *, mode='synthetic', feed='sip'):
    output=Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('OUTPUT_DIRECTORY_NOT_EMPTY')
    output.mkdir(parents=True,exist_ok=True)
    if mode not in {'synthetic','tiingo','alpaca'}:
        raise ValueError('UNSUPPORTED_MODE')
    report={'status':'RUNNING','mode':mode,'brain_equivalent':False,
            'real_market_data_verified':False,'historical_pit_verified':False,
            'stage_results':{},'checks':{}}
    def finish():
        paths=sorted(p for p in output.rglob('*') if p.is_file() and p.name!='manifest.json')
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        paths=sorted(p for p in output.rglob('*') if p.is_file() and p.name!='manifest.json')
        manifest={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        return report
    # T0 verifies local imports/Parquet and calendar independently of market auth.
    test=pd.DataFrame({'value':[1.]}); test.to_parquet(output/'t0.parquet')
    assert pd.read_parquet(output/'t0.parquet').equals(test)
    report['stage_results']['T0']='PASS'
    report['stage_results']['T1']=import_alpha_files([export],output/'t1',catalog)
    registry=json.loads((output/'t1/alpha_registry.json').read_text())
    rows=[r for r in registry['records'] if r['alpha_id']==alpha_id]
    if len(rows)!=1 or not rows[0]['dependencies']['safe']:
        raise ValueError('SELECTED_ALPHA_NOT_UNIQUELY_SUPPORTED')
    selected=rows[0]
    report['selected_alpha_id']=alpha_id
    report['definition_hash']=selected['definition_hash']
    report['local_variant_id']='local_diagnostic__'+selected['definition_hash'][:16]
    original=selected['settings']
    local={k:original[k] for k in ['delay','decay','neutralization']}
    report['local_settings']=local
    report['excluded_brain_settings']={k:v for k,v in original.items() if k not in local}
    report['universe_policy']='FOUR_FIXED_DIAGNOSTIC_SECURITIES_NOT_BRAIN_TOP3000'
    report['stage_results']['T2_mapping']=map_library(output/'t1/alpha_registry.json',catalog,output/'t2')
    fields=[x['identifier'] for x in selected['dependencies']['fields']]
    if not set(fields)<={'open','high','low','close','volume'}:
        raise ValueError('NOT_OHLCV_DIAGNOSTIC_ALPHA')
    symbols=['AAPL','MSFT','SPY','XOM']
    # Fixed dates for engineering verification only; no return-performance selection.
    start,end='2024-01-02','2024-06-28'
    dates=[]
    for day in pd.date_range(start,end):
        try: dates.append(session_bounds(day)[1])
        except ValueError: pass
    dates=pd.DatetimeIndex(dates)
    fetched=pd.Timestamp.now(tz='UTC')
    arrays={}
    if mode=='alpaca':
        from .alpaca_data import fetch_bars
        key=os.environ.get('APCA_API_KEY_ID'); secret=os.environ.get('APCA_API_SECRET_KEY')
        report['feed']=feed
        if not key or not secret:
            report['status']='BLOCKED_DATA_AUTH'
            report['stage_results']['T2_data']='NOT_RUN_AUTH_REQUIRED'
            report['stage_results']['T3']='NOT_RUN'
            return finish()
        for symbol in symbols:
            try:
                payload,evidence=fetch_bars(symbol,start,end,key,secret,feed=feed)
            except ValueError as exc:
                report['status']='BLOCKED_ALPACA'; report['error_code']=str(exc)
                return finish()
            (output/f'raw_{symbol}.json').write_text(json.dumps(payload,indent=2)+'\n')
            (output/f'alpaca_evidence_{symbol}.json').write_text(json.dumps(evidence,indent=2)+'\n')
            if not payload:
                report['status']='BLOCKED_EMPTY_SAMPLE'; return finish()
            frame=pd.DataFrame(payload)
            frame.index=pd.DatetimeIndex([session_bounds(x)[1] for x in frame['date']])
            arrays[symbol]=frame.reindex(dates)
        report['data_notice']='Explicit '+feed+' feed, historical download; not BRAIN or Tiingo parity.'
    elif mode=='tiingo':
        token=os.environ.get('TIINGO_API_KEY')
        if not token:
            report.update(status='BLOCKED_DATA_AUTH')
            report['stage_results']['T2_data']='NOT_RUN_AUTH_REQUIRED'
            report['stage_results']['T3']='NOT_RUN'
            return finish()
        for symbol in symbols:
            try:
                response=requests.get(f'https://api.tiingo.com/tiingo/daily/{symbol}/prices',
                    headers={'Authorization':'Token '+token},params={'startDate':start,'endDate':end},
                    timeout=30,allow_redirects=False)
                if response.status_code!=200:
                    report.update(status='BLOCKED_PROVIDER_HTTP',http_status=response.status_code)
                    return finish()
                payload=response.json()
            except (requests.RequestException,ValueError) as exc:
                report.update(status='BLOCKED_PROVIDER_RESPONSE',error_type=type(exc).__name__)
                return finish()
            if not isinstance(payload,list) or not payload:
                report.update(status='BLOCKED_EMPTY_SAMPLE'); return finish()
            (output/f'raw_{symbol}.json').write_text(json.dumps(payload,indent=2)+'\n')
            frame=pd.DataFrame(payload)
            if not {'date',*fields}<=set(frame):
                report.update(status='BLOCKED_SAMPLE_SCHEMA'); return finish()
            frame.index=pd.DatetimeIndex([session_bounds(str(x)[:10])[1] for x in frame['date']])
            arrays[symbol]=frame.reindex(dates)
        report['data_notice']='Downloaded historical bars: availability at fetch only; not historical vintage reconstruction.'
    else:
        rng=np.random.default_rng(42)
        for symbol in symbols:
            close=100*np.exp(np.cumsum(rng.normal(0,0.01,len(dates))))
            arrays[symbol]=pd.DataFrame({'close':close,'open':close*(1+rng.normal(0,0.003,len(dates))),
                 'high':close*1.02,'low':close*0.98,'volume':rng.integers(100000,900000,len(dates))},index=dates)
        report['data_notice']='All prices and volumes are synthetic, including recognizable symbol labels.'
    records=[]
    for symbol,frame in arrays.items():
        for field in fields:
            for timestamp,value in frame[field].items():
                records.append(dict(security_id=symbol,field=field,value=float(value),event_time=timestamp,
                    available_at=(timestamp+pd.Timedelta(hours=5) if mode=='synthetic' else fetched),
                    revision_id='v1',source=mode,unit='shares' if field=='volume' else 'USD',
                    adjustment='raw',fetched_at=fetched))
    bars=pd.DataFrame(records)
    if not np.isfinite(bars.value).all():
        report.update(status='BLOCKED_MISSING_OR_NONFINITE_DATA'); return finish()
    bars.to_parquet(output/'observations.parquet',index=False)
    cutoff=max(fetched,dates[-1]+pd.Timedelta(days=1))
    visible=load_asof(output/'observations.parquet',cutoff,fields)
    matrices={field:visible[visible.field==field].pivot(index='event_time',columns='security_id',values='value').reindex(index=dates,columns=symbols) for field in fields}
    code=selected['raw']['regular']['code']
    values=evaluate_factor(code,matrices,local)
    prefix=evaluate_factor(code,{k:v.iloc[:-5] for k,v in matrices.items()},local)
    pd.testing.assert_frame_equal(prefix,values.iloc[:-5])
    future=bars.iloc[:1].copy(); future['available_at']=cutoff+pd.Timedelta(days=1)
    future['revision_id']='future'; future['value']=999999
    pd.concat([bars,future],ignore_index=True).to_parquet(output/'future_revision_fixture.parquet',index=False)
    filtered=load_asof(output/'future_revision_fixture.parquet',cutoff,fields)
    pd.testing.assert_frame_equal(visible,filtered)
    composite=combine_equal_weight({report['local_variant_id']:values},[report['local_variant_id']])
    ranking=composite.iloc[-1].rename('score').rename_axis('symbol').reset_index().sort_values(['score','symbol'],ascending=[False,True])
    ranking['data_mode']=mode
    ranking.to_csv(output/'ranking.csv',index=False)
    values.to_parquet(output/'factor_values.parquet')
    report['checks']={'prefix_invariant':True,'future_revision_excluded':True,
        'delayed_first_row_missing':bool(values.iloc[0].isna().all()) if local['delay'] else True,
        'finite_latest_scores':bool(np.isfinite(ranking.score).all())}
    report['observation_count']=len(bars)
    report['sessions']=len(dates)
    report['stage_results']['T2_data']='SYNTHETIC_ASOF_PASS' if mode=='synthetic' else 'REAL_HISTORICAL_DOWNLOAD_PASS_NOT_PIT'
    report['stage_results']['T3']='LOCAL_VARIANT_PASS' if all(report['checks'].values()) else 'FAIL'
    report['real_market_data_verified']=mode in {'tiingo','alpaca'}
    report['status']=('SYNTHETIC_WORKFLOW_PASS' if mode=='synthetic' else 'REAL_DATA_LOCAL_DIAGNOSTIC_PASS') if all(report['checks'].values()) else 'FAIL'
    return finish()
