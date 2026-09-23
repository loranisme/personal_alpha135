"""Bind exact SEC field/basis PIT panels to the factor evaluator."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from .sec_field_mapping import SecFieldBinding
from .sec_fundamentals import SecFundamentalProvider


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HASH = re.compile(r"[0-9a-f]{64}")
_RELEASE_HASH_KEYS = (
    "processed_manifest_sha256", "canonical_mapping_hash",
    "derived_field_catalog_hash", "security_mapping_hash", "pit_contract_hash",
    "universe_snapshot_hash", "raw_manifest_hash",
)


def binding_alias(binding: SecFieldBinding) -> str:
    """Use both the SEC concept and accounting basis in a parser-safe alias."""
    field, basis = binding.canonical_field, binding.required_basis
    if not _IDENTIFIER.fullmatch(field) or not _IDENTIFIER.fullmatch(basis):
        raise ValueError("SEC_INPUT_ALIAS_INVALID")
    return f"sec__{field}__{basis}"


def _panel_sha256(panel: pd.DataFrame) -> str:
    """Bind values, missingness, dates, IDs and dtypes without filling data."""
    digest = hashlib.sha256()
    metadata = {
        "index": [str(value) for value in panel.index],
        "columns": [str(value) for value in panel.columns],
        "dtypes": [str(value) for value in panel.dtypes],
    }
    digest.update(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode())
    digest.update(pd.util.hash_pandas_object(panel, index=True).to_numpy(dtype="uint64").tobytes())
    return digest.hexdigest()


def load_sec_factor_inputs(
    provider: SecFundamentalProvider,
    bindings: Sequence[SecFieldBinding],
    start: str,
    end: str,
    mode: str = "HISTORICAL_DIAGNOSTIC",
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Read only the requested basis from a provider-verified SEC release."""
    requested: dict[str, tuple[str, str]] = {}
    requested_tracks: dict[str, str] = {}
    for binding in bindings:
        alias = binding_alias(binding)
        if alias in requested:
            raise ValueError(f"SEC_INPUT_ALIAS_DUPLICATE:{alias}")
        if binding.allowed_track not in {"SEC_SOURCE_LINKED", "SEC_HYPOTHESIS_PROXY"}:
            raise ValueError(f"SEC_INPUT_BINDING_TRACK_INVALID:{alias}")
        requested[alias] = (binding.canonical_field, binding.required_basis)
        requested_tracks[alias] = binding.allowed_track
    if not requested:
        raise ValueError("SEC_INPUT_BINDINGS_EMPTY")

    proxy_pairs = [
        requested[alias] for alias, track in requested_tracks.items()
        if track == "SEC_HYPOTHESIS_PROXY"
    ]
    raw = provider.load_panels(
        list(requested.values()), start, end, mode, proxy_bindings=proxy_pairs
    )
    expected = {f"{field}__{basis}" for field, basis in requested.values()}
    if set(raw) != expected:
        raise ValueError("SEC_PROVIDER_INPUT_SET_MISMATCH")
    panels = {
        alias: raw[f"{field}__{basis}"].copy()
        for alias, (field, basis) in requested.items()
    }
    for panel in panels.values():
        # Preserve the provider's exchange sessions while avoiding a synthetic
        # calendar frequency being interpreted as an observation frequency.
        panel.index = pd.DatetimeIndex(panel.index.values, name=panel.index.name)
    first = next(iter(panels.values()))
    if any(not panel.index.equals(first.index) for panel in panels.values()):
        raise ValueError("SEC_INPUT_ALIGNMENT_MISMATCH")
    security_ids = sorted({str(security_id) for panel in panels.values()
                           for security_id in panel.columns})
    if not security_ids:
        raise ValueError("SEC_INPUT_NO_SECURITIES")
    panels = {alias: panel.reindex(columns=security_ids)
              for alias, panel in panels.items()}
    first = next(iter(panels.values()))
    evidence = dict(provider.evidence_hashes)
    evidence.update({
        "availability_mode": mode,
        "start": str(pd.Timestamp(start).date()),
        "end": str(pd.Timestamp(end).date()),
        "security_ids": [str(value) for value in first.columns],
        "field_basis_by_alias": {
            alias: [field, basis] for alias, (field, basis) in requested.items()
        },
        "binding_tracks_by_alias": requested_tracks,
        "panel_sha256": {
            alias: _panel_sha256(panel) for alias, panel in panels.items()
        },
    })
    return panels, evidence


