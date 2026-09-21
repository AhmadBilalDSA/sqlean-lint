"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

dbt-core integration for sqlean-lint.

Closest pure-``dbt-core``: scans the **compiled** SQL artifacts dbt writes into
``target/compiled/`` (after ``dbt compile``) and runs the sqlean-lint PRO AST
engine over every model. It:

* discovers the dbt project root by walking upward for ``dbt_project.yml``;
* lints every ``*.sql`` model under the compiled artifacts directory;
* surfaces Cartesian-join / wildcard / non-sargable findings with file:line:col.

This is a thin, importable adapter over :func:`sqlean_lint.engine.lint_source`
— it never re-implements the engine and leaves the core untouched.

Usage (Python):

    from sqlean_lint.integrations import run_dbt_lint
    results = run_dbt_lint(project_dir=".")

Usage (module, CLI guard):

    python -m sqlean_lint.integrations.dbt_core --project-dir . --format text
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..engine import lint_source
from ..rules import LintViolation

DBT_PROJECT_FILE = "dbt_project.yml"
_DEFAULT_COMPILED = "target/compiled"


class DbtIntegrationError(RuntimeError):
    """Raised when no dbt project / compiled artifacts can be located."""


def find_dbt_project(start: Optional[str | Path] = None) -> Path:
    """Walk upward from *start* (default: cwd) for a ``dbt_project.yml``.

    Returns the directory containing it, or raises :class:`DbtIntegrationError`.
    """
    current = Path(start or ".").resolve()
    for candidate in [current, *current.parents]:
        if (candidate / DBT_PROJECT_FILE).is_file():
            return candidate
    raise DbtIntegrationError(
        f"no {DBT_PROJECT_FILE} found from {current} (or any parent). "
        "Run this inside a dbt project, or pass --project-dir."
    )


def _compiled(models_root: Path) -> List[Path]:
    """All compiled ``.sql`` model files under a dbt target root."""
    if models_root.is_file():
        return [models_root]
    if not models_root.is_dir():
        raise DbtIntegrationError(
            f"compiled artifacts not found at '{models_root}'. Run `dbt compile` first."
        )
    return [p for p in sorted(models_root.rglob("*.sql")) if p.is_file()]


def lint_dbt_models(
    compiled_dir: str | Path = _DEFAULT_COMPILED,
    *,
    dialect: str = "duckdb",
    optimize: bool = True,
    with_cloud: bool = False,
) -> Dict[Path, List[LintViolation]]:
    """Lint compiled dbt models into ``{path: [violations]}`` (no I/O writes).

    ``lint_sql``/``lint_source`` never raise on arbitrary input, so garbage or
    half-compiled model SQL degrades to a parser finding instead of a crash.
    """
    models = _compiled(Path(compiled_dir))
    grouped: Dict[Path, List[LintViolation]] = {}
    for model in models:
        sql = model.read_text(encoding="utf-8", errors="replace")
        result = lint_source(
            sql,
            dialect=dialect,
            file_path=str(model),
            optimize=optimize,
            with_cloud=with_cloud,
        )
        grouped[model] = result.violations
    return grouped


def run_dbt_lint(
    project_dir: str | Path = ".",
    *,
    dialect: str = "duckdb",
    optimize: bool = True,
    with_cloud: bool = False,
) -> Tuple[List[LintViolation], Dict[str, Any]]:
    """Full dbt lint: locate project root, lint compiled models, return summary.

    Returns ``(violations, summary)`` where summary carries counts plus the
    number of flagged **Cartesian joins** (``SQL-CART-001``).
    """
    root = find_dbt_project(project_dir)
    compiled = root / _DEFAULT_COMPILED
    grouped = lint_dbt_models(
        compiled, dialect=dialect, optimize=optimize, with_cloud=with_cloud,
    )
    violations: List[LintViolation] = [
        v for vs in grouped.values() for v in vs
    ]
    cartesian = sum(1 for v in violations if v.rule_id == "SQL-CART-001")
    summary: Dict[str, Any] = {
        "project_dir": str(root),
        "models_linted": len(grouped),
        "total_violations": len(violations),
        "cartesian_joins": cartesian,
        "models_with_issues": sum(1 for vs in grouped.values() if vs),
    }
    return violations, summary


def _cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sqlean_lint.integrations.dbt_core",
        description="Lint compiled dbt models with sqlean-lint PRO.",
    )
    parser.add_argument("--project-dir", default=".", help="dbt project root (auto-detected).")
    parser.add_argument("--dialect", default="duckdb", help="SQL dialect.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    args = parser.parse_args(argv)

    try:
        violations, summary = run_dbt_lint(
            args.project_dir, dialect=args.dialect,
        )
    except DbtIntegrationError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if args.format == "json":
        import json

        print(json.dumps(
            {"summary": summary,
             "violations": [v.to_dict() for v in violations]},
            indent=2,
        ))
        return 0

    print(f"sqlean-lint PRO - dbt sweep")
    print(f"  project   : {summary['project_dir']}")
    print(f"  models    : {summary['models_linted']}")
    print(f"  findings  : {summary['total_violations']} (Cartesian joins: {summary['cartesian_joins']})")
    if violations:
        print("  ---")
        for v in violations:
            sev = getattr(v.severity, "value", None) or v.severity
            print(f"  {v.rule_id:<14} {str(sev):<8} {v.line}:{v.col}  {v.message}")
    return 0 if not violations else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
