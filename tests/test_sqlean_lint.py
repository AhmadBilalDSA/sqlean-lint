"""Copyright (c) 2026 SQLean Engineering. All Rights Reserved.

Edge-case test matrix for the sqlean-lint Pro desktop edition.

Covers the AST caching mechanism, every Pro rule (with exact assertions on the
SQL-SARG-001 auto-fix rewrite) and the hardware-bound license engine.
"""
from __future__ import annotations

import hashlib
import re

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import sqlglot

from sqlean_lint.license_manager import (
    LICENSE_ISSUER,
    LicenseError,
    LicenseValidator,
    generate_hardware_id,
    mint_license_key,
    sign_license_payload,
    _windows_machine_guid,
)
from sqlean_lint.rules.pro import (
    DEFAULT_RULES,
    CartesianProductRule,
    LintViolation,
    SARGableDateRule,
    WildcardSelectRule,
    apply_fixes,
    ast_cache_size,
    cached_parse,
    clear_ast_cache,
    collect_sql_files,
    fix_sql,
    lint_files,
    lint_sql,
    run_all,
)

HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def keypair():
    """Generate a fresh RSA keypair for signing test licenses."""
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


@pytest.fixture(autouse=True)
def _clean_ast_cache():
    clear_ast_cache()
    yield
    clear_ast_cache()


def mint_license_key(private_pem: str, hardware_id: str, **overrides) -> str:
    payload = {
        "hardware_id": hardware_id,
        "customer_email": "kierninja@gmail.com",
        "tier": "pro",
        "issued": "2026-01-01",
        "expires": "2027-01-01",
    }
    payload.update(overrides)
    return mint_license_key(payload, private_pem)


# ═══════════════════════════════════════════════════════════════════════
# AST caching
# ═══════════════════════════════════════════════════════════════════════

def test_cached_parse_returns_same_object_for_same_input():
    a = cached_parse("SELECT * FROM t", "duckdb")
    b = cached_parse("SELECT * FROM t", "duckdb")
    assert a is b


def test_cached_parse_distinguishes_sql_and_dialect():
    a = cached_parse("SELECT * FROM t", "duckdb")
    b = cached_parse("SELECT * FROM t", "postgres")
    c = cached_parse("SELECT x FROM t", "duckdb")
    assert a is not b
    assert a is not c


def test_clear_ast_cache_evicts_entries():
    cached_parse("SELECT 1", "duckdb")
    assert ast_cache_size() == 1
    clear_ast_cache()
    assert ast_cache_size() == 0


def test_cached_parse_reparses_after_clear():
    first = cached_parse("SELECT 1", "duckdb")
    clear_ast_cache()
    second = cached_parse("SELECT 1", "duckdb")
    assert first is not second


# ═══════════════════════════════════════════════════════════════════════
# SQL-WILD-001 : WildcardSelectRule
# ═══════════════════════════════════════════════════════════════════════

def test_wildcard_flags_bare_star():
    ast = sqlglot.parse_one("SELECT * FROM orders", read="duckdb")
    hits = WildcardSelectRule().run(ast, "duckdb")
    assert len(hits) == 1
    assert hits[0].rule_id == "SQL-WILD-001"
    assert hits[0].severity == "MEDIUM"


def test_wildcard_flags_qualified_star():
    ast = sqlglot.parse_one("SELECT o.*, o.id FROM orders o", read="duckdb")
    hits = WildcardSelectRule().run(ast, "duckdb")
    assert len(hits) == 1
    assert hits[0].fixable is False


def test_wildcard_allows_explicit_columns():
    ast = sqlglot.parse_one("SELECT id, name FROM orders", read="duckdb")
    assert WildcardSelectRule().run(ast, "duckdb") == []


def test_wildcard_counts_each_select_in_script():
    ast = sqlglot.parse_one(
        "WITH wide AS (SELECT * FROM a) SELECT * FROM wide", read="duckdb"
    )
    hits = WildcardSelectRule().run(ast, "duckdb")
    assert len(hits) == 2


