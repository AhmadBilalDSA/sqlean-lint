"""SQL-NOTIN-001 [HIGH]: NOT IN (<subquery>) NULL-trap detection."""
from __future__ import annotations

from typing import List

from sqlglot import exp

from .base import BaseRule
from ..types import LintContext, RuleViolation, Severity


class NullInSubqueryRule(BaseRule):
    """Detect the three-valued-logic trap in NOT IN subqueries.

    ``x NOT IN (SELECT y FROM t)`` returns zero rows whenever the subquery
    yields a single NULL, silently emptying entire reports. The safe forms
    are ``NOT EXISTS`` or an anti-join.
    """

    rule_id = "SQL-NOTIN-001"
    severity = Severity.HIGH
    title = "NOT IN over subquery (NULL trap)"
    description = (
        "NOT IN combined with any NULL produced by the subquery evaluates to "
        "UNKNOWN for every outer row, dropping all results."
    )

    def check(self, expression: exp.Expression, context: LintContext) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        for not_node in expression.find_all(exp.Not):
            inner = not_node.this
            if not isinstance(inner, exp.In):
                continue
            if inner.args.get("query") is None:
                continue  # literal list: no NULL-trap risk
            try:
                probe = inner.sql(dialect=context.dialect)
            except Exception:  # noqa: BLE001
                probe = ""
            violations.append(
                self.make_violation(
                    not_node,
                    context,
                    message=(
                        "NOT IN (<subquery>) is vulnerable to the SQL three-valued-logic trap: "
                        "if the subquery ever returns one NULL the whole predicate becomes "
                        f"UNKNOWN and every row disappears. Predicate: {probe}"
                    ),
                    suggested_fix=(
                        "Rewrite as NOT EXISTS (SELECT 1 FROM <source> WHERE <source>.<col> = "
                        "<outer expr> AND ...) or use an ANTI JOIN."
                    ),
                )
            )
        return violations
