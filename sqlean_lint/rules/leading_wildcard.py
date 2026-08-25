"""SQL-LIKE-001 [MEDIUM]: leading-wildcard LIKE patterns."""
from __future__ import annotations

from typing import List

from sqlglot import exp

from .base import BaseRule
from ..types import LintContext, RuleViolation, Severity


class LeadingWildcardRule(BaseRule):
    """LIKE '%abc' cannot use B-tree range scans.

    A leading wildcard removes any prefix anchor, forcing the storage engine
    to evaluate the pattern against every row of the column.
    """

    rule_id = "SQL-LIKE-001"
    severity = Severity.MEDIUM
    title = "Leading wildcard LIKE"
    description = (
        "Patterns starting with '%' disable prefix range scans and degrade into "
        "full column scans."
    )

    def check(self, expression: exp.Expression, context: LintContext) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        for like in expression.find_all(exp.Like, exp.ILike):
            pattern = like.args.get("expression")
            if (
                isinstance(pattern, exp.Literal)
                and pattern.is_string
                and str(pattern.name).startswith("%")
            ):
                violations.append(
                    self.make_violation(
                        like,
                        context,
                        message=(
                            f"Leading wildcard pattern {pattern.this!r} forces a full scan of the "
                            "matched column because no prefix can be used for range seeks."
                        ),
                        suggested_fix=(
                            "Anchor a prefix ('abc%'), maintain a reversed column + 'rev(needle)%' "
                            "for suffix search, or move to a full-text/trigram index."
                        ),
                        anchor=pattern,
                    )
                )
        return violations