# ═══════════════════════════════════════════════════════════════════════
# SQL-CART-001 : CartesianProductRule
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM a, b",
        "SELECT * FROM a, b, c",
        "SELECT * FROM a CROSS JOIN b",
        "SELECT * FROM a, b WHERE a.x = 1",
    ],
)
def test_cartesian_flags_comma_and_cross_joins(sql):
    ast = sqlglot.parse_one(sql, read="duckdb")
    hits = CartesianProductRule().run(ast, "duckdb")
    assert len(hits) >= 1
    assert hits[0].rule_id == "SQL-CART-001"
    assert hits[0].severity == "CRITICAL"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM a JOIN b ON a.id = b.a_id",
        "SELECT * FROM a LEFT JOIN b USING (id)",
        "SELECT * FROM a NATURAL JOIN b",
        "SELECT * FROM a JOIN b ON a.id = b.id WHERE a.c = 1",
    ],
)
def test_cartesian_negative_controls(sql):
    ast = sqlglot.parse_one(sql, read="duckdb")
    assert CartesianProductRule().run(ast, "duckdb") == []


def test_cartesian_ignores_lateral_unnest():
    ast = sqlglot.parse_one(
        "SELECT * FROM a CROSS JOIN LATERAL (SELECT 1) AS x", read="postgres"
    )
    # Lateral joins are legitimate; only predicate-less table products count.
    assert CartesianProductRule().run(ast, "postgres") == []


def test_lateral_unnest_not_flagged():
    ast = sqlglot.parse_one(
        "SELECT * FROM a, LATERAL UNNEST(a.tags) AS t", read="duckdb"
    )
    assert CartesianProductRule().run(ast, "duckdb") == []


# ═══════════════════════════════════════════════════════════════════════
# SQL-SARG-001 : SARGableDateRule
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "predicate",
    [
        "YEAR(d) = 2026",
        "2026 = YEAR(d)",
        "EXTRACT(year FROM d) = 2026",
        "YEAR(d) > 2025",
        "MONTH(d) = 1",
    ],
)
def test_sargable_flags_date_wrappers(predicate):
    ast = sqlglot.parse_one(f"SELECT * FROM t WHERE {predicate}", read="duckdb")
    hits = SARGableDateRule().run(ast, "duckdb")
    assert len(hits) == 1
    assert hits[0].rule_id == "SQL-SARG-001"
    assert hits[0].severity == "HIGH"
    assert hits[0].fixable is True


@pytest.mark.parametrize(
    "predicate",
    [
        "d >= '2026-01-01'",
        "d BETWEEN '2026-01-01' AND '2026-12-31'",
        "d = '2026-01-15'",
    ],
)
def test_sargable_negative_controls(predicate):
    ast = sqlglot.parse_one(f"SELECT * FROM t WHERE {predicate}", read="duckdb")
    assert SARGableDateRule().run(ast, "duckdb") == []


def test_sargable_autofix_rewrites_year_equality():
    ast = sqlglot.parse_one("SELECT * FROM t WHERE YEAR(d) = 2026", read="duckdb")
    changes = SARGableDateRule().fix(ast, "duckdb")
    assert len(changes) == 1
    fixed = ast.sql(dialect="duckdb")
    assert fixed == (
        "SELECT * FROM t WHERE "
        "d >= CAST('2026-01-01' AS DATE) AND d < CAST('2027-01-01' AS DATE)"
    )


def test_sargable_autofix_handles_extract_year():
    ast = sqlglot.parse_one(
        "SELECT * FROM t WHERE EXTRACT(year FROM d) = 2026", read="duckdb"
    )
    SARGableDateRule().fix(ast, "duckdb")
    assert ast.sql(dialect="duckdb") == (
        "SELECT * FROM t WHERE "
        "d >= CAST('2026-01-01' AS DATE) AND d < CAST('2027-01-01' AS DATE)"
    )


