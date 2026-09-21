# Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.
"""Enterprise cross-module integrity suite for sqlean-lint PRO.

Validates that the core engine, connectors and integrations compose together
without breaking the non-negotiable invariants:

* ``lint_sql`` / ``fix_sql`` never raise across all 8 supported dialects.
* DuckDB connection + query integrity (typed and untyped paths).
* Explicit transaction safety: commit / rollback / auto-rollback on bad SQL.
* Offline RSA lease rules using real keys and real signatures (never mocks).
* Markdown/JSON report writers, FinOps estimator, schema validator and the
  Jinja pre-processor all degrade gracefully on arbitrary input.

Never mocks ``sqlean_lint.license_manager`` — real RSA test keys are used so
the cryptographic path is exercised end to end.
"""
from __future__ import annotations

import datetime
import json
import re

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from sqlean_lint.connectors.duckdb_local import DuckDBLocalConnector
from sqlean_lint.export.report_writer import lint_report, report_json, report_markdown
from sqlean_lint.integrations.finops_cost import estimate_finops
from sqlean_lint.integrations.jinja_parser import sanitize, strip_jinja
from sqlean_lint.integrations.schema_validator import (
    SchemaCatalog,
    check_schema,
)
from sqlean_lint.license_manager import (
    LicenseValidator,
    generate_hardware_id,
    mint_license_key,
)
from sqlean_lint.rules import fix_sql, lint_sql

DIALECTS = (
    "duckdb", "postgres", "mysql", "sqlite",
    "snowflake", "bigquery", "databricks", "tsql",
)

# SQL that triggers every Pro rule family (must parse in all 8 dialects).
PROBING_SQL = """SELECT * FROM orders o, customers c
WHERE YEAR(o.created_at) = 2026
  AND o.customer_id = c.customer_id;"""


