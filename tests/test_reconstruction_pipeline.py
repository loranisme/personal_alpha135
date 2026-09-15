import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from us_equity_alpha.reconstruction_pipeline import add_benchmark_capabilities, run_reconstruction_pipeline
from us_equity_alpha.proxy_converter import default_market_capabilities, verify_factors
from us_equity_alpha.reconstruction import reconstruct_library


def test_beta_contrast_retains_both_horizons_and_missing_benchmark_blocks():
    close=pd.DataFrame(np.exp(np.random.default_rng(3).normal(0,.01,(400,3)).cumsum(0)),columns=['A','B','SPY'])
    inputs={'tiingo_eod': {'close':close}}
    caps=default_market_capabilities({'tiingo_eod':{'close'}})
    augmented,caps=add_benchmark_capabilities(inputs,caps)
    src=[{'alpha_id':'B','expression':'group_rank(-(beta_last_30_days_spy-beta_last_360_days_spy),industry)','settings':{}}]
    r=reconstruct_library(src,caps)
    assert len(r['factors'])==1
    expression=r['factors'][0]['expression']
    assert '360' in expression and '30' in expression
    verified,_=verify_factors(r,augmented)
    assert verified['factors'][0]['build_status']=='COMPUTE_VERIFIED'
    short={p:{k:v.iloc[:124] for k,v in fields.items()} for p,fields in augmented.items()}
    verified,_=verify_factors(r,short)
    assert verified['factors'][0]['build_status']=='COMPILED'
    no_spy={'tiingo_eod':{'close':close.drop(columns='SPY')}}
    _,caps2=add_benchmark_capabilities(no_spy,default_market_capabilities({'tiingo_eod':{'close'}}))
    assert not reconstruct_library(src,caps2)['factors']


def test_complete_private_library_runs_and_research_is_not_activated(tmp_path):
    root=Path(__file__).resolve().parents[1]
    registry=root/'private/runs/t1-live-import-20260907-v5/alpha_registry.json'
    if not registry.exists():pytest.skip('private source unavailable')
    mapping=root/'private/runs/t2-live-mapping-20260907-v2/field_mapping.json'
    providers={p:root/f'private/runs/one-alpha-{mode}-authorized-v1' for p,mode in [('alpaca_sip','alpaca'),('tiingo_eod','tiingo')]}
    result=run_reconstruction_pipeline(registry,mapping,providers,tmp_path/'library')
    assert result['source_count']==result['card_count']==886
    assert result['reconstructed_source_count']+result['deferred_source_count']==886
    assert result['compute_verified_source_count']<=result['reconstructed_source_count']
    assert result['research_eligible_count']==result['released_count']==0
    assert result['performance_inputs_used'] is False
    library=json.loads((tmp_path/'library/project_factor_library.json').read_text())
    assert all(f['provenance_kind']=='LOCAL_RECONSTRUCTION' for f in library['factors'])
    assert (tmp_path/'library/source_reconstruction_index.csv').exists()
    with pytest.raises(FileExistsError):
        run_reconstruction_pipeline(registry,mapping,providers,tmp_path/'library')


def test_cli_exposes_reconstruction_separately_from_legacy_migration(capsys):
    from us_equity_alpha.cli import main
    with pytest.raises(SystemExit) as exc:
        main(['reconstruct-library','--help'])
    assert exc.value.code==0
    assert '--provider-run' in capsys.readouterr().out
