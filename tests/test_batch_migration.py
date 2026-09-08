"""Integration checks use frozen, credential-free local fixtures when present."""
import json
import runpy
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT/'private/runs/one-alpha-alpaca-authorized-v1'


@pytest.mark.skipif(not RUN.exists(), reason='private real-data snapshots unavailable')
def test_batch_partitions_library_and_preserves_settings(tmp_path):
    migrate = runpy.run_path(str(ROOT/'scripts/migrate_available_alphas.py'))['migrate']
    summary = migrate(RUN/'t1/alpha_registry.json', RUN/'t2/migration_registry.json',
                      {p: ROOT/f'private/runs/one-alpha-{p}-authorized-v1' for p in ['alpaca', 'tiingo']}, tmp_path)
    ready = json.loads((tmp_path/'library.json').read_text())
    deferred = json.loads((tmp_path/'deferred.json').read_text())
    original = json.loads((RUN/'t1/alpha_registry.json').read_text())['records']
    all_ids = [r['alpha_id'] for r in ready + deferred]
    assert len(all_ids) == len(set(all_ids)) == len(original)
    assert summary['available_local_variants'] == 5
    assert summary['deferred'] == 881
    for r in ready:
        assert r['original_settings'] == r['local_settings'] | r['excluded_brain_settings']
        assert not r['brain_equivalent']
        assert r['cross_provider']['comparable'] > 0
        assert all(x['prefix_invariant'] for x in r['validation'].values())
    assert all(r['blockers'] for r in deferred)
    assert all('original_record' not in r for r in deferred)
    assert all('sharpe' not in repr(r) for r in deferred)
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_EMPTY'):
        migrate('', '', {}, tmp_path)
