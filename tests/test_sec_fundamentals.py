import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from us_equity_alpha.sec_fundamentals import SecFundamentalProvider


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_path(path: Path) -> str:
    if path.is_file():
        return _sha_file(path)
    entries = [
        {"path": member.relative_to(path).as_posix(), "sha256": _sha_file(member)}
        for member in sorted(path.rglob("*"))
        if member.is_file()
    ]
    encoded = json.dumps(
        entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _base_panel() -> pd.DataFrame:
    rows = []
    for session, cutoff in [
        ("2025-01-02", "2025-01-02T21:00:00Z"),
        ("2025-01-03", "2025-01-03T21:00:00Z"),
    ]:
        rows.extend(
            [
                {
                    "session": pd.Timestamp(session),
                    "signal_cutoff": pd.Timestamp(cutoff),
                    "security_id": "sec-a",
                    "issuer_cik": "0000000001",
                    "canonical_field": "revenue",
                    "reported_basis": "TTM",
                    "value": 10.0,
                    "availability_mode": "HISTORICAL_DIAGNOSTIC",
                },
                {
                    "session": pd.Timestamp(session),
                    "signal_cutoff": pd.Timestamp(cutoff),
                    "security_id": "sec-entity",
                    "issuer_cik": "0000000002",
                    "canonical_field": "revenue",
                    "reported_basis": "TTM",
                    "value": 20.0,
                    "availability_mode": "HISTORICAL_DIAGNOSTIC",
                },
                {
                    "session": pd.Timestamp(session),
                    "signal_cutoff": pd.Timestamp(cutoff),
                    "security_id": "sec-entity",
                    "issuer_cik": "0000000002",
                    "canonical_field": "shares_outstanding_sec",
                    "reported_basis": "INSTANT",
                    "value": 100.0,
                    "availability_mode": "HISTORICAL_DIAGNOSTIC",
                },
            ]
        )
    return _complete_panel_contract(pd.DataFrame(rows))


def _complete_panel_contract(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    observed = pd.to_datetime(result.signal_cutoff, utc=True) - pd.Timedelta(days=1)
    result["selected_available_at"] = observed
    result["historical_available_at"] = observed
    result["first_seen_at"] = observed
    result["available_at"] = observed
    result["unit"] = "USD"
    result["accession_number"] = "0001-25-000001"
    result["source_fact_id"] = [
        f"fact:{security}:{field}:{session.date()}"
        for security, field, session in zip(
            result.security_id,
            result.canonical_field,
            pd.to_datetime(result.session),
            strict=True,
        )
    ]
    result["semantic_grade"] = "SOURCE_LINKED_DIRECT"
    result["mapping_version"] = "SEC_MAPPING_V1"
    result["definition_hash"] = None
    return result


def _base_catalog() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "canonical_field": "revenue",
                "supported_basis": "TTM",
                "identity_usage": "ENTITY_LEVEL",
            },
            {
                "canonical_field": "shares_outstanding_sec",
                "supported_basis": "INSTANT",
                "identity_usage": "SHARES",
            },
        ]
    )


def _base_identity() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "security_id": "sec-a",
                "issuer_cik": "0000000001",
                "mapping_status": "VERIFIED_SINGLE_CLASS",
                "effective_from": "2019-01-01T00:00:00Z",
                "effective_to": "",
            },
            {
                "security_id": "sec-entity",
                "issuer_cik": "0000000002",
                "mapping_status": "VERIFIED_ENTITY_LEVEL_ONLY",
                "effective_from": "2019-01-01T00:00:00Z",
                "effective_to": "",
            },
        ]
    )


def _write_release(
    root: Path,
    *,
    panel: pd.DataFrame | None = None,
    catalog: pd.DataFrame | None = None,
    identity: pd.DataFrame | None = None,
) -> Path:
    root.mkdir()
    panel_path = root / "daily_pit_fundamental_panel.parquet"
    (panel if panel is not None else _base_panel()).to_parquet(
        panel_path,
        index=False,
        partition_cols=["availability_mode", "canonical_field"],
    )
    (catalog if catalog is not None else _base_catalog()).to_csv(
        root / "canonical_field_catalog.csv", index=False
    )
    (identity if identity is not None else _base_identity()).to_csv(
        root / "cik_security_map.csv", index=False
    )
    (root / "derived_field_definitions.json").write_text("{}\n", encoding="utf-8")
    output_names = [
        "daily_pit_fundamental_panel.parquet",
        "canonical_field_catalog.csv",
        "cik_security_map.csv",
        "derived_field_definitions.json",
    ]
    hashes = {name: _sha_path(root / name) for name in output_names}
    manifest = {
        "dataset_version": "SEC_PIT_FUNDAMENTALS_V1",
        "validation_status": "PASS",
        "canonical_mapping_hash": hashes["canonical_field_catalog.csv"],
        "derived_field_catalog_hash": hashes["derived_field_definitions.json"],
        "security_mapping_hash": hashes["cik_security_map.csv"],
        "pit_contract_hash": "c" * 64,
        "universe_snapshot_hash": "d" * 64,
        "raw_manifest_hash": "e" * 64,
        "output_hashes": hashes,
    }
    manifest_path = root / "processed_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


