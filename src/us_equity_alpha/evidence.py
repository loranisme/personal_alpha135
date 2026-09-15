"""Append-only evidence journal. Permission is committed before label access."""
from __future__ import annotations
import json
import sqlite3
import uuid
from pathlib import Path
import pandas as pd


def _time(value):
    t=pd.Timestamp(value)
    if pd.isna(t): raise ValueError('INVALID_TIME')
    return (t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')).isoformat()


def purge_rows(labels, validation_start, selection_at=None):
    boundary=pd.Timestamp(_time(validation_start)); available=pd.Timestamp(_time(selection_at or validation_start))
    return labels[(pd.to_datetime(labels.exit_time,utc=True)<boundary)&(pd.to_datetime(labels.label_available_at,utc=True)<available)].copy()


def _scope_overlap(a,b):
    return '*' in a or '*' in b or bool(set(a)&set(b))


def _overlap(a,b):
    return a['start']<=b['end'] and b['start']<=a['end'] and _scope_overlap(a['security_scope'],b['security_scope'])


class EvidenceLedger:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, token TEXT, payload TEXT NOT NULL, at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    def _connect(self):
        return sqlite3.connect(self.path,timeout=30,isolation_level=None)
    @staticmethod
    def _insert(db,kind,payload,token=None):
        db.execute('INSERT INTO events(kind,token,payload) VALUES(?,?,?)',(kind,token,json.dumps(payload,sort_keys=True,allow_nan=False)))
    @staticmethod
    def _range(spec):
        s=dict(spec); s['start']=_time(s['start']); s['end']=_time(s['end'])
        if s['start']>s['end'] or not s.get('security_scope') or not s.get('lineage_scope'): raise ValueError('INVALID_SCOPE')
        for key in ('security_scope','lineage_scope'):
            if not isinstance(s[key],list) or any(not isinstance(x,str) or not x.strip() for x in s[key]):
                raise ValueError('INVALID_SCOPE')
        return s
    def record_exposure(self,spec):
        s=self._range(spec)
        with self._connect() as db: self._insert(db,'EXPOSURE',s)
    def record_trial(self,spec):
        if not spec.get('trial_id') or spec.get('kind') not in {'MAIN','CONTROL','FACTOR','REVISION'}: raise ValueError('INVALID_TRIAL')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                trials=[json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE kind='TRIAL'")]
                if any(x['trial_id']==spec['trial_id'] for x in trials): raise ValueError('DUPLICATE_TRIAL')
                cap={'MAIN':1,'CONTROL':4}.get(spec['kind'])
                if cap is not None and sum(x['kind']==spec['kind'] for x in trials)>=cap: raise ValueError('TRIAL_BUDGET_EXCEEDED')
                if spec['kind']=='CONTROL' and not spec.get('changed_parameter'): raise ValueError('CONTROL_PARAMETER_REQUIRED')
                self._insert(db,'TRIAL',spec); db.commit()
            except BaseException: db.rollback(); raise
    def open_holdout(self,spec):
        s=self._range(spec)
        if s.get('history_complete') is not True: raise ValueError('FORWARD_ONLY: HISTORY_UNVERIFIED')
        for key in ['holdout_id','candidate_hash','config_hash','environment_hash','data_definition_hash','protocol_hash','report_code_hash']:
            if not s.get(key): raise ValueError('MISSING_'+key.upper())
        token=str(uuid.uuid4())
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                for kind,payload in db.execute("SELECT kind,payload FROM events WHERE kind IN ('EXPOSURE','OPENED')"):
                    old=json.loads(payload)
                    if _overlap(s,old): raise ValueError('ALREADY_OPENED' if kind=='OPENED' else 'CONTAMINATED')
                self._insert(db,'OPENED',s,token); db.commit()
            except BaseException: db.rollback(); raise
        return token
    def _append_for_token(self,token,kind,payload):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if not db.execute("SELECT 1 FROM events WHERE token=? AND kind='OPENED'",(token,)).fetchone(): raise ValueError('UNKNOWN_ACCESS_TOKEN')
                if kind=='READ_STARTED' and db.execute("SELECT 1 FROM events WHERE token=? AND kind='READ_STARTED'",(token,)).fetchone(): raise ValueError('LABELS_ALREADY_READ')
                self._insert(db,kind,payload,token); db.commit()
            except BaseException: db.rollback(); raise
    def mark_read_started(self,token): self._append_for_token(token,'READ_STARTED',{})
    def finish(self,token,status): self._append_for_token(token,'FINISHED',{'status':status})
    def audit_replay(self,token):
        result={'evidence_kind':'AUDIT_REPLAY','original_token':token,'new_evidence':False}
        self._append_for_token(token,'AUDIT_REPLAY',result)
        return result
    def read_holdout(self,spec,loader):
        token=self.open_holdout(spec)
        self.mark_read_started(token)
        try:
            data=loader(); self.finish(token,'READ_COMPLETED'); return token,data
        except BaseException:
            self.finish(token,'FAILED_AFTER_READ_STARTED'); raise
    def export(self):
        with self._connect() as db:
            rows=[{'sequence':seq,'kind':kind,'token':token,'payload':json.loads(payload),'at':at} for seq,kind,token,payload,at in db.execute('SELECT seq,kind,token,payload,at FROM events ORDER BY seq')]
        return {'events':rows,'trials':[r['payload'] for r in rows if r['kind']=='TRIAL']}
