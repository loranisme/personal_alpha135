"""Immutable candidate-linked engineering packages and honest forward records."""
from __future__ import annotations
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from .reporting import canonical_hash


def _load(value):
    return json.loads(Path(value).read_text()) if isinstance(value,(str,Path)) else copy.deepcopy(value)


def freeze_release(candidate_manifest,evaluation_records,output_root,release_id,mode='PAPER',synthetic_demo=False):
    candidate=_load(candidate_manifest)
    if not re.fullmatch(r'[A-Za-z0-9_-]+',release_id): raise ValueError('INVALID_RELEASE_ID')
    if not candidate.get('candidate_id') or not candidate.get('candidate_frozen_at'): raise ValueError('UNFROZEN_CANDIDATE')
    records=evaluation_records
    if isinstance(records,(str,Path)): records=[json.loads(x) for x in Path(records).read_text().splitlines() if x.strip()]
    records=copy.deepcopy(records)
    for row in records:
        if row.get('candidate_id')!=candidate['candidate_id']: raise ValueError('EVALUATION_CANDIDATE_MISMATCH')
        if row.get('candidate_manifest_hash') not in (None,canonical_hash(candidate)): raise ValueError('CANDIDATE_PARAMETERS_CHANGED')
    # Current evidence adapters are diagnostic; publication requires an independently verified gate implementation.
    if not synthetic_demo: raise ValueError('INSUFFICIENT_EVIDENCE: real release certification not available')
    if mode!='PAPER': raise ValueError('SYNTHETIC_DEMO_REQUIRES_PAPER')
    root=Path(output_root); root.mkdir(parents=True,exist_ok=True); final=root/release_id
    if final.exists(): raise FileExistsError(final)
    temp=Path(tempfile.mkdtemp(prefix='.release-',dir=root))
    try:
        raw=Path(candidate_manifest).read_bytes() if isinstance(candidate_manifest,(str,Path)) else json.dumps(candidate,sort_keys=True).encode()
        manifest=dict(release_id=release_id,candidate_id=candidate['candidate_id'],candidate_frozen_at=candidate['candidate_frozen_at'],candidate_manifest=candidate,candidate_manifest_hash=canonical_hash(candidate),candidate_raw_hash=hashlib.sha256(raw).hexdigest(),evaluation_records=records,evaluation_records_hash=canonical_hash(records),mode=mode,artifact_kind='SYNTHETIC_DEMO',engineering_status='NOT_RUN',evidence_status='INSUFFICIENT_EVIDENCE',holdout_eligibility='UNVERIFIED',forward_status='NOT_STARTED',forward_start=None,gate_results={'G5':False,'G6':False},activation_record=None,released_at=datetime.now(timezone.utc).isoformat())
        manifest['release_hash']=canonical_hash(manifest)
        (temp/'release_manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        (temp/'candidate_manifest.json').write_bytes(raw)
        (temp/'evaluation_records.jsonl').write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in records))
        os.rename(temp,final)
        return final/'release_manifest.json'
    except BaseException:
        shutil.rmtree(temp,ignore_errors=True); raise


def load_release_manifest(path, *, allow_synthetic=False):
    path=Path(path); m=json.loads(path.read_text())
    if m.get('release_hash')!=canonical_hash({k:v for k,v in m.items() if k!='release_hash'}): raise ValueError('RELEASE_HASH_MISMATCH')
    if m.get('candidate_manifest_hash')!=canonical_hash(m['candidate_manifest']): raise ValueError('CANDIDATE_HASH_MISMATCH')
    raw=(path.parent/'candidate_manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=m.get('candidate_raw_hash') or canonical_hash(json.loads(raw))!=m['candidate_manifest_hash']: raise ValueError('CANDIDATE_FILE_MISMATCH')
    records=[json.loads(x) for x in (path.parent/'evaluation_records.jsonl').read_text().splitlines() if x]
    if canonical_hash(records)!=m.get('evaluation_records_hash'): raise ValueError('EVALUATION_HASH_MISMATCH')
    if m.get('artifact_kind')!='SYNTHETIC_DEMO' or not allow_synthetic: raise ValueError('INSUFFICIENT_EVIDENCE: real release certification not available')
    if m.get('mode')!='PAPER' or m.get('activation_record') is not None or any(m.get('gate_results',{}).values()): raise ValueError('INVALID_SYNTHETIC_ACTIVATION')
    return m


def compare_replay(left,right):
    volatile={'run_id','generated_at','reported_at'}
    def clean(x):
        if isinstance(x,dict): return {k:clean(v) for k,v in x.items() if k not in volatile}
        if isinstance(x,list): return [clean(v) for v in x]
        return x
    a,b=clean(_load(left)),clean(_load(right))
    return dict(numerically_equal=a==b,left_numerical_hash=canonical_hash(a),right_numerical_hash=canonical_hash(b),excluded_keys=sorted(volatile))


def append_forward_record(path,record):
    row=copy.deepcopy(record)
    required={'run_id','candidate_id','status','scheduled_at'}
    if not required<=row.keys() or row['status'] not in {'MISSED','OBSERVED','SYNTHETIC_DEMO','STOPPED'}: raise ValueError('INVALID_FORWARD_RECORD')
    if row['status']=='OBSERVED':
        raise ValueError('OBSERVED_REQUIRES_VERIFIED_LIVE_RUN_ADAPTER: retrospective backfill forbidden')
    if row['status'] in {'MISSED','STOPPED'} and not row.get('reason'): raise ValueError('REASON_REQUIRED')
    dt=datetime.fromisoformat(row['scheduled_at'].replace('Z','+00:00'))
    if dt.tzinfo is None: raise ValueError('TIMEZONE_REQUIRED')
    row['recorded_at']=datetime.now(timezone.utc).isoformat()
    row['counts_as_forward_observation']=False
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+',encoding='utf-8') as f:
        fcntl.flock(f,fcntl.LOCK_EX); f.seek(0)
        prior=[json.loads(line) for line in f if line.strip()]
        if any(x['run_id']==row['run_id'] for x in prior): raise ValueError('DUPLICATE_FORWARD_RUN')
        row['previous_record_hash']=canonical_hash(prior[-1]) if prior else None
        f.write(json.dumps(row,sort_keys=True)+'\n'); f.flush(); os.fsync(f.fileno())
    return row
