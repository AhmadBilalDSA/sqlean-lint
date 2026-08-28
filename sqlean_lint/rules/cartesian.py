"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

SQL-CART-001 [CRITICAL]: cartesian product / predicate-less join detection."""
from __future__ import annotations

from typing import List

from sqlglot import exp

from .base import BaseRule
from ..types import LintContext, RuleViolation, Severity


class CartesianJoinRule(BaseRule):
    """Detect O(N x M) explosions before they reach the warehouse.

    Catches:
      * explicit ``CROSS JOIN``;
      * ``JOIN``/comma joins without an ``ON``/``USING`` predicate
        (sqlglot lowers ``FROM a, b`` into a predicate-less join);
    """

    rule_id = "SQL-CART-001"
    severity = Severity.CRITICAL
    title = "Cartesian product / missing join predicate"
    description = (
        "Joins without predicates (or explicit CROSS JOINs) force the engine "
        "to materialize every pairwise row combination."
    )

    def check(self, expression: exp.Expression, context: LintContext) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        for join in expression.find_all(exp.Join):
            target = join.this
            if isinstance(target, (exp.Lateral, exp.Unnest)):
                continue  # lateral flatten / unnest are legitimately predicate-less
            if join.args.get("method"):
                continue  # NATURAL JOIN defines implicit semantics
            has_on = join.args.get("on") is not None
            has_using = join.args.get("using") is not None
            kind = (join.kind or "").upper()

            if kind == "CROSS":
                violations.append(
                    self.make_violation(
                        join,
                        context,
                        message=(
                            "Explicit CROSS JOIN produces an O(N*M) pairwise explosion; "
                            "every row of the left side is paired with every row of the right side."
                        ),
                        suggested_fix=(
                            "Replace with an INNER JOIN ... ON <real join key>, or add the "
                            "missing correlation predicate if a cartesian product was unintended."
                        ),
                    )
                )
            elif not has_on and not has_using:
                violations.append(
                    self.make_violation(
                        join,
                        context,
                        message=(
                            "Join without ON/USING predicate detected (includes comma-style "
                            "'FROM a, b' joins): the optimizer must emit a full O(N*M) nested-loop "
                            "cartesian product."
                        ),
                        suggested_fix=(
                            "Add an explicit equality predicate, e.g. 'FROM a JOIN b ON a.id = b.a_id'."
                        ),
                    )
                )
        return violations
