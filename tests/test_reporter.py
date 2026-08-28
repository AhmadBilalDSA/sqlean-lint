"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Offline artifact validation: JSON schema, air-gapped HTML, CP1252 terminal."""
import json

from sqlean_lint.engine import lint_query
from sqlean_lint.reporter import _md_cell, build_summary, to_html, to_json, to_markdown, to_terminal
from sqlean_lint.types import CostEstimate, LintResult, RuleViolation, Severity

MESSY_SQL = """
WITH raw AS (
    SELECT * FROM orders o JOIN customers c
    WHERE UPPER(o.status) = 'PAID'
    ORDER BY o.id
)
SELECT customer_id FROM raw
WHERE customer_id NOT IN (SELECT id FROM blocked)
"""


def dirty_result() -> LintResult:
    return lint_query(MESSY_SQL, "duckdb")


def clean_results():
    return [lint_query("SELECT 1", "duckdb")]


def test_json_report_schema():
    payload = json.loads(to_json([dirty_result()]))
    assert payload["tool"] == "sqlean-lint"
    assert payload["version"]
    summary = payload["summary"]
    for key in ("files", "total_violations", "by_severity", "max_risk_score", "has_critical"):
        assert key in summary
    assert set(summary["by_severity"]) == {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
    entry = payload["results"][0]
    for key in ("file_path", "raw_sql", "violations", "cost", "optimized_sql"):
        assert key in entry


def test_json_report_is_deterministic():
    assert to_json([dirty_result()]) == to_json([dirty_result()])


def test_html_is_fully_self_contained():
    html = to_html([dirty_result()])
    lowered = html.lower()
    assert "<style>" in lowered and "</style>" in lowered
    assert "<svg" in lowered
    assert "http://" not in html
    assert "https://" not in html
    assert "<link" not in lowered
    assert "<script src" not in lowered
    assert "integrity=" not in lowered
    assert "__PAYLOAD__" not in html  # template fully substituted


def test_html_escapes_payload_script_breakouts():
    result = lint_query("SELECT 'a</script>b' AS payload", "duckdb")
    html = to_html([result])
    # exactly one closing </script>: our own; the payload occurrence is escaped
    assert html.count("</script>") == 1
    assert "<\\/script>" in html


def test_markdown_structure_and_pipe_escaping():
    markdown = to_markdown([dirty_result()])
    assert markdown.startswith("# SQLean-Lint Report")
    assert "| Rule | Severity | Location | Message |" in markdown
    assert markdown.count("```sql") >= 2
    assert _md_cell("a|b") == "a\\|b"


def test_terminal_output_cp1252_safe():
    text = to_terminal([dirty_result()])
    encoded = text.encode("cp1252", errors="strict")  # must never raise
    assert encoded.decode("cp1252")
    assert "<query>" in text or "sqlean-lint" in text.lower()


def test_terminal_replaces_unencodable_glyphs():
    violation = RuleViolation(
        rule_id="SQL-TEST-001",
        severity=Severity.LOW,
        title="unicode probe",
        message="glyph \u2605 star should vanish on cp1252",
    )
    result = LintResult(
        file_path="<probe>",
        raw_sql="SELECT 1",
        violations=[violation],
        cost=CostEstimate(),
    )
    text = to_terminal([result])
    encoded = text.encode("cp1252", errors="ignore")
    assert "\u2605" not in encoded.decode("cp1252")


def test_all_reporters_handle_empty_results():
    results = []
    summary = build_summary(results)
    assert summary["files"] == 0 and summary["total_violations"] == 0
    assert json.loads(to_json(results))["summary"]["total_violations"] == 0
    assert "No violations detected." in to_markdown(clean_results())
    assert to_html([]).count("<svg") >= 1
    assert "files=0" in to_terminal([])
