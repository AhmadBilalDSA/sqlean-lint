"""Copyright (c) 2026 SQLean Engineering. All Rights Reserved.

Extensible rule engine and AST backend for sqlean-lint Pro.

This module defines the rule contract (``BaseRule``), the violation payload
(``LintViolation``) and a core set of semantic SQL rules implemented on top of
the ``sqlglot`` AST:

  * SQL-WILD-001  — flag ``SELECT *``
  * SQL-CART-001  — flag implicit comma joins (cartesian products)
  * SQL-SARG-001  — flag non-SARGable ``YEAR(col) = ...`` wrappers and rewrite
                    them into an equivalent half-open range predicate.

``run()`` inspects the AST and returns violations; ``fix()`` rewrites the AST
in place and returns a (possibly empty) list of change descriptions.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

import sqlglot
from sqlglot import exp

# ── Violation payload ──────────────────────────────────────────────────

SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


@dataclass
class LintViolation:
    """A single lint finding anchored to a source location."""

    rule_id: str
    message: str
    severity: str = "MEDIUM"
    line: int = 1
    col: int = 1
    snippet: str = ""
    fixable: bool = False

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "severity": self.severity,
            "line": self.line,
            "col": self.col,
            "snippet": self.snippet,
            "fixable": self.fixable,
        }


# ── AST caching ────────────────────────────────────────────────────────

_AST_CACHE: dict = {}


def cached_parse(sql: str, dialect: str):
    """Parse ``sql`` and memoize the AST per (sql, dialect) pair.

    Reparsing a large script across many rules is the dominant cost in a lint
    pass; caching the tree here gives a significant speed-up when the same
    source is re-linted (e.g. settings changes that only alter severities).
    """
    key = (sql, dialect)
    node = _AST_CACHE.get(key)
    if node is None:
        node = sqlglot.parse_one(sql, read=dialect)
        _AST_CACHE[key] = node
    return node


def clear_ast_cache() -> None:
    """Evict every cached AST (call after large edits to bound memory)."""
    _AST_CACHE.clear()


def ast_cache_size() -> int:
    return len(_AST_CACHE)


# ── Base rule ──────────────────────────────────────────────────────────

class BaseRule(abc.ABC):
    """Contract that every lint rule implements."""

    rule_id: str = "SQL-000-000"
    severity: str = "MEDIUM"
    title: str = "Base rule"
    description: str = ""

    @abc.abstractmethod
    def run(self, ast, dialect: str) -> List[LintViolation]:
        """Inspect one statement AST and return all matching violations."""

    def fix(self, ast, dialect: str) -> List[str]:
        """Rewrite *ast* in place; return human-readable change descriptions.

        The default implementation declares the rule non-fixable.
        """
        return []

    # ── Helpers ────────────────────────────────────────────────────────

    def violation(
        self,
        message: str,
        *,
        severity: Optional[str] = None,
        line: int = 1,
        col: int = 1,
        snippet: str = "",
        fixable: Optional[bool] = None,
    ) -> LintViolation:
        return LintViolation(
            rule_id=self.rule_id,
            message=message,
            severity=severity or self.severity,
            line=line,
            col=col,
            snippet=snippet,
            fixable=self._default_fixable() if fixable is None else fixable,
        )

    def _default_fixable(self) -> bool:
        return self.__class__.fix is not BaseRule.fix


# ── Shared helpers ─────────────────────────────────────────────────────

def line_col_of(node) -> tuple[int, int]:
    """Best-effort (line, col) for a sqlglot node via its token metadata."""
    try:
        meta = getattr(node, "meta", {}) or {}
        line = meta.get("line")
        col = meta.get("col")
        if isinstance(line, int) and isinstance(col, int):
            return line, col
    except Exception:  # noqa: BLE001
        pass
    return 1, 1


def render(node, dialect: str) -> str:
    try:
        return node.sql(dialect=dialect)
    except Exception:  # noqa: BLE001
        return ""


# ── SQL-WILD-001 : SELECT * ────────────────────────────────────────────

def _is_star(projection) -> bool:
    if isinstance(projection, exp.Star):
        return True
    return isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)


class WildcardSelectRule(BaseRule):
    """Flag every ``SELECT *`` (including qualified ``t.*``)."""

    rule_id = "SQL-WILD-001"
    severity = "MEDIUM"
    title = "Wildcard SELECT"
    description = (
        "SELECT * materializes every source column, inflating read volume, "
        "memory and cloud egress while making downstream schema changes silent."
    )

    def run(self, ast, dialect: str) -> List[LintViolation]:
        violations: List[LintViolation] = []
        for select in ast.find_all(exp.Select):
            for projection in select.expressions:
                if _is_star(projection):
                    line, col = line_col_of(projection)
                    violations.append(
                        self.violation(
                            f"Found SELECT *; enumerate the columns actually needed "
                            "to avoid scanning unused data.",
                            line=line,
                            col=col,
                            snippet=render(projection, dialect),
                        )
                    )
        return violations


# ── SQL-CART-001 : implicit comma joins ────────────────────────────────

class CartesianProductRule(BaseRule):
    """Flag implicit comma joins (``FROM a, b``) that become cartesian products
    without an explicit join predicate."""

    rule_id = "SQL-CART-001"
    severity = "CRITICAL"
    title = "Implicit comma join / cartesian product"
    description = (
        "Comma-separated tables in FROM without a join predicate force the "
        "optimizer to emit a full O(N*M) cross product."
    )

    def run(self, ast, dialect: str) -> List[LintViolation]:
        violations: List[LintViolation] = []
        for join in ast.find_all(exp.Join):
            if isinstance(join.this, (exp.Lateral, exp.Unnest)):
                continue
            if join.args.get("method"):
                continue  # NATURAL JOIN defines its own semantics
            has_on = join.args.get("on") is not None
            has_using = join.args.get("using") is not None
            if not has_on and not has_using:
                line, col = line_col_of(join)
                violations.append(
                    self.violation(
                        "Implicit comma join found without a join predicate; this "
                        "produces a cartesian product (O(N*M)).",
                        line=line,
                        col=col,
                        snippet=render(join.this, dialect),
                    )
                )
        return violations


# ── SQL-SARG-001 : non-SARGable date wrappers ──────────────────────────

_DATE_TIME_FUNCS = (
    "YEAR", "QUARTER", "MONTH", "DAY", "HOUR",
    "MINUTE", "SECOND", "DATE", "TIMESTAMP", "EXTRACT",
)


def _func_name(node) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this or "").upper()
    return type(node).__name__.upper()


def _col_inside(func: exp.Func) -> Optional[exp.Expression]:
    return func.find(exp.Column)


def _date_literal(iso_date: str) -> exp.Expression:
    """Build a dialect-neutral, explicitly typed DATE literal.

    ``iso_date`` must be ISO-8601 (``YYYY-MM-DD``). The returned
    ``exp.cast(... AS DATE)`` node renders as ``CAST('...' AS DATE)`` under
    every supported dialect, avoiding session/format dependent coercion.
    """
    literal = exp.Literal.string(iso_date)
    return exp.cast(literal, to="DATE")


class SARGableDateRule(BaseRule):
    """Flag predicates that wrap an indexed date column in ``YEAR(col) = ...``
    style functions, and rewrite them into SARGable half-open ranges."""

    rule_id = "SQL-SARG-001"
    severity = "HIGH"
    title = "Non-SARGable date predicate"
    description = (
        "Wrapping a date column in YEAR()/EXTRACT() defeats index range scans, "
        "forcing a full scan and preventing partition pruning."
    )

    def run(self, ast, dialect: str) -> List[LintViolation]:
        violations: List[LintViolation] = []
        for predicate in ast.find_all(
            exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NEQ
        ):
            for side in (predicate.this, predicate.expression):
                func = self._year_func(side)
                if func is not None and _col_inside(func) is not None:
                    line, col = line_col_of(predicate)
                    violations.append(
                        self.violation(
                            f"Predicate applies '{_func_name(func)}()' to a date column, "
                            "making it non-SARGable: indexes and partition pruning cannot "
                            "be used and a full scan follows.",
                            line=line,
                            col=col,
                            snippet=render(predicate, dialect),
                            fixable=True,
                        )
                    )
                    break
        return violations

    def fix(self, ast, dialect: str) -> List[str]:
        """Rewrite ``YEAR(col) = <year>`` predicates into half-open ranges.

        Only year-level extractions are rewritable to a range (``MONTH(d) = 1``
        is periodic and cannot be expressed as a range). e.g.
        ``YEAR(d) = 2026`` becomes

            d >= CAST('2026-01-01' AS DATE) AND d < CAST('2027-01-01' AS DATE)

        The bounds use an explicitly typed DATE literal (``exp.cast``). sqlglot
        transpiles that node to ``CAST('...' AS DATE)`` in every supported
        dialect, so the emitted SQL is normalized across DuckDB, Postgres and
        Snowflake and never depends on session/format date settings.
        """
        changes: List[str] = []
        for predicate in ast.find_all(exp.EQ):
            func_side, other = self._year_eq_sides(predicate)
            if func_side is None:
                continue
            col = _col_inside(func_side)
            year_lit = other if isinstance(other, exp.Literal) else None
            if col is None or year_lit is None or not year_lit.is_number:
                continue
            try:
                year = int(year_lit.name)
            except (ValueError, TypeError):
                continue

            start = datetime(year, 1, 1)
            end = datetime(year + 1, 1, 1)
            col_sql = render(col, dialect)
            start_lit = _date_literal(start.strftime("%Y-%m-%d"))
            end_lit = _date_literal(end.strftime("%Y-%m-%d"))
            start_sql = start_lit.sql(dialect=dialect)
            end_sql = end_lit.sql(dialect=dialect)

            old_repr = render(predicate, dialect)
            replacement = exp.And(
                this=exp.GTE(this=col.copy(), expression=start_lit),
                expression=exp.LT(this=col.copy(), expression=end_lit),
            )
            predicate.replace(replacement)
            changes.append(
                f"Rewrite '{old_repr}' to "
                f"'{col_sql} >= {start_sql} AND {col_sql} < {end_sql}'"
            )
        return changes

    # ── internal helpers ───────────────────────────────────────────────

    def _year_func(self, side):
        """Return *side* if it directly wraps a column in a date-time function."""
        if not isinstance(side, (exp.Func, exp.Anonymous)):
            return None
        name = _func_name(side)
        if name not in _DATE_TIME_FUNCS:
            return None
        if isinstance(side, exp.Extract):
            return side
        return side if side.find(exp.Column) is not None else None

    @staticmethod
    def _is_year_func(side) -> bool:
        """True when *side* extracts the year (YEAR(x) or EXTRACT(YEAR FROM x))."""
        if isinstance(side, exp.Year):
            return True
        if isinstance(side, exp.Extract):
            part = side.this
            part_name = str(getattr(part, "name", "") or part or "").upper()
            return part_name in ("YEAR", "YY", "YYYY")
        return type(side).__name__.upper() == "YEAR"

    def _year_eq_sides(self, predicate):
        """Return (function_side, other_side) when the equality is
        ``YEAR(col) = <year literal>`` (either order)."""
        for a, b in (
            (predicate.this, predicate.expression),
            (predicate.expression, predicate.this),
        ):
            if self._is_year_func(a) and isinstance(b, exp.Literal) and b.is_number:
                return a, b
        return None, None


# ── Registry ───────────────────────────────────────────────────────────

PRO_RULE_CLASSES = (
    WildcardSelectRule,
    CartesianProductRule,
    SARGableDateRule,
)

DEFAULT_RULES: Sequence[BaseRule] = tuple(cls() for cls in PRO_RULE_CLASSES)


def run_all(
    ast, dialect: str, rules: Optional[Sequence[BaseRule]] = None
) -> List[LintViolation]:
    """Run every rule against *ast* and return all collected violations."""
    active = rules if rules is not None else DEFAULT_RULES
    results: List[LintViolation] = []
    for rule in active:
        results.extend(rule.run(ast, dialect))
    return results


def apply_fixes(
    ast, dialect: str, rules: Optional[Sequence[BaseRule]] = None
) -> List[str]:
    """Apply every rule's ``fix()`` and return the aggregate change log."""
    active = rules if rules is not None else DEFAULT_RULES
    changes: List[str] = []
    for rule in active:
        changes.extend(rule.fix(ast, dialect))
    return changes


