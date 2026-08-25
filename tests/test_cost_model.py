"""Risk-score assertions: safe vs pathological queries and determinism."""
from sqlean_lint.cost_model import estimate_cost
from sqlean_lint.engine import lint_query
from sqlean_lint.parser import safe_parse
from sqlean_lint.types import RiskLevel

MESSY = """
WITH raw AS (
    SELECT * FROM orders o JOIN customers c
    WHERE UPPER(o.status) = 'PAID'
      AND YEAR(o.created_at) = 2026
    ORDER BY o.id
), agg AS (
    SELECT customer_id FROM raw
     WHERE customer_id NOT IN (SELECT id FROM blocked WHERE id IN (SELECT x FROM deep))
)
SELECT * FROM agg
"""


def test_trivial_query_scores_zero():
    result = lint_query("SELECT 1", "duckdb")
    assert result.cost.risk_score == 0
    assert result.cost.risk_level == RiskLevel.LOW
    assert result.cost.scan_complexity == "O(1)"


def test_simple_scan_is_low_complexity():
    result = lint_query("SELECT a FROM t WHERE a > 1", "duckdb")
    assert result.cost.risk_score < 25
    assert result.cost.scan_complexity == "O(N)"


def test_cartesian_dominates_score_and_complexity():
    result = lint_query("SELECT * FROM a CROSS JOIN b", "duckdb")
    assert result.cost.risk_score >= 50
    assert result.cost.join_risk >= 50
    assert result.cost.scan_complexity == "O(N*M)"
    assert result.cost.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_sargable_only_lands_in_medium_band():
    result = lint_query("SELECT * FROM t WHERE YEAR(d) = 2026", "duckdb")
    assert 25 <= result.cost.risk_score < 50
    assert result.cost.risk_level == RiskLevel.MEDIUM


def test_unbounded_sort_contributes():
    result = lint_query(
        "WITH x AS (SELECT * FROM t ORDER BY id) SELECT * FROM x", "duckdb"
    )
    assert result.cost.sort_risk >= 20
    assert result.cost.risk_score >= 20


def test_pathological_query_caps_at_critical():
    result = lint_query(MESSY, "duckdb")
    assert result.cost.risk_score <= 100
    assert result.cost.risk_level == RiskLevel.CRITICAL
    assert "cartesian" in result.cost.explanation.lower()
    assert "non-sargable" in result.cost.explanation.lower()


def test_cost_model_is_deterministic():
    first = lint_query(MESSY, "duckdb").cost
    second = lint_query(MESSY, "duckdb").cost
    assert first.to_dict() == second.to_dict()


def test_nesting_depth_adds_bonus():
    shallow_parsed = safe_parse(
        "SELECT * FROM t WHERE a IN (SELECT x FROM u)", "duckdb"
    )
    deep_parsed = safe_parse(
        "SELECT * FROM t WHERE a IN (SELECT x FROM u WHERE b IN (SELECT y FROM v))",
        "duckdb",
    )
    shallow = estimate_cost([], shallow_parsed.statements)
    deep = estimate_cost([], deep_parsed.statements)
    assert deep.risk_score - shallow.risk_score >= 5
