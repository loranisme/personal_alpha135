import pandas as pd
from us_equity_alpha.security_master import load_security_asof, liquidity_eligible

def test_future_record_cannot_replace_past():
    rows=pd.DataFrame([{'security_id':'A','effective_at':'2020-01-01Z'.replace('01Z','01T00:00:00Z'),'available_at':'2020-01-01T00:00:00Z','domicile':'US'},{'security_id':'A','effective_at':'2020-01-01T00:00:00Z','available_at':'2021-01-01T00:00:00Z','domicile':'GB'}])
    assert load_security_asof(rows,'2020-06-01T00:00:00Z').iloc[0].domicile=='US'

def test_adv_missing_not_shrunk_and_boundary():
    bars=pd.DataFrame({'close':[5.]*60,'volume':[2000000.]*60})
    assert liquidity_eligible(bars,252)['eligible']
    bars.loc[0,'volume']=float('nan')
    assert not liquidity_eligible(bars,252)['eligible']
    assert not liquidity_eligible(bars.fillna(2000000),251)['eligible']
