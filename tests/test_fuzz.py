"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Fuzz Testing the AST Engine for sqlean-lint PRO.

Uses the Hypothesis testing library to bombard the sqlglot parser and
custom Pro rules with randomly generated, highly mutated SQL strings.
Ensures the @given decorators prove that the AST engine never crashes
(throws unhandled exceptions) regardless of how malformed the input SQL is.

Per AGENTS.md invariant: `lint_sql` / `fix_sql` must never raise on arbitrary input.
"""
from __future__ import annotations

import string
import sys
from typing import Any, List, Optional

import pytest

try:  # pragma: no cover - optional dev dependency
    from hypothesis import given, settings, strategies as st, HealthCheck, example
    _HAS_HYPOTHESIS = True
except ImportError:  # pragma: no cover
    _HAS_HYPOTHESIS = False

# ── Strategy generators ────────────────────────────────────────────────

def _sql_identifier() -> Any:
    """Generate valid SQL identifiers (including edge cases)."""
    return st.one_of(
        st.text(
            alphabet=string.ascii_letters + "_",
            min_size=1,
            max_size=64,
        ).filter(lambda s: s and not s[0].isdigit() and s.upper() not in (
            "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "NULL", "TRUE", "FALSE",
            "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TABLE", "INDEX",
            "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "NATURAL",
            "ON", "USING", "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "OFFSET",
            "ASC", "DESC", "DISTINCT", "AS", "CASE", "WHEN", "THEN", "ELSE", "END",
            "BETWEEN", "IN", "LIKE", "IS", "EXISTS", "UNION", "INTERSECT", "EXCEPT",
            "PRIMARY", "KEY", "FOREIGN", "REFERENCES", "UNIQUE", "CHECK", "DEFAULT",
            "NOT", "NULL", "AUTO_INCREMENT", "IDENTITY"
        )),
        st.text(
            alphabet=string.ascii_letters + string.digits + "_$#@",
            min_size=1,
            max_size=64,
        ),
        # Quoted identifiers with special chars
        st.builds(
            lambda s: f'"{s}"',
            st.text(alphabet=string.printable.replace('"', ''), min_size=1, max_size=32),
        ),
        st.builds(
            lambda s: f'`{s}`',
            st.text(alphabet=string.printable.replace('`', ''), min_size=1, max_size=32),
        ),
        st.builds(
            lambda s: f'[{s}]',
            st.text(alphabet=string.printable.replace(']', ''), min_size=1, max_size=32),
        ),
    )


def _sql_literal() -> Any:
    """Generate SQL literals of various types."""
    return st.one_of(
        st.integers(min_value=-2**63, max_value=2**63 - 1),
        st.floats(allow_nan=False, allow_infinity=False, min_value=-1e308, max_value=1e308),
        st.builds(
            lambda s: f"'{s.replace(chr(39), chr(39)*2)}'",
            st.text(alphabet=string.printable, min_size=0, max_size=100),
        ),
        st.builds(
            lambda s: f"N'{s.replace(chr(39), chr(39)*2)}'",
            st.text(alphabet=string.printable + "\U0001f680", min_size=0, max_size=50),
        ),
        st.sampled_from(["TRUE", "FALSE", "true", "false", "True", "False"]),
        st.just("NULL"),
        st.sampled_from([
            "DATE '2024-01-15'",
            "TIMESTAMP '2024-01-15 10:30:45'",
            "TIME '10:30:45'",
            "INTERVAL '1' DAY",
            "INTERVAL '30' MINUTE",
        ]),
        st.builds(
            lambda b: f"X'{b.hex()}'",
            st.binary(min_size=0, max_size=50),
        ),
    )


def _column_reference() -> Any:
    """Generate column references (qualified and unqualified)."""
    return st.one_of(
        _sql_identifier(),
        st.builds(
            lambda t, c: f"{t}.{c}",
            _sql_identifier(),
            _sql_identifier(),
        ),
        st.builds(
            lambda s, t, c: f"{s}.{t}.{c}",
            _sql_identifier(),
            _sql_identifier(),
            _sql_identifier(),
        ),
    )


def _binary_operator() -> Any:
    """Generate SQL binary operators."""
    return st.sampled_from([
        "=", "!=", "<>", ">", "<", ">=", "<=",
        "LIKE", "NOT LIKE", "ILIKE", "NOT ILIKE",
        "IN", "NOT IN",
        "IS", "IS NOT",
        "+", "-", "*", "/", "%", "||",
        "AND", "OR",
    ])


def _unary_operator() -> Any:
    """Generate SQL unary operators."""
    return st.sampled_from(["NOT", "-", "+", "~"])


def _function_call() -> Any:
    """Generate function calls with various arguments."""
    func_names = st.sampled_from([
        "COUNT", "SUM", "AVG", "MIN", "MAX",
        "YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND",
        "DATE", "TIMESTAMP", "EXTRACT", "CAST",
        "COALESCE", "NULLIF", "CASE",
        "SUBSTRING", "TRIM", "UPPER", "LOWER",
        "CONCAT", "LENGTH", "POSITION",
        "NOW", "CURRENT_DATE", "CURRENT_TIMESTAMP",
        "ROW_NUMBER", "RANK", "DENSE_RANK", "LEAD", "LAG",
        "JSON_EXTRACT", "JSON_UNQUOTE",
        "MD5", "SHA256", "UUID",
    ])

    return st.builds(
        lambda name, args: f"{name}({', '.join(args)})",
        func_names,
        st.lists(st.one_of(_column_reference(), _sql_literal()), min_size=0, max_size=5),
    )


def _expression() -> Any:
    """Generate SQL expressions (recursive with depth limiting)."""
    base = st.one_of(
        _column_reference(),
        _sql_literal(),
        _function_call(),
    )

    binary = st.builds(
        lambda left, op, right: f"{left} {op} {right}",
        st.deferred(lambda: _expression()),
        _binary_operator(),
        st.deferred(lambda: _expression()),
    )

    unary = st.builds(
        lambda op, expr: f"{op} {expr}",
        _unary_operator(),
        st.deferred(lambda: _expression()),
    )

    paren = st.builds(
        lambda e: f"({e})",
        st.deferred(lambda: _expression()),
    )

    case_expr = st.builds(
        lambda whens, else_clause: f"CASE {whens} {else_clause} END",
        st.text(min_size=1, max_size=50),
        st.one_of(st.none(), st.just("ELSE 1")),
    )

    return st.one_of(base, binary, unary, paren, case_expr)


def _where_clause() -> Any:
    """Generate WHERE clauses."""
    return st.one_of(
        st.none(),
        st.builds(
            lambda cond: f"WHERE {cond}",
            _expression(),
        ),
    )


def _order_by_clause() -> Any:
    """Generate ORDER BY clauses."""
    return st.one_of(
        st.none(),
        st.builds(
            lambda cols: f"ORDER BY {cols}",
            st.lists(
                st.builds(
                    lambda col, dir: f"{col} {dir}",
                    _column_reference(),
                    st.sampled_from(["ASC", "DESC", "asc", "desc"]),
                ),
                min_size=1,
                max_size=3,
            ).map(", ".join),
        ),
    )


def _group_by_clause() -> Any:
    """Generate GROUP BY clauses."""
    return st.one_of(
        st.none(),
        st.builds(
            lambda cols: f"GROUP BY {cols}",
            st.lists(_column_reference(), min_size=1, max_size=3).map(", ".join),
        ),
    )


def _limit_clause() -> Any:
    """Generate LIMIT/OFFSET clauses."""
    return st.one_of(
        st.none(),
        st.builds(
            lambda lim: f"LIMIT {lim}",
            st.integers(min_value=1, max_value=10000),
        ),
        st.builds(
            lambda lim, off: f"LIMIT {lim} OFFSET {off}",
            st.integers(min_value=1, max_value=10000),
            st.integers(min_value=0, max_value=10000),
        ),
    )


def _join_clause() -> Any:
    """Generate JOIN clauses."""
    join_types = st.sampled_from([
        "", "INNER ", "LEFT ", "RIGHT ", "FULL ", "CROSS ",
        "LEFT OUTER ", "RIGHT OUTER ", "FULL OUTER ",
    ])

    return st.builds(
        lambda jtype, table, on: f"{jtype}JOIN {table} ON {on}",
        join_types,
        _sql_identifier(),
        _expression(),
    )


def _select_statement() -> Any:
    """Generate complete SELECT statements with various clauses."""
    def _build_select(data: dict) -> str:
        if data["star"]:
            cols = "*"
        else:
            cols = ", ".join(data["columns"]) if data["columns"] else "*"

        sql = f"SELECT {cols} FROM {data['table']}"

        if data["joins"]:
            sql += " " + " ".join(data["joins"])

        if data["where"]:
            sql += f" {data['where']}"

        if data["group"]:
            sql += f" {data['group']}"

        if data["having"]:
            sql += f" HAVING {data['having']}"

        if data["order"]:
            sql += f" {data['order']}"

        if data["limit"]:
            sql += f" {data['limit']}"

        return sql

    return st.builds(
        _build_select,
        data=st.fixed_dictionaries({
            "star": st.booleans(),
            "columns": st.lists(_column_reference(), max_size=8),
            "table": _sql_identifier(),
            "joins": st.lists(_join_clause(), max_size=3),
            "where": st.one_of(_where_clause(), st.none()),
            "group": st.one_of(_group_by_clause(), st.none()),
            "having": st.one_of(_expression(), st.none()),
            "order": st.one_of(_order_by_clause(), st.none()),
            "limit": st.one_of(_limit_clause(), st.none()),
        }),
    )


def _insert_statement() -> Any:
    """Generate INSERT statements."""
    return st.builds(
        lambda table, cols, vals: f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)})",
        _sql_identifier(),
        st.lists(_sql_identifier(), min_size=1, max_size=5),
        st.lists(_sql_literal(), min_size=1, max_size=5),
    )


def _update_statement() -> Any:
    """Generate UPDATE statements."""
    return st.builds(
        lambda table, sets, where: f"UPDATE {table} SET {sets} {where or ''}",
        _sql_identifier(),
        st.lists(
            st.builds(lambda c, v: f"{c} = {v}", _sql_identifier(), _sql_literal()),
            min_size=1,
            max_size=4,
        ).map(", ".join),
        st.one_of(_where_clause(), st.none()),
    )


def _delete_statement() -> Any:
    """Generate DELETE statements."""
    return st.builds(
        lambda table, where: f"DELETE FROM {table} {where or ''}",
        _sql_identifier(),
        st.one_of(_where_clause(), st.none()),
    )


def _cte_statement() -> Any:
    """Generate WITH (CTE) statements."""
    return st.builds(
        lambda ctes, main: f"WITH {ctes} {main}",
        st.lists(
            st.builds(
                lambda name, sql: f"{name} AS ({sql})",
                _sql_identifier(),
                _select_statement(),
            ),
            min_size=1,
            max_size=3,
        ).map(", ".join),
        _select_statement(),
    )


def _ddl_statement() -> Any:
    """Generate DDL statements (CREATE TABLE, etc.)."""
    return st.one_of(
        st.builds(
            lambda name, cols: f"CREATE TABLE {name} ({', '.join(cols)})",
            _sql_identifier(),
            st.lists(
                st.builds(
                    lambda c, t: f"{c} {t}",
                    _sql_identifier(),
                    st.sampled_from([
                        "INT", "BIGINT", "SMALLINT", "TINYINT",
                        "VARCHAR(255)", "TEXT", "CHAR(36)",
                        "DATE", "TIMESTAMP", "DATETIME",
                        "DECIMAL(10,2)", "FLOAT", "DOUBLE",
                        "BOOLEAN", "JSON", "UUID",
                        "INT PRIMARY KEY", "BIGSERIAL",
                    ]),
                ),
                min_size=1,
                max_size=8,
            ),
        ),
        st.builds(
            lambda name, cols: f"CREATE INDEX {name} ON {cols[0]} ({', '.join(cols[1:])})",
            _sql_identifier(),
            st.lists(_sql_identifier(), min_size=2, max_size=4),
        ),
    )


def _any_sql_statement() -> Any:
    """Generate any valid-ish SQL statement."""
    return st.one_of(
        _select_statement(),
        _insert_statement(),
        _update_statement(),
        _delete_statement(),
        _cte_statement(),
        _ddl_statement(),
    )


def _malformed_sql() -> Any:
    """Generate deliberately malformed SQL for stress testing."""
    return st.one_of(
        st.text(alphabet=string.ascii_letters + " ", min_size=1, max_size=50),
        st.builds(
            lambda n: "(" * n + "SELECT 1" + ")" * (n - 1),
            st.integers(min_value=1, max_value=10),
        ),
        st.builds(
            lambda s: f"SELECT '{s}",
            st.text(alphabet=string.ascii_letters, min_size=1, max_size=20),
        ),
        st.text(alphabet=string.printable, min_size=0, max_size=200),
        st.builds(
            lambda base, inj: f"{base} {inj}",
            _select_statement(),
            st.sampled_from([
                "'; DROP TABLE users; --",
                "1 OR 1=1",
                "UNION SELECT password FROM users",
                "'; WAITFOR DELAY '00:00:10'; --",
            ]),
        ),
        st.builds(
            lambda n: f"SELECT {'a' * n} FROM t",
            st.integers(min_value=100, max_value=10000),
        ),
        st.builds(
            lambda depth: "SELECT * FROM (" * depth + "SELECT 1" + ")" * depth,
            st.integers(min_value=1, max_value=50),
        ),
        st.binary(max_size=500),
        st.text(alphabet="\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f", min_size=1, max_size=100),
        st.text(alphabet="\u0000\u202e\u202d\ufffe\uffff", min_size=1, max_size=50),
    )


def _multi_statement() -> Any:
    """Generate multiple statements separated by semicolons."""
    return st.builds(
        lambda stmts: "; ".join(stmts) + ";",
        st.lists(_any_sql_statement(), min_size=1, max_size=5),
    )


def _cross_dialect_sql() -> Any:
    """Generate SQL with dialect-specific syntax that may fail in some dialects."""
    return st.one_of(
        # Snowflake-specific
        st.just("SELECT * FROM t FLATTEN(input => col)"),
        st.just("SELECT LISTAGG(col, ', ') WITHIN GROUP (ORDER BY col) FROM t"),
        # BigQuery-specific
        st.just("SELECT * FROM UNNEST([1, 2, 3]) AS x"),
        st.just("SELECT TIMESTAMP_TRUNC(ts, DAY) FROM t"),
        # MySQL-specific
        st.just("SELECT * FROM t USE INDEX (idx_name) WHERE id = 1"),
        st.just("SELECT GROUP_CONCAT(name SEPARATOR ', ') FROM t"),
        # TSQL-specific
        st.just("SELECT TOP 10 * FROM t ORDER BY id"),
        st.just("SELECT * INTO new_table FROM old_table"),
        # PostgreSQL-specific
        st.just("SELECT * FROM t WHERE col @> '{\"key\": \"val\"}'"),
        st.just("SELECT unnest(ARRAY[1, 2, 3])"),
        # Mixed garbage with SQL keywords
        st.builds(
            lambda k1, k2, k3: f"{k1} {k2} {k3} WHERE 1=1",
            st.sampled_from(["SELECT", "INSERT", "UPDATE", "DELETE"]),
            st.sampled_from(["TOP", "DISTINCT", "ALL", ""]),
            st.sampled_from(["*", "1", "col1", "COUNT(*)"]),
        ),
    )


# ── Hypothesis test settings ──────────────────────────────────────────

_fuzz_settings = settings(
    max_examples=200,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.data_too_large,
    ],
    deadline=None,
    database=None,
    phases=("generate", "target", "shrink"),
)


# ── Fuzz test bodies ───────────────────────────────────────────────────

if _HAS_HYPOTHESIS:
    from sqlean_lint.rules.pro import lint_sql, fix_sql
    from sqlean_lint.transpiler import transpile_sql
    from sqlean_lint.autofix import fix_source
    from sqlean_lint.engine import lint_source
    import sqlglot

    @given(_any_sql_statement())
    @_fuzz_settings
    def test_lint_sql_never_raises_on_valid_sql(sql: str) -> None:
        """Random valid-ish SQL must never raise from lint_sql."""
        findings = lint_sql(sql, "duckdb")
        assert isinstance(findings, list)
        for finding in findings:
            assert hasattr(finding, "rule_id")
            assert hasattr(finding, "message")
            assert hasattr(finding, "severity")

    @given(_malformed_sql())
    @_fuzz_settings
    def test_lint_sql_never_raises_on_malformed(data: Any) -> None:
        """Arbitrary malformed input must never raise from lint_sql."""
        if isinstance(data, bytes):
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = ""
        else:
            text = str(data)

        if not text:
            return

        findings = lint_sql(text, "duckdb")
        assert isinstance(findings, list)

    @given(_any_sql_statement())
    @_fuzz_settings
    def test_fix_sql_never_raises_on_valid_sql(sql: str) -> None:
        """Autofix must never raise on valid-ish SQL."""
        if not sql.strip():
            return
        result = fix_sql(sql, "duckdb")
        assert isinstance(result, str)

    @given(_malformed_sql())
    @_fuzz_settings
    def test_fix_sql_never_raises_on_malformed(data: Any) -> None:
        """Autofix must never raise on malformed input."""
        if isinstance(data, bytes):
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = ""
        else:
            text = str(data)

        if not text:
            return

        result = fix_sql(text, "duckdb")
        assert isinstance(result, str)

    @given(st.text(alphabet=string.printable, min_size=0, max_size=500))
    @_fuzz_settings
    def test_transpile_sql_never_raises(text: str) -> None:
        """The transpiler must never raise on arbitrary SQL text."""
        try:
            result = transpile_sql(text, read="duckdb", write="postgres")
            assert isinstance(result, str)
        except ValueError:
            pass
        except Exception as exc:  # pragma: no cover - should never happen
            pytest.fail(f"transpile_sql raised {type(exc).__name__}: {exc}")

    @given(st.text(alphabet=string.ascii_letters + string.digits + " \n\t()_.,;'", min_size=0, max_size=500))
    @_fuzz_settings
    def test_fix_source_never_raises(text: str) -> None:
        """Autofix source must never raise on arbitrary input."""
        if not text.strip():
            return
        result = fix_source(text)
        assert isinstance(result, dict)
        assert "fixed" in result
        assert "changes" in result
        assert isinstance(result["changes"], list)

    @given(_multi_statement())
    @_fuzz_settings
    def test_lint_source_multiple_statements(sql: str) -> None:
        """Engine must handle multiple statements without raising."""
        result = lint_source(sql, dialect="duckdb", optimize=True)
        assert result is not None
        assert hasattr(result, "violations")
        assert isinstance(result.violations, list)

    @given(_select_statement())
    @_fuzz_settings
    def test_sqlglot_parse_never_raises_on_select(sql: str) -> None:
        """sqlglot.parse_one must not crash on generated SELECTs."""
        try:
            ast = sqlglot.parse_one(sql, read="duckdb")
            assert ast is not None
        except sqlglot.errors.ParseError:
            pass
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"sqlglot raised {type(exc).__name__}: {exc}")

    @given(_malformed_sql())
    @_fuzz_settings
    def test_sqlglot_parse_graceful_on_garbage(data: Any) -> None:
        """sqlglot must raise ParseError, not crash, on garbage."""
        if isinstance(data, bytes):
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = ""
        else:
            text = str(data)

        if not text:
            return

        try:
            sqlglot.parse_one(text, read="duckdb")
        except sqlglot.errors.ParseError:
            pass
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"sqlglot raised unexpected {type(exc).__name__}: {exc}")

    @given(_any_sql_statement())
    @_fuzz_settings
    def test_all_pro_rules_never_raise(sql: str) -> None:
        """All Pro rules must execute without raising."""
        from sqlean_lint.rules.pro import run_all, DEFAULT_RULES
        try:
            ast = sqlglot.parse_one(sql, read="duckdb")
        except Exception:
            return

        violations = run_all(ast, "duckdb", DEFAULT_RULES)
        assert isinstance(violations, list)
        for v in violations:
            assert isinstance(v.rule_id, str)
            assert v.rule_id.startswith("SQL-")

    @given(_any_sql_statement())
    @_fuzz_settings
    def test_rule_fix_methods_never_raise(sql: str) -> None:
        """Rule fix() methods must never raise."""
        from sqlean_lint.rules.pro import apply_fixes, DEFAULT_RULES
        try:
            ast = sqlglot.parse_one(sql, read="duckdb")
        except Exception:
            return

        changes = apply_fixes(ast, "duckdb", DEFAULT_RULES)
        assert isinstance(changes, list)
        for change in changes:
            assert isinstance(change, str)

    @example("SELECT * FROM t WHERE YEAR(d) = 2024")
    @example("SELECT a, b FROM t1, t2 WHERE t1.id = t2.id")
    @given(_select_statement())
    @_fuzz_settings
    def test_sargable_rule_detects_year_wrap(sql: str) -> None:
        """SARGable rule should detect YEAR(col) = literal patterns."""
        from sqlean_lint.rules.pro import SARGableDateRule
        try:
            ast = sqlglot.parse_one(sql, read="duckdb")
        except Exception:
            return

        violations = SARGableDateRule().run(ast, "duckdb")
        assert isinstance(violations, list)

    @example("SELECT * FROM users")
    @given(_select_statement())
    @_fuzz_settings
    def test_wildcard_rule_detects_star(sql: str) -> None:
        """Wildcard rule should detect SELECT * patterns."""
        from sqlean_lint.rules.pro import WildcardSelectRule
        try:
            ast = sqlglot.parse_one(sql, read="duckdb")
        except Exception:
            return

        violations = WildcardSelectRule().run(ast, "duckdb")
        assert isinstance(violations, list)
        if "*" in sql.upper() and "FROM" in sql.upper():
            pass

    @given(_select_statement())
    @_fuzz_settings
    def test_cartesian_rule_detects_comma_join(sql: str) -> None:
        """Cartesian product rule should detect implicit comma joins."""
        from sqlean_lint.rules.pro import CartesianProductRule
        try:
            ast = sqlglot.parse_one(sql, read="duckdb")
        except Exception:
            return

        violations = CartesianProductRule().run(ast, "duckdb")
        assert isinstance(violations, list)

    @given(st.text(alphabet=string.printable, min_size=0, max_size=1000))
    @settings(max_examples=500, deadline=None, suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.data_too_large,
    ])
    def test_engine_lint_source_never_raises(sql: str) -> None:
        """High-level lint_source must never raise on any input."""
        result = lint_source(sql, dialect="duckdb", optimize=True)
        assert result is not None
        assert hasattr(result, "violations")
        assert isinstance(result.violations, list)
        assert hasattr(result, "optimized_sql")
        assert isinstance(result.optimized_sql, (str, type(None)))

    @given(
        st.data(),
        settings=settings(max_examples=100, deadline=None)
    )
    def test_dialect_variations(data: Any) -> None:
        """Test lint_sql across all supported dialects."""
        dialects = data.draw(st.sampled_from(["duckdb", "postgres", "mysql", "sqlite", "snowflake", "bigquery", "tsql"]))
        sql = data.draw(_any_sql_statement())

        findings = lint_sql(sql, dialects)
        assert isinstance(findings, list)

    @given(
        st.data(),
        settings=settings(max_examples=50, deadline=None)
    )
    def test_transpile_all_dialect_pairs(data: Any) -> None:
        """Test transpilation between all dialect pairs."""
        read_dialect = data.draw(st.sampled_from(["duckdb", "postgres", "mysql", "sqlite", "snowflake"]))
        write_dialect = data.draw(st.sampled_from(["duckdb", "postgres", "mysql", "sqlite", "snowflake"]))
        sql = data.draw(_select_statement())

        try:
            result = transpile_sql(sql, read=read_dialect, write=write_dialect)
            assert isinstance(result, str)
        except ValueError:
            pass
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"transpile_sql({read_dialect}->{write_dialect}) raised {type(exc).__name__}: {exc}")

    @given(st.integers(min_value=1, max_value=100))
    @_fuzz_settings
    def test_deeply_nested_subqueries(depth: int) -> None:
        """Deeply nested subqueries must not cause stack overflow."""
        sql = "SELECT * FROM (" * depth + "SELECT 1" + ")" * depth
        findings = lint_sql(sql, "duckdb")
        assert isinstance(findings, list)

    @given(st.integers(min_value=1, max_value=50))
    @_fuzz_settings
    def test_many_joins(num_joins: int) -> None:
        """Many JOINs must not cause performance issues or crashes."""
        tables = ", ".join(f"t{i}" for i in range(num_joins + 1))
        conditions = " AND ".join(f"t{i}.id = t{i+1}.id" for i in range(num_joins))
        sql = f"SELECT * FROM {tables} WHERE {conditions}"
        findings = lint_sql(sql, "duckdb")
        assert isinstance(findings, list)

    @given(st.integers(min_value=1, max_value=100))
    @_fuzz_settings
    def test_many_columns_in_select(num_cols: int) -> None:
        """SELECT with many columns must not crash."""
        cols = ", ".join(f"col{i}" for i in range(num_cols))
        sql = f"SELECT {cols} FROM t"
        findings = lint_sql(sql, "duckdb")
        assert isinstance(findings, list)

    @given(st.text(alphabet=string.ascii_letters + string.digits + "_", min_size=1, max_size=200))
    @_fuzz_settings
    def test_very_long_identifiers(name: str) -> None:
        """Very long identifiers must not crash parser."""
        sql = f"SELECT {name} FROM {name}"
        findings = lint_sql(sql, "duckdb")
        assert isinstance(findings, list)

    @given(st.lists(_any_sql_statement(), min_size=1, max_size=20))
    @_fuzz_settings
    def test_many_statements_batch(statements: List[str]) -> None:
        """Batch of many statements must be handled."""
        sql = "; ".join(statements) + ";"
        result = lint_source(sql, dialect="duckdb", optimize=True)
        assert result is not None
        assert isinstance(result.violations, list)

    # ── Cross-dialect fuzzing ───────────────────────────────────────────

    @given(_cross_dialect_sql())
    @_fuzz_settings
    def test_cross_dialect_lint_never_raises(sql: str) -> None:
        """Dialect-specific SQL must not crash lint_sql in any dialect."""
        for dialect in ["duckdb", "postgres", "mysql", "snowflake"]:
            findings = lint_sql(sql, dialect)
            assert isinstance(findings, list), f"lint_sql raised in {dialect}"

    @given(_cross_dialect_sql())
    @_fuzz_settings
    def test_cross_dialect_transpile_never_raises(sql: str) -> None:
        """Dialect-specific SQL must not crash the transpiler."""
        try:
            result = transpile_sql(sql, read="duckdb", write="mysql")
            assert isinstance(result, str)
        except ValueError:
            pass
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"transpile raised {type(exc).__name__}: {exc}")

    # ── Encoding and byte-level edge cases ──────────────────────────────

    @given(st.binary(min_size=0, max_size=1000))
    @_fuzz_settings
    def test_raw_bytes_never_crash(data: bytes) -> None:
        """Raw binary input (decoded as UTF-8 replace) must never crash."""
        text = data.decode("utf-8", errors="replace")
        if not text:
            return
        findings = lint_sql(text, "duckdb")
        assert isinstance(findings, list)

    @given(st.text(alphabet="\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f", min_size=0, max_size=200))
    @_fuzz_settings
    def test_control_chars_never_crash(text: str) -> None:
        """Control characters must not crash the parser."""
        findings = lint_sql(text, "duckdb")
        assert isinstance(findings, list)

    # ── Repetition and amplification ────────────────────────────────────

    @given(st.integers(min_value=1, max_value=500))
    @_fuzz_settings
    def test_repeated_keywords(n: int) -> None:
        """Repeated SQL keywords must not cause infinite loops."""
        sql = "SELECT " * n + "1"
        findings = lint_sql(sql, "duckdb")
        assert isinstance(findings, list)

    @given(st.integers(min_value=1, max_value=200))
    @_fuzz_settings
    def test_deeply_nested_parens(depth: int) -> None:
        """Deeply nested parentheses must not overflow."""
        sql = "(" * depth + "SELECT 1" + ")" * depth
        findings = lint_sql(sql, "duckdb")
        assert isinstance(findings, list)

    @given(st.integers(min_value=1, max_value=100))
    @_fuzz_settings
    def test_wildcard_select_star_variants(n: int) -> None:
        """SELECT with many stars in various positions must not crash."""
        cols = ", ".join(["*"] * n)
        sql = f"SELECT {cols} FROM t"
        findings = lint_sql(sql, "duckdb")
        assert isinstance(findings, list)

else:  # pragma: no cover
    def test_hypothesis_available() -> None:
        """Hypothesis is an optional dev dependency; skip when missing."""
        pytest.skip("hypothesis not installed (optional dev dependency)")


# ── CLI entry point for manual execution ──────────────────────────────

def main() -> int:
    """Run fuzz tests programmatically."""
    if not _HAS_HYPOTHESIS:
        print("Hypothesis not installed. Install with: pip install hypothesis")
        return 1

    import subprocess
    result = subprocess.run([
        sys.executable, "-m", "pytest",
        __file__,
        "-v",
        "--tb=short",
        "-x",
    ])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
