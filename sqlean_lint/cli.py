"""Typer CLI: files, directories, glob patterns or inline queries."""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import List, Optional

import typer

from ._version import __version__
from .engine import lint_paths, lint_query, resolve_targets
from .parser import SUPPORTED_DIALECTS, normalize_dialect
from .reporter import to_html, to_json, to_markdown, to_terminal
from .types import LintResult, Severity, coerce_severity, severity_rank

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help=(
        "sqlean-lint - semantic SQL performance linter, AST auto-optimizer and "
        "cloud cost gate. 100% local: zero network calls, zero telemetry."
    ),
)

_FORMATS = ("rich", "json", "html", "markdown")
_FAIL_ON_RANK = {"any": 1, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _fail(message: str) -> "typer.Exit":
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    return typer.Exit(code=2)


@app.command()
def lint(
    targets: Optional[List[str]] = typer.Argument(
        None, help="SQL files, directories or recursive glob patterns."
    ),
    query: Optional[str] = typer.Option(
        None, "--query", "-q", help="Lint an inline SQL string instead of files."
    ),
    dialect: str = typer.Option(
        "duckdb",
        "--dialect",
        "-d",
        help=f"SQL dialect: {', '.join(SUPPORTED_DIALECTS)}.",
    ),
    fix: bool = typer.Option(
        False, "--fix", help="Apply provably safe AST rewrites in place to matched files."
    ),
    fmt: str = typer.Option(
        "rich", "--format", "-f", help="Output format: rich, json, html or markdown."
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write the report to this file instead of stdout."
    ),
    min_severity: str = typer.Option(
        "LOW", "--min-severity", help="Only consider violations at/above this severity."
    ),
    fail_on: str = typer.Option(
        "CRITICAL", "--fail-on", help="Quality-gate threshold: any, medium, high or critical."
    ),
    optimize: bool = typer.Option(
        True, "--optimize/--no-optimize", help="Compute auto-optimized SQL variants."
    ),
    version_flag: bool = typer.Option(False, "--version", help="Print version and exit."),
) -> None:
    """Run the linter and enforce the quality gate (exit 0 pass / 1 gate / 2 usage)."""
    if version_flag:
        typer.echo(f"sqlean-lint {__version__}")
        raise typer.Exit(code=0)

    try:
        dialect_name = normalize_dialect(dialect)
    except ValueError as err:
        raise _fail(str(err)) from None

    try:
        min_rank = severity_rank(coerce_severity(min_severity))
    except ValueError as err:
        raise _fail(str(err)) from None

    fail_key = fail_on.strip().lower()
    if fail_key not in _FAIL_ON_RANK:
        raise _fail(f"Invalid --fail-on {fail_on!r}; expected any|medium|high|critical.")
    threshold = _FAIL_ON_RANK[fail_key]

    normalized_format = fmt.strip().lower()
    if normalized_format not in _FORMATS:
        raise _fail(f"Invalid --format {fmt!r}; expected {'|'.join(_FORMATS)}.")

    # ---- gather inputs -------------------------------------------------
    if query is not None and query.strip():
        results: List[LintResult] = [lint_query(query, dialect_name, optimize=optimize)]
    elif targets:
        try:
            paths = resolve_targets(targets)
        except FileNotFoundError as err:
            raise _fail(str(err)) from None
        if not paths:
            raise _fail("No SQL files found for the given targets.")
        results = lint_paths([str(p) for p in paths], dialect_name, optimize=optimize)
    else:
        raise _fail("Provide SQL targets (files/directories/globs) or use --query.")

    # ---- optional in-place fixes --------------------------------------
    if fix:
        fixed = 0
        for result in results:
            if result.optimized_sql and result.file_path != "<query>":
                Path(result.file_path).write_text(result.optimized_sql, encoding="utf-8")
                fixed += 1
                typer.secho(f"fixed: {result.file_path}", fg=typer.colors.GREEN, err=True)
        typer.secho(f"{fixed} file(s) rewritten with provably safe rewrites.", err=True)

    # ---- severity-filtered view ---------------------------------------
    visible = [
        dataclasses.replace(
            result,
            violations=[v for v in result.violations if severity_rank(v.severity) >= min_rank],
        )
        for result in results
    ]

    # ---- render --------------------------------------------------------
    if normalized_format == "rich":
        rendered = to_terminal(visible)
    elif normalized_format == "json":
        rendered = to_json(visible)
    elif normalized_format == "html":
        rendered = to_html(visible)
    else:
        rendered = to_markdown(visible)

    if output is not None:
        output.write_text(rendered, encoding="utf-8")
        typer.secho(f"report written: {output}", fg=typer.colors.BLUE, err=True)
    else:
        typer.echo(rendered)

    # ---- quality gate ---------------------------------------------------
    worst_visible_rank = 0
    worst_label = "CLEAN"
    for result in visible:
        for violation in result.violations:
            rank = severity_rank(violation.severity)
            if rank > worst_visible_rank:
                worst_visible_rank = rank
                worst_label = violation.severity.value
    total_visible = sum(len(result.violations) for result in visible)

    if worst_visible_rank >= threshold:
        typer.secho(
            f"QUALITY GATE FAILED: worst visible severity {worst_label} "
            f"(--fail-on {fail_on.upper()}), {total_visible} finding(s). exit=1",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.secho(
        f"QUALITY GATE PASSED: {total_visible} visible finding(s), "
        f"threshold --fail-on {fail_on.upper()}. exit=0",
        fg=typer.colors.GREEN,
        err=True,
    )
    raise typer.Exit(code=0)


if __name__ == "__main__":  # pragma: no cover
    app()
