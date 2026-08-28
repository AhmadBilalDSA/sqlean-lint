"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

SQL-CAST-001 [MEDIUM]: CAST(...) applied to join keys."""
from __future__ import annotations

from typing import List

from sqlglot import exp

from .base import BaseRule
from ..types import LintContext, RuleViolation, Severity


class JoinTypeCastRule(BaseRule):
    """Type casts on join keys defeat hash-table pre-computation.

    Hash joins build their probe table from raw key values; wrapping either
    side in CAST means every probe row must be converted at runtime and
    storage-level zone maps / clustering on that column stop applying.
    """

    rule_id = "SQL-CAST-001"
    severity = Severity.MEDIUM
    title = "CAST on join key"
    description = (
        "Casting a join key prevents hash-table pre-computation and index seeks; "
        "align types physically instead of converting at query time."
    )

    def check(self, expression: exp.Expression, context: LintContext) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        for join in expression.find_all(exp.Join):
            on_clause = join.args.get("on")
            if on_clause is None:
                continue
            for equality in on_clause.find_all(exp.EQ):
                for side in (equality.this, equality.expression):
                    if isinstance(side, exp.Cast) and side.find(exp.Column) is not None:
                        violations.append(
                            self.make_violation(
                                equality,
                                context,
                                message=(
                                    "Join predicate casts a key with CAST(...): the optimizer cannot "
                                    "use pre-built hash tables or indexes on the raw column."
                                ),
                                suggested_fix=(
                                    "Cast the constant/literal side once, or persist both keys "
                                    "in the same physical type so no runtime cast is needed."
                                ),
                            )
                        )
                        break  # one finding per join predicate is enough
        return violations
