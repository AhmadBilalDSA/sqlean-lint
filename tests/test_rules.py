"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Positive detections, negative controls and edge cases for every rule."""
import pytest

from sqlean_lint.engine import lint_query
from sqlean_lint.types import Severity


def violations_of(sql: str, rule_id: str, dialect: str = "duckdb"):
    result = lint_query(sql, dialect)
    return [v for v in result.violations if v.rule_id == rule_id]


# --------------------------------------------------------------------------
# SQL-CART-001 cartesian / predicate-less joins
# --------------------------------------------------------------------------

def test_cartesian_flags_explicit_cross_join():
    hits = violations_of("SELECT * FROM a CROSS JOIN b", "SQL-CART-001")
    assert len(hits) == 1
    assert hits[0].severity == Severity.CRITICAL


def test_cartesian_flags_join_without_on():
    assert violations_of("SELECT * FROM a JOIN b WHERE a.x = 1", "SQL-CART-001")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM a, b WHERE a.i = b.i",
        "SELECT * FROM a, b, c",
    ],
)
def test_cartesian_flags_comma_joins(sql):
    assert violations_of(sql, "SQL-CART-001")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM a JOIN b ON a.id = b.id",
        "SELECT * FROM a LEFT JOIN b ON a.id = b.a_id",
        "SELECT * FROM a NATURAL JOIN b",
        "SELECT * FROM a JOIN b USING (id)",
    ],
)
def test_cartesian_negative_controls(sql):
    assert violations_of(sql, "SQL-CART-001") == []


# --------------------------------------------------------------------------
# SQL-SARG-001 non-SARGable predicates
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "predicate",
    [
        "YEAR(d) = 2026",
        "UPPER(name) = 'X'",
        "col + 1 > 10",
        "EXTRACT(year FROM d) = 2026",
        "DATE(d) = '2026-01-15'",
    ],
)
def test_sargable_positive_detections(predicate):
    hits = violations_of(f"SELECT * FROM t WHERE {predicate}", "SQL-SARG-001")
    assert len(hits) == 1
    assert hits[0].severity == Severity.HIGH
    assert hits[0].suggested_fix


def test_sargable_ignores_function_over_literal_only():
    sql = "SELECT * FROM t WHERE col = UPPER('literal')"
    assert violations_of(sql, "SQL-SARG-001") == []


@pytest.mark.parametrize(
    "predicate",
    ["col = 2026", "name = 'X'", "d BETWEEN '2026-01-01' AND '2026-12-31'"],
)
def test_sargable_negative_controls(predicate):
    sql = f"SELECT * FROM t WHERE {predicate}"
    assert violations_of(sql, "SQL-SARG-001") == []


# --------------------------------------------------------------------------
# SQL-STAR-001 select star in nested blocks
# --------------------------------------------------------------------------

def test_select_star_flags_cte_body():
    hits = violations_of("WITH x AS (SELECT * FROM t) SELECT * FROM x", "SQL-STAR-001")
    assert len(hits) == 1  # outer top-level SELECT * stays allowed
    assert hits[0].line >= 1


def test_select_star_flags_derived_table():
    hits = violations_of("SELECT * FROM (SELECT * FROM t) s", "SQL-STAR-001")
    assert len(hits) == 1


def test_select_star_flags_qualified_star_in_cte():
    hits = violations_of("WITH x AS (SELECT t.* FROM t) SELECT * FROM x", "SQL-STAR-001")
    assert len(hits) == 1


def test_select_star_allows_top_level():
    assert violations_of("SELECT * FROM t", "SQL-STAR-001") == []


# --------------------------------------------------------------------------
# SQL-NOTIN-001 NULL-trap NOT IN subqueries
# --------------------------------------------------------------------------

def test_not_in_subquery_flagged_with_exists_fix():
    hits = violations_of(
        "SELECT * FROM o WHERE o.k NOT IN (SELECT c.k FROM c)", "SQL-NOTIN-001"
    )
    assert len(hits) == 1
    assert hits[0].severity == Severity.HIGH
    assert hits[0].suggested_fix and "NOT EXISTS" in hits[0].suggested_fix


def test_plain_in_subquery_not_flagged():
    sql = "SELECT * FROM o WHERE o.k IN (SELECT c.k FROM c)"
    assert violations_of(sql, "SQL-NOTIN-001") == []


