"""Stage-aware configuration contracts.

Unresolved production decisions are blockers.  Metadata and mapping stages are
deliberately usable before strategy release settings have been chosen.
"""

from collections.abc import Mapping
from typing import Any


RELEASE_REQUIRED_FIELDS = (
    "N",
    "cash_min",
    "single_name_cap",
    "sector_cap",
    "rebalance_calendar",
    "reference_price",
    "execution_window",
    "turnover_limit",
    "quote_policy",
    "account_snapshot_ttl",
    "execution_protection",
    "cost_model",
    "evaluation_protocol",
)

STAGE_REQUIRED_FIELDS = {
    "metadata": (),
    "mapping": (),
    "synthetic": (),
    "development": ("evaluation_protocol",),
    "validation": ("evaluation_protocol",),
    "holdout": ("evaluation_protocol",),
    "candidate": RELEASE_REQUIRED_FIELDS,
    "paper-release": RELEASE_REQUIRED_FIELDS,
    "release": RELEASE_REQUIRED_FIELDS,
}


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _code(field: str) -> str:
    return f"MISSING_{field.upper()}"


def validate_config(config: Mapping[str, Any], stage: str) -> list[str]:
    """Return stable blocking codes for unresolved inputs at *stage*."""
    required = STAGE_REQUIRED_FIELDS.get(stage)
    if required is None:
        return ["UNKNOWN_STAGE"]
    missing = [_code(field) for field in required if _missing(config.get(field))]
    return ["BLOCKED_CONFIG", *missing] if missing else []
