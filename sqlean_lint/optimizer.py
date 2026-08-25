"""Provably-safe AST transformation engine.

Every rewrite in this module preserves result semantics:

* ``YEAR(d) = 2026``            ->  ``d >= CAST('2026-01-01' AS DATE) AND d < CAST('2027-01-01' AS DATE)``
* ``DATE(d) = '2026-01-15'``    ->  ``d >= CAST('2026-01-15' AS DATE) AND d < CAST('2026-01-16' AS DATE)``
* ``x NOT IN (SELECT c ...)``   ->  ``NOT EXISTS (SELECT 1 ... AND c = x)`` (guarded)
* CTE ``ORDER BY`` w/o LIMIT    ->  removed (presentation-only sort)

Rewrites are skipped whenever any preconditions are not provably met;
the optimizer never guesses.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlglot import exp

from .parser import find_from, get_with_key, nested_context


@dataclass(frozen=True)
class Transformation:
    """Human-readable record of one applied rewrite."""

    rule_id: str
    description: str

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "description": self.description}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _is_number_literal(node: object) -> bool:
    return isinstance(node, exp.Literal) and node.is_number


def _year_of(node: exp.Expression) -> Optional[Tuple[exp.Column, int]]:
    """Return (column, year-literal) for YEAR(col)=N shaped comparisons."""
    for func, value in ((node.this, node.expression), (node.expression, node.this)):
        if isinstance(func, exp.Year) and isinstance(value, exp.Literal) and value.is_number:
            column = func.this
            if isinstance(column, exp.Column):
                try:
                    return column, int(float(str(value.name)))
                except ValueError:
                    return None
    return None


def _date_eq(node: exp.Expression) -> Optional[Tuple[exp.Column, str]]:
    """Return (column, iso-date-string) for DATE(col)='YYYY-MM-DD' comparisons."""
    for func, value in ((node.this, node.expression), (node.expression, node.this)):
        if (
            isinstance(func, exp.Date)
            and isinstance(value, exp.Literal)
            and value.is_string
        ):
            candidate = str(value.name)
            column = func.this
            if isinstance(column, exp.Column) and len(candidate) == 10 and candidate[4] == "-":
                try:
                    _dt.date.fromisoformat(candidate)
                except ValueError:
                    continue
                return column, candidate
    return None


def _cast_date(date_string: str) -> exp.Cast:
    return exp.cast(exp.Literal.string(date_string), "DATE")


def _range_predicate(column: exp.Column, start: str, end: str) -> exp.And:
    return exp.And(
        this=exp.GTE(this=column.copy(), expression=_cast_date(start)),
        expression=exp.LT(this=column.copy(), expression=_cast_date(end)),
    )


def _source_aliases(from_clause: exp.From) -> set:
    aliases = set()
    for relation in [from_clause.this, *from_clause.expressions]:
        name = getattr(relation, "alias_or_name", "")
        if name:
            aliases.add(name)
    return aliases


# --------------------------------------------------------------------------
# individual passes
# --------------------------------------------------------------------------

def _pass_datetime_ranges(tree: exp.Expression, applied: List[Transformation]) -> None:
    """SARGable half-open ranges replace YEAR()/DATE() equality predicates."""
    for predicate in list(tree.find_all(exp.EQ)):
        hit = _year_of(predicate)
        if hit is not None:
            column, year = hit
            start, end = f"{year}-01-01", f"{year + 1}-01-01"
            predicate.replace(_range_predicate(column, start, end))
            applied.append(
                Transformation(
                    "OPT-SARG-RANGE",
                    f"YEAR({column.sql()}) = {year} rewritten to half-open range "
                    f"[{start}, {end}) so partition/index pruning applies.",
                )
            )
            continue
        hit = _date_eq(predicate)
        if hit is not None:
            column, day = hit
            next_day = (_dt.date.fromisoformat(day) + _dt.timedelta(days=1)).isoformat()
            predicate.replace(_range_predicate(column, day, next_day))
            applied.append(
                Transformation(
                    "OPT-SARG-RANGE",
                    f"DATE({column.sql()}) = '{day}' rewritten to half-open range "
                    f"[{day}, {next_day}).",
                )
            )


def _pass_not_in_to_not_exists(tree: exp.Expression, applied: List[Transformation]) -> None:
    """NOT IN (<subquery>) becomes NOT EXISTS under conservative guards."""
    for not_node in list(tree.find_all(exp.Not)):
        in_node = not_node.this
        if not isinstance(in_node, exp.In):
            continue
        query = in_node.args.get("query")
        if query is None:
            continue
        inner = query.this if isinstance(query, exp.Subquery) else query
        if not isinstance(inner, exp.Select):
            continue
        projections = inner.expressions
        if len(projections) != 1 or not isinstance(projections[0], exp.Column):
            continue
        # Conservative guards: only simple single-source SELECT bodies.
        blocked_args = ("group", "having", "qualify", "limit", "offset", "windows",
                        "joins", "laterals", get_with_key(inner) or "_none_")
        if any(inner.args.get(key) for key in blocked_args):
            continue
        from_clause = find_from(inner)
        if from_clause is None:
            continue

        outer_expression = in_node.this
        inner_aliases = _source_aliases(from_clause)
        # The copied outer predicate must not accidentally bind to an inner alias.
        conflicting = any(
            column.table and str(column.table) in inner_aliases
            for column in outer_expression.find_all(exp.Column)
        )
        if conflicting:
            continue

        condition = inner.args.get("where")
        correlation = exp.EQ(
            this=projections[0].copy(), expression=outer_expression.copy()
        )
        new_condition = (
            exp.And(this=condition.this.copy(), expression=correlation)
            if condition is not None
            else correlation
        )
        exists_body = exp.Select(expressions=[exp.Literal.number(1)])
        from_key = "from_" if "from_" in exists_body.arg_types else "from"
        exists_body.set(from_key, from_clause.copy())
        exists_body.set("where", exp.Where(this=new_condition))
        not_node.replace(exp.Not(this=exp.Exists(this=exists_body)))
        applied.append(
            Transformation(
                "OPT-NOT-IN-EXISTS",
                "NOT IN (<subquery>) rewritten to NULL-safe NOT EXISTS (... AND key = outer).",
            )
        )


def _pass_strip_cte_order_by(tree: exp.Expression, applied: List[Transformation]) -> None:
    """Remove presentation-only ORDER BY from CTE bodies lacking LIMIT."""
    for select in tree.find_all(exp.Select):
        if select.args.get("order") is None or select.args.get("limit") is not None:
            continue
        if nested_context(select) != "cte":
            continue
        label = ""
        parent = select.parent
        if isinstance(parent, exp.CTE):
            label = parent.alias_or_name or ""
        select.set("order", None)
        applied.append(
            Transformation(
                "OPT-CTE-SORT-DROP",
                f"Redundant ORDER BY dropped from CTE '{label or '?'}' "
                "(no LIMIT; outer query re-sorts anyway).",
            )
        )


_PASSES = (_pass_datetime_ranges, _pass_not_in_to_not_exists, _pass_strip_cte_order_by)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def optimize_expression(ast: exp.Expression) -> Tuple[exp.Expression, List[Transformation]]:
    """Return an optimized *copy* of ``ast`` plus the applied transformations."""
    tree = ast.copy()
    applied: List[Transformation] = []
    for pass_fn in _PASSES:
        pass_fn(tree, applied)
    return tree, applied


def optimize_sql(sql: str, dialect: str) -> Tuple[str, List[Transformation]]:
    """Optimize every statement of a script and re-render SQL text."""
    from .parser import parse_script  # local import avoids cycles at module load

    statements = parse_script(sql, dialect)
    rendered: List[str] = []
    applied: List[Transformation] = []
    for statement in statements:
        optimized, changes = optimize_expression(statement)
        rendered.append(optimized.sql(dialect=dialect))
        applied.extend(changes)
    text = ";\n".join(rendered)
    if sql.rstrip().endswith(";"):
        text += ";"
    return text, applied
