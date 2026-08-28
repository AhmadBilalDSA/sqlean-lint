"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

End-to-end CLI runs: exit codes, formats, severity gates and --fix."""
import json
import pathlib

import pytest
from typer.testing import CliRunner

from sqlean_lint.cli import app


def make_runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)  # click < 8.2
    except TypeError:  # pragma: no cover - click >= 8.2 removed mix_stderr
        return CliRunner()


runner = make_runner()

CROSS_JOIN = "SELECT * FROM a CROSS JOIN b"
MEDIUM_ONLY = "WITH x AS (SELECT * FROM t) SELECT * FROM x"


def test_help_lists_flags():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for flag in ("--dialect", "--format", "--fix", "--output", "--min-severity", "--fail-on"):
        assert flag in result.stdout


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.2.0" in result.stdout


def test_json_clean_query_passes_gate():
    result = runner.invoke(app, ["--query", "SELECT 1", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["summary"]["total_violations"] == 0


def test_critical_finding_fails_gate():
    result = runner.invoke(
        app, ["--query", CROSS_JOIN, "--format", "json", "--fail-on", "critical"]
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["summary"]["has_critical"] is True


def test_min_severity_filter_changes_gate_outcome():
    base = ["--query", MEDIUM_ONLY, "--format", "json", "--fail-on", "medium"]
    without_filter = runner.invoke(app, base)
    assert without_filter.exit_code == 1  # MEDIUM visible at LOW floor

    with_filter = runner.invoke(app, base + ["--min-severity", "HIGH"])
    assert with_filter.exit_code == 0  # MEDIUM hidden behind HIGH floor
    payload = json.loads(with_filter.stdout)
    assert payload["summary"]["total_violations"] == 0


def test_fix_rewrites_file_in_place(scratch: pathlib.Path):
    sql_file = scratch / "query.sql"
    sql_file.write_text("SELECT * FROM t WHERE YEAR(d) = 2026\n", encoding="utf-8")

    first = runner.invoke(app, [str(sql_file), "--fix", "--format", "json"])
    assert first.exit_code == 0  # HIGH finding, but gate threshold is CRITICAL

    rewritten = sql_file.read_text(encoding="utf-8")
    assert ">=" in rewritten and "<" in rewritten
    assert "YEAR(" not in rewritten.upper()

    second = runner.invoke(app, [str(sql_file), "--format", "json"])
    assert second.exit_code == 0
    payload = json.loads(second.stdout)
    rule_ids = [v["rule_id"] for v in payload["results"][0]["violations"]]
    assert "SQL-SARG-001" not in rule_ids


def test_directory_discovery(scratch: pathlib.Path):
    (scratch / "good.sql").write_text("SELECT 1\n", encoding="utf-8")
    (scratch / "bad.sql").write_text(CROSS_JOIN + "\n", encoding="utf-8")

    result = runner.invoke(app, [str(scratch), "--format", "json", "--fail-on", "any"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["summary"]["files"] == 2


def test_glob_pattern_discovery(scratch: pathlib.Path):
    (scratch / "one.sql").write_text("SELECT * FROM a CROSS JOIN b\n", encoding="utf-8")
    (scratch / "two.txt").write_text("ignore me\n", encoding="utf-8")
    pattern = str(scratch / "*.sql")
    result = runner.invoke(app, [pattern, "--format", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["summary"]["files"] == 1


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--query", "SELECT 1", "--dialect", "oracle"],
        ["--query", "SELECT 1", "--format", "yaml"],
        ["--query", "SELECT 1", "--fail-on", "catastrophic"],
        ["missing-dir/"],
    ],
)
def test_usage_errors_exit_two(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 2


def test_html_report_written_to_output(scratch: pathlib.Path):
    out_file = scratch / "report.html"
    result = runner.invoke(
        app,
        ["--query", "SELECT 1", "--format", "html", "--output", str(out_file)],
    )
    assert result.exit_code == 0
    written = out_file.read_text(encoding="utf-8")
    assert "<svg" in written
    assert "http://" not in written


def test_markdown_format_to_stdout():
    result = runner.invoke(app, ["--query", "SELECT 1", "--format", "markdown"])
    assert result.exit_code == 0
    assert result.stdout.startswith("# SQLean-Lint Report")


def test_rich_format_prints_summary_header():
    result = runner.invoke(app, ["--query", CROSS_JOIN])
    assert result.exit_code == 1
    assert "SQLEAN-LINT SEMANTIC SQL QUALITY REPORT" in result.stdout.upper()
