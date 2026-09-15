import json
from us_equity_alpha.cli import main

def test_policy_check_does_not_certify_release(tmp_path,capsys):
    path=tmp_path/'check.json'
    assert main(['policy-check','--policy','config/policy_v2.json','--output',str(path)])==0
    payload=json.loads(path.read_text())
    assert payload['status']=='POLICY_IMPLEMENTATION_APPROVED'
    assert payload['release_allowed'] is False
    assert payload['implementation_complete'] is False
