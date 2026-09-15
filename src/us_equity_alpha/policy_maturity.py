"""Evidence maturity summary; callers must supply audited same-candidate counts.

This pure summary never certifies observations or authorizes a release.
"""
def evidence_maturity(record):
    for key in ('sessions','rebalances','calendar_months','full_calendar_years','holdout_sessions','holdout_rebalances','holdout_calendar_months'):
        value=record.get(key,0)
        if type(value) is not int or value<0:raise ValueError('INVALID_COUNT:'+key)
    level='NONE'
    if record.get('engineering_pass') is True:level='E0'
    if level=='E0' and record.get('data_complete') is True:
        sessions=record.get('sessions',0); batches=record.get('rebalances',0); months=record.get('calendar_months',0)
        if sessions>=126 and batches>=26 and months>=6:level='E1'
        if sessions>=252 and batches>=50 and months>=12:level='E2'
        if sessions>=756 and batches>=150 and record.get('full_calendar_years',0)>=3:level='E3'
        if (level=='E3' and record.get('holdout_after_e3') is True and record.get('holdout_pass') is True
            and record.get('holdout_sessions',0)>=252 and record.get('holdout_rebalances',0)>=50
            and record.get('holdout_calendar_months',0)>=12 and record.get('alpha_gate')=='ALPHA_VALIDATION_PASS'
            and record.get('portfolio_gate')=='PORTFOLIO_VALIDATION_PASS'):level='E4'
    return {'level':level,'alpha_gate':record.get('alpha_gate','NOT_EVALUABLE'),
            'portfolio_gate':record.get('portfolio_gate','NOT_EVALUABLE'),
            'release_allowed':False,'certification':'SUMMARY_ONLY_REQUIRES_VERIFIED_OBSERVATION_LEDGER'}