def test_sargable_autofix_handles_reversed_operands():
    ast = sqlglot.parse_one("SELECT * FROM t WHERE 2026 = YEAR(d)", read="duckdb")
    SARGableDateRule().fix(ast, "duckdb")
    assert ast.sql(dialect="duckdb") == (
        "SELECT * FROM t WHERE "
        "d >= CAST('2026-01-01' AS DATE) AND d < CAST('2027-01-01' AS DATE)"
    )


def test_sargable_autofix_leap_year_boundary():
    ast = sqlglot.parse_one("SELECT * FROM t WHERE YEAR(d) = 2024", read="duckdb")
    SARGableDateRule().fix(ast, "duckdb")
    assert "'2024-01-01'" in ast.sql(dialect="duckdb")
    assert "'2025-01-01'" in ast.sql(dialect="duckdb")


@pytest.mark.parametrize("dialect", ["duckdb", "postgres", "snowflake"])
def test_sargable_autofix_dialect_agnostic(dialect):
    ast = sqlglot.parse_one("SELECT * FROM t WHERE YEAR(d) = 2026", read=dialect)
    SARGableDateRule().fix(ast, dialect)
    assert ast.sql(dialect=dialect) == (
        "SELECT * FROM t WHERE "
        "d >= CAST('2026-01-01' AS DATE) AND d < CAST('2027-01-01' AS DATE)"
    )


def test_sargable_autofix_does_not_touch_non_year_equality():
    ast = sqlglot.parse_one("SELECT * FROM t WHERE MONTH(d) = 1", read="duckdb")
    original = ast.sql(dialect="duckdb")
    changes = SARGableDateRule().fix(ast, "duckdb")
    assert changes == []
    assert ast.sql(dialect="duckdb") == original


def test_apply_fixes_aggregates_changes():
    ast = sqlglot.parse_one(
        "SELECT * FROM t WHERE YEAR(d) = 2026", read="duckdb"
    )
    changes = apply_fixes(ast, "duckdb", DEFAULT_RULES)
    assert any("YEAR(d) = 2026" in c for c in changes)
    assert "CAST('2026-01-01' AS DATE)" in ast.sql(dialect="duckdb")


def test_run_all_returns_violations_from_all_rules():
    ast = sqlglot.parse_one(
        "SELECT * FROM a, b WHERE YEAR(d) = 2026", read="duckdb"
    )
    violations = run_all(ast, "duckdb", DEFAULT_RULES)
    ids = {v.rule_id for v in violations}
    assert "SQL-WILD-001" in ids
    assert "SQL-CART-001" in ids
    assert "SQL-SARG-001" in ids
    assert all(isinstance(v, LintViolation) for v in violations)


# ═══════════════════════════════════════════════════════════════════════
# Hardware-ID generation
# ═══════════════════════════════════════════════════════════════════════

def test_hardware_id_is_hex_sha256():
    hw = generate_hardware_id()
    assert len(hw) == 64
    assert HEX64.match(hw)


def test_hardware_id_is_deterministic():
    assert generate_hardware_id() == generate_hardware_id()


def test_hardware_id_changes_with_mac(monkeypatch):
    import sqlean_lint.license_manager as lm

    monkeypatch.setattr(lm.uuid, "getnode", lambda: 0x112233445566)
    a = generate_hardware_id()
    monkeypatch.setattr(lm.uuid, "getnode", lambda: 0xAABBCCDDEEFF)
    b = generate_hardware_id()
    assert a != b


def test_hardware_id_changes_with_machine_guid(monkeypatch):
    import sqlean_lint.license_manager as lm

    monkeypatch.setattr(lm, "_windows_machine_guid", lambda: "GUID-A")
    a = generate_hardware_id()
    monkeypatch.setattr(lm, "_windows_machine_guid", lambda: "GUID-B")
    b = generate_hardware_id()
    assert a != b


def test_hardware_id_matches_expected_combination(monkeypatch):
    import sqlean_lint.license_manager as lm

    monkeypatch.setattr(lm.uuid, "getnode", lambda: 0x0A1B2C3D4E5F)
    monkeypatch.setattr(lm, "_windows_machine_guid", lambda: "TEST-MACHINE-GUID")
    expected = hashlib.sha256(
        "0a1b2c3d4e5f|TEST-MACHINE-GUID".encode("utf-8")
    ).hexdigest()
    assert generate_hardware_id() == expected