@pytest.fixture
def keypair():
    """Generate a fresh RSA keypair for signing test licenses (real keys)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    return public_pem, private_pem


# ═══════════════════════════════════════════════════════════════════════
# 8-dialect cross-module integrity
# ═══════════════════════════════════════════════════════════════════════

def test_lint_sql_never_raises_all_dialects():
    for dialect in DIALECTS:
        result = lint_sql(PROBING_SQL, dialect)
        assert isinstance(result, list)
        rule_ids = {v.rule_id for v in result}
        assert {"SQL-WILD-001", "SQL-CART-001", "SQL-SARG-001"} <= rule_ids


def test_fix_sql_never_raises_all_dialects():
    for dialect in DIALECTS:
        out = fix_sql(PROBING_SQL, dialect)
        assert isinstance(out, str)
        assert out.strip()


def test_lint_sql_tolerates_garbage_input():
    garbage = "\x00" * 50 + "{{{{}}}}%%%{{{ not valid sql at all" * 5
    for dialect in DIALECTS:
        assert lint_sql(garbage, dialect) == []


def test_fix_sql_returns_same_on_garbage():
    garbage = "SELECT FROM WHERE;" + "\x00" * 20
    for dialect in DIALECTS:
        assert fix_sql(garbage, dialect) == garbage


def test_fix_sql_is_never_silently_lossy():
    """After a real fix the rewritten SQL must still reference the columns."""
    before = "SELECT * FROM orders WHERE YEAR(created_at) = 2026"
    fixed = fix_sql(before, "duckdb")
    assert "created_at" in fixed.lower()
    assert "2026" in fixed


# ═══════════════════════════════════════════════════════════════════════
# DuckDB connection + query integrity
# ═══════════════════════════════════════════════════════════════════════

def test_duckdb_connect_and_query_integrity():
    conn = DuckDBLocalConnector()
    conn.connect({})
    conn.execute_query("CREATE TABLE t (id INT, name VARCHAR)")
    conn.execute_query("INSERT INTO t VALUES (1, 'alpha'), (2, 'beta')")

    typed = conn.execute_query_typed("SELECT * FROM t ORDER BY id")
    columns, types, rows = typed
    assert columns == ["id", "name"]
    assert types == ["INTEGER", "VARCHAR"] or len(types) == 2
    assert [r["id"] for r in rows] == [1, 2]
    conn.close()


def test_duckdb_schema_and_error_boundary():
    conn = DuckDBLocalConnector()
    conn.connect({})
    conn.execute_query("CREATE TABLE t (x INT)")
    schema = conn.get_schema("t")
    assert schema["columns"][0]["name"] == "x"

    bad = conn.execute_query_safe("SELECT * FROM missing_table_xyz")
    assert bad["ok"] is False
    assert bad["error"]
    assert bad["error_type"]
    conn.close()


def test_duckdb_query_safe_success_shape():
    conn = DuckDBLocalConnector()
    conn.connect({})
    conn.execute_query("CREATE TABLE t (x INT)")
    conn.execute_query("INSERT INTO t VALUES (42)")
    result = conn.execute_query_safe("SELECT * FROM t")
    assert result["ok"] is True
    assert result["row_count"] == 1
    assert result["rows"][0]["x"] == 42
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Transaction safety
# ═══════════════════════════════════════════════════════════════════════

def test_transaction_commit_persists():
    conn = DuckDBLocalConnector()
    conn.connect({})
    conn.execute_query("CREATE TABLE t (x INT)")
    conn.begin_transaction()
    conn.execute_query("INSERT INTO t VALUES (1)")
    conn.commit()
    assert conn.execute_query("SELECT COUNT(*)  AS n FROM t")[0]["n"] == 1
    conn.close()


def test_transaction_rollback_discards():
    conn = DuckDBLocalConnector()
    conn.connect({})
    conn.execute_query("CREATE TABLE t (x INT)")
    conn.begin_transaction()
    conn.execute_query("INSERT INTO t VALUES (1)")
    conn.rollback()
    assert conn.execute_query("SELECT COUNT(*)  AS n FROM t")[0]["n"] == 0
    conn.close()


def test_bad_sql_auto_rolls_back_transaction():
    """A failed statement inside an explicit transaction must roll it back."""
    conn = DuckDBLocalConnector()
    conn.connect({})
    conn.execute_query("CREATE TABLE t (x INT)")
    conn.begin_transaction()
    conn.execute_query("INSERT INTO t VALUES (1)")
    result = conn.execute_query_safe("INSERT INTO t VALUES ('not-an-int')")
    assert result["ok"] is False
    assert conn.in_transaction() is False
    assert conn.execute_query("SELECT COUNT(*)  AS n FROM t")[0]["n"] == 0
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Offline RSA lease rules (real keys, real signatures)
# ═══════════════════════════════════════════════════════════════════════

def _sign_pro_key(private_pem: str, hw: str) -> str:
    """Mint a valid Pro license key signed for *hw* (real RSA signature)."""
    payload = {
        "hardware_id": hw,
        "tier": "pro",
        "issued": "2026-01-01",
        "expires": "2027-01-01",
        "customer_email": "kierninja@gmail.com",
    }
    return mint_license_key(payload, private_pem)


def test_offline_lease_round_trip(keypair, scratch):
    public_pem, private_pem = keypair
    hw = generate_hardware_id()
    key = _sign_pro_key(private_pem, hw)
    validator = LicenseValidator(
        public_key_pem=public_pem, license_path=scratch / "offline_lease.json"
    )
    assert validator.grant_lease(key) is True
    info = validator.lease_info()
    assert info is not None
    assert info["valid"] is True
    assert validator.is_licensed(hardware_id=hw) is True


def test_offline_lease_expires_after_window(keypair, scratch):
    public_pem, private_pem = keypair
    hw = generate_hardware_id()
    key = _sign_pro_key(private_pem, hw)
    validator = LicenseValidator(
        public_key_pem=public_pem, license_path=scratch / "lease2.json"
    )
    assert validator.grant_lease(key) is True
    blob = scratch / "lease2.json"
    data = json.loads(blob.read_text(encoding="utf-8"))
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=31)).isoformat()
    data["lease"]["first_seen"] = old
    data["lease_sig"] = validator._lease_signature(data["lease"], data["key"])
    blob.write_text(json.dumps(data), encoding="utf-8")
    assert validator.lease_info()["valid"] is False


def test_tampered_lease_invalidates(keypair, scratch):
    public_pem, private_pem = keypair
    hw = generate_hardware_id()
    key = _sign_pro_key(private_pem, hw)
    validator = LicenseValidator(
        public_key_pem=public_pem, license_path=scratch / "lease3.json"
    )
    assert validator.grant_lease(key) is True
    blob = scratch / "lease3.json"
    data = json.loads(blob.read_text(encoding="utf-8"))
    data["lease"]["last_seen"] = "2099-01-01T00:00:00+00:00"
    blob.write_text(json.dumps(data), encoding="utf-8")
    assert validator.lease_info()["valid"] is False


# ═══════════════════════════════════════════════════════════════════════
# Report writers (Markdown / JSON)
# ═══════════════════════════════════════════════════════════════════════

def test_report_markdown_shape():
    findings = lint_sql("SELECT * FROM orders", "duckdb")
    md = report_markdown(findings, dialect="duckdb")
    assert md.startswith("#")
    assert "SELECT *" in md or "SQL-WILD-001" in md


def test_report_json_schema():
    findings = lint_sql("SELECT * FROM orders", "duckdb")
    doc = report_json(findings, dialect="duckdb")
    assert doc["schema_version"]
    assert doc["findings"]
    assert doc["findings"][0]["rule_id"] == "SQL-WILD-001"
    json.dumps(doc)  # must be serializable


def test_lint_report_writes_file(scratch):
    findings = lint_sql("SELECT * FROM orders", "duckdb")
    out = scratch / "audit.md"
    text = lint_report(findings, output=out, fmt="md", dialect="duckdb")
    assert text
    assert out.exists()
    assert "#" in out.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════
# FinOps cost estimator
# ═══════════════════════════════════════════════════════════════════════

def test_finops_snowflake_estimate():
    findings = lint_sql(PROBING_SQL, "snowflake")
    est = estimate_finops(findings, dialect="snowflake", platform="snowflake", tables=2)
    assert est.as_is_usd_per_query >= 0
    assert est.savings_usd_per_query >= 0
    assert est.projected_monthly_savings >= 0
    assert est.breakdown


def test_finops_never_raises_on_empty():
    est = estimate_finops([], platform="snowflake")
    assert est.savings_usd_per_query == 0
    assert est.savings_pct == 0
    assert est.projected_monthly_savings == 0
    assert est.breakdown == []


# ═══════════════════════════════════════════════════════════════════════
# Schema validator + Jinja pre-processor
# ═══════════════════════════════════════════════════════════════════════

def test_schema_validator_flags_missing_table():
    catalog = SchemaCatalog()
    catalog.add_table("orders", ["id", "amount"])
    v = check_schema("SELECT id FROM missing_orders", catalog)
    assert any(x.kind == "table" for x in v)


def test_schema_validator_pass_clean_query():
    catalog = SchemaCatalog()
    catalog.add_table("orders", ["id", "amount"])
    v = check_schema("SELECT id, amount FROM orders", catalog)
    assert v == []


def test_schema_validator_never_raises_on_garbage():
    catalog = SchemaCatalog()
    assert check_schema("\x00{{{{ not valid", catalog) == []


def test_jinja_parser_strips_dbt_tokens():
    sql = "SELECT * FROM {{ ref('stg_orders') }} WHERE {{ config(materialized='table') }}"
    cleaned, count = strip_jinja(sql)
    assert count >= 1
    assert "{{" not in cleaned
    assert "__dbt_ref_stg_orders__" in cleaned
    assert sanitize(sql)


def test_jinja_parser_never_raises_on_binary():
    assert strip_jinja("\x00\x01\x02 not sql")
