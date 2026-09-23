"""Detached, performance-blind SEC fundamental Alpha expansion release."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from .expanded_library import assemble_expanded_library, deduplicate_sec_candidates
from .factors import FUNCTIONS
from .sec_deferred_audit import (
    audit_fundamental_deferred_sources,
    normalized_registry_sources,
)
from .sec_factor_inputs import load_sec_factor_inputs
from .sec_factor_verification import verify_sec_factor
from .sec_field_mapping import load_sec_field_bindings
from .sec_fundamentals import SecFundamentalProvider
from .sec_proxy_reconstruction import load_proxy_hypotheses, reconstruct_sec_hypothesis_proxy
from .sec_source_reconstruction import build_sec_source_linked_candidates


_DELIVERY_FILES = (
    "expanded_alpha_library.json", "fundamental_deferred_source_map.csv",
    "sec_source_linked_factors.json", "sec_hypothesis_proxy_factors.json",
    "source_reconstruction_index.csv", "deduplication_report.csv",
    "conversion_report.json", "validation_report.md",
)
_INPUT_NAMES = (
    "registry", "brain_field_catalog", "v4_library", "v4_manifest",
    "v4_source_index", "sec_manifest", "field_bindings", "proxy_rules",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False,
                               allow_nan=False, default=str) + "\n", encoding="utf-8")


def _safe_input(name: str, path: Path | str) -> Path:
    result = Path(path)
    if "latest" in (Path(part).stem.lower() for part in result.parts):
        raise ValueError("UNPINNED_INPUT_PATH:" + name)
    if not result.is_file() or result.is_symlink():
        raise ValueError("MISSING_OR_UNSAFE_INPUT:" + name)
    return result


def _v4_baseline(library: Mapping[str, Any], index: pd.DataFrame,
                 manifest: Mapping[str, Any], root: Path) -> None:
    if len(library.get("factors", [])) != 135:
        raise ValueError("V4_BASE_FACTOR_COUNT_MISMATCH")
    if len(index) != 886 or index.source_alpha_id.nunique() != 886:
        raise ValueError("V4_BASE_SOURCE_COUNT_MISMATCH")
    if not isinstance(manifest, Mapping) or not manifest:
        raise ValueError("V4_MANIFEST_INVALID")
    for relative, expected in manifest.items():
        if not isinstance(relative, str) or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError("V4_MANIFEST_INVALID")
        item = root / relative
        if item.is_symlink() or not item.is_file() or not item.resolve().is_relative_to(root.resolve()):
            raise ValueError("V4_MANIFEST_FILE_MISSING_OR_UNSAFE:" + relative)
        if _sha(item) != expected:
            raise ValueError("V4_MANIFEST_HASH_MISMATCH:" + relative)
    for required in ("project_factor_library.json", "source_reconstruction_index.csv"):
        if required not in manifest:
            raise ValueError("V4_MANIFEST_REQUIRED_ENTRY_MISSING:" + required)


def _catalog(provider: SecFundamentalProvider) -> pd.DataFrame:
    # The provider has already hash-verified this catalog against its PASS manifest.
    return provider._catalog.copy(deep=True)


def _source_rows(registry: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    try:
        result = normalized_registry_sources(registry)
    except ValueError as exc:
        raise ValueError("REGISTRY_SOURCE_IDS_INVALID") from exc
    return result


def _write_csv(path: Path, rows: pd.DataFrame) -> None:
    rows.to_csv(path, index=False, lineterminator="\n")


def _verify_compiled_by_track(
    compiled: list[dict[str, Any]], bindings: Mapping[str, Any],
    provider: SecFundamentalProvider, start: str, end: str,
) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame]]:
    """Keep strict and proxy reads separate even when they share a SEC field."""
    verified: list[dict[str, Any]] = []
    values: dict[str, pd.DataFrame] = {}
    for track in ("SEC_SOURCE_LINKED", "SEC_HYPOTHESIS_PROXY"):
        batch = [candidate for candidate in compiled
                 if candidate["provenance_kind"] == track]
        if not batch:
            continue
        requested = {tuple(bound.split("/", 1)) for candidate in batch
                     for bound in candidate["field_bindings"].values()}
        selected = [binding for binding in bindings.values()
                    if (binding.canonical_field, binding.required_basis) in requested
                    and (track != "SEC_SOURCE_LINKED"
                         or binding.allowed_track == "SEC_SOURCE_LINKED")]
        # Several BRAIN names can resolve to one SEC series. In a proxy batch,
        # explicit proxy opt-in is scoped to this batch, never the strict one.
        selected = list({(binding.canonical_field, binding.required_basis): replace(
            binding, allowed_track=track,
            semantic_grade=("HYPOTHESIS_PROXY" if track == "SEC_HYPOTHESIS_PROXY"
                            else binding.semantic_grade),
        ) for binding in selected}.values())
        if { (b.canonical_field, b.required_basis) for b in selected } != requested:
            raise ValueError("SEC_COMPILED_BINDING_MISSING")
        panels, panel_evidence = load_sec_factor_inputs(provider, selected, start, end)
        for candidate in batch:
            checked, matrix = verify_sec_factor(candidate, panels, panel_evidence)
            verified.append(checked)
            if matrix is not None:
                values[checked["local_factor_id"]] = matrix
    unknown = {candidate["provenance_kind"] for candidate in compiled} - {
        "SEC_SOURCE_LINKED", "SEC_HYPOTHESIS_PROXY"
    }
    if unknown:
        raise ValueError("SEC_COMPILED_TRACK_INVALID:" + ",".join(sorted(unknown)))
    return verified, values


def _relationship_index(v4_index: pd.DataFrame, demand: pd.DataFrame,
                        strict_ledger: list[dict[str, Any]], proxy_ledger: list[dict[str, Any]],
                        relationships: list[dict[str, Any]], dedup: pd.DataFrame,
                        verified: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    strict = {row["source_alpha_id"]: row for row in strict_ledger}
    proxy = {row["source_alpha_id"]: row for row in proxy_ledger}
    related: dict[str, list[dict[str, Any]]] = {}
    for row in relationships:
        related.setdefault(row["source_alpha_id"], []).append(row)
    dedup_by_source = {}
    for row in dedup.to_dict("records"):
        for sid in str(row["source_alpha_ids"]).split("|"):
            dedup_by_source[sid] = row["dedup_reason"]
    verification_failures: dict[str, set[str]] = {}
    for row in verified or []:
        if row.get("build_status") == "COMPUTE_VERIFIED":
            continue
        for sid in row.get("source_alpha_ids", []):
            verification_failures.setdefault(sid, set()).update(row.get("verification_failures", []))
    demanded = {row["source_alpha_id"]: row for row in demand.to_dict("records")}
    rows = []
    for source in v4_index.to_dict("records"):
        sid = source["source_alpha_id"]
        links = related.get(sid, [])
        candidate = proxy.get(sid, strict.get(sid, {}))
        if source["decision"] != "DEFERRED":
            status = "PRIOR_V4"
        elif links:
            status = dedup_by_source.get(sid, "UNCLASSIFIED_RELATIONSHIP")
            if status == "NEW_UNIQUE":
                status = ("NEW_SOURCE_LINKED" if links[0]["provenance_kind"] == "SEC_SOURCE_LINKED"
                          else "NEW_HYPOTHESIS_PROXY")
        else:
            status = "DEFERRED"
        rows.append({"source_alpha_id": sid, "v4_decision": source["decision"],
                     "expansion_status": status,
                     "demand_class": demanded.get(sid, {}).get("demand_class", "PRIOR_V4"),
                     "local_factor_ids": "|".join(sorted({link["local_factor_id"] for link in links})),
                     "blocking_reasons": "|".join(sorted(set(candidate.get("blocking_reasons", []))
                                                       | verification_failures.get(sid, set()))),
                     "original_expression_hash": demanded.get(sid, {}).get("original_expression_hash", "")})
    result = pd.DataFrame(rows)
    if len(result) != 886 or result.source_alpha_id.nunique() != 886:
        raise ValueError("EXPANSION_SOURCE_INDEX_INCOMPLETE")
    return result


def run_sec_alpha_expansion(
    registry_path: Path | str, brain_field_catalog_path: Path | str,
    v4_library_path: Path | str, v4_manifest_path: Path | str,
    v4_source_index_path: Path | str, sec_manifest_path: Path | str,
    field_bindings_path: Path | str, proxy_rules_path: Path | str,
    output_dir: Path | str,
) -> Mapping[str, Any]:
    """Validate pinned inputs, then atomically publish a review-only release."""
    output = Path(output_dir)
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    supplied = dict(zip(_INPUT_NAMES, (
        registry_path, brain_field_catalog_path, v4_library_path, v4_manifest_path,
        v4_source_index_path, sec_manifest_path, field_bindings_path, proxy_rules_path,
    )))
    paths = {name: _safe_input(name, path) for name, path in supplied.items()}
    sec_status = _json(paths["sec_manifest"])
    if not isinstance(sec_status, Mapping) or sec_status.get("validation_status") != "PASS":
        raise ValueError("SEC_RELEASE_NOT_APPROVED")
    # This checks every SEC output hash and evidence binding before any BRAIN source is read.
    provider = SecFundamentalProvider(paths["sec_manifest"])
    input_hashes = {name: _sha(path) for name, path in paths.items()}
    v4_manifest = _json(paths["v4_manifest"])
    v4_library = _json(paths["v4_library"])
    v4_index = pd.read_csv(paths["v4_source_index"], dtype=str, keep_default_na=False)
    _v4_baseline(v4_library, v4_index, v4_manifest, paths["v4_manifest"].parent)
    for name, relative in (("v4_library", "project_factor_library.json"),
                           ("v4_source_index", "source_reconstruction_index.csv")):
        if (paths[name].resolve() != (paths["v4_manifest"].parent / relative).resolve()
                or input_hashes[name] != v4_manifest[relative]):
            raise ValueError("V4_PINNED_FILE_MISMATCH:" + name)
    catalog = _catalog(provider)
    configured_bindings = _json(paths["field_bindings"])
    if not isinstance(configured_bindings, list):
        raise ValueError("SEC_BINDINGS_SCHEMA_INVALID")
    release_fields = set(catalog.canonical_field.astype(str))
    unavailable_configured_fields = sorted({
        str(row.get("canonical_field"))
        for row in configured_bindings
        if isinstance(row, Mapping)
        and isinstance(row.get("canonical_field"), str)
        and row["canonical_field"] not in release_fields
    })
    bindings = load_sec_field_bindings(
        paths["field_bindings"],
        catalog,
        allow_unavailable_release_derived=True,
    )
    proxy_rules = load_proxy_hypotheses(paths["proxy_rules"], bindings)
    registry = _json(paths["registry"])
    fields = _json(paths["brain_field_catalog"])
    sources = _source_rows(registry)
    demand = audit_fundamental_deferred_sources(registry, v4_index, fields, bindings)
    evidence = dict(provider.evidence_hashes)
    # The release is a current-universe development snapshot. Entity-level
    # identity is the conservative whole-release assumption for reconstruction.
    enriched = demand.copy(deep=True)
    enriched["identity_status"] = "VERIFIED_ENTITY_LEVEL_ONLY"
    enriched["sec_catalog"] = [catalog.to_dict("records") for _ in range(len(enriched))]
    enriched["sec_evidence_hashes"] = [evidence for _ in range(len(enriched))]
    operators = set(FUNCTIONS) | {"add", "subtract", "multiply", "divide", "power"}
    strict_candidates, strict_ledger = build_sec_source_linked_candidates(
        registry, enriched, bindings, operators)
    proxy_candidates = []
    proxy_ledger = []
    for row in enriched.to_dict("records"):
        if row["candidate_track"] != "SEC_HYPOTHESIS_PROXY":
            continue
        result = reconstruct_sec_hypothesis_proxy(sources[row["source_alpha_id"]],
                                                   row, bindings, proxy_rules)
        proxy_ledger.append({key: result[key] for key in (
            "source_alpha_id", "original_expression_hash", "build_status",
            "semantic_status", "blocking_reasons")})
        if result["build_status"] == "COMPILED":
            proxy_candidates.append(result)
    compiled = strict_candidates + proxy_candidates
    verified_records: list[dict[str, Any]] = []
    values: dict[str, pd.DataFrame] = {}
    if compiled:
        start, end = sec_status.get("date_min"), sec_status.get("date_max")
        if not isinstance(start, str) or not isinstance(end, str) or not start or not end:
            raise ValueError("SEC_RELEASE_DATE_RANGE_MISSING")
        verified_records, values = _verify_compiled_by_track(
            compiled, bindings, provider, start, end
        )
    passed = [row for row in verified_records if row["build_status"] == "COMPUTE_VERIFIED"]
    unique, dedup, relationships = deduplicate_sec_candidates(v4_library, passed)
    expanded = assemble_expanded_library(
        v4_library, unique, relationships,
        base_library_manifest_sha256=input_hashes["v4_manifest"],
        sec_evidence_hashes=evidence)
    expanded["field_bindings_sha256"] = input_hashes["field_bindings"]
    source_index = _relationship_index(v4_index, demand, strict_ledger, proxy_ledger,
                                       relationships, dedup, verified_records)
    reasons = Counter(reason for row in verified_records for reason in row.get("verification_failures", []))
    dedup_counts = Counter(dedup.get("dedup_reason", pd.Series(dtype=str)))
    report = {
        "schema_version": "SEC_ALPHA_EXPANSION_REPORT_V1",
        "deferred_sources_examined": len(demand),
        "strict_source_candidates": int(demand.candidate_track.eq("SEC_SOURCE_LINKED").sum()),
        "strict_sources_unlocked": len(strict_candidates),
        "proxy_sources_represented": len(proxy_candidates),
        "compiled_candidates": len(compiled),
        "compute_verified_candidates": len(passed),
        "duplicates_against_v4": dedup_counts["DUPLICATE_V4"],
        "duplicates_within_expansion": sum(value for key, value in dedup_counts.items()
                                           if key.startswith("DUPLICATE_NEW") or key == "DUPLICATE_CROSS_TRACK"),
        "new_unique_formulas": len(unique),
        "final_expanded_unique_formula_count": len(expanded["factors"]),
        "verification_failure_counts": dict(sorted(reasons.items())),
        "unavailable_configured_canonical_fields": unavailable_configured_fields,
        "demand_split": dict(sorted(demand.demand_class.value_counts().to_dict().items())),
        "usage_tier": "DIAGNOSTIC_ONLY", "filter_executed": False,
        "current_library_pointer_updated": False, "live_orders_submitted": 0,
    }
    if report["demand_split"] != {"FUNDAMENTAL_ONLY": 86, "MIXED_DOMAIN": 203,
                                   "NO_FUNDAMENTAL": 280}:
        raise ValueError("BASELINE_DEMAND_SPLIT_MISMATCH")
    # Verify inputs once more before publishing, closing a time-of-check/use gap.
    if any(_sha(paths[name]) != digest for name, digest in input_hashes.items()):
        raise ValueError("PINNED_INPUT_CHANGED_DURING_EXPANSION")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".sec-alpha-expansion-", dir=output.parent))
    try:
        _dump(stage / "expanded_alpha_library.json", expanded)
        _write_csv(stage / "fundamental_deferred_source_map.csv", demand)
        _dump(stage / "sec_source_linked_factors.json", [r for r in verified_records
               if r["provenance_kind"] == "SEC_SOURCE_LINKED"])
        _dump(stage / "sec_hypothesis_proxy_factors.json", [r for r in verified_records
               if r["provenance_kind"] == "SEC_HYPOTHESIS_PROXY"])
        _write_csv(stage / "source_reconstruction_index.csv", source_index)
        _write_csv(stage / "deduplication_report.csv", dedup)
        _dump(stage / "conversion_report.json", report)
        (stage / "factor_values").mkdir()
        for factor_id, matrix in sorted(values.items()):
            if factor_id in {r["local_factor_id"] for r in passed}:
                matrix.to_parquet(stage / "factor_values" / (factor_id + ".parquet"))
        (stage / "validation_report.md").write_text(
            "# SEC expanded Alpha library V1 — review only\n\n"
            f"SEC dataset: `{evidence['dataset_version']}`; manifest SHA-256: `{input_hashes['sec_manifest']}`.\n\n"
            "Current-universe development snapshot: survivorship bias exists; this is not a historical PIT universe. "
            "Selection evidence is DEVELOPMENT_EXPOSED. Formula computation is diagnostic only.\n\n"
            "Availability uses the pinned SEC PIT release. Determinism, chronological-prefix invariance, "
            "future-mutation and coverage results are recorded per candidate. Limited coverage and semantic "
            "losses remain visible in candidate records. V4 factors and its full manifest were hash-verified "
            "and copied without semantic modification.\n\n"
            "No Alpha Filter, current-library pointer update, broker execution, order submission, "
            "or forward clock occurred.\n", encoding="utf-8")
        hashes = {str(p.relative_to(stage)): _sha(p) for p in sorted(stage.rglob("*")) if p.is_file()}
        if set(_DELIVERY_FILES) - set(hashes):
            raise ValueError("EXPANSION_DELIVERY_INCOMPLETE")
        manifest = {
            "schema_version": "EXPANDED_ALPHA_LIBRARY_V1", "validation_status": "PASS",
            "base_factor_count": 135, "final_unique_factor_count": len(expanded["factors"]),
            "input_hashes": input_hashes, "output_hashes": hashes,
            "usage_tier": "DIAGNOSTIC_ONLY", "current_library_pointer_updated": False,
        }
        _dump(stage / "manifest.json", manifest)
        if any(_sha(stage / name) != digest for name, digest in hashes.items()):
            raise ValueError("EXPANSION_OUTPUT_HASH_MISMATCH")
        if output.exists():
            output.rmdir()
        os.replace(stage, output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"status": "PASS", "output_dir": str(output), "base_factor_count": 135,
            "new_unique_factor_count": len(unique), "final_unique_factor_count": len(expanded["factors"]),
            "usage_tier": "DIAGNOSTIC_ONLY", "filter_executed": False,
            "current_library_pointer_updated": False, "live_orders_submitted": 0}
