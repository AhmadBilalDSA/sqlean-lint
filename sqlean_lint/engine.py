"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Lint orchestration shared by the CLI, MCP server and GitHub Action."""
from __future__ import annotations

import glob as globlib
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import sqlglot

from .cost_model import estimate_cost
from .dialect_detector import detect_dialect
from .optimizer import optimize_expression
from .parser import parser_failure_violation, safe_parse
from .rules import BaseRule, get_default_rules
from .types import LintContext, LintResult


def _split_semantically(sql: str) -> List[str]:
    """Coarse top-level statement splitter used as a parse fallback.

    Splits on semicolons that are not inside string literals or comments. This
    mirrors :func:`split_statement_offsets` but returns statement *text* blocks
    so a failing whole-buffer parse can still be linted statement-by-statement.
    """
    blocks: List[str] = []
    for start, end in split_statement_offsets(sql):
        block = sql[start:end].strip()
        if block:
            blocks.append(block)
    return blocks or ([sql] if sql.strip() else [])


def _lint_ast_node(
    statement,
    context: LintContext,
    active_rules: Sequence[BaseRule],
) -> List:
    """Run every active rule against one statement AST.

    This is the single per-statement entry point used by the batch parse loop
    in :func:`lint_source`. Keeping it isolated guarantees that a rule failure
    on one statement never masks findings from the remaining (valid)
    statements in a multi-statement script.
    """
    if statement is None:
        return []
    violations: List = []
    for rule in active_rules:
        try:
            violations.extend(rule.check(statement, context))
        except Exception:  # noqa: BLE001 - one failing rule never kills a pass
            continue
    return violations


def lint_source(
    sql: str,
    *,
    dialect: str = "duckdb",
    file_path: str = "<query>",
    optimize: bool = True,
    rules: Optional[Sequence[BaseRule]] = None,
    profile: bool = False,
    with_cloud: bool = False,
    detect: bool = False,
) -> LintResult:
    """Lint one SQL source string end-to-end (parse, rules, cost, optimize).

    Multi-statement scripts are parsed into a list of AST expressions (via
    ``sqlglot.parse``) and every statement is linted independently. A syntax
    error inside one statement never masks findings in the remaining valid
    statements: that statement yields a ``PARSER-001`` violation while the
    others still get their rules applied.

    Parameters
    ----------
    detect:
        When True, automatically detect the SQL dialect from the content
        instead of using the provided ``dialect`` parameter.
    """
    started = time.perf_counter()
    if sql.startswith("\ufeff"):
        # Windows editors emit UTF-8 BOMs; parsers must not choke on them.
        sql = sql[1:]
    if detect:
        dialect = detect_dialect(sql)
    context = LintContext(dialect=dialect, raw_sql=sql, file_path=file_path)
    result = LintResult(file_path=file_path, raw_sql=sql)

    active_rules = list(rules) if rules is not None else get_default_rules()

    # ── Multi-statement parse: split the buffer into a list of AST roots ──
    # Parse the whole buffer first so that clean scripts are handled in a
    # single pass (fast path). If the buffer contains any statement that fails
    # to parse, fall back to a per-statement split so one bad statement cannot
    # swallow the findings of the valid ones (the "0 finding(s) on a
    # multi-statement script" bug).
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except Exception:  # noqa: BLE001 - any parse failure is handled below
        statements = []
    statements = [s for s in statements if s is not None]

    if not statements:
        # The whole buffer could not be parsed; fall back to coarse splitting
        # so each statement still gets an independent parse + failure finding.
        from .parser import safe_parse

        for segment in _split_semantically(sql):
            parsed = safe_parse(segment, dialect)
            if parsed.ok:
                for statement in parsed.statements:
                    result.violations.extend(
                        _lint_ast_node(statement, context, active_rules)
                    )
            else:
                result.violations.append(parser_failure_violation(parsed, context))
        result.cost = estimate_cost(result.violations, statements)
        result.duration_ms = (time.perf_counter() - started) * 1000
        return result

    for statement in statements:
        result.violations.extend(_lint_ast_node(statement, context, active_rules))

    result.cost = estimate_cost(result.violations, statements)

    if optimize:
        rendered: List[str] = []
        changed_any = False
        for statement in statements:
            optimized_tree, changes = optimize_expression(statement)
            if changes:
                changed_any = True
            rendered.append(optimized_tree.sql(dialect=dialect))
        if changed_any:
            text = ";\n".join(rendered)
            if sql.rstrip().endswith(";"):
                text += ";"
            result.optimized_sql = text

    result.duration_ms = (time.perf_counter() - started) * 1000
    return result


