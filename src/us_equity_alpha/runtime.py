"""File-only orchestration. No network orders; research gates precede label IO."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


def read_object(path) -> dict:
    if path is None:
        raise ValueError('INPUT_PATH_REQUIRED')
    value=json.loads(Path(path).read_text())
    if not isinstance(value,dict):
        raise ValueError('CONFIG_ROOT_NOT_OBJECT')
    return value


def _output(args):
    if args.output is None:
        raise ValueError('OUTPUT_REQUIRED')
    return Path(args.output)


def save_result(payload, path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream:
        stream.write(json.dumps(payload,indent=2,sort_keys=True,default=str,allow_nan=False)+'\n')


def _account(args):
    account=read_object(args.account)
    supplied=json.loads(Path(args.positions).read_text())
    positions=supplied.get('positions') if isinstance(supplied,dict) else supplied
    if account.get('positions') is not None and account['positions']!=positions:
        raise ValueError('POSITIONS_ACCOUNT_MISMATCH')
    account['positions']=positions
    return account


def run_command(args):
    if args.command=='validate':
        return _validate(args)
    if args.command=='freeze':
        from .releases import freeze_release
        config=read_object(args.config)
        result=freeze_release(config['candidate_manifest'],config['evaluation_records'],_output(args),
                              config['release_id'],mode=config.get('mode','PAPER'),synthetic_demo=config.get('synthetic_demo') is True)
        return {'status':'FROZEN','stage':args.stage,'result':result},0
    if args.command=='signal':
        return _signal(args)
    if args.command=='execution-preview':
        from .execution import build_execution_plan
        from .reporting import write_execution_report
        signal=read_object(args.signal_json)
        if 'signal_snapshot' in signal:signal=signal['signal_snapshot']
        if signal.get('synthetic_demo') is not True:
            raise ValueError('CERTIFIED_RELEASE_REQUIRED')
        policy=read_object(args.policy)
        q=json.loads(Path(args.quotes).read_text())
        quotes=pd.DataFrame(q.get('quotes',[]) if isinstance(q,dict) else q)
        plan=build_execution_plan(signal,_account(args),quotes,policy,now=args.now)
        result=write_execution_report({'signal_snapshot':signal,'execution_plan':plan,'quotes':quotes.to_dict('records'),
                                      'mode':signal.get('mode','PAPER'),'evidence_status':signal.get('evidence_status','INSUFFICIENT_EVIDENCE'),
                                      'synthetic_demo':signal.get('synthetic_demo',False)},_output(args))
        return {'status':result['run_status'],'result':result},0 if result['run_status'] not in {'BLOCKED','EXPIRED','SUPERSEDED'} else 2
    if args.command=='reconcile':
        from .account import reconcile_fills
        data=json.loads(Path(args.fills).read_text())
        if not isinstance(data,dict) or not data.get('asof') or not isinstance(data.get('fills'),list):
            raise ValueError('FILLS_BUNDLE_REQUIRED')
        result=reconcile_fills(_account(args),data['fills'],asof=data['asof'])
        path=_output(args)/'reconciled_account.json'
        save_result(result,path)
        return {'status':'FILLS_IMPORTED_ACCOUNT_REFRESH_REQUIRED','result':result,'output':path},0
    raise ValueError('UNKNOWN_RUNTIME_COMMAND')


def _validate(args):
    from .validation import validate_protocol, build_trade_labels, alphalens_diagnostics
    protocol=read_object(args.protocol)
    gate=validate_protocol(protocol)
    if gate['status']!='CONFIG_COMPLETE':
        return gate,2
    stage=args.stage.upper().replace('-','_')
    if stage not in {'DEVELOPMENT','SYNTHETIC','VALIDATION','FINAL_HOLDOUT','HOLDOUT'}:
        raise ValueError('UNKNOWN_VALIDATION_STAGE')
    # Formal historical evaluation needs certified PIT/action/execution inputs
    # and evaluator gates, not an arbitrary JSON flag. Never read labels here.
    if stage in {'VALIDATION','FINAL_HOLDOUT','HOLDOUT'}:
        return {'status':'NOT_EVALUABLE','stage':stage,'errors':['CERTIFIED_HISTORICAL_EVALUATION_NOT_AVAILABLE'],
                'holdout_eligibility':'UNVERIFIED','forward_status':'PENDING','labels_opened':False},2
    data=read_object(args.input)
    if data.get('data_kind')!='SYNTHETIC_DEMO':
        return {'status':'BLOCKED_EVIDENCE','errors':['ONLY_EXPLICIT_SYNTHETIC_ENGINEERING_INPUTS_ALLOWED'],
                'labels_used_for_selection':False},2
    signals=pd.DataFrame(data['signals'])
    prices=pd.DataFrame(data['trade_prices']).set_index('timestamp')
    prices.index=pd.to_datetime(prices.index,utc=True)
    horizon=int(data.get('horizon',1))
    labels=build_trade_labels(signals,prices,horizon)
    factor_report=alphalens_diagnostics(labels,horizon=horizon,quantiles=int(data.get('quantiles',2)))
    result={'status':'SYNTHETIC_DEMO','engineering_status':'PASS','evidence_status':'INSUFFICIENT_EVIDENCE',
            'stage':stage,'factor_report':factor_report,'formal_validation_completed':False,
            'holdout_opened':False,'protocol_sha256':hashlib.sha256(Path(args.protocol).read_bytes()).hexdigest()}
    save_result(result,_output(args)/'validation_report.json')
    return result,0


def _signal(args):
    # Release loader validates saved manifest/hash before scores/config are used.
    from .releases import load_release_manifest
    from .portfolio import select_targets
    from .execution import signal_hash, policy_hash
    from .reporting import write_signal_report
    release=load_release_manifest(args.release, allow_synthetic=args.synthetic_demo)
    data=read_object(args.input)
    mode=args.mode.upper()
    if mode not in {'PAPER','MANUAL_LIVE'}:raise ValueError('INVALID_MODE')
    if mode=='MANUAL_LIVE':raise ValueError('G6_MANUAL_LIVE_NOT_ENABLED')
    candidate=release['candidate_manifest']
    config=candidate['config']
    policy=candidate['execution_policy']
    ranking=pd.DataFrame(data['ranking'])
    if data.get('rebalance_due') is True:
        target_frame=select_targets(ranking,config)
        targets=target_frame.astype(object).where(pd.notna(target_frame),None).to_dict('records')
    else:
        if 'previous_target_weights' not in data:raise ValueError('PREVIOUS_TARGETS_REQUIRED')
        targets=data['previous_target_weights']
    snapshot={k:data[k] for k in ('signal_id','signal_cutoff','execution_window_start','execution_window_end','valid_until','rebalance_due','anchors')}
    snapshot.update(release_id=release['release_id'],mode=mode,ranking=ranking.to_dict('records'),target_weights=targets,
                    execution_policy_hash=policy_hash(policy),synthetic_demo=release.get('artifact_kind')=='SYNTHETIC_DEMO',
                    evidence_status=release.get('evidence_status','INSUFFICIENT_EVIDENCE'))
    snapshot['run_status']='SIGNAL_ONLY' if data.get('rebalance_due') is True else 'NO_REBALANCE'
    snapshot['signal_hash']=signal_hash(snapshot)
    result=write_signal_report(snapshot,_output(args))
    return {'status':result['run_status'],'signal_id':snapshot['signal_id'],'result':result},0