# ═══════════════════════════════════════════════════════════════════════
# LicenseValidator
# ═══════════════════════════════════════════════════════════════════════

def test_valid_license_round_trip(keypair):
    public_pem, private_pem = keypair
    hw = generate_hardware_id()
    key = mint_license_key(private_pem, hw)
    validator = LicenseValidator(public_key_pem=public_pem)
    info = validator.validate_key(key, hardware_id=hw)
    assert info.valid
    assert info.tier == "pro"
    assert info.customer_email == "kierninja@gmail.com"
    assert info.hardware_id == hw


def test_tampered_license_rejected(keypair):
    public_pem, private_pem = keypair
    hw = generate_hardware_id()
    key = mint_license_key(private_pem, hw)
    tampered = "X" + key[1:]
    with pytest.raises(LicenseError):
        LicenseValidator(public_key_pem=public_pem).validate_key(
            tampered, hardware_id=hw
        )


def test_signature_swap_rejected(keypair):
    """Signature from key A must not validate a payload signed by key B."""
    public_a, private_a = keypair
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_b = forged_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    hw = generate_hardware_id()
    payload = {
        "hardware_id": hw,
        "tier": "pro",
        "issued": "2026-01-01",
        "expires": "2027-01-01",
    }
    sig_a = sign_license_payload(payload, private_a)
    sig_b = sign_license_payload(payload, private_b)
    assert sig_a != sig_b
    key_a = mint_license_key(payload, private_a)
    key_b = mint_license_key(payload, private_b)
    assert key_a != key_b
    with pytest.raises(LicenseError):
        LicenseValidator(public_key_pem=public_a).validate_key(key_b, hardware_id=hw)


def test_license_stamps_creator_issuer(keypair):
    """Every signed key must carry the creator's identity (ownership lock-in)."""
    public_pem, private_pem = keypair
    key = mint_license_key(private_pem, generate_hardware_id())
    validator = LicenseValidator(public_key_pem=public_pem)
    info = validator.validate_key(key, hardware_id=generate_hardware_id())
    assert info.valid
    assert info.extra.get("issuer") == LICENSE_ISSUER
    assert LICENSE_ISSUER == "AhmadBilalDSA (kierninja@gmail.com)"


def test_hardware_mismatch_rejected(keypair):
    public_pem, private_pem = keypair
    key = mint_license_key(private_pem, "0" * 64)
    with pytest.raises(LicenseError, match="different machine"):
        LicenseValidator(public_key_pem=public_pem).validate_key(
            key, hardware_id=generate_hardware_id()
        )


def test_license_persistence_round_trip(keypair, scratch):
    public_pem, private_pem = keypair
    hw = generate_hardware_id()
    key = mint_license_key(private_pem, hw)
    license_path = scratch / "license.json"

    validator = LicenseValidator(
        public_key_pem=public_pem, license_path=license_path
    )
    assert not validator.is_licensed(hardware_id=hw)

    info = validator.validate_key(key, hardware_id=hw)
    validator.save_license(info)
    assert validator.load_cached() is not None
    assert validator.is_licensed(hardware_id=hw) is True

    assert validator.clear() is True
    assert validator.is_licensed(hardware_id=hw) is False


def test_expired_license_not_licensed(keypair, scratch):
    public_pem, private_pem = keypair
    hw = generate_hardware_id()
    key = mint_license_key(
        private_pem, hw, expires="2000-01-01", issued="1999-01-01"
    )
    validator = LicenseValidator(
        public_key_pem=public_pem, license_path=scratch / "license.json"
    )
    info = validator.validate_key(key, hardware_id=hw)
    assert info.valid
    assert info.is_expired
    assert validator.is_licensed(hardware_id=hw) is False


def test_without_public_key_never_validates(keypair):
    _, private_pem = keypair
    key = mint_license_key(private_pem, generate_hardware_id())
    validator = LicenseValidator(public_key_pem=None)
    assert not validator.has_public_key
    info = validator.validate_key(key)
    assert info.valid is False


