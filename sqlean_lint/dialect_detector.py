"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

High-speed heuristic and trial-based SQL dialect detection utility.

Analyses vendor-specific syntax signatures (bracketed identifiers, proprietary
functions, system metadata patterns) to identify the most probable SQL dialect
before falling back to trial-parsing against every candidate grammar.

All public functions are guaranteed never to raise on arbitrary input.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel

# ── Dialect signature regex patterns ────────────────────────────────────
# Each tuple is (dialect_name, compiled_regex). Order matters: the first
# match wins during heuristic scanning.
DIALECT_SIGNATURES: List[Tuple[str, re.Pattern[str]]] = [
    ("tsql", re.compile(
        r"(\[[\w\s]+\]|SET\s+IDENTITY_INSERT|GETDATE\(\)|@@\w+"
        r"|sys\.\w+|N'[^']*')",
        re.IGNORECASE,
    )),
    ("postgres", re.compile(
        r"(::[a-zA-Z_]+|ILIKE|RETURNING\s|NOW\(\)|GENERATE_SERIES)",
        re.IGNORECASE,
    )),
    ("snowflake", re.compile(
        r"(\bQUALIFY\b|\bIFF\b|\bFLATTEN\b|STAGE\s*=\s*|METADATA\$)",
        re.IGNORECASE,
    )),
    ("mysql", re.compile(
        r"(`[^`]+`|\bAUTO_INCREMENT\b|\bLIMIT\s+\d+,\s*\d+|\bNOW\(\)\b)",
        re.IGNORECASE,
    )),
    ("duckdb", re.compile(
        r"(read_csv(_auto)?|read_parquet|INSTALL\s+\w+|LOAD\s+\w+)",
        re.IGNORECASE,
    )),
    ("bigquery", re.compile(
        r"(`[\w-]+\.[\w-]+\.[\w-]+`|SAFE_CAST|STRUCT|STRING_AGG)",
        re.IGNORECASE,
    )),
]

# Dialects to try during trial-parse fallback, in priority order.
# ``None`` represents generic ANSI SQL (no dialect override).
CANDIDATE_FALLBACK_ORDER: List[Optional[str]] = [
    "duckdb", "tsql", "postgres", "snowflake", "mysql",
    "sqlite", "bigquery", "databricks", None,
]


def detect_dialect(sql: str) -> str:
    """Analyse query syntax signatures to identify the most probable SQL dialect.

    This function is **guaranteed never to raise**. It returns ``"duckdb"``
    as the ultimate fallback when no signature or trial-parse succeeds.

    Parameters
    ----------
    sql:
        Raw SQL text of any length.  Only the first 10 000 characters are
        inspected for performance.

    Returns
    -------
    str
        One of the dialect names recognised by sqlglot.
    """
    if not sql or not sql.strip():
        return "duckdb"

    snippet = sql[:10_000]

    # 1. Signature-based heuristic matching (instant, no parsing).
    for dialect, pattern in DIALECT_SIGNATURES:
        if pattern.search(snippet):
            return dialect

    # 2. Trial parse validation against candidate dialects.
    for dialect in CANDIDATE_FALLBACK_ORDER:
        try:
            parsed = sqlglot.parse(
                snippet, read=dialect, error_level=ErrorLevel.IGNORE,
            )
            if parsed and any(node is not None for node in parsed):
                return dialect or "duckdb"
        except Exception:  # noqa: BLE001 – trial parse is best-effort
            continue

    return "duckdb"


def safe_parse_sql(
    sql: str,
    preferred_dialect: Optional[str] = None,
) -> Tuple[List[exp.Expression], str]:
    """Parse SQL text without raising fatal exceptions.

    Tries the *preferred* dialect first, then the auto-detected dialect, then
    every candidate in ``CANDIDATE_FALLBACK_ORDER``.  ``ErrorLevel.IGNORE``
    lets sqlglot produce partial AST nodes (including ``Command`` nodes for
    unsupported DDL) instead of raising.

    Parameters
    ----------
    sql:
        Raw SQL text.
    preferred_dialect:
        User-configured dialect (e.g. from the Settings modal).  When
        ``None`` or ``"auto"``, auto-detection is used exclusively.

    Returns
    -------
    tuple[list[Expression], str]
        ``(nodes, resolved_dialect)`` – *nodes* may be empty if no dialect
        could parse the input at all.  *resolved_dialect* is the dialect
        that produced the (possibly partial) AST.
    """
    if not sql or not sql.strip():
        return [], preferred_dialect or "duckdb"

    # Build the ordered list of dialects to attempt.
    detected = detect_dialect(sql)
    dialects_to_try: List[Optional[str]] = []

    if preferred_dialect and preferred_dialect != "auto":
        dialects_to_try.append(preferred_dialect)
    if detected not in dialects_to_try:
        dialects_to_try.append(detected)
    for cand in CANDIDATE_FALLBACK_ORDER:
        if cand not in dialects_to_try:
            dialects_to_try.append(cand)

    # Phase 1: clean parse with ErrorLevel.IGNORE.
    for dialect in dialects_to_try:
        try:
            nodes = sqlglot.parse(
                sql, read=dialect, error_level=ErrorLevel.IGNORE,
            )
            if nodes and any(n is not None for n in nodes):
                return (
                    [n for n in nodes if n is not None],
                    dialect or "duckdb",
                )
        except Exception:  # noqa: BLE001 – fallback is best-effort
            continue

    # Phase 2: last-resort permissive T-SQL parse (most permissive grammar).
    try:
        nodes = sqlglot.parse(
            sql, read="tsql", error_level=ErrorLevel.IGNORE,
        )
        return (
            [n for n in nodes if n is not None],
            "tsql",
        )
    except Exception:  # noqa: BLE001
        return [], preferred_dialect or "duckdb"
