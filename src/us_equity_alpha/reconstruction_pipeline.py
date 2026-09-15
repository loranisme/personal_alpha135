"""Build an immutable local reconstruction library from saved read-only evidence."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .factor_library import build_registry_view, compute_registry_factors
from .proxy_converter import build_source_view, default_market_capabilities, verify_factors
from .proxy_pipeline import _load_provider_bundle
from .reconstruction import POLICY, reconstruct_library


def _implementation_files():
    return (
        'reconstruction.py',
        'reconstruction_pipeline.py',
        'derived_capabilities.py',
        'history_verification.py',
        'economic_descriptions.py',
        'factor_library.py',
        'proxy_converter.py',
        'factors.py',
        'operators.py',
    )


def _replay_input_path(provider, history_evidence):
    if history_evidence is not None and provider == history_evidence.get('provider'):
        return 'HASHED_LONG_HISTORY_BARS'
    return 'ORDINARY_PROVIDER_BARS'


def add_benchmark_capabilities(inputs, capabilities):
    from .derived_capabilities import add_local_market_capabilities
    return add_local_market_capabilities(inputs, capabilities)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_reconstruction_pipeline(registry_path, field_mapping_path, provider_runs, output_dir,
                                *, history_snapshot=None, history_limit=50,
                                benchmark_json=None):
    output=Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('OUTPUT_DIRECTORY_NOT_EMPTY')
    registry=json.loads(Path(registry_path).read_text())
    if not isinstance(registry,dict) or registry.get('sync_scope_complete') is not True or not isinstance(registry.get('records'),list):
        raise ValueError('REGISTRY_SYNC_SCOPE_INCOMPLETE')
    records=registry['records']
    if len(records)!=886:
        raise ValueError('REGISTRY_SOURCE_COUNT_MISMATCH')
    mappings=json.loads(Path(field_mapping_path).read_text())
    if not isinstance(mappings,list):
        raise ValueError('FIELD_MAPPING_SCHEMA_INVALID')
    field_map={r['field_id']:r for r in mappings if isinstance(r,dict) and r.get('field_id')}
    inputs,evidence=_load_provider_bundle(provider_runs)
    raw_inputs = {provider: dict(fields) for provider, fields in inputs.items()}
    capability_evidence = copy.deepcopy(evidence)
    history_evidence = None
    if history_snapshot is not None:
        from .history_verification import load_tiingo_history_snapshot
        history_fields, history_evidence = load_tiingo_history_snapshot(
            history_snapshot, history_limit, benchmark_json
        )
        raw_inputs['tiingo_eod'] = history_fields
        capability_evidence['tiingo_eod'] = history_evidence
    capabilities=default_market_capabilities(
        {p:set(f) for p,f in raw_inputs.items()}, capability_evidence
    )
    inputs,capabilities=add_benchmark_capabilities(raw_inputs,capabilities)
    conversion=reconstruct_library(build_source_view(records),capabilities)
    for card in conversion['cards']:
        card['field_evidence']=[{'field_id':name,'dataset':field_map.get(name,{}).get('dataset','UNKNOWN'),
                               'description':field_map.get(name,{}).get('description')}
                              for name in card.get('observables',[])]
    verified,matrices=verify_factors(conversion,inputs)
    summary=verified['summary']
    # One source may contribute several independent components; count IDs once.
    verified_sources={s for f in verified['factors'] if f['build_status']=='COMPUTE_VERIFIED' for s in f['source_alpha_ids']}
    summary['compute_verified_source_count']=len(verified_sources)
    summary['partial_intent_source_count']=sum(d['semantic_status']=='PARTIAL_INTENT' and bool(d['local_factor_ids']) for d in verified['decisions'])
    summary['input_evidence']={'registry_sha256':_digest(registry_path),'field_mapping_sha256':_digest(field_mapping_path),
        'provider_manifest_sha256':{p:_digest(Path(d)/'manifest.json') for p,d in provider_runs.items()},
        'provider_evidence':evidence,'historical_pit_verified':False,'real_data_scope':'SAVED_SAMPLE_ONLY'}
    if history_evidence is not None:
        summary['long_history_verification'] = history_evidence
    summary['implementation_sha256'] = {
        name: _digest(Path(__file__).with_name(name))
        for name in _implementation_files()
    }
    ids=[f['local_factor_id'] for f in verified['factors'] if f['build_status']=='COMPUTE_VERIFIED']
    view=build_registry_view(verified,ids,usage='diagnostic')
    summary['t3_diagnostic_adapter_count']=len(view)
    replay_checks = {}
    for provider, ordinary_fields in raw_inputs.items():
        applicable_ids = [fid for fid in ids if provider in matrices.get(fid, {})]
        replayed = compute_registry_factors(verified, applicable_ids, provider, ordinary_fields, usage='diagnostic')
        for fid in applicable_ids:
            pd.testing.assert_frame_equal(replayed[fid], matrices[fid][provider])
        replay_checks[provider] = {
            'status': 'PASS',
            'factor_count': len(applicable_ids),
            'input_path': _replay_input_path(provider, history_evidence),
        }
    summary['t3_recomputation_checks'] = replay_checks
    summary['status']='LOCAL_RECONSTRUCTION_DIAGNOSTIC_LIBRARY_BUILT'
    output.mkdir(parents=True,exist_ok=True)
    def save(name,data):
        (output/name).write_text(json.dumps(data,indent=2,ensure_ascii=False,sort_keys=True)+'\n')
    save('conversion_policy.json',POLICY)
    save('data_capabilities.json',capabilities)
    save('hypothesis_families.json',verified['families'])
    save('project_factor_library.json',{'schema_version':2,'factors':verified['factors']})
    save('conversion_report.json',summary)
    save('t3_diagnostic_registry_view.json',{'usage':'diagnostic','factors':view})
    for name,rows in [('hypothesis_cards.jsonl',verified['cards']),('reconstruction_decisions.jsonl',verified['decisions'])]:
        (output/name).write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in rows))
    with (output/'source_reconstruction_index.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        fields=['source_alpha_id','decision','semantic_status','local_factor_ids','compute_verified_any_component','missing_fields','blockers']
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for d in verified['decisions']:
            writer.writerow({**{k:' | '.join(d[k]) if isinstance(d[k],list) else d[k] for k in fields if k in d},
                             'compute_verified_any_component':d['source_alpha_id'] in verified_sources})
    with (output/'local_formulas.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=['local_factor_id','family_id','expression','source_count','build_status','usage_tier','lost']);writer.writeheader()
        for f in verified['factors']:
            writer.writerow({**{k:f[k] for k in ['local_factor_id','family_id','expression','build_status','usage_tier']},'source_count':len(f['source_alpha_ids']),'lost':' | '.join(f['lost'])})
    for fid,providers in matrices.items():
        folder=output/'factor_values'/fid;folder.mkdir(parents=True)
        for p,frame in providers.items():
            frame.to_parquet(folder/f'{p}.parquet')
    save('manifest.json',{str(p.relative_to(output)):_digest(p) for p in sorted(output.rglob('*')) if p.is_file()})
    return summary
