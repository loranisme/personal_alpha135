"""Evidence-bounded mapping from BRAIN field metadata to external sources."""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any


DIRECT = "DIRECT_CANDIDATE"
PROXY = "DERIVABLE_PROXY"
SPECIALIST = "SPECIALIST_REQUIRED"
UNKNOWN = "UNKNOWN"

SOURCE_URLS = {
    "alpaca": "https://docs.alpaca.markets/us/docs/historical-stock-data-1",
    "alpaca_options": "https://docs.alpaca.markets/us/docs/historical-option-data",
    "tiingo_eod": "https://www.tiingo.com/documentation/end-of-day",
    "tiingo_fundamentals": "https://www.tiingo.com/documentation/fundamentals",
    "sec_companyfacts": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
    "factset_estimates": "https://insight.factset.com/resources/factset-consensus-estimates-datafeed",
    "factset_revere": "https://go.factset.com/hubfs/Website_Downloads/Statistical%20Package%20Integration/Docs%203.0/ExtractVectorFormula.pdf",
    "ravenpack": "https://vdp-ireland-prod.ravenpack.com/products/news-analytics",
    "cboe": "https://datashop.cboe.com/options",
    "msci_gics": "https://www.msci.com/indexes/index-resources/gics",
}

PRICE_DIRECT = {"open", "high", "low", "close", "volume"}
PRICE_DERIVED = {"returns", "adv20", "sharesout"}
GROUP_FIELDS = {"market", "sector", "industry", "subindustry"}
COMMON_FUNDAMENTALS = {
    "assets", "assets_curr", "bookvalue_ps", "cash", "cashflow_dividends",
    "cashflow_op", "debt", "debt_lt", "ebit", "ebitda", "enterprise_value",
    "equity", "goodwill", "income", "inventory", "inventory_turnover",
    "liabilities", "liabilities_curr", "operating_income", "receivable",
    "return_assets", "return_equity", "revenue", "sales", "sales_growth",
    "working_capital",
}
ANALYST_REPORTED = {
    "actual_cashflow_per_share_value_quarterly", "anl4_capex_value",
    "capital_expenditure_amount", "capital_expenditure_reported_value",
    "cash_flow_from_operations", "free_cash_flow_reported_value",
    "operating_cashflow_reported_value", "research_development_expense",
}
NEWS12_DERIVED = {
    "news_atr14", "news_atr_ratio", "news_cap", "news_close_vol",
    "news_indx_perf", "news_max_dn_ret", "news_max_up_ret",
    "news_mins_4_pct_dn", "news_mins_4_pct_up", "news_pct_1min",
    "news_pct_30min",
}


