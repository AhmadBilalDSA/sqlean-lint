"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Multi-dialect parsing, safe-parse error shaping and position utilities."""
import pytest

from sqlean_lint.parser import (
    SUPPORTED_DIALECTS,
    node_position,
    normalize_dialect,
    parse_one,
    parse_script,
    parser_failure_violation,
    safe_parse,
)
from sqlean_lint.types import LintContext, Severity

DIALECT_SAMPLES = {
    "snowflake": "SELECT TO_DATE(col) FROM tbl QUALIFY ROW_NUMBER() OVER (ORDER BY d) = 1",
    "bigquery": "SELECT EXTRACT(YEAR FROM ts) FROM `proj.dataset.tbl`",
    "postgres": "SELECT DISTINCT ON (k) * FROM t ORDER BY k",
    "duckdb": "SELECT SUM(x) FILTER (WHERE y > 1) FROM t",
    "databricks": "SELECT CAST(a AS DOUBLE) FROM t",
    "mysql": "SELECT `col` FROM `tbl` LIMIT 1 OFFSET 2",
    "sqlite": "SELECT x FROM t WHERE y GLOB 'a*'",
    "tsql": "SELECT TOP 3 * FROM t",
}


@pytest.mark.parametrize("dialect", sorted(DIALECT_SAMPLES))
def test_every_supported_dialect_parses(dialect):
    tree = parse_one(DIALECT_SAMPLES[dialect], dialect)
    assert tree is not None
    assert tree.sql()


def test_supported_dialect_registry_is_exact():
    assert set(SUPPORTED_DIALECTS) == set(DIALECT_SAMPLES)


@pytest.mark.parametrize(
    ("alias", "expected"),
    [("postgresql", "postgres"), ("t-sql", "tsql"), ("mssql", "tsql"), ("spark", "databricks")],
)
def test_dialect_aliases(alias, expected):
    assert normalize_dialect(alias) == expected


def test_unsupported_dialect_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported dialect"):
        normalize_dialect("oracle")


def test_parse_script_handles_multiple_statements():
    statements = parse_script("SELECT 1; SELECT 2;", "duckdb")
    assert len(statements) == 2


def test_safe_parse_ok_shape():
    parsed = safe_parse("SELECT 1;\nSELECT 2;", "duckdb")
    assert parsed.ok is True
    assert parsed.error_message is None
    assert len(parsed.statements) == 2


def test_safe_parse_structured_failure_with_position():
    parsed = safe_parse("SELECT ] FROM t", "duckdb")
    assert parsed.ok is False
    assert parsed.error_message
    assert isinstance(parsed.error_line, int) and parsed.error_line >= 1
    assert isinstance(parsed.error_col, int) and parsed.error_col >= 1


def test_parser_failure_violation_fields():
    context = LintContext(dialect="duckdb", raw_sql="SELECT ] FROM t", file_path="<query>")
    violation = parser_failure_violation(safe_parse("SELECT ] FROM t", "duckdb"), context)
    assert violation.rule_id == "PARSER-001"
    assert violation.severity == Severity.HIGH
    assert violation.line >= 1 and violation.col >= 1


def test_node_position_resolves_multiline_source():
    sql = "SELECT *\nFROM t\nWHERE UPPER(name) = 'X'"
    tree = parse_one(sql, "duckdb")
    context = LintContext(dialect="duckdb", raw_sql=sql)
    from sqlglot import exp

    node = tree.find(exp.Upper)
    assert node is not None
    line, col = node_position(node, context)
    # Lines are exact; columns anchor to sqlglot's expression token (deterministic
    # per version) - here within the WHERE clause on the same line as UPPER(...).
    assert line == 3
    assert 7 <= col <= len("WHERE UPPER(name)")


def test_node_position_falls_back_to_text_search():
    # Table nodes carry no token meta; the snippet search must still locate them.
    sql = "SELECT *\nFROM some_table\nWHERE x = 1"
    tree = parse_one(sql, "duckdb")
    context = LintContext(dialect="duckdb", raw_sql=sql)
    from sqlglot import exp

    table = tree.find(exp.Table)
    line, col = node_position(table, context)
    assert line == 2
    assert col >= 1
