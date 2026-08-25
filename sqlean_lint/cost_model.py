"""Deterministic query plan-risk estimator (0-100 index).

Scoring contributions (documented, reproducible, no ML):

* Cartesian / predicate-less join ......... +50 (+5 per extra, capped +60 total)
* Non-SARGable full-scan predicates ....... +25 (+2 per extra, capped +35 total)
* Nested unbounded sorts .................. +20 (+5 per extra, capped +30 total)
* Subquery nesting depth beyond level 1 ... +5 per extra level, capped +10

The final score is clamped to [0, 100] and banded into risk levels.
The same input always yields byte-identical output (CI-cache friendly).
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from sqlglot import exp

from .types import CostEstimate, RiskLevel, RuleViolation

RULE_CARTESIAN = "SQL-CART-001"
RULE_SARGABLE = "SQL-SARG-001"
RULE_SORT = "SQL-SORT-001"


def _max_nesting_depth(expressions: Sequence[exp.Expression]) -> int:
    """Deepest chain of nested subqueries across all statements."""
    deepest = 0
    for tree in expressions:
        for subquery in tree.find_all(exp.Subquery):
            depth = 1
            parent = subquery.parent
            while parent is not None:
                if isinstance(parent, (exp.Subquery, exp.CTE)):
                    depth += 1
                parent = parent.parent
            deepest = max(deepest, depth)
    return deepest


def _band(score: int) -> RiskLevel:
    if score >= 75:
        return RiskLevel.CRITICAL
    if score >= 50:
        return RiskLevel.HIGH
    if score >= 25:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def estimate_cost(
    violations: List[RuleViolation],
    expressions: Optional[Sequence[exp.Expression]] = None,
) -> CostEstimate:
    """Compute the deterministic risk index for one linted source."""
    counts: dict = {}
    for violation in violations:
        counts[violation.rule_id] = counts.get(violation.rule_id, 0) + 1

    parts: List[str] = []
    join_risk = 0
    sort_risk = 0
    sarg_risk = 0

    cartesian = counts.get(RULE_CARTESIAN, 0)
    if cartesian:
        join_risk = 50 + min(cartesian - 1, 2) * 5
        parts.append(
            f"{cartesian} cartesian/predicate-less join(s) force O(N*M) pairwise explosion (+{join_risk})"
        )

    sargable = counts.get(RULE_SARGABLE, 0)
    if sargable:
        sarg_risk = 25 + min(sargable - 1, 5) * 2
        parts.append(
            f"{sargable} non-SARGable predicate(s) disable index/partition pruning (+{sarg_risk})"
        )

    sorts = counts.get(RULE_SORT, 0)
    if sorts:
        sort_risk = 20 + min(sorts - 1, 2) * 5
        parts.append(
            f"{sorts} unbounded nested sort(s) materialize intermediate pipelines (+{sort_risk})"
        )

    depth_bonus = 0
    depth = _max_nesting_depth(expressions or [])
    if depth > 1:
        depth_bonus = min((depth - 1) * 5, 10)
        parts.append(
            f"Subquery nesting depth {depth} multiplies intermediate row propagation (+{depth_bonus})"
        )

    raw_score = join_risk + sarg_risk + sort_risk + depth_bonus
    score = max(0, min(int(raw_score), 100))

    trees = list(expressions or [])
    has_tables = any(tree.find(exp.Table) is not None for tree in trees)
    if trees and not has_tables:
        complexity = "O(1)"
    elif cartesian:
        complexity = "O(N*M)"
    else:
        complexity = "O(N)"

    explanation = "; ".join(parts) if parts else "No structural risk signals detected."
    return CostEstimate(
        risk_score=score,
        risk_level=_band(score),
        scan_complexity=complexity,
        join_risk=join_risk,
        sort_risk=sort_risk,
        explanation=explanation,
    )