def verify_sec_input_evidence(
    inputs: Mapping[str, pd.DataFrame], evidence: Mapping[str, Any]
) -> None:
    """Fail closed when an input matrix disagrees with its pinned release record."""
    if not isinstance(evidence, Mapping) or evidence.get("dataset_version") != "SEC_PIT_FUNDAMENTALS_V1":
        raise ValueError("SEC_INPUT_EVIDENCE_MISSING")
    if any(not isinstance(evidence.get(key), str) or not _HASH.fullmatch(evidence[key])
           for key in _RELEASE_HASH_KEYS):
        raise ValueError("SEC_INPUT_EVIDENCE_MISSING")
    for evidence_key, output_name in (
        ("canonical_mapping_hash", "canonical_field_catalog.csv"),
        ("derived_field_catalog_hash", "derived_field_definitions.json"),
        ("security_mapping_hash", "cik_security_map.csv"),
    ):
        if evidence.get(f"output:{output_name}") != evidence[evidence_key]:
            raise ValueError(f"SEC_INPUT_EVIDENCE_INCONSISTENT:{evidence_key}")
    panel_output_hash = evidence.get("output:daily_pit_fundamental_panel.parquet")
    if not isinstance(panel_output_hash, str) or not _HASH.fullmatch(panel_output_hash):
        raise ValueError("SEC_INPUT_EVIDENCE_MISSING")
    if evidence.get("availability_mode") not in {"HISTORICAL_DIAGNOSTIC", "FORWARD_OBSERVED"}:
        raise ValueError("SEC_INPUT_EVIDENCE_MISSING")
    try:
        start = pd.Timestamp(evidence["start"])
        end = pd.Timestamp(evidence["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("SEC_INPUT_EVIDENCE_MISSING") from exc
    if start > end or start.tzinfo is not None or end.tzinfo is not None:
        raise ValueError("SEC_INPUT_EVIDENCE_MISSING")
    bindings = evidence.get("field_basis_by_alias")
    tracks = evidence.get("binding_tracks_by_alias")
    hashes = evidence.get("panel_sha256")
    if (not isinstance(bindings, Mapping) or not isinstance(tracks, Mapping)
            or not isinstance(hashes, Mapping)):
        raise ValueError("SEC_INPUT_EVIDENCE_MISSING")
    if (not inputs or set(inputs) != set(bindings) or set(inputs) != set(tracks)
            or set(inputs) != set(hashes)):
        raise ValueError("SEC_INPUT_SET_MISMATCH")
    ids = evidence.get("security_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("SEC_INPUT_EVIDENCE_MISSING")
    for alias, panel in inputs.items():
        pair = bindings[alias]
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or alias != f"sec__{pair[0]}__{pair[1]}"):
            raise ValueError(f"SEC_INPUT_ALIAS_BASIS_MISMATCH:{alias}")
        if tracks[alias] not in {"SEC_SOURCE_LINKED", "SEC_HYPOTHESIS_PROXY"}:
            raise ValueError(f"SEC_INPUT_BINDING_TRACK_INVALID:{alias}")
        if not isinstance(panel, pd.DataFrame):
            raise ValueError("SEC_INPUT_NOT_PANEL")
        if list(panel.columns) != ids or panel.empty or panel.index.has_duplicates:
            raise ValueError("SEC_INPUT_SECURITY_OR_SESSION_MISMATCH")
        if not isinstance(panel.index, pd.DatetimeIndex) or panel.index.tz is not None:
            raise ValueError("SEC_INPUT_SECURITY_OR_SESSION_MISMATCH")
        if (panel.index.min() < start or panel.index.max() > end
                or not panel.index.is_monotonic_increasing):
            raise ValueError("SEC_INPUT_SECURITY_OR_SESSION_MISMATCH")
        if not isinstance(hashes[alias], str) or not _HASH.fullmatch(hashes[alias]):
            raise ValueError("SEC_INPUT_EVIDENCE_MISSING")
        if _panel_sha256(panel) != hashes[alias]:
            raise ValueError(f"SEC_INPUT_PANEL_HASH_MISMATCH:{alias}")