@pytest.fixture
def sec_release_fixture(tmp_path: Path) -> Path:
    return _write_release(tmp_path / "release")


def test_provider_rejects_tampered_release(sec_release_fixture: Path):
    catalog = sec_release_fixture.parent / "canonical_field_catalog.csv"
    catalog.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="OUTPUT_HASH_MISMATCH"):
        SecFundamentalProvider(sec_release_fixture)


def test_provider_rejects_evidence_hash_that_disagrees_with_hashed_output(
    sec_release_fixture: Path,
):
    payload = json.loads(sec_release_fixture.read_text(encoding="utf-8"))
    payload["canonical_mapping_hash"] = "0" * 64
    sec_release_fixture.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="SEC_EVIDENCE_OUTPUT_HASH_MISMATCH"):
        SecFundamentalProvider(sec_release_fixture)


def test_provider_rejects_malformed_external_evidence_hash(sec_release_fixture: Path):
    payload = json.loads(sec_release_fixture.read_text(encoding="utf-8"))
    payload["raw_manifest_hash"] = "not-a-sha256"
    sec_release_fixture.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="SEC_EVIDENCE_HASH_INVALID:raw_manifest_hash"):
        SecFundamentalProvider(sec_release_fixture)


def test_provider_recomputes_declared_local_external_evidence(sec_release_fixture: Path):
    raw_manifest = sec_release_fixture.parent / "raw_manifest.json"
    raw_manifest.write_text('{"objects": []}\n', encoding="utf-8")
    payload = json.loads(sec_release_fixture.read_text(encoding="utf-8"))
    payload["raw_manifest_hash"] = _sha_file(raw_manifest)
    payload["evidence_files"] = {"raw_manifest_hash": "raw_manifest.json"}
    sec_release_fixture.write_text(json.dumps(payload), encoding="utf-8")

    provider = SecFundamentalProvider(sec_release_fixture)
    assert (
        provider.evidence_hashes["evidence_file:raw_manifest_hash"]
        == "raw_manifest.json"
    )

    raw_manifest.write_text('{"objects": ["tampered"]}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="SEC_EVIDENCE_FILE_HASH_MISMATCH"):
        SecFundamentalProvider(sec_release_fixture)


def test_provider_returns_requested_field_basis_panel(sec_release_fixture: Path):
    provider = SecFundamentalProvider(sec_release_fixture)
    panels = provider.load_panels(
        [("revenue", "TTM")], "2025-01-01", "2025-03-31"
    )
    assert set(panels) == {"revenue__TTM"}
    assert panels["revenue__TTM"].index.name == "session"
    assert panels["revenue__TTM"].columns.name == "security_id"
    assert list(panels["revenue__TTM"].columns) == ["sec-a", "sec-entity"]
    assert provider.evidence_hashes["dataset_version"] == "SEC_PIT_FUNDAMENTALS_V1"


def test_provider_preserves_complete_exchange_session_index(
    sec_release_fixture: Path,
) -> None:
    panel = SecFundamentalProvider(sec_release_fixture).load_panels(
        [("revenue", "TTM")], "2025-01-02", "2025-01-06"
    )["revenue__TTM"]

    assert panel.index.tolist() == [
        pd.Timestamp("2025-01-02"),
        pd.Timestamp("2025-01-03"),
        pd.Timestamp("2025-01-06"),
    ]
    assert panel.loc[pd.Timestamp("2025-01-06")].isna().all()


def test_provider_pushes_partition_and_date_filters_into_parquet_reader(
    sec_release_fixture: Path, monkeypatch: pytest.MonkeyPatch
):
    provider = SecFundamentalProvider(sec_release_fixture)
    original = pd.read_parquet
    calls: list[dict] = []

    def recording_read_parquet(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", recording_read_parquet)
    provider.load_panels([("revenue", "TTM")], "2025-01-02", "2025-01-03")

    assert len(calls) == 1
    filters = calls[0]["filters"]
    assert ("availability_mode", "==", "HISTORICAL_DIAGNOSTIC") in filters
    assert ("canonical_field", "in", ["revenue"]) in filters
    assert ("reported_basis", "in", ["TTM"]) in filters
    assert ("session", ">=", pd.Timestamp("2025-01-02")) in filters
    assert ("session", "<=", pd.Timestamp("2025-01-03")) in filters


def test_entity_only_identity_allows_entity_field_but_rejects_share_class_field(
    sec_release_fixture: Path,
):
    provider = SecFundamentalProvider(sec_release_fixture)
    assert "sec-entity" in provider.load_panels(
        [("revenue", "TTM")], "2025-01-01", "2025-01-31"
    )["revenue__TTM"].columns
    with pytest.raises(
        ValueError,
        match="SEC_PANEL_IDENTITY_NOT_USABLE:SHARES:sec-entity:VERIFIED_ENTITY_LEVEL_ONLY",
    ):
        provider.load_panels(
            [("shares_outstanding_sec", "INSTANT")], "2025-01-01", "2025-01-31"
        )


def test_provider_applies_effective_dated_identity_per_signal_cutoff(tmp_path: Path):
    panel = pd.DataFrame(
        [
            {
                "session": pd.Timestamp("2025-01-03"),
                "signal_cutoff": pd.Timestamp("2025-01-03T21:00:00Z"),
                "security_id": "sec-transition",
                "issuer_cik": "0000000001",
                "canonical_field": "revenue",
                "reported_basis": "TTM",
                "value": 10.0,
                "availability_mode": "HISTORICAL_DIAGNOSTIC",
            },
            {
                "session": pd.Timestamp("2025-01-06"),
                "signal_cutoff": pd.Timestamp("2025-01-06T21:00:00Z"),
                "security_id": "sec-transition",
                "issuer_cik": "0000000002",
                "canonical_field": "revenue",
                "reported_basis": "TTM",
                "value": 11.0,
                "availability_mode": "HISTORICAL_DIAGNOSTIC",
            },
        ]
    )
    identity = pd.DataFrame(
        [
            {
                "security_id": "sec-transition",
                "issuer_cik": "0000000001",
                "mapping_status": "VERIFIED_SINGLE_CLASS",
                "effective_from": "2019-01-01T00:00:00Z",
                "effective_to": "2025-01-06T00:00:00Z",
            },
            {
                "security_id": "sec-transition",
                "issuer_cik": "0000000002",
                "mapping_status": "VERIFIED_SINGLE_CLASS",
                "effective_from": "2025-01-06T00:00:00Z",
                "effective_to": "",
            },
        ]
    )
    manifest = _write_release(
        tmp_path / "transition",
        panel=_complete_panel_contract(panel),
        catalog=_base_catalog(),
        identity=identity,
    )
    values = SecFundamentalProvider(manifest).load_panels(
        [("revenue", "TTM")], "2025-01-03", "2025-01-06"
    )["revenue__TTM"]
    assert values["sec-transition"].tolist() == [10.0, 11.0]


def test_provider_rejects_panel_issuer_outside_effective_identity(tmp_path: Path):
    panel = _base_panel().query("security_id == 'sec-a'").copy()
    panel["issuer_cik"] = "0000000999"
    manifest = _write_release(tmp_path / "mismatch", panel=panel)
    provider = SecFundamentalProvider(manifest)
    with pytest.raises(ValueError, match="SEC_PANEL_IDENTITY_ISSUER_MISMATCH"):
        provider.load_panels([("revenue", "TTM")], "2025-01-01", "2025-01-31")


def test_provider_rejects_unknown_field(sec_release_fixture: Path):
    provider = SecFundamentalProvider(sec_release_fixture)
    with pytest.raises(ValueError, match="SEC_FIELD_BASIS_UNKNOWN"):
        provider.load_panels([("unknown", "TTM")], "2025-01-01", "2025-03-31")


def test_provider_rejects_panel_lookahead_even_in_hash_pinned_release(
    tmp_path: Path,
) -> None:
    panel = _base_panel()
    panel["selected_available_at"] = panel.signal_cutoff + pd.Timedelta(seconds=1)
    panel["historical_available_at"] = panel.selected_available_at
    panel["first_seen_at"] = panel.selected_available_at
    panel["available_at"] = panel.selected_available_at
    manifest = _write_release(tmp_path / "lookahead", panel=panel)

    with pytest.raises(ValueError, match="SEC_DAILY_PANEL_LOOKAHEAD"):
        SecFundamentalProvider(manifest).load_panels(
            [("revenue", "TTM")], "2025-01-01", "2025-01-31"
        )


def test_provider_rechecks_panel_hash_after_initialization(
    sec_release_fixture: Path,
) -> None:
    provider = SecFundamentalProvider(sec_release_fixture)
    panel_root = sec_release_fixture.parent / "daily_pit_fundamental_panel.parquet"
    (panel_root / "post-init-tamper.txt").write_text("tampered", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="OUTPUT_HASH_MISMATCH:daily_pit_fundamental_panel.parquet",
    ):
        provider.load_panels(
            [("revenue", "TTM")], "2025-01-01", "2025-01-31"
        )


def test_provider_rejects_manifest_mutation_after_initialization(
    sec_release_fixture: Path,
) -> None:
    provider = SecFundamentalProvider(sec_release_fixture)
    payload = json.loads(sec_release_fixture.read_text(encoding="utf-8"))
    payload["created_at"] = "2025-01-01T00:00:00Z"
    sec_release_fixture.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="SEC_MANIFEST_CHANGED_AFTER_PROVIDER_INIT"):
        provider.load_panels(
            [("revenue", "TTM")], "2025-01-01", "2025-01-31"
        )
