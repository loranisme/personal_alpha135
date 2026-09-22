import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from us_equity_alpha.sec_field_mapping import load_sec_field_bindings
from us_equity_alpha.sec_deferred_audit import audit_fundamental_deferred_sources


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def frozen_inputs():
    registry = json.loads((ROOT / "private/runs/brain-sync-20260907-live/alpha_export.json").read_text())
    index = pd.read_csv(ROOT / "private/project_factor_library/reconstruction-v4/source_reconstruction_index.csv")
    fields = json.loads((ROOT / "private/local_snapshots/wq_usa_top3000_delay1_data_fields.json").read_text())
    catalog = pd.read_csv(ROOT / "data_pipelines/sec_edgar/mappings/canonical_fields.csv")
    derived = json.loads((ROOT / "data_pipelines/sec_edgar/config/derived_fields.json").read_text())["definitions"]
    catalog = pd.concat([catalog, pd.DataFrame([
        dict(canonical_field=name, required_basis=("DAILY_PROXY" if name.endswith("_proxy") else "INSTANT" if name in {"total_debt", "working_capital", "book_value_per_share"} else "TTM"),
             semantic_grade=definition["semantic_grade"], sign_convention=definition["sign_convention"])
        for name, definition in derived.items()
    ])], ignore_index=True)
    bindings = load_sec_field_bindings(ROOT / "config/sec_brain_field_bindings.json", catalog)
    return registry, index, fields, bindings


def test_audit_classifies_all_deferred_sources_once(frozen_inputs):
    result = audit_fundamental_deferred_sources(*frozen_inputs)
    assert len(result) == result.source_alpha_id.nunique() == 569
    assert result.demand_class.value_counts().to_dict() == {
        "NO_FUNDAMENTAL": 280, "MIXED_DOMAIN": 203, "FUNDAMENTAL_ONLY": 86}
    assert not result.original_expression_hash.isna().any()


def test_audit_never_uses_brain_performance(frozen_inputs):
    registry, index, fields, bindings = frozen_inputs
    changed = copy.deepcopy(registry)
    for record in changed["records"]:
        record["is"] = {"sharpe": -999.0, "fitness": 999.0}
    pd.testing.assert_frame_equal(audit_fundamental_deferred_sources(registry, index, fields, bindings),
                                  audit_fundamental_deferred_sources(changed, index, fields, bindings))


def test_audit_rejects_changed_input_version(frozen_inputs):
    registry, index, fields, bindings = frozen_inputs
    with pytest.raises(ValueError, match="INPUT_VERSION_MISMATCH"):
        audit_fundamental_deferred_sources(registry, index.iloc[:-1], fields, bindings)


def test_candidate_track_does_not_imply_reconstruction_admission(frozen_inputs):
    result = audit_fundamental_deferred_sources(*frozen_inputs)
    candidates = result[result.candidate_track.eq("SEC_SOURCE_LINKED")]
    assert not candidates.empty
    assert all("FORMULA_AND_PIT_REVIEW_PENDING" in reasons for reasons in candidates.blocking_reasons)
