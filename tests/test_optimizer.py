"""Semantic-preservation tests for the AST rewrite engine."""
import re

from sqlean_lint.optimizer import optimize_sql
from sqlean_lint.parser import parse_one


def norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().upper()


def test_year_equality_becomes_half_open_range():
    out, changes = optimize_sql("SELECT * FROM t WHERE YEAR(d) = 2026", "duckdb")
    assert "2026-01-01" in out and "2027-01-01" in out
    assert ">=" in out and "<" in out
    assert "YEAR(" not in norm(out)
    assert [c.rule_id for c in changes] == ["OPT-SARG-RANGE"]
    parse_one(out, "duckdb")  # output must remain valid SQL


def test_reversed_year_operands_handled():
    out, changes = optimize_sql("SELECT * FROM t WHERE 2026 = YEAR(d)", "duckdb")
    assert "2026-01-01" in out and "2027-01-01" in out
    assert changes and changes[0].rule_id == "OPT-SARG-RANGE"


def test_date_equality_becomes_one_day_range():
    out, _ = optimize_sql("SELECT * FROM t WHERE DATE(d) = '2026-01-15'", "duckdb")
    assert "'2026-01-15'" in out and "'2026-01-16'" in out
    parse_one(out, "duckdb")


def test_not_in_subquery_becomes_not_exists():
    sql = (
        "SELECT * FROM orders WHERE customer_id NOT IN "
        "(SELECT id FROM blocked WHERE active = 1)"
    )
    out, changes = optimize_sql(sql, "duckdb")
    assert "NOT EXISTS" in norm(out)
    # inner WHERE preserved plus correlation predicate
    flat = norm(out)
    assert "ACTIVE = 1" in flat
    assert "ID = CUSTOMER_ID" in flat
    assert any(c.rule_id == "OPT-NOT-IN-EXISTS" for c in changes)
    parse_one(out, "duckdb")


def test_unsafe_not_in_left_untouched():
    sql = (
        "SELECT * FROM o WHERE k NOT IN "
        "(SELECT g FROM t GROUP BY g)"
    )
    out, changes = optimize_sql(sql, "duckdb")
    # Guarded: no provably-safe rewrite is available, so NOT EXISTS must not
    # appear anywhere and no transformation may be recorded.
    assert "NOT EXISTS" not in norm(out)
    assert "GROUP BY" in norm(out)
    assert not any(c.rule_id == "OPT-NOT-IN-EXISTS" for c in changes)


def test_cte_order_by_stripped():
    sql = "WITH x AS (SELECT * FROM t ORDER BY id) SELECT * FROM x"
    out, changes = optimize_sql(sql, "duckdb")
    assert "ORDER BY" not in norm(out)
    assert any(c.rule_id == "OPT-CTE-SORT-DROP" for c in changes)


def test_cte_order_by_with_limit_preserved():
    sql = "WITH x AS (SELECT * FROM t ORDER BY id LIMIT 3) SELECT * FROM x"
    out, changes = optimize_sql(sql, "duckdb")
    assert "ORDER BY" in norm(out)
    assert not any(c.rule_id == "OPT-CTE-SORT-DROP" for c in changes)


def test_optimizer_is_idempotent():
    messy = (
        "WITH x AS (SELECT * FROM t WHERE YEAR(d) = 2025 ORDER BY d) "
        "SELECT a FROM x WHERE a NOT IN (SELECT b FROM u)"
    )
    once_sql, once_changes = optimize_sql(messy, "duckdb")
    twice_sql, twice_changes = optimize_sql(once_sql, "duckdb")
    assert norm(once_sql) == norm(twice_sql)
    assert twice_changes == []


def test_clean_query_is_unchanged():
    sql = "SELECT * FROM t WHERE a = 1"
    out, changes = optimize_sql(sql, "duckdb")
    assert norm(out) == norm(sql)
    assert changes == []
