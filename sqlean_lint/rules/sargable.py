"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

SQL-SARG-001 [HIGH]: non-SARGable predicates wrapping columns."""
from __future__ import annotations

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from .base import BaseRule
from ..types import LintContext, RuleViolation, Severity

# Functions that, when wrapped around a column inside a predicate, defeat
# index range scans / partition pruning.
NON_SARGABLE_FUNCTIONS: Set[str] = {
    "YEAR", "QUARTER", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND",
    "DATE", "TIME", "TIMESTAMP", "EXTRACT",
    "UPPER", "LOWER", "TRIM", "LTRIM", "RTRIM", "BTRIM", "INITCAP",
    "SUBSTR", "SUBSTRING", "LEFT", "RIGHT", "REPLACE", "TRANSLATE",
    "CONCAT_WS",
    "ABS", "ROUND", "FLOOR", "CEIL", "CEILING",
}

_DATE_TIME_FUNCTIONS: Set[str] = {
    "YEAR", "QUARTER", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND",
    "DATE", "TIME", "TIMESTAMP", "EXTRACT",
}

_TEXT_FUNCTIONS: Set[str] = {
    "UPPER", "LOWER", "TRIM", "LTRIM", "RTRIM", "BTRIM", "INITCAP",
    "SUBSTR", "SUBSTRING", "LEFT", "RIGHT", "REPLACE", "TRANSLATE",
}

COMPARISON_OPERATORS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def func_name(func_node: exp.Func) -> str:
    """Best-effort SQL name of a function node across sqlglot versions."""
    if isinstance(func_node, exp.Anonymous):
        return str(func_node.this or "").upper()
    return type(func_node).__name__.upper()


def _wraps_nonsargable_function(side: exp.Expression) -> Optional[str]:
    """Return the offending function name if one wraps a column."""
    for func in side.find_all(exp.Func):
        name = func_name(func)
        if name in NON_SARGABLE_FUNCTIONS and func.find(exp.Column) is not None:
            return name
    return None


def _wraps_column_arithmetic(side: exp.Expression) -> bool:
    """True for arithmetic over a column, e.g. 'col + 1 > 10'."""
    for arith in side.find_all(exp.Add, exp.Sub):
        if arith.find(exp.Column) is not None:
            return True
    return False


def _fix_for(name: Optional[str], arithmetic: bool) -> str:
    if arithmetic:
        return "Move arithmetic to the literal side of the comparison, e.g. 'col > 9' instead of 'col + 1 > 10'."
    if name and name.upper() in _DATE_TIME_FUNCTIONS:
        return (
            "Rewrite as a half-open range on the raw column so partition/index pruning survives, "
            "e.g. col >= DATE '2026-01-01' AND col < DATE '2027-01-01'."
        )
    if name and name.upper() in _TEXT_FUNCTIONS:
        return (
            "Persist a normalized/generated column (or normalize the literal instead of the column), "
            "e.g. store name_upper and compare against UPPER(<literal>)."
        )
    return "Rewrite the predicate so the indexed/partitioned column appears bare on one side."


class SargableRule(BaseRule):
    """Flag predicates where functions or arithmetic wrap the column."""

    rule_id = "SQL-SARG-001"
    severity = Severity.HIGH
    title = "Non-SARGable predicate"
    description = (
        "Wrapping an indexed/partitioned column in a function or arithmetic forces "
        "a full scan because the engine cannot seek the index or prune partitions."
    )

    def check(self, expression: exp.Expression, context: LintContext) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        reported: Set[int] = set()
        for predicate in expression.find_all(*COMPARISON_OPERATORS):
            if id(predicate) in reported:
                continue
            for side in (predicate.this, predicate.expression):
                if side is None or isinstance(side, (exp.Literal, exp.Column)):
                    continue
                func_name_hit = _wraps_nonsargable_function(side)
                arithmetic = func_name_hit is None and _wraps_column_arithmetic(side)
                if func_name_hit or arithmetic:
                    reported.add(id(predicate))
                    violations.append(
                        self.make_violation(
                            predicate,
                            context,
                            message=(
                                f"Predicate applies '{func_name_hit or 'arithmetic'}' directly to a "
                                "column, making it non-SARGable: indexes and partition pruning "
                                "cannot be used and a full scan follows."
                            ),
                            suggested_fix=_fix_for(func_name_hit, arithmetic),
                        )
                    )
                    break
        return violations
