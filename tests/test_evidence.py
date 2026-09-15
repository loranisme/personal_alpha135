import concurrent.futures
import pandas as pd
import pytest
from us_equity_alpha.evidence import EvidenceLedger, purge_rows


def spec(**kw):
    return dict(holdout_id='H1', start='2025-01-01', end='2025-02-01', security_scope=['A'], lineage_scope=['F'], history_complete=True, candidate_hash='c', config_hash='k', environment_hash='e', data_definition_hash='d', protocol_hash='p', report_code_hash='r', **kw)


def test_purge_checks_exit_and_availability():
    rows=pd.DataFrame({'exit_time':pd.to_datetime(['2025-01-02','2025-01-06','2025-01-02'],utc=True), 'label_available_at':pd.to_datetime(['2025-01-02','2025-01-06','2025-01-07'],utc=True)})
    assert purge_rows(rows,pd.Timestamp('2025-01-06',tz='UTC')).index.tolist()==[0]


def test_unknown_history_and_brain_overlap_block(tmp_path):
    ledger=EvidenceLedger(tmp_path/'e.db')
    s=spec(); s['history_complete']=False
    with pytest.raises(ValueError,match='FORWARD_ONLY'): ledger.open_holdout(s)
    ledger.record_exposure({'start':'2024-01-01','end':'2025-01-02','security_scope':['*'],'lineage_scope':['F'],'source':'BRAIN_SUMMARY'})
    with pytest.raises(ValueError,match='CONTAMINATED'): ledger.open_holdout(spec())


def test_cross_id_concurrency_and_replay(tmp_path):
    path=tmp_path/'e.db'; EvidenceLedger(path)
    def go(i):
        s=spec(); s['holdout_id']=str(i)
        try: return EvidenceLedger(path).open_holdout(s)
        except ValueError: return None
    with concurrent.futures.ThreadPoolExecutor(2) as pool: results=list(pool.map(go,range(2)))
    assert len([x for x in results if x])==1
    token=next(x for x in results if x)
    ledger=EvidenceLedger(path)
    ledger.mark_read_started(token)
    ledger.finish(token,'FAILED')
    assert ledger.audit_replay(token)['evidence_kind']=='AUDIT_REPLAY'
    with pytest.raises(ValueError,match='ALREADY_OPENED'): ledger.open_holdout(spec())


def test_trial_budget_keeps_failures(tmp_path):
    ledger=EvidenceLedger(tmp_path/'e.db')
    ledger.record_trial({'trial_id':'main','kind':'MAIN','status':'FAILED'})
    for i in range(4): ledger.record_trial({'trial_id':str(i),'kind':'CONTROL','status':'FAILED','changed_parameter':'fees'})
    with pytest.raises(ValueError,match='BUDGET'): ledger.record_trial({'trial_id':'extra','kind':'CONTROL','status':'PLANNED'})
    assert len(ledger.export()['trials'])==5


def test_failed_loader_consumes_holdout_and_no_second_reader(tmp_path):
    ledger=EvidenceLedger(tmp_path/'e.db')
    calls=[]
    def fail(): calls.append(1); raise RuntimeError('failed report')
    with pytest.raises(RuntimeError): ledger.read_holdout(spec(),fail)
    with pytest.raises(ValueError,match='ALREADY_OPENED'): ledger.read_holdout(spec(),fail)
    assert calls==[1]


def test_lineage_rename_cannot_reopen_same_labels(tmp_path):
    ledger=EvidenceLedger(tmp_path/'e.db')
    ledger.open_holdout(spec())
    renamed=spec(); renamed['lineage_scope']=['different_provider_and_factor']
    with pytest.raises(ValueError,match='ALREADY_OPENED'):
        ledger.open_holdout(renamed)


@pytest.mark.parametrize('field',['security_scope','lineage_scope'])
def test_string_scope_rejected(tmp_path,field):
    s=spec(); s[field]='ABC'
    with pytest.raises(ValueError,match='INVALID_SCOPE'):
        EvidenceLedger(tmp_path/'e.db').open_holdout(s)
