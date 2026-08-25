"""GitHub Action runner for the sqlean-lint quality gate.

Emits (to $GITHUB_OUTPUT):
    issues_found  - violation count after the severity filter
    risk_score    - maximum cost-model risk score (0-100)
    has_critical  - 'true' when at least one CRITICAL violation exists

Also writes a Markdown step summary and emits ::error/::warning annotations.
Exit codes mirror the CLI gate: 0 pass, 1 threshold breached, 2 input error.
"""
from __future__ import annotations

import os
import sys
from typing import List

from .engine import lint_paths
from .reporter import to_markdown
from .types import Severity, severity_rank

_FAIL_ON_RANK = {"any": 1, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _split_targets(raw: str) -> List[str]:
    for separator in ("\n", ","):
        raw = raw.replace(separator, " ")
    return [token for token in raw.split() if token]


def main() -> int:
    targets = _split_targets(os.environ.get("INPUT_PATHS", "**/*.sql"))
    dialect = os.environ.get("INPUT_DIALECT", "duckdb")
    fail_on = os.environ.get("INPUT_FAIL_ON", "critical").strip().lower()
    min_severity = os.environ.get("INPUT_MIN_SEVERITY", "LOW").strip().upper()

    try:
        results = lint_paths(targets, dialect=dialect, optimize=False)
    except FileNotFoundError as err:
        print(f"::error title=sqlean-lint::{err}")
        return 2

    visible = [
        result.__class__(
            file_path=result.file_path,
            raw_sql=result.raw_sql,
            violations=[
                v for v in result.violations
                if severity_rank(v.severity) >= severity_rank(min_severity)
            ],
            cost=result.cost,
            optimized_sql=result.optimized_sql,
            duration_ms=result.duration_ms,
        )
        for result in results
    ]

    issues_found = sum(len(result.violations) for result in visible)
    risk_score = max((result.cost.risk_score for result in results), default=0)
    has_critical = any(
        v.severity == Severity.CRITICAL for result in visible for v in result.violations
    )

    outputs = f"issues_found={issues_found}\nrisk_score={risk_score}\nhas_critical={'true' if has_critical else 'false'}\n"
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(outputs)
    else:
        sys.stdout.write(outputs)

    # Annotations for every HIGH/CRITICAL finding.
    for result in visible:
        for violation in result.violations:
            level = violation.severity.value
            if level in ("HIGH", "CRITICAL"):
                command = "error" if level == "CRITICAL" else "warning"
                print(
                    f"::{command} file={result.file_path},line={violation.line},"
                    f"title={violation.rule_id} ({level})::{violation.title}",
                    flush=True,
                )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    report = to_markdown(visible)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report)
    else:
        sys.stdout.write(report)

    threshold = _FAIL_ON_RANK.get(fail_on, 4)
    worst = max(
        (severity_rank(v.severity) for result in visible for v in result.violations),
        default=0,
    )
    print(f"sqlean-lint gate: issues={issues_found} risk={risk_score}/100 has_critical={str(has_critical).lower()}")
    return 1 if worst >= threshold else 0


if __name__ == "__main__":
    sys.exit(main())
