"""Package supported local variants; preserve every deferred BRAIN record.

Run with PYTHONPATH=src. Uses existing real-data snapshots, never credentials.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from us_equity_alpha.factors import evaluate_factor, FUNCTIONS
from us_equity_alpha.proxy_converter import build_source_view


def migrate(registry, mapping, snapshots, output):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('OUTPUT_MUST_BE_EMPTY')
    output.mkdir(parents=True, exist_ok=True)
    records = json.loads(Path(registry).read_text())['records']
    maps = {x['record_ref']: x for x in json.loads(Path(mapping).read_text())}
    inputs = {}
    for provider, directory in snapshots.items():
        directory = Path(directory)
        manifest = json.loads((directory/'manifest.json').read_text())
        for file, digest in manifest.items():
            if hashlib.sha256((directory/file).read_bytes()).hexdigest() != digest:
                raise ValueError('SOURCE_HASH_MISMATCH')
        raw = {s: pd.DataFrame(json.loads((directory/f'raw_{s}.json').read_text())).set_index('date')
               for s in ['AAPL', 'MSFT', 'SPY', 'XOM']}
        for frame in raw.values():
            frame.index = pd.to_datetime(frame.index, utc=True).date
        inputs[provider] = {f: pd.DataFrame({s: df[f] for s, df in raw.items()})
                            for f in ['open', 'high', 'low', 'close', 'volume']}
    ready, deferred = [], []
    for record in records:
        aid = record['alpha_id']
        dep = record['dependencies']
        fields = {x['identifier'] for x in dep['fields']}
        blockers = []
        if not dep['safe']:
            blockers.append('UNSAFE_OR_UNSUPPORTED_PARSE')
        missing = fields - set.intersection(*(set(v) for v in inputs.values()))
        if missing:
            blockers.append('FIELDS_NOT_VERIFIED:' + ','.join(sorted(missing)))
        operators = set(dep['operators']) - set(FUNCTIONS)
        if operators:
            blockers.append('OPERATORS_NOT_IMPLEMENTED:' + ','.join(sorted(operators)))
        if record['settings'].get('neutralization') != 'NONE':
            blockers.append('NEUTRALIZATION_NOT_IMPLEMENTED:' + str(record['settings'].get('neutralization')))
        if not maps[aid]['combined_direct_candidate']:
            blockers.append('NOT_DIRECT_MAPPING_CANDIDATE')
        if blockers:
            deferred.append({'alpha_id': aid, 'blockers': blockers, 'mapping_blockers': maps[aid]['blockers'],
                             'source_record': build_source_view([record])[0]})
            continue
        settings = {k: record['settings'][k] for k in ['delay', 'decay', 'neutralization']}
        expression = record['raw']['regular']['code']
        definition = {'alpha_id': aid, 'local_variant_id': 'local_diagnostic__'+record['definition_hash'][:16],
                      'expression': expression, 'local_settings': settings,
                      'original_settings': record['settings'],
                      'excluded_brain_settings': {k:v for k,v in record['settings'].items() if k not in settings},
                      'fields': sorted(fields), 'brain_equivalent': False,
                      'historical_pit_verified': False, 'universe': 'FOUR_FIXED_DIAGNOSTIC_SECURITIES',
                      'source_provenance': record['provenance'], 'validation': {}}
        matrices = {}
        try:
            for provider, data in inputs.items():
                selected = {f: data[f] for f in fields}
                factor = evaluate_factor(expression, selected, settings)
                prefix = evaluate_factor(expression, {f:x.iloc[:-5] for f,x in selected.items()}, settings)
                pd.testing.assert_frame_equal(factor.iloc[:-5], prefix)
                if not np.isfinite(factor.iloc[-1]).all():
                    raise ValueError('LATEST_NOT_FINITE')
                if not factor.notna().any().any():
                    raise ValueError('NO_VALID_VALUES')
                matrices[provider] = factor
                definition['validation'][provider] = {'status': 'REAL_DATA_LOCAL_VARIANT_PASS',
                    'finite_values': int(np.isfinite(factor).sum().sum()), 'prefix_invariant': True,
                    'source_run': str(Path(snapshots[provider]).resolve()),
                    'source_manifest_sha256': hashlib.sha256((Path(snapshots[provider])/'manifest.json').read_bytes()).hexdigest()}
        except (ValueError, AssertionError, TypeError) as exc:
            deferred.append({'alpha_id': aid, 'blockers': ['EXECUTION_FAILED:'+str(exc)],
                             'source_record': build_source_view([record])[0]})
            continue
        folder = output/'available'/aid
        folder.mkdir(parents=True)
        for provider, factor in matrices.items():
            factor.to_parquet(folder/f'{provider}_factor.parquet')
            factor.iloc[-1].sort_values(ascending=False).rename('score').to_csv(folder/f'{provider}_ranking.csv')
        left, right = matrices.values()
        if not left.index.equals(right.index) or not left.columns.equals(right.columns):
            raise ValueError('CROSS_PROVIDER_ALIGNMENT_MISMATCH')
        valid = left.notna() & right.notna()
        definition['cross_provider'] = {'comparable': int(valid.sum().sum()),
            'different': int(((left-right).abs().gt(1e-12)&valid).sum().sum())}
        (folder/'definition.json').write_text(json.dumps(definition, indent=2))
        ready.append(definition)
    summary = {'imported': len(records), 'available_local_variants': len(ready),
               'distinct_expressions': len({' '.join(x['expression'].split()) for x in ready}),
               'deferred': len(deferred), 'brain_equivalent_certified': 0,
               'selection_policy': 'DATA_AND_ENGINE_CAPABILITY_ONLY_NO_PERFORMANCE_SELECTION',
               'available_ids': [x['alpha_id'] for x in ready]}
    (output/'library.json').write_text(json.dumps(ready, indent=2))
    (output/'deferred.json').write_text(json.dumps(deferred, indent=2))
    (output/'summary.json').write_text(json.dumps(summary, indent=2))
    hashes = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(output.rglob('*')) if p.is_file()}
    (output/'manifest.json').write_text(json.dumps(hashes, indent=2))
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True)
    args = p.parse_args()
    base = Path('private/runs')
    run = base/'one-alpha-alpaca-authorized-v1'
    print(json.dumps(migrate(run/'t1/alpha_registry.json', run/'t2/migration_registry.json',
        {provider: base/f'one-alpha-{provider}-authorized-v1' for provider in ['alpaca', 'tiingo']},
        args.output), indent=2))
