import json
import pandas as pd
from us_equity_alpha.workflow import run_workflow

def test_real_definition_synthetic_data_workflow_is_not_live_validation(tmp_path):
    source=tmp_path/'export.json'
    source.write_text(json.dumps({'records':[{'id':'SYNTHETIC_ID','regular':{'code':'rank(close-ts_delay(close,1))'},'settings':{'delay':1,'decay':0,'neutralization':'NONE','universe':'TOP3000','truncation':0.02}}],'sync_scope_complete':False}))
    catalog=tmp_path/'catalog.json'
    catalog.write_text(json.dumps([{'id':'close','dataset':'pv1','type':'MATRIX'}]))
    report=run_workflow(source,catalog,'SYNTHETIC_ID',tmp_path/'out',mode='synthetic')
    assert report['status']=='SYNTHETIC_WORKFLOW_PASS'
    assert report['real_market_data_verified'] is False
    assert report['brain_equivalent'] is False
    assert report['checks']['prefix_invariant']
    assert report['checks']['delayed_first_row_missing']
    assert report['checks']['future_revision_excluded']
    assert report['checks']['finite_latest_scores']
    assert report['excluded_brain_settings']['truncation']==0.02
    assert pd.read_csv(tmp_path/'out/ranking.csv').shape[0]==4

def test_missing_tiingo_credentials_never_fall_back_to_synthetic(tmp_path,monkeypatch):
    monkeypatch.delenv('TIINGO_API_KEY',raising=False)
    source=tmp_path/'export.json'; catalog=tmp_path/'catalog.json'
    source.write_text(json.dumps({'records':[{'id':'S','regular':{'code':'rank(close)'},'settings':{'delay':1,'decay':0,'neutralization':'NONE'}}]}))
    catalog.write_text(json.dumps([{'id':'close','dataset':'pv1','type':'MATRIX'}]))
    report=run_workflow(source,catalog,'S',tmp_path/'out',mode='tiingo')
    assert report['status']=='BLOCKED_DATA_AUTH'
    assert not (tmp_path/'out/ranking.csv').exists()
