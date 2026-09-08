"""Read-only Alpaca daily bars with explicit feed and bounded pagination."""
from datetime import date,timedelta
import requests
import pandas as pd


def fetch_bars(symbol,start,end,key,secret,*,feed='sip',session=None):
    if not key or not secret:
        raise ValueError('AUTH_REQUIRED')
    if feed not in {'sip','iex'}:
        raise ValueError('UNSUPPORTED_FEED')
    if not symbol.replace('.','').replace('-','').isalnum():
        raise ValueError('INVALID_SYMBOL')
    client=session or requests.Session()
    params={'timeframe':'1Day','start':pd.Timestamp(start,tz='America/New_York').isoformat(),
            'end':pd.Timestamp(date.fromisoformat(end)+timedelta(days=1),tz='America/New_York').isoformat(),
            'adjustment':'raw','feed':feed,'limit':1000,'sort':'asc'}
    records=[]; request_ids=[]; seen=set()
    for _ in range(10):
        try:
            response=client.get(f'https://data.alpaca.markets/v2/stocks/{symbol}/bars',
                params=params,headers={'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret},
                timeout=30,allow_redirects=False)
        except requests.RequestException as exc:
            raise ValueError('ALPACA_NETWORK_'+type(exc).__name__) from None
        request_ids.append(response.headers.get('X-Request-ID'))
        if response.status_code!=200:
            raise ValueError('ALPACA_HTTP_'+str(response.status_code))
        try: payload=response.json()
        except ValueError: raise ValueError('ALPACA_INVALID_JSON') from None
        if not isinstance(payload,dict) or not isinstance(payload.get('bars'),list):
            raise ValueError('ALPACA_INVALID_SCHEMA')
        records.extend(payload['bars'])
        token=payload.get('next_page_token')
        if not token: break
        if not isinstance(token,str) or token in seen:
            raise ValueError('ALPACA_INVALID_PAGINATION')
        seen.add(token); params['page_token']=token
    else: raise ValueError('ALPACA_PAGE_LIMIT')
    rows=[]
    for bar in records:
        if not {'t','o','h','l','c','v'}<=set(bar):
            raise ValueError('ALPACA_INVALID_BAR')
        day=str(pd.Timestamp(bar['t']).tz_convert('America/New_York').date())
        if start<=day<=end:
            rows.append({'date':day,'open':bar['o'],'high':bar['h'],'low':bar['l'],
                         'close':bar['c'],'volume':bar['v'],'vwap':bar.get('vw')})
    if len({r['date'] for r in rows})!=len(rows):
        raise ValueError('ALPACA_DUPLICATE_SESSION')
    return rows,{'feed':feed,'request_ids':request_ids,'raw_bars':records,
                 'endpoint':'https://data.alpaca.markets/v2/stocks/'+symbol+'/bars'}
