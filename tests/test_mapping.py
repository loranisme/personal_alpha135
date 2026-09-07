import json
from us_equity_alpha.mapping import map_field, map_library
from us_equity_alpha.probes import probe_data

def test_semantics_are_field_specific():
    def m(name,dataset,typ='MATRIX'): return map_field({'id':name,'dataset':dataset,'type':typ,'description':'synthetic'})
    assert m('close','pv1')['mapping_class']=='DIRECT_CANDIDATE'
    assert m('vwap','pv1')['candidates'].get('tiingo') is None
    assert m('mystery_footnote','fundamental6')['mapping_class']=='UNKNOWN'
    assert m('free_cash_flow_reported_value','analyst4')['mapping_class']!='SPECIALIST_REQUIRED'
    assert m('forecast_mean','analyst4')['mapping_class']=='SPECIALIST_REQUIRED'
    assert m('atr','news12')['mapping_class']=='DERIVABLE_PROXY'
    assert m('score','news12')['mapping_class']=='UNKNOWN'
    assert 'VECTOR_AGGREGATION_UNKNOWN' in m('close','pv1','VECTOR')['blockers']
    assert m('beta','model51')['local_replacement_id']

def test_missing_settings_and_noid_separate(tmp_path):
    cat=tmp_path/'cat.json'; cat.write_text(json.dumps({'observed_fields':[{'id':'close','dataset':'pv1','type':'MATRIX'}]}))
    reg=tmp_path/'reg.json'; reg.write_text(json.dumps({'records':[{'alpha_id':'SYNTHETIC','settings':None,'dependencies':{'fields':[{'identifier':'close'}]}},{'alpha_id':None,'local_experiment_id':'SYNTHETIC_LOCAL','settings':{},'dependencies':{'fields':[{'identifier':'close'}]}}]}))
    result=map_library(reg,cat,tmp_path/'out')
    assert result['actual_alpha_count']==1 and result['no_id_record_count']==1
    assert result['certified_complete_alpha_count']==0
    rows=json.loads((tmp_path/'out/migration_registry.json').read_text())
    assert 'SETTINGS_MISSING' in rows[0]['blockers']

def test_no_auth_is_not_pass(tmp_path,monkeypatch):
    for k in ['APCA_API_KEY_ID','APCA_API_SECRET_KEY','TIINGO_API_KEY']: monkeypatch.delenv(k,raising=False)
    for provider in ['alpaca','tiingo']:
        r=probe_data(provider,tmp_path/provider)
        assert r['status']=='NOT_RUN_AUTH_REQUIRED' and r['sample_count']==0


def test_settings_neutralization_is_a_required_data_dependency(tmp_path):
    cat = tmp_path / 'cat.json'
    cat.write_text(json.dumps([
        {'id': 'close', 'dataset': 'pv1', 'type': 'MATRIX'},
        {'id': 'industry', 'dataset': 'pv1', 'type': 'GROUP'},
    ]))
    reg = tmp_path / 'reg.json'
    reg.write_text(json.dumps({'records': [{
        'alpha_id': 'SYNTHETIC',
        'settings': {'neutralization': 'INDUSTRY'},
        'dependencies': {'safe': True, 'unknown_identifiers': [], 'fields': [{'identifier': 'close'}]},
    }]}))

    map_library(reg, cat, tmp_path / 'out')

    row = json.loads((tmp_path / 'out' / 'migration_registry.json').read_text())[0]
    assert row['alpaca_direct_candidate'] is False
    assert row['tiingo_direct_candidate'] is False
    assert row['combined_direct_candidate'] is False
    assert 'GROUP_TAXONOMY_IDENTITY_UNVERIFIED' in row['blockers']
