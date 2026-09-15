"""Resumable read-only Tiingo download. Hidden credential stays in this process."""
import argparse,getpass,json,os,time,hashlib
from pathlib import Path
import pandas as pd
import requests


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args();root=a.run
    config=json.loads((root/'frozen_request.json').read_text());universe=pd.read_csv(root/'universe.csv')
    assert len(universe)==500 and hashlib.sha256((root/'universe.csv').read_bytes()).hexdigest()==config['universe_sha256']
    raw=root/'raw';raw.mkdir(exist_ok=True)
    key=os.environ.get('TIINGO_API_KEY') or getpass.getpass('Tiingo API token (hidden, memory only): ')
    session=requests.Session();session.headers.update({'Authorization':'Token '+key})
    symbols=universe.Symbol.tolist()+['SPY'];report={'status':'RUNNING','expected':len(symbols),'complete':0,'errors':[]}
    def save():
        tmp=root/'download_status.tmp';tmp.write_text(json.dumps(report,indent=2)+'\n');tmp.replace(root/'download_status.json')
    for s in symbols:
        file=raw/(s+'.json');digest=raw/(s+'.sha256')
        if file.exists() and digest.exists() and hashlib.sha256(file.read_bytes()).hexdigest()==digest.read_text().strip():
            report['complete']+=1;save();continue
        try:
            response=session.get(f'https://api.tiingo.com/tiingo/daily/{s}/prices',params={'startDate':config['download_start'],'endDate':config['requested_end']},timeout=45,allow_redirects=False)
            if response.status_code!=200:
                report['errors'].append({'symbol':s,'http_status':response.status_code});report['status']='BLOCKED_PROVIDER';save();print('Stopped:',s,'HTTP',response.status_code,'; saved progress, resume supported.',flush=True);return 2
            rows=response.json()
            if not isinstance(rows,list) or not rows or not {'date','open','close','adjOpen','adjClose','divCash','splitFactor'}<=set(rows[0]):
                report['errors'].append({'symbol':s,'reason':'EMPTY_OR_SCHEMA'});save();continue
            file.write_text(json.dumps(rows,separators=(',',':'))+'\n');digest.write_text(hashlib.sha256(file.read_bytes()).hexdigest()+'\n')
            report['complete']+=1;save();print(f"{report['complete']}/{len(symbols)} {s}",flush=True)
        except (requests.RequestException,ValueError) as exc:
            report['errors'].append({'symbol':s,'reason':type(exc).__name__});report['status']='BLOCKED_NETWORK_OR_RESPONSE';save();print('Stopped; safe status file saved.',flush=True);return 2
        time.sleep(1)
    report['status']='COMPLETE' if report['complete']==len(symbols) else 'PARTIAL';save()
    print('Download',report['status'],flush=True)
    return 0 if report['status']=='COMPLETE' else 2
if __name__=='__main__':raise SystemExit(main())
