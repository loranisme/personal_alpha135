"""Review-bound issuer succession evidence; never infer a historical security window."""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd

from .raw_store import RawArtifact, read_raw_artifact
from .mapping import apply_canonical_mapping, load_mapping_catalog
from .normalize import normalize_companyfacts
from .periods import materialize_period_bases
from .submissions import build_accession_index


DEFAULT_REVIEW = Path(__file__).resolve().parents[2] / "config" / "reviewed_issuer_succession.json"
_URL = "https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/d71068d8k12b.htm"
_CIKS = ("0000034088", "0002115436")
_DATE = pd.Timestamp("2026-07-01T00:00:00Z")
_FIELDS = {
    "ticker", "predecessor_cik", "successor_cik", "effective_date",
    "sec_filing_url", "sec_filing_sha256", "predecessor_companyfacts_sha256",
    "predecessor_submissions_sha256", "successor_companyfacts_sha256",
    "successor_submissions_sha256",
}


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _read_review(path: Path) -> tuple[dict[str, str], str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("SUCCESSION_REVIEW_MISSING_OR_UNSAFE")
    content = path.read_bytes()
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "bridges"} \
            or payload["schema_version"] != 1 or not isinstance(payload["bridges"], list) \
            or len(payload["bridges"]) != 1:
        raise ValueError("SUCCESSION_REVIEW_SCHEMA_INVALID")
    bridge = payload["bridges"][0]
    if not isinstance(bridge, dict) or set(bridge) != _FIELDS or any(
        not isinstance(value, str) or not value for value in bridge.values()
    ):
        raise ValueError("SUCCESSION_REVIEW_BRIDGE_INVALID")
    if (bridge["ticker"], bridge["predecessor_cik"], bridge["successor_cik"],
        bridge["sec_filing_url"]) != ("XOM", *_CIKS, _URL):
        raise ValueError("SUCCESSION_REVIEW_IDENTITY_CONFLICT")
    if pd.Timestamp(bridge["effective_date"], tz="UTC") != _DATE:
        raise ValueError("SUCCESSION_REVIEW_EFFECTIVE_DATE_CONFLICT")
    for key in _FIELDS:
        if key.endswith("sha256") and not re.fullmatch(r"[0-9a-f]{64}", bridge[key]):
            raise ValueError("SUCCESSION_REVIEW_HASH_INVALID:" + key)
    return bridge, hashlib.sha256(content).hexdigest()


