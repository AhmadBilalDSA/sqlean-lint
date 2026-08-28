"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Auto-fix engine with safety tiers for provably-safe rewrites.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .optimizer import Transformation, optimize_expression
from .parser import parse_script


SAFE_RULE_IDS: frozenset[str] = frozenset(
    {"OPT-SARG-RANGE", "OPT-NOT-IN-EXISTS", "OPT-CTE-SORT-DROP"}
)


@dataclass(frozen=True)
class Patch:
    """A single auto-fix patch describing one atomic transformation."""

    rule_id: str
    description: str
    line: int
    col: int
    before: str
    after: str
    review_required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "description": self.description,
            "line": self.line,
            "col": self.col,
            "before": self.before,
            "after": self.after,
            "review_required": self.review_required,
        }


@dataclass
class FixResult:
    """Outcome of a fix pass: the rewritten SQL and the patches applied."""

    fixed_sql: str
    original_sql: str
    patches: List[Patch] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return len(self.patches) > 0

    @property
    def review_required(self) -> bool:
        return any(p.review_required for p in self.patches)

    def unified_diff(self) -> str:
        return unified_diff(self.original_sql, self.fixed_sql)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixed_sql": self.fixed_sql,
            "original_sql": self.original_sql,
            "changed": self.changed,
            "review_required": self.review_required,
            "patches": [p.to_dict() for p in self.patches],
        }


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def unified_diff(
    original: str,
    optimized: str,
    fromfile: str = "a.sql",
    tofile: str = "b.sql",
) -> str:
    """Return a unified-diff string between two SQL texts."""
    original_lines = original.splitlines(keepends=True)
    optimized_lines = optimized.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            original_lines,
            optimized_lines,
            fromfile=fromfile,
            tofile=tofile,
        )
    )


def fix_source(
    sql: str,
    dialect: str = "duckdb",
    *,
    apply_safe: bool = True,
) -> FixResult:
    """Parse *sql*, apply provably-safe optimisations, and return a FixResult.

    SAFE patches (review_required=False) are emitted for:
      - OPT-SARG-RANGE
      - OPT-NOT-IN-EXISTS
      - OPT-CTE-SORT-DROP

    When *apply_safe* is False the SQL is returned unchanged and no patches
    are generated.
    """
    if not apply_safe:
        return FixResult(fixed_sql=sql, original_sql=sql)

    statements = parse_script(sql, dialect)
    rendered_parts: List[str] = []
    patches: List[Patch] = []

    for statement in statements:
        optimized, transformations = optimize_expression(statement)
        rendered_parts.append(optimized.sql(dialect=dialect))

        for t in transformations:
            is_safe = t.rule_id in SAFE_RULE_IDS
            patches.append(
                Patch(
                    rule_id=t.rule_id,
                    description=t.description,
                    line=0,
                    col=0,
                    before=statement.sql(dialect=dialect),
                    after=optimized.sql(dialect=dialect),
                    review_required=not is_safe,
                )
            )

    fixed_sql = ";\n".join(rendered_parts)
    if sql.rstrip().endswith(";"):
        fixed_sql += ";"

    return FixResult(fixed_sql=fixed_sql, original_sql=sql, patches=patches)


def verify_equivalence(
    sql_a: str,
    sql_b: str,
    dialect: str = "duckdb",
) -> bool:
    """Execute both queries in an in-memory DuckDB and compare results.

    Returns True when both produce identical row sets, False otherwise
    (including on execution errors).
    """
    try:
        import duckdb
    except ImportError:
        return False

    try:
        conn = duckdb.connect(":memory:")
        rows_a = conn.execute(sql_a).fetchall()
        rows_b = conn.execute(sql_b).fetchall()
        return rows_a == rows_b
    except Exception:  # noqa: BLE001
        return False
