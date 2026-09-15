import json
import pytest
from us_equity_alpha.releases import freeze_release, compare_replay, append_forward_record


def test_diagnostic_candidate_cannot_release(tmp_path):
    candidate={'candidate_id':'c1','candidate_frozen_at':'2026-09-09T00:00:00Z','parameters':{'n':20},'evidence_status':'DIAGNOSTIC_ONLY'}
    with pytest.raises(ValueError,match='INSUFFICIENT'):
        freeze_release(candidate,[],tmp_path,'r1')
    out=freeze_release(candidate,[],tmp_path,'demo1',synthetic_demo=True)
    manifest=json.loads(out.read_text())
    assert manifest['candidate_manifest']==candidate
    assert manifest['forward_start'] is None
    assert manifest['activation_record'] is None
    assert manifest['artifact_kind']=='SYNTHETIC_DEMO'
    with pytest.raises(FileExistsError): freeze_release(candidate,[],tmp_path,'demo1',synthetic_demo=True)


def test_replay_ignores_only_volatile_metadata():
    assert compare_replay({'run_id':'1','score':2},{'run_id':'2','score':2})['numerically_equal']
    assert not compare_replay({'score':2},{'score':3})['numerically_equal']


def test_forward_no_backfill_or_duplicate(tmp_path):
    path=tmp_path/'forward.jsonl'
    record=dict(run_id='x',candidate_id='c',status='MISSED',scheduled_at='2026-09-09T00:00:00Z',reason='not run')
    append_forward_record(path,record)
    with pytest.raises(ValueError,match='DUPLICATE'): append_forward_record(path,record)
    with pytest.raises(ValueError): append_forward_record(path,dict(record,run_id='y',status='OBSERVED'))


def test_load_detects_modified_package(tmp_path):
    from us_equity_alpha.releases import load_release_manifest
    candidate={'candidate_id':'c','candidate_frozen_at':'2026-09-09T00:00:00Z'}
    out=freeze_release(candidate,[],tmp_path,'demo',synthetic_demo=True)
    assert load_release_manifest(out,allow_synthetic=True)['candidate_id']=='c'
    with pytest.raises(ValueError): load_release_manifest(out)
    (out.parent/'candidate_manifest.json').write_text('{}')
    with pytest.raises(ValueError,match='CANDIDATE'): load_release_manifest(out,allow_synthetic=True)
