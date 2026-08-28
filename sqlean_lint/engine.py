"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Lint orchestration shared by the CLI, MCP server and GitHub Action."""
from __future__ import annotations

import glob as globlib
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .cost_model import estimate_cost
from .optimizer import optimize_expression
from .parser import parser_failure_violation, safe_parse
from .rules import BaseRule, get_default_rules
from .types import LintContext, LintResult


def lint_source(
    sql: str,
    *,
    dialect: str = "duckdb",
    file_path: str = "<query>",
    optimize: bool = True,
    rules: Optional[Sequence[BaseRule]] = None,
    profile: bool = False,
    with_cloud: bool = False,
) -> LintResult:
    """Lint one SQL source string end-to-end (parse, rules, cost, optimize)."""
    started = time.perf_counter()
    if sql.startswith("\ufeff"):
        # Windows editors emit UTF-8 BOMs; parsers must not choke on them.
        sql = sql[1:]
    context = LintContext(dialect=dialect, raw_sql=sql, file_path=file_path)
    result = LintResult(file_path=file_path, raw_sql=sql)

    parsed = safe_parse(sql, dialect)
    if not parsed.ok:
        result.violations.append(parser_failure_violation(parsed, context))
        result.cost = estimate_cost(result.violations, [])
        result.duration_ms = (time.perf_counter() - started) * 1000
        return result

    active_rules = list(rules) if rules is not None else get_default_rules()
    for statement in parsed.statements:
        for rule in active_rules:
            result.violations.extend(rule.check(statement, context))

    result.cost = estimate_cost(result.violations, parsed.statements)

    if optimize:
        rendered: List[str] = []
        changed_any = False
        for statement in parsed.statements:
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
) -> LintResult:
    """Convenience wrapper for inline queries."""
    return lint_source(
        sql, dialect=dialect, file_path="<query>", optimize=optimize,
        profile=profile, with_cloud=with_cloud,
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
) -> List[LintResult]:
    """Lint every SQL file behind ``targets`` (files/dirs/globs), UTF-8 read."""
    results: List[LintResult] = []
    for path in resolve_targets(targets):
        sql = path.read_text(encoding="utf-8", errors="replace")
        results.append(
            lint_source(
                sql, dialect=dialect, file_path=str(path), optimize=optimize,
                profile=profile, with_cloud=with_cloud,
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
) -> List[LintResult]:
    """Lint a dbt project's compiled SQL artifacts under ``dbt_dir/compiled/``."""
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
                profile=profile, with_cloud=with_cloud,
            )
        )
    return results