def _reviewed_artifact(
    root: Path, cik: str, source_type: str, expected_hash: str, as_of: pd.Timestamp,
) -> tuple[bytes, RawArtifact]:
    ledger = root / "download_ledger.jsonl"
    if not ledger.is_file() or ledger.is_symlink():
        raise ValueError("SUCCESSION_RAW_LEDGER_MISSING_OR_UNSAFE:" + source_type)
    candidates: list[RawArtifact] = []
    for number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        try:
            artifact = RawArtifact(**json.loads(line))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"SUCCESSION_RAW_LEDGER_INVALID:{number}") from exc
        if artifact.cik == cik and artifact.source_type == source_type:
            candidates.append(artifact)
    if not candidates:
        raise ValueError(f"SUCCESSION_REVIEWED_ARTIFACT_MISSING:{cik}:{source_type}")
    try:
        observed = [
            artifact for artifact in candidates
            if pd.Timestamp(artifact.requested_at) <= as_of
            and pd.Timestamp(artifact.first_seen_at) <= as_of
        ]
    except (TypeError, ValueError) as exc:
        raise ValueError("SUCCESSION_ARTIFACT_TIME_INVALID") from exc
    if not observed:
        raise ValueError(f"SUCCESSION_ARTIFACT_NOT_AVAILABLE_ASOF:{cik}:{source_type}")
    artifact = max(observed, key=lambda row: (row.requested_at, row.sha256))
    if artifact.sha256 != expected_hash:
        raise ValueError(f"SUCCESSION_LATEST_ARTIFACT_NOT_REVIEWED:{cik}:{source_type}")
    expected_url = (
        _URL if source_type == "issuer_succession_filing" else
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json" if source_type == "companyfacts" else
        f"https://data.sec.gov/submissions/CIK{cik}.json"
    )
    if artifact.source_url != expected_url or artifact.http_status != 200 \
            or artifact.receipt_time_verified is not True:
        raise ValueError(f"SUCCESSION_ARTIFACT_PROVENANCE_INVALID:{cik}:{source_type}")
    try:
        requested = pd.Timestamp(artifact.requested_at)
        received = pd.Timestamp(artifact.first_seen_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("SUCCESSION_ARTIFACT_TIME_INVALID") from exc
    if requested.tzinfo is None or received.tzinfo is None or requested > as_of or received > as_of \
            or requested > received:
        raise ValueError(f"SUCCESSION_ARTIFACT_NOT_AVAILABLE_ASOF:{cik}:{source_type}")
    return read_raw_artifact(root, artifact), artifact


def verify_xom_successor_bridge(
    *, predecessor_root: Path, successor_root: Path, filing_root: Path,
    as_of: pd.Timestamp, review_path: Path = DEFAULT_REVIEW,
) -> dict[str, Any]:
    """Verify official source objects before admitting only the post-event XOM issuer.

    This confirms issuer succession but grants no pre-existing historical
    security identity. The caller must intersect the effective date with its
    independently verified universe window.
    """
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        raise ValueError("SUCCESSION_ASOF_TIMEZONE_REQUIRED")
    cutoff = cutoff.tz_convert("UTC")
    bridge, review_hash = _read_review(Path(review_path))
    if cutoff < _DATE:
        raise ValueError("SUCCESSION_NOT_EFFECTIVE_ASOF")
    evidence_first_seen: list[pd.Timestamp] = []
    for role, cik, root in (
        ("predecessor", _CIKS[0], Path(predecessor_root)),
        ("successor", _CIKS[1], Path(successor_root)),
    ):
        facts, facts_artifact = _reviewed_artifact(
            root, cik, "companyfacts", bridge[f"{role}_companyfacts_sha256"], cutoff
        )
        submission, submission_artifact = _reviewed_artifact(
            root, cik, "submissions", bridge[f"{role}_submissions_sha256"], cutoff
        )
        evidence_first_seen.extend([
            pd.Timestamp(facts_artifact.first_seen_at).tz_convert("UTC"),
            pd.Timestamp(submission_artifact.first_seen_at).tz_convert("UTC"),
        ])
        for label, body in (("companyfacts", facts), ("submissions", submission)):
            payload = json.loads(body)
            if str(payload.get("cik", "")).zfill(10) != cik:
                raise ValueError(f"SUCCESSION_RAW_CIK_MISMATCH:{role}:{label}")
        if role == "successor":
            payload = json.loads(submission)
            pairs = zip(payload.get("tickers", []), payload.get("exchanges", []))
            if ("XOM", "NYSE") not in {(str(a).upper(), str(b).upper()) for a, b in pairs}:
                raise ValueError("SUCCESSION_SUCCESSOR_LISTING_MISSING")
    filing, filing_artifact = _reviewed_artifact(
        Path(filing_root), _CIKS[1], "issuer_succession_filing",
        bridge["sec_filing_sha256"], cutoff,
    )
    parser = _VisibleText()
    parser.feed(filing.decode("utf-8"))
    text = " ".join(" ".join(parser.parts).split()).lower()
    required = (
        "redomiciliation merger became effective on july 1, 2026",
        "successor registrant of exxonmobil",
        "automatically exchanged for one share of common stock",
        "trading under the current exxonmobil ticker symbol",
    )
    if not all(phrase in text for phrase in required):
        raise ValueError("SUCCESSION_FILING_SEMANTICS_MISMATCH")
    evidence_first_seen.append(pd.Timestamp(filing_artifact.first_seen_at).tz_convert("UTC"))
    return {
        "ticker": "XOM",
        "predecessor_cik": _CIKS[0],
        "successor_cik": _CIKS[1],
        "effective_at": _DATE,
        "bridge_first_seen_at": max(evidence_first_seen),
        "sec_filing_url": _URL,
        "sec_filing_sha256": bridge["sec_filing_sha256"],
        "review_hash": review_hash,
        "predecessor_ledger_hash": hashlib.sha256(
            (Path(predecessor_root) / "download_ledger.jsonl").read_bytes()
        ).hexdigest(),
        "filing_ledger_hash": hashlib.sha256(
            (Path(filing_root) / "download_ledger.jsonl").read_bytes()
        ).hexdigest(),
    }


def build_xom_predecessor_manual_versions(
    *, predecessor_root: Path, successor_root: Path, filing_root: Path,
    mapping_path: Path, as_of: pd.Timestamp,
) -> pd.DataFrame:
    """Normalize the reviewed predecessor only for the five-field manual gate.

    These rows are reconciliation evidence, not a historical security panel.
    They are never added to the released versions or daily PIT dataset.
    """
    reviewed = verify_xom_successor_bridge(
        predecessor_root=predecessor_root, successor_root=successor_root,
        filing_root=filing_root, as_of=as_of,
    )
    cutoff = pd.Timestamp(as_of).tz_convert("UTC")
    artifacts: list[RawArtifact] = []
    ledger = Path(predecessor_root) / "download_ledger.jsonl"
    for number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        try:
            artifact = RawArtifact(**json.loads(line))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"SUCCESSION_MANUAL_LEDGER_INVALID:{number}") from exc
        if (artifact.cik == reviewed["predecessor_cik"]
                and artifact.source_type in {"companyfacts", "submissions", "submissions_file"}
                and artifact.http_status == 200
                and artifact.receipt_time_verified is True
                and pd.Timestamp(artifact.first_seen_at) <= cutoff):
            artifacts.append(artifact)
    latest: dict[tuple[str, str], RawArtifact] = {}
    for artifact in artifacts:
        key = (artifact.source_type, artifact.source_url)
        if key not in latest or (artifact.requested_at, artifact.sha256) > (
                latest[key].requested_at, latest[key].sha256):
            latest[key] = artifact
    company = [a for a in latest.values() if a.source_type == "companyfacts"]
    submission = [a for a in latest.values() if a.source_type == "submissions"]
    older = sorted(
        (a for a in latest.values() if a.source_type == "submissions_file"),
        key=lambda item: item.source_url,
    )
    if len(company) != 1 or len(submission) != 1:
        raise ValueError("SUCCESSION_MANUAL_REQUIRED_ARTIFACTS_MISSING")
    company_payload = json.loads(read_raw_artifact(predecessor_root, company[0]))
    submission_payload = json.loads(read_raw_artifact(predecessor_root, submission[0]))
    older_payloads = [json.loads(read_raw_artifact(predecessor_root, item)) for item in older]
    accession_index = build_accession_index(submission_payload, older_payloads)
    normalized = normalize_companyfacts(
        company_payload, accession_index,
        {"issuer_cik": reviewed["predecessor_cik"],
         "security_id": "XOM_PREDECESSOR_MANUAL_REVIEW_ONLY", "ticker": "XOM"},
        company[0].sha256, pd.Timestamp(company[0].first_seen_at),
        "SEC_PIT_FUNDAMENTALS_V1_MANUAL_REVIEW",
    )
    mapped, _ = apply_canonical_mapping(normalized, load_mapping_catalog(mapping_path))
    versions = materialize_period_bases(mapped)
    versions["manual_review_scope"] = "XOM_PREDECESSOR_ONLY_NOT_RELEASED"
    return versions
