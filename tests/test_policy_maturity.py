from us_equity_alpha.policy_maturity import evidence_maturity

def test_engineering_is_not_forward():
    assert evidence_maturity({'engineering_pass':True})['level']=='E0'

def test_three_year_maturity_can_fail_alpha():
    x=evidence_maturity(dict(engineering_pass=True,data_complete=True,sessions=800,rebalances=160,calendar_months=40,full_calendar_years=3,alpha_gate='FAIL',portfolio_gate='FAIL'))
    assert x['level']=='E3' and x['release_allowed'] is False

def test_no_holdout_pass_without_separate_valid_period():
    x=evidence_maturity(dict(engineering_pass=True,data_complete=True,sessions=1000,rebalances=200,calendar_months=50,full_calendar_years=4,holdout_pass=True))
    assert x['level']=='E3'

def test_e4_consumes_actual_gate_status_names():
    r=dict(engineering_pass=True,data_complete=True,sessions=800,rebalances=160,calendar_months=40,full_calendar_years=3,holdout_after_e3=True,holdout_pass=True,holdout_sessions=252,holdout_rebalances=50,holdout_calendar_months=12,alpha_gate='ALPHA_VALIDATION_PASS',portfolio_gate='PORTFOLIO_VALIDATION_PASS')
    assert evidence_maturity(r)['level']=='E4'
