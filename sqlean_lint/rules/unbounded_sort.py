"""SQL-SORT-001 [HIGH]: unbounded ORDER BY inside CTEs / subqueries."""
from __future__ import annotations

from typing import List

from sqlglot import exp

from .base import BaseRule
from ..parser import nested_context
from ..types import LintContext, RuleViolation, Severity


class UnboundedSortRule(BaseRule):
    """Sorts that feed another pipeline stage buy nothing but CPU.

    An ORDER BY inside a CTE or derived table without LIMIT/TOP only exists
    to satisfy human reading order; the outer query re-sorts anyway. Each one
    is a full materialization + sort of the intermediate result.
    """

    rule_id = "SQL-SORT-001"
    severity = Severity.HIGH
    title = "Unbounded sort in nested block"
    description = (
        "ORDER BY without LIMIT inside a CTE/subquery forces a full sort of the "
        "intermediate rows that the outer query will reorder again."
    )

    def check(self, expression: exp.Expression, context: LintContext) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        for select in expression.find_all(exp.Select):
            order = select.args.get("order")
            if order is None:
                continue  # window OVER (ORDER BY ...) lives inside Window nodes: excluded
            if select.args.get("limit") is not None:
                continue
            scope = nested_context(select)
            if scope is None:
                continue  # final/outer ORDER BY is meaningful presentation logic
            violations.append(
                self.make_violation(
                    order,
                    context,
                    message=(
                        f"ORDER BY inside {scope} '{select_alias(select)}' has no LIMIT: the engine "
                        "must fully sort every intermediate row although the outer query discards "
                        "that ordering."
                    ),
                    suggested_fix=(
                        "Delete this ORDER BY, or add an explicit LIMIT/TOP when a bounded "
                        "top-N inside the block is truly intended."
                    ),
                )
            )
        return violations


def select_alias(select: exp.Select) -> str:
    parent = select.parent
    if isinstance(parent, exp.CTE) and parent.alias_or_name:
        return parent.alias_or_name
    return "derived table"