def _dataset_id(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("id") or value.get("name")
    return str(value or "UNKNOWN")


def _base(field: Mapping[str, Any]) -> dict[str, Any]:
    identifier = str(field.get("id") or field.get("name") or "")
    return {
        "field_id": identifier,
        "dataset": _dataset_id(field.get("dataset")),
        "field_type": str(field.get("type") or "UNKNOWN").upper(),
        "unit": field.get("unit"),
        "description": str(field.get("description") or ""),
        "mapping_class": UNKNOWN,
        "candidates": {"alpaca": None, "tiingo": None, "sec": None, "specialist": None},
        "source_urls": [],
        "semantic_difference": "No documented semantic match was established.",
        "availability_rule": "UNKNOWN",
        "blockers": ["SEMANTICS_UNRESOLVED"],
        "local_replacement_id": None,
        "numeric_parity_status": "UNVERIFIED_NO_REFERENCE_VALUES",
    }


def _proxy(row: dict[str, Any], difference: str) -> None:
    row["mapping_class"] = PROXY
    row["local_replacement_id"] = "local_proxy__" + row["field_id"]
    row["semantic_difference"] = difference
    row["blockers"] = ["ORIGINAL_FIELD_NOT_REPRODUCED", "NUMERIC_PARITY_UNVERIFIED"]


def map_field(field: Mapping[str, Any]) -> dict[str, Any]:
    """Classify one field using explicit dataset and field-level rules."""
    row = _base(field)
    name, dataset, typ = row["field_id"], row["dataset"], row["field_type"]

    if dataset == "pv1" and name in PRICE_DIRECT:
        row["mapping_class"] = DIRECT
        row["candidates"].update(alpaca=f"stock_bars.{name}", tiingo=f"eod.{name}")
        row["source_urls"] = [SOURCE_URLS["alpaca"], SOURCE_URLS["tiingo_eod"]]
        row["semantic_difference"] = "Feed, session, corrections and adjustment policy must be locked before parity testing."
        row["availability_rule"] = "After the selected provider publishes the completed daily bar."
        row["blockers"] = ["FEED_AND_ADJUSTMENT_POLICY_UNVERIFIED", "NUMERIC_PARITY_UNVERIFIED"]
    elif dataset == "pv1" and name == "vwap":
        row["mapping_class"] = DIRECT
        row["candidates"]["alpaca"] = "stock_bars.vw"
        row["source_urls"] = [SOURCE_URLS["alpaca"]]
        row["semantic_difference"] = "Alpaca documents bar VWAP; venue feed and session scope may differ. Tiingo EOD does not document VWAP."
        row["availability_rule"] = "After the selected Alpaca daily bar is complete."
        row["blockers"] = ["FEED_SCOPE_UNVERIFIED", "NUMERIC_PARITY_UNVERIFIED"]
    elif dataset == "pv1" and name == "dividend":
        row["mapping_class"] = DIRECT
        row["candidates"]["tiingo"] = "eod.divCash"
        row["source_urls"] = [SOURCE_URLS["tiingo_eod"]]
        row["semantic_difference"] = "Tiingo documents ex-date cash dividend; BRAIN timing and adjustment identity require parity testing."
        row["availability_rule"] = "Use only when provider availability timestamp is on or before the decision cutoff."
        row["blockers"] = ["CORPORATE_ACTION_TIMING_UNVERIFIED", "NUMERIC_PARITY_UNVERIFIED"]
    elif dataset == "pv1" and name == "cap":
        row["mapping_class"] = DIRECT
        row["candidates"]["tiingo"] = "fundamentals.daily.marketCap"
        row["source_urls"] = [SOURCE_URLS["tiingo_fundamentals"]]
        row["semantic_difference"] = "BRAIN metadata specifies millions; provider unit conversion and as-of timing must be verified."
        row["availability_rule"] = "Provider daily metric date plus recorded retrieval/availability timestamp."
        row["blockers"] = ["UNIT_CONVERSION_REQUIRED", "PIT_AVAILABILITY_UNVERIFIED", "NUMERIC_PARITY_UNVERIFIED"]
    elif dataset == "pv1" and name in PRICE_DERIVED:
        row["candidates"].update(alpaca="stock_bars", tiingo="eod")
        row["source_urls"] = [SOURCE_URLS["alpaca"], SOURCE_URLS["tiingo_eod"]]
        _proxy(row, "Requires a locally frozen return, rolling-window or shares definition; it is a new local factor input.")
        row["availability_rule"] = "After all required source observations are available."
    elif dataset == "pv1" and name in GROUP_FIELDS:
        row["candidates"]["tiingo"] = "fundamentals.meta.sicIndustry" if name in {"industry", "subindustry"} else "local_universe_group"
        row["candidates"]["specialist"] = "licensed historical taxonomy"
        row["source_urls"] = [SOURCE_URLS["tiingo_fundamentals"], SOURCE_URLS["msci_gics"]]
        _proxy(row, "The original taxonomy and point-in-time membership are unknown; SIC or GICS is a distinct local taxonomy.")
        row["availability_rule"] = "Classification effective_at and available_at must both precede the cutoff."
    elif dataset == "fundamental6" and name in COMMON_FUNDAMENTALS:
        row["mapping_class"] = DIRECT
        row["candidates"].update(tiingo=f"fundamentals.statement.semantic:{name}", sec=f"companyfacts.semantic:{name}")
        row["source_urls"] = [SOURCE_URLS["tiingo_fundamentals"], SOURCE_URLS["sec_companyfacts"]]
        row["semantic_difference"] = "A documented statement concept exists, but tag, period, currency, restatement and normalization parity remain unverified."
        row["availability_rule"] = "Use public release/filing timestamp, never fiscal period end alone."
        row["blockers"] = ["FIELD_TAG_AND_PERIOD_MAPPING_UNVERIFIED", "NUMERIC_PARITY_UNVERIFIED"]
    elif dataset == "fundamental2":
        row["candidates"].update(tiingo="fundamentals.statement.definition_lookup", sec="companyfacts.tag_mapping")
        row["source_urls"] = [SOURCE_URLS["tiingo_fundamentals"], SOURCE_URLS["sec_companyfacts"]]
        _proxy(row, "Detailed footnote concepts require issuer-tag mapping and can differ from a normalized vendor series.")
        row["availability_rule"] = "Use filing acceptance/public release time and preserve revisions."
    elif dataset == "analyst4" and name in ANALYST_REPORTED:
        row["candidates"].update(tiingo="fundamentals.statement.definition_lookup", sec="companyfacts.tag_mapping")
        row["source_urls"] = [SOURCE_URLS["tiingo_fundamentals"], SOURCE_URLS["sec_companyfacts"]]
        _proxy(row, "Reported value can be reconstructed from filings, but the analyst dataset's normalization and timestamp are not identical by evidence.")
        row["availability_rule"] = "Public release/filing availability, preserving revisions."
    elif dataset == "analyst4":
        row["mapping_class"] = SPECIALIST
        row["candidates"]["specialist"] = "FactSet Consensus Estimates candidate"
        row["source_urls"] = [SOURCE_URLS["factset_estimates"]]
        row["semantic_difference"] = "Consensus, dispersion, contributor count, prior estimate and guidance histories require a licensed estimate source."
        row["availability_rule"] = "Vendor timestamped estimate history; entitlement and point-in-time coverage unverified."
        row["blockers"] = ["SPECIALIST_ESTIMATES_REQUIRED", "ORIGIN_VENDOR_UNVERIFIED", "ENTITLEMENT_UNVERIFIED"]
    elif dataset == "model51":
        row["candidates"].update(alpaca="stock_bars+SPY", tiingo="eod+SPY")
        row["source_urls"] = [SOURCE_URLS["alpaca"], SOURCE_URLS["tiingo_eod"]]
        _proxy(row, "Locally derive using calendar-day windows; return, regression and missing-data conventions must be frozen.")
        row["availability_rule"] = "After security and SPY bars are simultaneously available."
    elif dataset == "option8" and name.startswith("historical_volatility_"):
        row["candidates"].update(alpaca="stock_bars", tiingo="eod")
        row["source_urls"] = [SOURCE_URLS["alpaca"], SOURCE_URLS["tiingo_eod"]]
        _proxy(row, "Close-to-close volatility is locally derivable, but annualization and calendar-day sampling must be frozen.")
        row["availability_rule"] = "After the daily close series is available."
    elif dataset in {"option8", "option9"}:
        row["candidates"].update(alpaca="raw_option_quotes_trades_bars", specialist="Cboe/OPRA raw and analytics candidate")
        row["source_urls"] = [SOURCE_URLS["alpaca_options"], SOURCE_URLS["cboe"]]
        _proxy(row, "Raw options permit a local surface or aggregate, but maturity interpolation, strike selection, weighting and Greeks can differ.")
        row["availability_rule"] = "Timestamped option observations; Alpaca documented history starts February 2024."
    elif dataset == "news18":
        row["mapping_class"] = SPECIALIST
        row["candidates"]["specialist"] = "RavenPack News Analytics"
        row["source_urls"] = [SOURCE_URLS["ravenpack"]]
        row["semantic_difference"] = "Named proprietary event, relevance, novelty, sentiment and impact analytics."
        row["availability_rule"] = "Licensed vendor event timestamp and revision policy."
        row["blockers"] = ["RAVENPACK_LICENSE_REQUIRED", "VERSION_AND_HISTORY_UNVERIFIED"]
    elif dataset == "news12" and (name in NEWS12_DERIVED or name == "atr"):
        row["candidates"].update(alpaca="stock_bars+news", tiingo="eod_or_intraday+news")
        row["source_urls"] = [SOURCE_URLS["alpaca"], SOURCE_URLS["tiingo_eod"]]
        _proxy(row, "Event identity, timestamp, session window, benchmark and adjustment conventions are not specified.")
        row["availability_rule"] = "All news-event and subsequent market timestamps must be available before use."
    elif dataset == "news12" and name in {"news_eps_actual", "news_short_interest"}:
        row["mapping_class"] = SPECIALIST
        row["semantic_difference"] = "The reviewed generic providers do not document the required normalized EPS-event or short-interest field."
        row["availability_rule"] = "UNKNOWN"
        row["blockers"] = ["SPECIALIST_EVENT_OR_SHORT_INTEREST_SOURCE_REQUIRED"]
    elif dataset == "pv13":
        row["mapping_class"] = SPECIALIST
        row["candidates"]["specialist"] = "FactSet Revere candidate"
        row["source_urls"] = [SOURCE_URLS["factset_revere"]]
        row["semantic_difference"] = "Relationship entity mapping, link type, history and graph-score construction are proprietary and unresolved."
        row["availability_rule"] = "Licensed point-in-time relationship history."
        row["blockers"] = ["SPECIALIST_RELATIONSHIP_DATA_REQUIRED", "ORIGIN_VENDOR_UNVERIFIED"]
    elif dataset in {"socialmedia8", "socialmedia12"}:
        row["mapping_class"] = SPECIALIST
        row["candidates"]["specialist"] = "unknown original social-data vendor/model"
        row["semantic_difference"] = "Buzz, sentiment and fast variants depend on a vendor-specific corpus and model."
        row["availability_rule"] = "Licensed point-in-time social stream and frozen model version."
        row["blockers"] = ["SOCIAL_DATA_AND_MODEL_REQUIRED", "ORIGIN_VENDOR_UNVERIFIED"]
    elif dataset == "model16":
        row["semantic_difference"] = "Proprietary score inputs, transformations and model version are not specified."
        row["availability_rule"] = "UNKNOWN"
        row["blockers"] = ["PROPRIETARY_MODEL_SPEC_UNAVAILABLE"]

    if typ == "VECTOR":
        row["blockers"].append("VECTOR_AGGREGATION_UNKNOWN")
    if typ == "GROUP" and "GROUP_TAXONOMY_IDENTITY_UNVERIFIED" not in row["blockers"]:
        row["blockers"].append("GROUP_TAXONOMY_IDENTITY_UNVERIFIED")
    return row


def _load_catalog(path: Path | str) -> dict[str, Mapping[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = payload.get("results") or payload.get("fields") or payload.get("dataFields") or []
    else:
        rows = []
    return {
        str(row.get("id") or row.get("name")): row
        for row in rows if isinstance(row, Mapping) and (row.get("id") or row.get("name"))
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def map_library(registry_path: Path | str, field_catalog: Path | str, output_dir: Path | str) -> dict[str, Any]:
    """Map a registry and emit field/Alpha coverage without certifying parity."""
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("OUTPUT_DIRECTORY_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    catalog = _load_catalog(field_catalog)
    records = registry.get("records", []) if isinstance(registry, Mapping) else []

    actual_records = [row for row in records if isinstance(row, Mapping) and row.get("alpha_id")]
    no_id_records = [row for row in records if isinstance(row, Mapping) and not row.get("alpha_id")]
    used_fields = sorted({
        str(field.get("identifier"))
        for row in records if isinstance(row, Mapping)
        for field in (row.get("dependencies") or {}).get("fields", [])
        if isinstance(field, Mapping) and field.get("identifier")
    })
    mappings = [map_field(catalog.get(name, {"id": name})) for name in used_fields]
    by_name = {row["field_id"]: row for row in mappings}

    alpha_rows: list[dict[str, Any]] = []
    for record in records:
        alpha_id = record.get("alpha_id")
        record_ref = alpha_id or record.get("local_experiment_id")
        dependencies = record.get("dependencies") or {}
        names = [str(item.get("identifier")) for item in dependencies.get("fields", []) if isinstance(item, Mapping)]
        required = [by_name[name] for name in names if name in by_name]
        blockers: list[str] = []
        if record.get("settings") is None:
            blockers.append("SETTINGS_MISSING")
        if not dependencies.get("safe", False):
            blockers.append("EXPRESSION_SEMANTICS_UNRESOLVED")
        if dependencies.get("unknown_identifiers"):
            blockers.append("UNKNOWN_IDENTIFIER")
        for item in required:
            blockers.extend(item["blockers"])
        effective_required = list(required)
        settings = record.get("settings") if isinstance(record.get("settings"), Mapping) else {}
        neutralization = str(settings.get("neutralization") or "UNKNOWN").upper()
        if neutralization in {"MARKET", "SECTOR", "INDUSTRY", "SUBINDUSTRY"}:
            group_name = neutralization.lower()
            group_mapping = map_field(catalog.get(group_name, {"id": group_name, "dataset": "pv1", "type": "GROUP"}))
            blockers.extend(group_mapping["blockers"])
            if not any(item["field_id"] == group_name for item in effective_required):
                effective_required.append(group_mapping)
        elif neutralization not in {"NONE"}:
            blockers.append("NEUTRALIZATION_SETTING_UNRESOLVED")
        blockers.append("ACTUAL_SAMPLE_PARITY_UNVERIFIED")

        provider_support: dict[str, bool] = {}
        for provider in ("alpaca", "tiingo"):
            provider_support[provider] = bool(effective_required) and all(
                item["mapping_class"] == DIRECT and item["candidates"].get(provider)
                for item in effective_required
            )
        provider_support["combined"] = bool(effective_required) and all(
            item["mapping_class"] == DIRECT
            and (item["candidates"].get("alpaca") or item["candidates"].get("tiingo") or item["candidates"].get("sec"))
            for item in effective_required
        )
        alpha_rows.append({
            "record_ref": record_ref,
            "is_actual_alpha": bool(alpha_id),
            "field_count": len(names),
            "neutralization": neutralization,
            "alpaca_direct_candidate": provider_support["alpaca"],
            "tiingo_direct_candidate": provider_support["tiingo"],
            "combined_direct_candidate": provider_support["combined"],
            "certified_complete": False,
            "blockers": sorted(set(blockers)),
        })

    field_class_counts = Counter(row["mapping_class"] for row in mappings)
    dataset_counts = Counter(row["dataset"] for row in mappings)
    provider_coverage: dict[str, Any] = {}
    for provider in ("alpaca", "tiingo", "combined"):
        if provider == "combined":
            field_count = sum(bool(row["candidates"].get("alpaca") or row["candidates"].get("tiingo") or row["candidates"].get("sec")) for row in mappings)
            alpha_key = "combined_direct_candidate"
        else:
            field_count = sum(bool(row["candidates"].get(provider)) for row in mappings)
            alpha_key = f"{provider}_direct_candidate"
        provider_coverage[provider] = {
            "candidate_field_count": field_count,
            "field_denominator": len(mappings),
            "direct_candidate_alpha_count": sum(bool(row[alpha_key]) for row in alpha_rows if row["is_actual_alpha"]),
            "alpha_denominator": len(actual_records),
            "certified_complete_alpha_count": 0,
        }

    json_rows = mappings
    (output / "field_mapping.json").write_text(json.dumps(json_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(output / "field_mapping.csv", [{
        **row,
        "candidates": json.dumps(row["candidates"], sort_keys=True),
        "source_urls": " | ".join(row["source_urls"]),
        "blockers": " | ".join(row["blockers"]),
    } for row in mappings], [
        "field_id", "dataset", "field_type", "unit", "description", "mapping_class",
        "candidates", "availability_rule", "semantic_difference", "blockers",
        "local_replacement_id", "numeric_parity_status", "source_urls",
    ])
    (output / "migration_registry.json").write_text(json.dumps(alpha_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(output / "migration_registry.csv", [{**row, "blockers": " | ".join(row["blockers"])} for row in alpha_rows], [
        "record_ref", "is_actual_alpha", "field_count", "neutralization",
        "alpaca_direct_candidate", "tiingo_direct_candidate", "combined_direct_candidate",
        "certified_complete", "blockers",
    ])

    summary = {
        "status": "MAPPED_UNCERTIFIED",
        "actual_alpha_count": len(actual_records),
        "no_id_record_count": len(no_id_records),
        "distinct_field_count": len(mappings),
        "field_mapping_class_counts": dict(sorted(field_class_counts.items())),
        "dataset_field_counts": dict(sorted(dataset_counts.items())),
        "provider_coverage": provider_coverage,
        "certified_complete_alpha_count": 0,
        "complete_library_provider": None,
        "numeric_parity_available": False,
    }
    (output / "coverage_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    decision = f"""# 数据源决定（T2）

口径日期：2026-09-07。当前只读同步获得 {len(actual_records)} 个不同 Alpha；本次映射识别 {len(mappings)} 个不同字段。映射仅证明文档候选或本地可推导性，不证明与 BRAIN 原始数据数值相同。

## 决定

第一阶段最小数据组合采用 **Tiingo EOD + Tiingo Fundamentals（需相应权限）** 作为日频价格、公司行动、日频市值和常见财务报表候选；使用 **SEC EDGAR Company Facts** 保存财报披露和修订证据。行业/子行业使用单独冻结的本地分类代理，必须生成新的本地字段 ID，不能宣称等同于 BRAIN 原分类。

Alpaca 保留为后续执行报价与可选行情层。它可提供股票 OHLCV/VWAP，且可提供原始期权行情，但当前映射不需要为了第一阶段日频研究同时购买 Alpaca 数据。若进入手工调仓清单的报价校验阶段，再单独验证 feed 权限、bid/ask、时间戳和历史窗口。

## 完整库结论

没有任何已审查供应商被认证为完整覆盖。Alpaca-only、Tiingo-only 和二者组合的认证完整 Alpha 数均为 0；原因是缺少原始历史数值做 parity、分类体系不明、分析师预期、RavenPack、社交情绪、关系图谱、期权曲面/聚合以及 proprietary model16 等输入仍未解决。

字段分类计数：`{json.dumps(dict(sorted(field_class_counts.items())), ensure_ascii=False)}`。

供应商结构候选计数（分母字段 {len(mappings)}、Alpha {len(actual_records)}）：`{json.dumps(provider_coverage, ensure_ascii=False)}`。这些数字不是已实现覆盖率。

## 防过拟合边界

本阶段不读取收益表现来选择字段或供应商，不用 Validation/Holdout 调参。代理字段一律换成本地 ID；无法确认语义、可用时点或历史修订的 Alpha 保持阻断。后续只对预先冻结的最小可运行子集做数值样本核验。
"""
    (output / "data_source_decision.md").write_text(decision, encoding="utf-8")
    return summary