# ── Safe engine entry points (CLI, GUI worker, fuzzing) ───────────────

def lint_sql(
    sql: str,
    dialect: str,
    rules: Optional[Sequence[BaseRule]] = None,
) -> List[LintViolation]:
    """Lint one SQL string without ever raising on garbage input.

    Unparseable SQL yields an empty finding list (parse failures are skipped,
    never fatal) which keeps headless checks and GUI scans resilient to
    arbitrary input such as fuzzed text.
    """
    if not sql or not sql.strip():
        return []
    try:
        ast = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001 - malformed SQL is gracefully ignored
        return []
    return run_all(ast, dialect, rules)


def fix_sql(
    sql: str,
    dialect: str,
    rules: Optional[Sequence[BaseRule]] = None,
) -> str:
    """Apply all safe auto-fixes to one SQL string (never raises).

    Returns the original text unchanged when parsing or rewriting fails.
    """
    if not sql or not sql.strip():
        return sql
    try:
        ast = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001
        return sql
    try:
        apply_fixes(ast, dialect, rules)
        return ast.sql(dialect=dialect)
    except Exception:  # noqa: BLE001
        return sql


def collect_sql_files(targets, excludes=()) -> List[str]:
    """Expand files/directories into a deduplicated, sorted SQL path list."""
    from pathlib import Path

    seen: set = set()
    collected: List[str] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            matches = sorted(path.rglob("*.sql"))
        elif path.is_file():
            matches = [path]
        else:
            continue
        for match in matches:
            key = str(match.resolve())
            if key in seen:
                continue
            if excludes and _excluded(match, excludes):
                continue
            seen.add(key)
            collected.append(str(match.resolve()))
    return collected


def lint_files(
    paths,
    dialect: str,
    rules: Optional[Sequence[BaseRule]] = None,
) -> dict:
    """Lint every path and return ``{path: [LintViolation, ...]}``.

    Files that cannot be read are skipped. This is the headless scan used by
    ``sqlean-lint check`` and by the GUI worker thread.
    """
    from pathlib import Path

    grouped: dict = {}
    for path in map(Path, paths):
        try:
            sql = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        violations = lint_sql(sql, dialect, rules)
        if violations:
            grouped[str(path)] = violations
    return grouped


def _excluded(match, excludes) -> bool:
    import fnmatch

    from pathlib import Path

    normalized = Path(match).as_posix()
    return any(
        fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(Path(match).name, pattern)
        for pattern in excludes
    )
