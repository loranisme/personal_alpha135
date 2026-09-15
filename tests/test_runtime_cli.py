import json
from us_equity_alpha.cli import main


def test_signal_missing_release_is_blocked_not_not_implemented(capsys):
    code=main(['signal','--release','/nonexistent/release','--mode','PAPER'])
    result=json.loads(capsys.readouterr().out)
    assert code==2
    assert result['status']=='BLOCKED_CONFIG'


def test_validation_does_not_open_label_path_before_protocol_gate(tmp_path,capsys):
    protocol=tmp_path/'protocol.json'; protocol.write_text('{}')
    code=main(['validate','--stage','VALIDATION','--protocol',str(protocol),'--input','/must/not/be/read','--output',str(tmp_path/'out')])
    result=json.loads(capsys.readouterr().out)
    assert code==2
    assert result['status']=='BLOCKED_CONFIG'
    assert 'INPUT_READ_ERROR' not in str(result)


def test_runtime_rejects_nonobject_configuration(tmp_path,capsys):
    config=tmp_path/'config.json';config.write_text('[]')
    code=main(['freeze','--stage','candidate','--config',str(config),'--output',str(tmp_path/'freeze')])
    result=json.loads(capsys.readouterr().out)
    assert code==2
    assert result['status']=='BLOCKED_CONFIG'


def test_synthetic_signal_freeze_and_execution_report_round_trip(tmp_path,capsys):
    from pathlib import Path
    fixture=json.loads(Path('tests/fixtures/daily_bundle.json').read_text())
    signal=fixture['signal_snapshot'];policy=fixture['execution_policy']
    config={'N':3,'cash_min':.1,'single_name_cap':.4,'sector_cap':.4}
    candidate={'candidate_id':'demo-candidate','candidate_frozen_at':'2026-09-08T00:00:00+00:00','config':config,'execution_policy':policy}
    freeze=tmp_path/'freeze.json';freeze.write_text(json.dumps({'candidate_manifest':candidate,'evaluation_records':[],'release_id':'demo','synthetic_demo':True,'mode':'PAPER'}))
    assert main(['freeze','--stage','candidate','--config',str(freeze),'--output',str(tmp_path/'releases')])==0
    capsys.readouterr()
    signal.update(signal_cutoff='2026-09-07T20:00:00+00:00')
    for row in signal['ranking']:row['eligible']=row['valid']
    inp=tmp_path/'input.json';inp.write_text(json.dumps(signal))
    release=tmp_path/'releases/demo/release_manifest.json'
    assert main(['signal','--release',str(release),'--mode','PAPER','--synthetic-demo','--input',str(inp),'--output',str(tmp_path/'daily')])==0
    payload=json.loads(capsys.readouterr().out)
    signal_path=Path(payload['result']['run_dir'])/'signal_snapshot.json'
    for name,value in [('account',fixture['account']),('positions',fixture['account']['positions']),('quotes',fixture['quotes']),('policy',policy)]:
        (tmp_path/f'{name}.json').write_text(json.dumps(value))
    code=main(['execution-preview','--signal-json',str(signal_path),'--account',str(tmp_path/'account.json'),'--positions',str(tmp_path/'positions.json'),'--quotes',str(tmp_path/'quotes.json'),'--policy',str(tmp_path/'policy.json'),'--now',fixture.get('now','2026-09-08T13:35:00+00:00'),'--output',str(tmp_path/'daily')])
    payload=json.loads(capsys.readouterr().out)
    assert code==2 and payload['status']=='BLOCKED'
    execution=json.loads((Path(payload['result']['run_dir'])/'execution_plan.json').read_text())
    assert all(x['approved_trade_shares']==0 for x in execution['orders'])
    assert payload['result']['qa']['csv_excel_equal']


def test_standalone_signal_cannot_assert_real_paper_evidence(tmp_path,capsys):
    signal=tmp_path/'signal.json';signal.write_text(json.dumps({'synthetic_demo':False,'evidence_status':'VALIDATION_PASS'}))
    code=main(['execution-preview','--signal-json',str(signal),'--account','missing','--positions','missing','--quotes','missing','--policy','missing','--output',str(tmp_path/'out')])
    result=json.loads(capsys.readouterr().out)
    assert code==2 and 'CERTIFIED_RELEASE_REQUIRED' in str(result)