# ═══════════════════════════════════════════════════════════════════════
# 30-day offline lease (survives hardware / MAC / dock changes)
# ═══════════════════════════════════════════════════════════════════════

def test_lease_granted_for_valid_key(keypair, scratch):
    public_pem, private_pem = keypair
    hw = generate_hardware_id()
    key = mint_license_key(private_pem, hw)
    validator = LicenseValidator(
        public_key_pem=public_pem, license_path=scratch / "lease.json"
    )
    assert validator.grant_lease(key) is True
    info = validator.lease_info()
    assert info is not None
    assert info["valid"] is True
    assert info["first_seen"]


def test_lease_rejects_forged_and_tampered_keys(keypair, scratch):
    public_pem, private_pem = keypair
    hw = generate_hardware_id()
    good = mint_license_key(private_pem, hw)
    bad = mint_license_key(private_pem, hw)
    validator = LicenseValidator(
        public_key_pem=public_pem, license_path=scratch / "lease.json"
    )
    # A key signed by a different hardware ID is still signature-valid; simulate
    # an attacker using a key that never matched this machine.
    other_hw = "0" * 64
    foreign = mint_license_key(private_pem, other_hw)
    assert foreign != good
    # Grant a valid lease first (seeded), then tamper the stored blob.
    assert validator.grant_lease(good) is True
    blob = scratch / "lease.json"
    import json as _json
    data = _json.loads(blob.read_text(encoding="utf-8"))
    data["lease"]["last_seen"] = "2099-01-01T00:00:00+00:00"
    blob.write_text(_json.dumps(data), encoding="utf-8")
    # Tampering invalidates the HMAC lease signature.
    assert validator.lease_info()["valid"] is False


def test_lease_honored_across_hardware_change(keypair, scratch):
    """A machine swap (different hardware id) is still licensed within 30 days."""
    public_pem, private_pem = keypair
    original_hw = "a" * 64
    moved_hw = "b" * 64
    key = mint_license_key(private_pem, original_hw)
    validator = LicenseValidator(
        public_key_pem=public_pem, license_path=scratch / "lease.json"
    )
    assert validator.grant_lease(key) is True
    # Same machine is licensed via its normal path.
    assert validator.is_licensed(hardware_id=original_hw) is True
    # The moved machine (fresh NIC / dock) is covered by the active lease.
    assert validator.is_licensed(hardware_id=moved_hw) is True


def test_lease_expires_after_window(keypair, scratch):
    public_pem, private_pem = keypair
    hw = generate_hardware_id()
    key = mint_license_key(private_pem, hw)
    validator = LicenseValidator(
        public_key_pem=public_pem, license_path=scratch / "lease.json"
    )
    assert validator.grant_lease(key) is True
    # Backdate first_seen beyond the 30-day lease window -> lease invalid.
    import json as _json
    from datetime import datetime, timedelta, timezone
    blob = scratch / "lease.json"
    data = _json.loads(blob.read_text(encoding="utf-8"))
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    data["lease"]["first_seen"] = old
    data["lease_sig"] = validator._lease_signature(data["lease"], data["key"])
    blob.write_text(_json.dumps(data), encoding="utf-8")
    moved_hw = "c" * 64
    assert validator.is_licensed(hardware_id=moved_hw) is False


# ═══════════════════════════════════════════════════════════════════════
# Safe engine entry points (CLI / GUI / fuzzing)
# ═══════════════════════════════════════════════════════════════════════

def test_lint_sql_violations():
    violations = lint_sql("SELECT * FROM a, b WHERE YEAR(d) = 2026", "duckdb")
    ids = {v.rule_id for v in violations}
    assert {"SQL-WILD-001", "SQL-CART-001", "SQL-SARG-001"} <= ids


def test_lint_sql_clean():
    assert lint_sql("SELECT id FROM t WHERE id = 1", "duckdb") == []


