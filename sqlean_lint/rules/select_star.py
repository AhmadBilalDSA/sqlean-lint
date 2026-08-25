"""SQL-STAR-001 [MEDIUM]: SELECT * inside CTEs and subqueries."""
from __future__ import annotations

from typing import List

from sqlglot import exp

from .base import BaseRule
from ..parser import nested_context
from ..types import LintContext, RuleViolation, Severity


def _is_star(projection: exp.Expression) -> bool:
    if isinstance(projection, exp.Star):
        return True
    # 't.*' parses as Column(this=Star, table=...)
    return isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)


class SelectStarRule(BaseRule):
    """Flag unbounded column lists inside analytical blocks.

    Top-level ``SELECT *`` is allowed (interactive exploration); nested CTE /
    derived-table bodies should project only the columns consumed downstream.
    """

    rule_id = "SQL-STAR-001"
    severity = Severity.MEDIUM
    title = "SELECT * inside CTE/subquery"
    description = (
        "SELECT * inside analytical CTEs or subqueries materializes every source column, "
        "inflating memory, shuffle bytes and cloud egress."
    )

    def check(self, expression: exp.Expression, context: LintContext) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        for select in expression.find_all(exp.Select):
            scope = nested_context(select)
            if scope is None:
                continue
            stars = [projection for projection in select.expressions if _is_star(projection)]
            if stars:
                violations.append(
                    self.make_violation(
                        select,
                        context,
                        message=(
                            f"SELECT * inside {scope} '{_label(select)}' carries every source column "
                            "through the pipeline even though downstream steps likely consume only a few."
                        ),
                        suggested_fix="Enumerate only the columns actually referenced downstream.",
                        anchor=stars[0],
                    )
                )
        return violations


def _label(select: exp.Select) -> str:
    parent = select.parent
    if isinstance(parent, exp.CTE):
        alias = parent.alias_or_name
        if alias:
            return alias
    return "block"