def lint_query(
    sql: str,
    dialect: str = "duckdb",
    optimize: bool = True,
    profile: bool = False,
    with_cloud: bool = False,
    detect: bool = False,
) -> LintResult:
    """Convenience wrapper for inline queries."""
    return lint_source(
        sql, dialect=dialect, file_path="<query>", optimize=optimize,
        profile=profile, with_cloud=with_cloud, detect=detect,
    )


def resolve_targets(targets: Iterable[str]) -> List[Path]:
    """Expand files, directories and recursive glob patterns to SQL files."""
    resolved: List[Path] = []
    seen: set = set()
    for target in targets:
        candidate = Path(target)
        if candidate.is_dir():
            matches = sorted(candidate.rglob("*.sql"))
        elif candidate.is_file():
            matches = [candidate]
        else:
            matches = [
                Path(found)
                for found in sorted(globlib.glob(target, recursive=True))
                if Path(found).is_file()
            ]
            if not matches:
                raise FileNotFoundError(f"No SQL files matched target: {target}")
        for match in matches:
            key = str(match.resolve())
            if key not in seen:
                seen.add(key)
                resolved.append(match)
    return resolved


def lint_paths(
    targets: Iterable[str],
    dialect: str = "duckdb",
    optimize: bool = True,
    profile: bool = False,
    with_cloud: bool = False,
    detect: bool = False,
) -> List[LintResult]:
    """Lint every SQL file behind ``targets`` (files/dirs/globs), UTF-8 read.

    Parameters
    ----------
    detect:
        When True, automatically detect the SQL dialect per-file from the
        content instead of using the provided ``dialect`` parameter.
    """
    results: List[LintResult] = []
    for path in resolve_targets(targets):
        sql = path.read_text(encoding="utf-8", errors="replace")
        results.append(
            lint_source(
                sql, dialect=dialect, file_path=str(path), optimize=optimize,
                profile=profile, with_cloud=with_cloud, detect=detect,
            )
        )
    return results


def lint_dbt(
    dbt_dir: str = "target",
    *,
    dialect: str = "duckdb",
    optimize: bool = True,
    profile: bool = False,
    with_cloud: bool = False,
    detect: bool = False,
) -> List[LintResult]:
    """Lint a dbt project's compiled SQL artifacts under ``dbt_dir/compiled/``.

    Parameters
    ----------
    detect:
        When True, automatically detect the SQL dialect per-file from the
        content instead of using the provided ``dialect`` parameter.
    """
    compiled_dir = Path(dbt_dir) / "compiled"
    if not compiled_dir.is_dir():
        raise FileNotFoundError(
            f"No compiled SQL directory found at '{compiled_dir}'. "
            "Run `dbt compile` first, or pass --dbt-dir."
        )
    sql_files = sorted(compiled_dir.rglob("*.sql"))
    if not sql_files:
        return []
    results: List[LintResult] = []
    for path in sql_files:
        sql = path.read_text(encoding="utf-8", errors="replace")
        results.append(
            lint_source(
                sql, dialect=dialect, file_path=str(path), optimize=optimize,
                profile=profile, with_cloud=with_cloud, detect=detect,
            )
        )
    return results


# ?? Enterprise IDE: statement-under-cursor helpers ?????????????????????
def split_statement_offsets(sql: str):
    """Return ``(start, end)`` char offsets of each top-level statement.

    Splits on semicolons that are *not* inside string literals, line comment
    (``--``) or block comment (``/* */``) bodies, so subqueries and text that
    merely contains ``;`` never produce a false statement boundary.
    """
    out = []
    start = None
    i = 0
    n = len(sql)
    in_str = False
    in_line = False
    in_block = False
    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if in_line:
            if c == "\n":
                in_line = False
            i += 1
            continue
        if in_block:
            if c == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            i += 1
            continue
        if in_str:
            if c == "'":
                if nxt == "'":
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if c == "-" and nxt == "-":
            in_line = True
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        if c == "'":
            in_str = True
            i += 1
            continue
        if c == ";":
            if start is not None:
                out.append((start, i))
            start = None
            i += 1
            continue
        if start is None and not c.isspace():
            start = i
        i += 1
    if start is not None:
        out.append((start, n))
    return out


def statement_under_cursor(sql: str, offset: int):
    """Return the top-level statement text containing ``offset``.

    ``offset`` is a character index into *sql*. Falls back to the whole buffer
    (``sql``) when the pointer sits in blank space between statements.
    """
    if not sql:
        return ""
    for s, e in split_statement_offsets(sql):
        if s <= offset <= e:
            return sql[s:e]
    return sql