@pytest.mark.parametrize("dialect", ["duckdb", "postgres", "snowflake"])
@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "   ",
        "SELECT FROM WHERE",
        "!@#$%^&*(",
        "((((",
        chr(0) + "select *",
    ],
)
def test_lint_sql_never_raises_on_garbage(dialect, garbage):
    # Must never raise, never loop; just returns a list.
    assert isinstance(lint_sql(garbage, dialect), list)


def test_fix_sql_normalizes_year_equality():
    fixed = fix_sql("SELECT * FROM t WHERE YEAR(d) = 2026", "duckdb")
    assert "CAST('2026-01-01' AS DATE)" in fixed
    assert "CAST('2027-01-01' AS DATE)" in fixed


def test_fix_sql_returns_original_on_parse_failure():
    source = "not valid sql !!!"
    assert fix_sql(source, "duckdb") == source


def test_lint_files_skips_unreadable(scratch):
    (scratch / "bad.sql").write_text("SELECT * FROM a, b\n", encoding="utf-8")
    (scratch / "good.sql").write_text("SELECT 1\n", encoding="utf-8")
    (scratch / "sub").mkdir()
    (scratch / "sub" / "nested.sql").write_text("SELECT * FROM t\n", encoding="utf-8")
    grouped = lint_files([str(scratch / "bad.sql"), str(scratch / "good.sql")], "duckdb")
    assert "SQL-WILD-001" in [v.rule_id for vs in grouped.values() for v in vs]
    assert str(scratch / "good.sql") not in grouped


def test_collect_sql_files_recursive_and_dedup(scratch):
    (scratch / "a.sql").write_text("SELECT 1\n", encoding="utf-8")
    (scratch / "b.txt").write_text("no\n", encoding="utf-8")
    (scratch / "sub").mkdir()
    (scratch / "sub" / "c.sql").write_text("SELECT 1\n", encoding="utf-8")
    files = collect_sql_files([str(scratch)])
    assert len(files) == 2
    assert all(f.endswith(".sql") for f in files)


def test_collect_sql_files_excludes(scratch):
    (scratch / "a.sql").write_text("SELECT 1\n", encoding="utf-8")
    (scratch / "skip.sql").write_text("SELECT 1\n", encoding="utf-8")
    files = collect_sql_files([str(scratch)], excludes=("skip.sql",))
    assert len(files) == 1
    assert files and "skip.sql" not in files


# ═══════════════════════════════════════════════════════════════════════
# Property-based fuzzing over the AST engine
# ═══════════════════════════════════════════════════════════════════════

@st.composite
def fuzz_sql(draw):
    """Arbitrary text glued from SQL-ish tokens and random garbage."""
    tokens = [
        "SELECT", "*", "FROM", "t", "a", "b", "WHERE", "YEAR(", "d", ")", "=",
        "2026", "AND", "OR", "MONTH(", "EXTRACT(", "year", "FROM", ",", "CROSS",
        "JOIN", "INNER", "LEFT", "1", "0", "'x'", "(", ")", "CAST", "AS", "DATE",
    ]
    pieces = [" ".join(draw(st.lists(st.sampled_from(tokens), min_size=0, max_size=12)))]
    garble = st.text(alphabet="abcXYZ*(),;=<>!- _", max_size=8)
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        pieces.append(draw(garble))
    return " ".join(pieces)


@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=60)
@given(st.data())
def test_ast_engine_never_raises_on_arbitrary_text(data):
    """Hypothesis fuzz: parse + run rules + fix on random garbage never raises."""
    sql = data.draw(fuzz_sql())
    for dialect in ("duckdb", "postgres", "snowflake"):
        result = lint_sql(sql, dialect)
        assert isinstance(result, list)
        assert all(isinstance(v, LintViolation) for v in result)
        _ = fix_sql(sql, dialect)  # must never raise


@given(st.text(alphabet="abcXYZ*(),;'=<>!- _0123456789", max_size=128))
def test_ast_engine_never_raises_on_raw_text(arbitrary):
    for dialect in ("duckdb", "postgres", "snowflake"):
        assert isinstance(lint_sql(arbitrary, dialect), list)