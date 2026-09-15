"""Current as-of, liquidity-filtered U.S. stock-selection universe."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from .security_master import liquidity_eligible


ASSET_COLUMNS = {
    "security_id",
    "ticker",
    "exchange",
    "status",
    "tradable",
    "security_type",
    "domicile",
    "source",
    "available_at",
}
BAR_COLUMNS = {"date", "close", "volume", "adjustment", "source", "available_at"}


def _timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("AS_OF_TIMEZONE_REQUIRED")
    return timestamp.tz_convert("UTC")


def _validate_policy(policy: Mapping[str, Any]) -> None:
    required = {
        "exchanges",
        "security_types",
        "minimum_sessions",
        "minimum_raw_close",
        "adv_window",
        "minimum_adv_usd",
        "minimum_universe_size",
        "maximum_universe_size",
    }
    if not required.issubset(policy):
        raise ValueError("UNIVERSE_POLICY_FIELDS_MISSING")
    if int(policy["minimum_sessions"]) != 252 or int(policy["adv_window"]) != 60:
        raise ValueError("UNSUPPORTED_UNIVERSE_HISTORY_POLICY")
    if int(policy["minimum_universe_size"]) != 500 or int(policy["maximum_universe_size"]) != 1000:
        raise ValueError("UNSUPPORTED_UNIVERSE_SIZE_POLICY")
    if float(policy["minimum_raw_close"]) != 5 or float(policy["minimum_adv_usd"]) != 10_000_000:
        raise ValueError("UNSUPPORTED_LIQUIDITY_POLICY")


def _classification_view(
    classifications: pd.DataFrame | None, as_of: pd.Timestamp
) -> pd.DataFrame:
    if classifications is None:
        return pd.DataFrame(columns=["security_id", "sector", "industry"])
    required = {"security_id", "sector", "industry", "available_at"}
    if not required.issubset(classifications.columns):
        raise ValueError("CLASSIFICATION_FIELDS_MISSING")
    frame = classifications.copy()
    frame["available_at"] = pd.to_datetime(frame.available_at, utc=True, errors="raise")
    frame = frame.loc[frame.available_at <= as_of]
    return (
        frame.sort_values(["security_id", "available_at"], kind="stable")
        .groupby("security_id", sort=False)
        .tail(1)
        .loc[:, ["security_id", "sector", "industry"]]
    )


def build_current_liquid_universe(
    assets: pd.DataFrame,
    bars: Mapping[str, pd.DataFrame],
    as_of: Any,
    policy: Mapping[str, Any],
    classifications: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Filter and rank a current snapshot; never claim historical PIT status."""
    cutoff = _timestamp(as_of)
    _validate_policy(policy)
    if not ASSET_COLUMNS.issubset(assets.columns):
        raise ValueError("ASSET_FIELDS_MISSING")
    if assets.security_id.duplicated().any():
        raise ValueError("DUPLICATE_SECURITY_ID")
    if assets.source.isna().any() or assets.available_at.isna().any():
        raise ValueError("ASSET_PROVENANCE_MISSING")
    current_assets = assets.copy()
    current_assets["available_at"] = pd.to_datetime(
        current_assets.available_at, utc=True, errors="raise"
    )
    current_assets = current_assets.loc[current_assets.available_at <= cutoff]
    classifications_view = _classification_view(classifications, cutoff)
    classification_map = classifications_view.set_index("security_id").to_dict("index")

    accepted: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    exchanges = set(policy["exchanges"])
    security_types = set(policy["security_types"])
    for asset in current_assets.to_dict("records"):
        security_id = str(asset["security_id"])
        reasons = []
        if asset.get("domicile") != "US":
            reasons.append("NON_US_DOMICILE")
        if asset.get("exchange") not in exchanges:
            reasons.append("EXCHANGE_NOT_ELIGIBLE")
        if asset.get("security_type") not in security_types:
            reasons.append("SECURITY_TYPE_NOT_ELIGIBLE")
        if str(asset.get("status", "")).upper() != "ACTIVE":
            reasons.append("INACTIVE")
        if asset.get("tradable") is not True:
            reasons.append("NOT_TRADABLE")
        panel = bars.get(security_id)
        if panel is None:
            reasons.append("BARS_MISSING")
            exclusions.append({"security_id": security_id, "ticker": asset.get("ticker"), "reasons": reasons})
            continue
        if not BAR_COLUMNS.issubset(panel.columns):
            raise ValueError(f"BAR_FIELDS_MISSING:{security_id}")
        if panel.source.isna().any() or panel.available_at.isna().any():
            raise ValueError(f"BAR_PROVENANCE_MISSING:{security_id}")
        if not panel.adjustment.astype(str).eq("raw").all():
            raise ValueError(f"BAR_ADJUSTMENT_MUST_BE_RAW:{security_id}")
        visible = panel.copy()
        visible["date"] = pd.to_datetime(visible.date, utc=True, errors="raise")
        visible["available_at"] = pd.to_datetime(
            visible.available_at, utc=True, errors="raise"
        )
        visible = visible.loc[
            (visible.date <= cutoff) & (visible.available_at <= cutoff)
        ].sort_values("date", kind="stable")
        if visible.date.duplicated().any():
            raise ValueError(f"DUPLICATE_BAR_SESSION:{security_id}")
        liquidity = liquidity_eligible(
            visible.loc[:, ["close", "volume"]], len(visible)
        )
        reasons.extend(liquidity["reasons"])
        if reasons:
            exclusions.append({"security_id": security_id, "ticker": asset.get("ticker"), "reasons": sorted(set(reasons))})
            continue
        classification = classification_map.get(security_id, {})
        accepted.append(
            {
                "security_id": security_id,
                "ticker": asset.get("ticker"),
                "exchange": asset.get("exchange"),
                "security_type": asset.get("security_type"),
                "latest_raw_close": float(visible.close.iloc[-1]),
                "adv60": float(liquidity["adv60"]),
                "sector": classification.get("sector"),
                "industry": classification.get("industry"),
                "asset_source": asset.get("source"),
                "bar_source": ",".join(sorted(set(visible.source.astype(str)))),
                "as_of": cutoff.isoformat(),
            }
        )
    ranked = pd.DataFrame(accepted)
    if ranked.empty:
        ranked = pd.DataFrame(
            columns=[
                "security_id", "ticker", "exchange", "security_type",
                "latest_raw_close", "adv60", "sector", "industry",
                "asset_source", "bar_source", "as_of",
            ]
        )
    ranked = ranked.sort_values(
        ["adv60", "security_id"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)
    pre_cap = len(ranked)
    maximum = int(policy["maximum_universe_size"])
    if len(ranked) > maximum:
        for row in ranked.iloc[maximum:].itertuples(index=False):
            exclusions.append(
                {"security_id": row.security_id, "ticker": row.ticker, "reasons": ["LIQUIDITY_RANK_CAP"]}
            )
        ranked = ranked.iloc[:maximum].reset_index(drop=True)
    minimum = int(policy["minimum_universe_size"])
    if len(ranked) < minimum:
        raise ValueError(f"BLOCKED_INSUFFICIENT_UNIVERSE:{len(ranked)}<{minimum}")
    evidence = {
        "status": "PASS_CURRENT_ONLY",
        "universe_kind": "CURRENT_AS_OF",
        "as_of": cutoff.isoformat(),
        "pre_cap_eligible_count": pre_cap,
        "eligible_count": len(ranked),
        "excluded_count": len(exclusions),
        "exclusions": exclusions,
        "historical_pit_verified": False,
        "historical_reuse_label": "SURVIVORSHIP_BIASED_DIAGNOSTIC",
        "classification_complete": bool(
            not ranked.sector.isna().any() and not ranked.industry.isna().any()
        ),
    }
    return ranked, evidence


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_current_universe_snapshot(
    assets: pd.DataFrame,
    bars: Mapping[str, pd.DataFrame],
    as_of: Any,
    policy: Mapping[str, Any],
    output_dir: Path | str,
    classifications: pd.DataFrame | None = None,
) -> dict[str, Path]:
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    eligible, evidence = build_current_liquid_universe(
        assets, bars, as_of, policy, classifications
    )
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "eligible": output / "eligible_universe.csv",
        "excluded": output / "excluded_universe.jsonl",
        "evidence": output / "universe_evidence.json",
        "manifest": output / "manifest.json",
    }
    eligible.to_csv(paths["eligible"], index=False)
    paths["excluded"].write_text(
        "".join(
            json.dumps(row, sort_keys=True) + "\n" for row in evidence["exclusions"]
        ),
        encoding="utf-8",
    )
    evidence_without_rows = {key: value for key, value in evidence.items() if key != "exclusions"}
    evidence_without_rows["universe_csv_sha256"] = _digest(paths["eligible"])
    paths["evidence"].write_text(
        json.dumps(evidence_without_rows, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "artifact_type": "CURRENT_LIQUID_US_UNIVERSE",
        "files": {
            path.name: _digest(path)
            for key, path in paths.items()
            if key != "manifest"
        },
        "historical_pit_verified": False,
        "live_orders_submitted": 0,
    }
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if len(pd.read_csv(paths["eligible"])) != len(eligible):
        raise ValueError("UNIVERSE_CSV_REOPEN_MISMATCH")
    return paths