def test_literal_not_in_list_not_flagged():
    sql = "SELECT * FROM o WHERE o.k NOT IN (1, 2, 3)"
    assert violations_of(sql, "SQL-NOTIN-001") == []


# --------------------------------------------------------------------------
# SQL-SORT-001 unbounded nested sorts
# --------------------------------------------------------------------------

def test_unbounded_sort_flagged_in_cte():
    hits = violations_of(
        "WITH x AS (SELECT * FROM t ORDER BY id) SELECT * FROM x", "SQL-SORT-001"
    )
    assert len(hits) == 1
    assert hits[0].severity == Severity.HIGH


def test_sort_with_limit_is_bounded():
    sql = "WITH x AS (SELECT * FROM t ORDER BY id LIMIT 3) SELECT * FROM x"
    assert violations_of(sql, "SQL-SORT-001") == []


def test_window_order_by_not_flagged():
    sql = "SELECT ROW_NUMBER() OVER (ORDER BY x) AS rn FROM t"
    assert violations_of(sql, "SQL-SORT-001") == []


def test_outer_order_by_not_flagged():
    sql = "SELECT * FROM t ORDER BY x"
    assert violations_of(sql, "SQL-SORT-001") == []


# --------------------------------------------------------------------------
# SQL-CAST-001 casts on join keys
# --------------------------------------------------------------------------

def test_cast_on_join_key_flagged():
    hits = violations_of(
        "SELECT * FROM a JOIN b ON CAST(a.k AS INT) = b.k2", "SQL-CAST-001"
    )
    assert len(hits) == 1
    assert hits[0].severity == Severity.MEDIUM


def test_plain_join_equality_clean():
    sql = "SELECT * FROM a JOIN b ON a.k = b.k2"
    assert violations_of(sql, "SQL-CAST-001") == []


def test_cast_outside_join_clause_ignored():
    sql = (
        "SELECT * FROM a JOIN b ON a.k = b.k2 "
        "WHERE CAST(a.d AS INT) > 5"
    )
    assert violations_of(sql, "SQL-CAST-001") == []


# --------------------------------------------------------------------------
# SQL-LIKE-001 leading wildcards
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "pattern",
    ["'%abc'", "'%abc%'" ],
)
def test_leading_wildcard_flagged(pattern):
    hits = violations_of(f"SELECT * FROM t WHERE name LIKE {pattern}", "SQL-LIKE-001")
    assert len(hits) == 1
    assert hits[0].severity == Severity.MEDIUM


def test_ilike_leading_wildcard_flagged():
    hits = violations_of("SELECT * FROM t WHERE name ILIKE '%x'", "SQL-LIKE-001")
    assert len(hits) == 1


@pytest.mark.parametrize("pattern", ["'abc%'", "'_abc'"])
def test_leading_wildcard_negative_controls(pattern):
    sql = f"SELECT * FROM t WHERE name LIKE {pattern}"
    assert violations_of(sql, "SQL-SARG-001") == []
    assert violations_of(sql, "SQL-LIKE-001") == []


# --------------------------------------------------------------------------
# violation payload sanity across all rules
# --------------------------------------------------------------------------

MESSY = """
WITH raw AS (
    SELECT * FROM orders o JOIN customers c
    WHERE UPPER(o.status) = 'PAID'
    ORDER BY o.id
)
SELECT customer_id FROM raw
WHERE customer_id NOT IN (SELECT id FROM blocked)
"""


def test_every_violation_has_complete_payload():
    result = lint_query(MESSY, "duckdb")
    assert result.violations, "messy query must produce findings"
    for violation in result.violations:
        assert violation.rule_id.startswith(("SQL-", "PARSER-"))
        assert isinstance(violation.severity, Severity)
        assert violation.title
        assert violation.message
        assert violation.line >= 1 and violation.col >= 1


def test_utf8_bom_prefixed_source_still_lints():
    """Windows tooling writes BOMs; they must never mask real findings."""
    result = lint_query("\ufeffSELECT * FROM a CROSS JOIN b", "duckdb")
    rule_ids = {v.rule_id for v in result.violations}
    assert "SQL-CART-001" in rule_ids
    assert "PARSER-001" not in rule_ids
