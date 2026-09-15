"""Append-only SQLite journal for forward paper observations."""
from __future__ import annotations
import json,sqlite3
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd

class PaperLog:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,signal_id TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_signal ON events(signal_id) WHERE kind='SIGNAL'")
    def _connect(self): return sqlite3.connect(self.path)
    def record_signal(self,event):
        required={"signal_id","pool_hash","universe_hash","config_hash","input_hashes","blockers","target_hash","execution_helper_status","live_orders_submitted"}
        if not required<=set(event) or event.get("live_orders_submitted")!=0: raise ValueError("INVALID_SIGNAL_EVENT")
        try:
            with self._connect() as db: db.execute("INSERT INTO events(kind,signal_id,payload,created_at) VALUES(?,?,?,?)",("SIGNAL",event["signal_id"],json.dumps(event,sort_keys=True),datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError: raise ValueError("DUPLICATE_SIGNAL_ID") from None
    def settle_signal(self,event):
        with self._connect() as db:
            found=db.execute("SELECT 1 FROM events WHERE kind='SIGNAL' AND signal_id=?",(event.get("signal_id"),)).fetchone()
            if not found: raise ValueError("UNKNOWN_SIGNAL_ID")
            if event.get("five_session_total_return") is None and not event.get("missing_reason"): raise ValueError("SETTLEMENT_OUTCOME_OR_REASON_REQUIRED")
            db.execute("INSERT INTO events(kind,signal_id,payload,created_at) VALUES(?,?,?,?)",("SETTLEMENT",event["signal_id"],json.dumps(event,sort_keys=True),datetime.now(timezone.utc).isoformat()))
    def export(self,output_dir=None):
        with self._connect() as db: raw=db.execute("SELECT sequence,kind,signal_id,payload,created_at FROM events ORDER BY sequence").fetchall()
        events=[{"sequence":r[0],"kind":r[1],"signal_id":r[2],"payload":json.loads(r[3]),"created_at":r[4]} for r in raw]
        result={"events":events,"research_admission":"NOT_EVALUATED","release_status":"NOT_EVALUATED"}
        if output_dir is not None:
            root=Path(output_dir);root.mkdir(parents=True,exist_ok=True)
            (root/"paper_events.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
            pd.DataFrame([{"sequence":x["sequence"],"kind":x["kind"],"signal_id":x["signal_id"],**x["payload"]} for x in events]).to_csv(root/"paper_summary.csv",index=False)
        return result
