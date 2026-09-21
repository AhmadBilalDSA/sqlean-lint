# Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.
"""FinOps cost-estimation engine for sqlean-lint PRO.

A deterministic, rule-based estimator that converts lint findings into dollar
figures against standard cloud data-warehouse pricing models. It computes:

* **Cost of the current (unoptimized) query** — the "as-is" spend.
* **Estimated post-fix cost** — what the same query would cost after the safe
  AST rewrites provided by ``fix_sql``.
* **Projected monthly savings** — the delta, extrapolated to a typical
  monthly execution frequency.

Pricing constants are baked in (no network, no telemetry) and keyed to the
same on-demand list prices exposed by ``cost_model``:

* **Snowflake** — per-credit compute cost tied to recommended warehouse size.
* **BigQuery / on-demand** — $/TB scanned with a minimum scan floor.

Sources of savings are attributed per rule so the report is auditable:
``SQL-CART-001`` (read amplification from cross-products), ``SQL-SARG-001``
(index/partition pruning restoring selective scans) and ``SQL-WILD-001``
(redundant column materialization).

All functions are pure and never raise on arbitrary input.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from sqlean_lint.cost_model import (
    SNOWFLAKE_LADDER,
    SNOWFLAKE_USD_PER_CREDIT,
)

# Default dollars-per-TB for BigQuery on-demand analysis pricing.
BIGQUERY_ANALYSIS_USD_PER_TB: float = 6.25
BIGQUERY_MIN_SCAN_BYTES: int = 10 * 1024 * 1024  # 10 MiB floor per table

# Severity -> compute-intensity multiplier used to scale the per-query cost.
_SEVERITY_WEIGHT: Dict[str, float] = {
    "LOW": 1.0,
    "MEDIUM": 2.0,
    "HIGH": 3.5,
    "CRITICAL": 6.0,
}

# Per-rule unit-cost coefficients for the savings model (arbitrary USD
# proportionality, documented and stable).
_RULE_SAVINGS_WEIGHT: Dict[str, float] = {
    "SQL-CART-001": 3.0,   # cross-products read N*M rows
    "SQL-SARG-001": 2.5,   # restores index/partition pruning
    "SQL-WILD-001": 1.5,   # drops redundant column materialization
    "SCHEMA-001": 0.5,     # failed queries still consume a small scan
}

MONTHLY_EXECUTIONS_DEFAULT: int = 1000


@dataclass
class RuleCostBreakdown:
    """Dollar attribution for one rule family."""

    rule_id: str
    count: int
    severity: str
    unit_weight: float
    estimated_usd_per_query: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FinOpsEstimate:
    """Top-level cost estimate for one linted source."""

    dialect: str
    platform: str
    as_is_usd_per_query: float
    post_fix_usd_per_query: float
    savings_usd_per_query: float
    savings_pct: float
    monthly_executions: int
    projected_monthly_savings: float
    breakdown: List[RuleCostBreakdown] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _rule_savings_factor(violations: List[Any]) -> float:
    """Sum weighted savings units across all fixable rule families."""
    seen: Dict[str, float] = {}
    for v in violations:
        rule_id = _rule_id(v)
        if rule_id not in _RULE_SAVINGS_WEIGHT:
            continue
        weight = _SEVERITY_WEIGHT.get(_severity(v).upper(), 2.0)
        seen[rule_id] = seen.get(rule_id, 0.0) + _RULE_SAVINGS_WEIGHT[rule_id] * weight
    return sum(seen.values())


def _rule_id(v: Any) -> str:
    return str(getattr(v, "rule_id", "") or (v.get("rule_id", "") if isinstance(v, dict) else ""))


def _severity(v: Any) -> str:
    return str(getattr(v, "severity", "MEDIUM") or (v.get("severity", "MEDIUM") if isinstance(v, dict) else "MEDIUM"))


def _as_is_cost(violations: List[Any], base_usd: float) -> float:
    """Scale *base_usd* by the severity of the worst finding."""
    worst = 1.0
    for v in violations:
        w = _SEVERITY_WEIGHT.get(_severity(v).upper(), 1.0)
        worst = max(worst, w)
    return round(base_usd * worst, 6)


def estimate_finops(
    violations: Optional[List[Any]] = None,
    *,
    dialect: str = "duckdb",
    platform: str = "snowflake",
    scan_bytes_hint: int = 100 * 1024 * 1024,
    tables: int = 1,
    monthly_executions: int = MONTHLY_EXECUTIONS_DEFAULT,
) -> FinOpsEstimate:
    """Estimate dollars and projected savings from lint *violations*.

    Args:
        violations: list of findings (LintViolation or dict-like).
        dialect: analyzed SQL dialect.
        platform: pricing model — ``"snowflake"`` or ``"bigquery"``.
        scan_bytes_hint: estimated per-table scan bytes for the base cost.
        tables: number of tables referenced (drive recommended warehouse size).
        monthly_executions: how many times the query runs per month.

    Returns:
        A :class:`FinOpsEstimate` with per-query and projected figures.
    """
    violations = list(violations or [])

    # ── base per-query cost ────────────────────────────────────────────
    if platform == "bigquery":
        total_scan = max(tables, 1) * scan_bytes_hint
        floored = max(total_scan, tables * BIGQUERY_MIN_SCAN_BYTES)
        tb = floored / (1024 ** 4)
        base_usd = tb * BIGQUERY_ANALYSIS_USD_PER_TB
    else:
        recommended = "XS"
        for _min_credits, label, credits in SNOWFLAKE_LADDER:
            if credits >= max(tables, 1):
                recommended = label
                break
        credits_hr = next(c for _m, _l, c in SNOWFLAKE_LADDER if _l == recommended)
        # Model a fast short query: ~1 second of a one-credit warehouse.
        base_usd = credits_hr * SNOWFLAKE_USD_PER_CREDIT / 3600.0

    as_is = _as_is_cost(violations, base_usd)
    savings_units = _rule_savings_factor(violations)
    post_fix = round(max(0.0, as_is - as_is * min(savings_units / 12.0, 0.85)), 6)
    savings = round(as_is - post_fix, 6)
    savings_pct = round((savings / as_is * 100.0) if as_is > 0 else 0.0, 2)

    projected = round(savings * monthly_executions, 2)

    # ── per-rule breakdown ─────────────────────────────────────────────
    breakdown: List[RuleCostBreakdown] = []
    grouped: Dict[str, Dict[str, Any]] = {}
    for v in violations:
        rid = _rule_id(v)
        if rid not in _RULE_SAVINGS_WEIGHT:
            continue
        entry = grouped.setdefault(rid, {
            "count": 0, "severity": _severity(v).upper(),
            "weight": _RULE_SAVINGS_WEIGHT[rid],
        })
        entry["count"] += 1
    for rid, entry in grouped.items():
        unit = round(entry["weight"] * _SEVERITY_WEIGHT.get(entry["severity"], 2.0), 4)
        breakdown.append(RuleCostBreakdown(
            rule_id=rid,
            count=entry["count"],
            severity=entry["severity"],
            unit_weight=entry["weight"],
            estimated_usd_per_query=round(base_usd * unit / 12.0, 6),
        ))

    notes: List[str] = []
    if violations:
        notes.append(
            "Savings assume the safe AST rewrites from fix_sql() are applied "
            "(SQL-CART-001, SQL-SARG-001, SQL-WILD-001)."
        )
    if platform == "snowflake":
        notes.append(f"Pricing: ${SNOWFLAKE_USD_PER_CREDIT}/credit on-demand.")
    else:
        notes.append(f"Pricing: ${BIGQUERY_ANALYSIS_USD_PER_TB}/TB BigQuery on-demand.")

    return FinOpsEstimate(
        dialect=dialect,
        platform=platform,
        as_is_usd_per_query=as_is,
        post_fix_usd_per_query=post_fix,
        savings_usd_per_query=savings,
        savings_pct=savings_pct,
        monthly_executions=monthly_executions,
        projected_monthly_savings=projected,
        breakdown=breakdown,
        notes=notes,
    )


def estimate(dialect: str = "duckdb", **kwargs: Any) -> FinOpsEstimate:
    """Alias for :func:`estimate_finops` (drop-in, discoverable)."""
    return estimate_finops(dialect=dialect, **kwargs)
