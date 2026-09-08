"""Run from project root with PYTHONPATH=src; tokens are hidden and memory-only."""
import argparse
import getpass
import json
import os
from us_equity_alpha.workflow import run_workflow
p=argparse.ArgumentParser()
p.add_argument('--mode',choices=['synthetic','tiingo','alpaca'],required=True)
p.add_argument('--feed',choices=['sip','iex'],default='sip')
p.add_argument('--output',required=True)
p.add_argument('--prompt-token',action='store_true')
a=p.parse_args()
if a.prompt_token:
    if a.mode=='alpaca':
        os.environ['APCA_API_KEY_ID']=getpass.getpass('Alpaca Key ID (hidden): ')
        os.environ['APCA_API_SECRET_KEY']=getpass.getpass('Alpaca Secret Key (hidden): ')
    elif a.mode=='tiingo':
        os.environ['TIINGO_API_KEY']=getpass.getpass('Tiingo API token (hidden): ')
r=run_workflow('private/runs/brain-sync-20260907-live/alpha_export.json',
 'private/local_snapshots/wq_usa_top3000_delay1_data_fields.json','e7z8gWME',a.output,mode=a.mode,feed=a.feed)
print(json.dumps(r,indent=2))
raise SystemExit(0 if r['status'].endswith('PASS') else 2)
