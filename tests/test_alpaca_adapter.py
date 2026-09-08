import pytest
from us_equity_alpha.alpaca_data import fetch_bars

class Response:
    status_code=200
    headers={'X-Request-ID':'synthetic-request'}
    def __init__(self,body): self.body=body
    def json(self): return self.body

class Session:
    def __init__(self): self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return Response({'bars':[{'t':'2024-01-02T05:00:00Z','o':10,'h':12,'l':9,'c':11,'v':100,'vw':10.5}], 'next_page_token':None})

def test_alpaca_request_records_feed_without_leaking_credentials():
    session=Session()
    rows,evidence=fetch_bars('AAPL','2024-01-02','2024-01-03','fake-key','fake-secret',feed='sip',session=session)
    assert rows[0]['close']==11
    assert rows[0]['date']=='2024-01-02'
    assert evidence['feed']=='sip'
    assert session.calls[0][1]['params']['feed']=='sip'
    assert session.calls[0][1]['allow_redirects'] is False
    assert 'fake-secret' not in str(evidence)
    assert session.calls[0][0].startswith('https://data.alpaca.markets/')

def test_missing_secret_never_requests_network():
    session=Session()
    with pytest.raises(ValueError,match='AUTH_REQUIRED'):
        fetch_bars('AAPL','2024-01-02','2024-01-03','fake-key','',session=session)
    assert not session.calls
