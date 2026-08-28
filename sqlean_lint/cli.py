"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

sqlean-lint CLI - linting plus the Phase 2 command surface.

The root ``app`` is a :class:`typer.Typer` whose generated group class is
:class:`SQLeanGroup`; its ``resolve_command`` silently routes unknown
invocations to ``lint``, preserving the historical bare-CLI ergonomics
(``sqlean-lint file.sql``, ``sqlean-lint --query ...``) while exposing real
subcommands: serve | convert | update | pricing | activate | status |
deactivate | ai."""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import List, Optional

import typer
from typer.core import TyperGroup

from ._version import __version__
from .engine import lint_dbt, lint_paths, lint_query, resolve_targets
from .parser import SUPPORTED_DIALECTS, normalize_dialect
from .reporter import to_html, to_json, to_markdown, to_terminal
from .types import LintResult, coerce_severity, severity_rank

_FORMATS = ("rich", "json", "html", "markdown")
_FAIL_ON_RANK = {"any": 1, "low": 1, "medium": 2, "high": 3, "critical": 4}

_LINT_EPILOG = (
    "Other commands: serve | convert | update | pricing | ai | activate | "
    "status | deactivate\nRun 'sqlean-lint <command> --help' for details."
)


class SQLeanGroup(TyperGroup):
    """TyperGroup that falls back to ``lint`` for unrecognized invocations.

    Injection must happen inside ``parse_args``: newer clicks validate
    group-level options before resolving a subcommand, which would reject
    legacy flag-style bare calls (``sqlean-lint --query ...``) otherwise.
    """

    def parse_args(self, ctx, args):
        if args and args[0] not in self.commands:
            args = ["lint", *args]
        return super().parse_args(ctx, args)

    def resolve_command(self, ctx, args):
        if args and args[0] not in self.commands:
            args = ["lint", *args]
        return super().resolve_command(ctx, args)


def _fail(message: str) -> "typer.Exit":
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    return typer.Exit(code=2)


def _echo_console_safe(text: str) -> None:
    """Echo text that may contain non-CP1252 glyphs (e.g. the Pro star)."""
    try:
        typer.echo(text)
    except UnicodeEncodeError:
        typer.echo(text.encode("cp1252", errors="replace").decode("cp1252"))


app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    cls=SQLeanGroup,
    help=(
        "sqlean-lint - semantic SQL performance linter, AST auto-optimizer and "
        "cloud cost gate. 100% local: zero network calls, zero telemetry.\n\n"
        "Subcommands: lint | serve | convert | update | pricing | ai | "
        "activate | status | deactivate"
    ),
)


# --------------------------------------------------------------------------
# lint (historical entry point)
# --------------------------------------------------------------------------

@app.command(name="lint", epilog=_LINT_EPILOG)
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
    profile: bool = typer.Option(
        False, "--profile/--no-profile", help="Attach local hardware telemetry (parse ms, peak RAM)."
    ),
    cloud: bool = typer.Option(
        False, "--cloud/--no-cloud", help="Estimate BigQuery / Snowflake scan costs locally."
    ),
    dbt: bool = typer.Option(
        False, "--dbt", help="Lint a dbt project's compiled artifacts (target/compiled)."
    ),
    dbt_dir: Path = typer.Option(
        Path("target"), "--dbt-dir", help="dbt target directory containing compiled/."
    ),
    diff: bool = typer.Option(
        False, "--diff", help="Print unified diffs between original and optimized SQL."
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
    if dbt:
        try:
            results: List[LintResult] = lint_dbt(
                str(dbt_dir), dialect=dialect_name, optimize=optimize,
                profile=profile, with_cloud=cloud,
            )
        except FileNotFoundError as err:
            raise _fail(str(err)) from None
        if not results:
            raise _fail(
                f"No compiled SQL under '{dbt_dir}'. Run `dbt compile` first, or pass --dbt-dir."
            )
    elif query is not None and query.strip():
        results = [lint_query(query, dialect_name, optimize=optimize,
                              profile=profile, with_cloud=cloud)]
    elif targets:
        try:
            paths = resolve_targets(targets)
        except FileNotFoundError as err:
            raise _fail(str(err)) from None
        if not paths:
            raise _fail("No SQL files found for the given targets.")
        results = lint_paths([str(p) for p in paths], dialect_name, optimize=optimize,
                             profile=profile, with_cloud=cloud)
    else:
        raise _fail("Provide SQL targets (files/directories/globs) or use --query.")

    # ---- optional in-place fixes (Pro-gated when enforcement is on) ----
    if fix:
        from .features import FEATURE_AUTOFIX, ProFeatureError, ensure_pro

        try:
            ensure_pro(FEATURE_AUTOFIX)
        except ProFeatureError as err:
            raise _fail(str(err)) from None
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
        from .knowledge import RULE_EDUCATION

        rendered = to_html(visible, extras={"learn": RULE_EDUCATION, "with_dag": True})
    else:
        rendered = to_markdown(visible)

    if diff:
        try:
            from .autofix import unified_diff

            chunks = []
            for result in visible:
                if result.optimized_sql and result.raw_sql.strip():
                    chunks.append(unified_diff(result.raw_sql, result.optimized_sql))
            if chunks:
                rendered = rendered + "\n\n" + "\n".join(chunks)
        except Exception as err:  # noqa: BLE001 - diff is additive, never fatal
            typer.secho(f"warning: could not render diff: {err}", fg=typer.colors.YELLOW, err=True)

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


# --------------------------------------------------------------------------
# serve - Web Studio
# --------------------------------------------------------------------------

@app.command(name="serve")
def serve(
    port: int = typer.Option(8080, "--port", "-p", help="TCP port to bind on localhost."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (keep it loopback)."),
    open_browser: bool = typer.Option(True, "--open-browser/--no-browser", help="Launch the UI."),
) -> None:
    """Start the air-gapped Web Studio (localhost only, zero telemetry)."""
    from .server import serve as run_server

    run_server(host=host, port=port, open_browser=open_browser)


# --------------------------------------------------------------------------
# convert - transpiler & data interop
# --------------------------------------------------------------------------

@app.command(name="convert")
def convert(
    query: Optional[str] = typer.Option(None, "--query", "-q", help="SQL text to convert."),
    input_path: Optional[Path] = typer.Option(None, "--input", "-i", help=".sql/.json/.py file."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write result to file."),
    source_dialect: str = typer.Option("duckdb", "--from", help="Source SQL dialect."),
    target_dialect: str = typer.Option("bigquery", "--to", help="Target SQL dialect."),
    table: str = typer.Option("data", "--table", help="Table name for JSON -> DDL mode."),
    export_format: Optional[str] = typer.Option(
        None, "--export", help="Materialize the query to csv, json or parquet (local DuckDB)."
    ),
    framework: str = typer.Option(
        "auto", "--framework", help="DataFrame framework for .py input: auto|pandas|pyspark|polars."
    ),
    ddl_dialect: str = typer.Option("duckdb", "--ddl-dialect", help="Dialect for generated DDL."),
) -> None:
    """Convert SQL across 9 dialects, JSON->DDL, DataFrame pipelines->SQL, or export data."""
    from .transpiler import (
        ExportResult,
        TranspileError,
        df_to_sql,
        json_to_sql,
        preview_query,
        sql_to_format,
        transpile_sql,
    )

    def emit(text: str) -> None:
        if output is not None:
            output.write_text(text, encoding="utf-8")
            typer.secho(f"written: {output}", fg=typer.colors.BLUE, err=True)
        else:
            typer.echo(text)

    try:
        if export_format is not None:
            sql_text = query or (input_path.read_text(encoding="utf-8") if input_path else "")
            result: ExportResult = sql_to_format(
                sql_text,
                export_format,
                str(output) if output is not None else None,
            )
            info = result.to_dict()
            typer.echo(
                f"[Export] format={info['format']} rows={info['rows_written']} "
                f"path={info['path'] or '(preview only)'}"
            )
            preview = preview_query(sql_text)
            if preview["ok"]:
                for row in preview["rows"][:5]:
                    typer.echo("  " + " | ".join(str(cell) for cell in row))
            return

        if input_path is not None and input_path.suffix.lower() == ".json":
            import json as jsonlib

            payload = jsonlib.loads(input_path.read_text(encoding="utf-8"))
            emit(json_to_sql(payload, table, ddl_dialect))
            return

        if input_path is not None and input_path.suffix.lower() == ".py":
            emit(df_to_sql(input_path.read_text(encoding="utf-8"), framework, target_dialect))
            return

        sql_text = query or (input_path.read_text(encoding="utf-8") if input_path else "")
        if not sql_text.strip():
            raise TranspileError("Provide --query or --input.")
        emit(transpile_sql(sql_text, source_dialect, target_dialect))
    except TranspileError as err:
        raise _fail(str(err)) from None


# --------------------------------------------------------------------------
# update - self-updater
# --------------------------------------------------------------------------

@app.command(name="update")
def update(
    yes: bool = typer.Option(False, "--yes", "-y", help="Install without asking."),
    check: bool = typer.Option(False, "--check", help="Check only; never install."),
) -> None:
    """Check GitHub Releases for a newer sqlean-lint binary."""
    from .updater import UpdaterError, self_update

    try:
        report = self_update(yes=yes and not check)
        typer.echo(report)
    except UpdaterError as err:
        typer.secho(f"update failed: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None


# --------------------------------------------------------------------------
# pricing - transparent, offline price sheet
# --------------------------------------------------------------------------

@app.command(name="pricing")
def pricing() -> None:
    """Show warehouse pricing constants used by the cost gate."""
    from .cost_model import (
        BIGQUERY_MIN_SCAN_BYTES,
        BIGQUERY_ONDEMAND_USD_PER_TB,
        SNOWFLAKE_LADDER,
        SNOWFLAKE_USD_PER_CREDIT,
    )

    typer.echo("sqlean-lint cloud cost model (constants baked in - fully offline)")
    typer.echo("")
    typer.echo("BigQuery (on-demand)")
    typer.echo(
        f"  ${BIGQUERY_ONDEMAND_USD_PER_TB:.2f} per TB scanned, "
        f"{BIGQUERY_MIN_SCAN_BYTES // 1048576} MiB minimum billed per table"
    )
    typer.echo("")
    typer.echo("Snowflake (credits/hour by warehouse size)")
    for _, size, credits in SNOWFLAKE_LADDER:
        typer.echo(f"  {size:<5} {credits:>4} credit(s)/hr")
    typer.echo(f"  ${SNOWFLAKE_USD_PER_CREDIT:.2f} per credit (configurable)")
    typer.echo("")
    typer.echo("sqlean-lint Pro - $29/year per seat:")
    typer.echo("  AST auto-fix engine, live metastore sync, cloud AI optimizer")
    typer.echo("  Purchase: https://polar.sh/AhmadBilalDSA/sqlean-lint")
    typer.echo("  Core linting stays free forever.")


# --------------------------------------------------------------------------
# licensing
# --------------------------------------------------------------------------

@app.command(name="activate")
def activate(
    key: str = typer.Option(..., "--key", "-k", help="License key from your purchase email."),
) -> None:
    """Activate sqlean-lint Pro with your license key (one HTTP call)."""
    from .license import LicenseError, activate

    try:
        info = activate(key.strip())
    except LicenseError as err:
        typer.secho(f"activation failed: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    typer.secho(
        f"Pro activated ({info.key_hint}). Tier: {info.tier}. Thank you!",
        fg=typer.colors.GREEN,
    )
    typer.echo("Offline grace applies automatically when validation cannot reach the server.")


@app.command(name="status")
def status() -> None:
    """Show current license state stored on this machine."""
    from .features import is_enforced, is_pro, upgrade_prompt
    from .license import load_cached

    state = load_cached()
    if state is None:
        typer.echo("No license cached on this machine.")
    else:
        typer.echo(f"key      : {state.key_hint}")
        typer.echo(f"tier     : {state.tier}")
        typer.echo(f"email    : {state.customer_email or '-'}")
        typer.echo(f"expires  : {state.expires or '-'}")
        typer.echo(f"valid    : {state.valid}")
    typer.echo(f"pro      : {is_pro()}")
    typer.echo(f"enforced : {is_enforced()} (set SQLEAN_LICENSE_ENFORCE=1 for strict gating)")
    if not is_pro():
        _echo_console_safe(upgrade_prompt("sqlean-lint Pro"))


@app.command(name="deactivate")
def deactivate() -> None:
    """Remove the cached license from this machine."""
    from .license import deactivate

    if deactivate():
        typer.echo("License removed from this machine.")
    else:
        typer.echo("No cached license found; nothing to do.")


# --------------------------------------------------------------------------
# ai - hybrid optimizer (local Ollama default)
# --------------------------------------------------------------------------

@app.command(name="ai")
def ai(
    query: Optional[str] = typer.Option(None, "--query", "-q", help="SQL to optimize."),
    input_path: Optional[Path] = typer.Option(None, "--input", "-i", help="Read SQL from file."),
    provider: str = typer.Option("ollama", "--provider", help="ollama|openai|anthropic|deepseek."),
    model: Optional[str] = typer.Option(None, "--model", help="Model name override."),
    endpoint: Optional[str] = typer.Option(None, "--endpoint", help="Custom API endpoint."),
    dialect: str = typer.Option("duckdb", "--dialect", "-d", help="SQL dialect."),
) -> None:
    """Request an AI-assisted rewrite; suggestions are validated locally before shown."""
    from .ai import optimize_with_ai

    sql_text = query or (input_path.read_text(encoding="utf-8") if input_path else "")
    if not sql_text.strip():
        raise _fail("Provide --query or --input.")
    suggestion = optimize_with_ai(
        sql_text, dialect=dialect, provider=provider, model=model, endpoint=endpoint
    )
    payload = suggestion.to_dict()
    typer.echo(f"provider={payload['provider']} model={payload['model']}")
    if payload["error"]:
        typer.secho(f"error: {payload['error']}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.echo(f"violations: {payload['baseline_violations']} -> {payload['remaining_violations']}"
               f" (validated={payload['validated']})")
    typer.echo("")
    typer.echo(payload["suggestion"])


if __name__ == "__main__":  # pragma: no cover
    app()
