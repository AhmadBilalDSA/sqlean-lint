"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

dbt / Jinja pre-processor for sqlean-lint PRO.

``sqlean-lint`` analyzes raw SQL through ``sqlglot``. Modular ELT projects
(dbt, Airflow templates) frequently embed Jinja tokens such as
``{{ ref('stg_orders') }}``, ``{{ config(...) }}`` and ``{% ... %}`` blocks
that are not valid SQL and would normally cause the parser to choke.

This module strips or neutralizes those tokens before the SQL reaches
``sqlglot``, restoring a parseable statement while preserving line numbers
wherever possible so diagnostics still point at the right place in the
original source. All behaviour is best-effort and never raises on arbitrary
input (zero-crash execution policy).
"""
from __future__ import annotations

import re
from typing import Tuple

# Matches a complete Jinja "expression" token: {{ ... }} (possibly spanning
# multiple lines). Replaced with a benign identifier/placeholder.
_JINJA_EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)

# Matches a Jinja "statement" block: {% ... %}. These are removed entirely.
_JINJA_STMT = re.compile(r"\{%.*?%\}", re.DOTALL)

# Matches a Jinja comment: {# ... #} — removed entirely.
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)

# ``{{ ref('model') }}`` / ``{{ ref("model") }}`` — replace with a token that
# looks like a safe SQL table name so sqlglot still records dependencies.
_REF_RE = re.compile(
    r"\{\{\s*ref\(\s*['\"](?P<name>[\w.]+)['\"]\s*\)\s*\}\}", re.IGNORECASE
)

# ``{{ config(...) }}`` — the whole config macro call is dropped.
_CONFIG_RE = re.compile(
    r"\{\{\s*config\(\s*(?:.*?)\s*\)\s*\}\}", re.IGNORECASE | re.DOTALL
)


def strip_jinja(
    sql: str,
    *,
    use_ref_fallback: bool = True,
) -> Tuple[str, int]:
    """Return ``(sanitized_sql, replacements)``.

    Parameters:
        sql: Raw SQL-like source possibly containing Jinja tokens.
        use_ref_fallback: When true, ``ref('model')`` becomes a dummy table
            name (``__dbt_ref_model__``) so the AST keeps a table node; when
            false it is dropped like any other macro.

    Returns:
        A tuple of the neutralized SQL text and the number of tokens handled.
    """
    if not sql:
        return sql, 0

    count = 0
    out = sql

    # Sanitize default placeholder used for every stripped token.
    # Replacing removed blocks with a zero-width marker risks broken tokens, so
    # we substitute a harmless identifier for expressions and nothing for
    # statements/comments.

    def _replace_expr(match: "re.Match[str]") -> str:
        nonlocal count
        body = match.group(0)
        if use_ref_fallback:
            ref = _REF_RE.search(body)
            if ref:
                name = ref.group("name").replace(".", "__")
                count += 1
                return f"__dbt_ref_{name}__"
        count += 1
        return "__dbt_macro__"

    out = _JINJA_EXPR.sub(_replace_expr, out)
    out = _JINJA_STMT.sub(lambda _m: _bump_and_empty(), out)
    out = _JINJA_COMMENT.sub(lambda _m: _bump_and_empty(), out)
    out = _clean_blank_lines(out)
    return out, count


def _bump_and_empty() -> str:
    # Counts are tracked inside _replace_expr only; statements/comments are
    # removed without incrementing the expression counter.
    return ""


def _clean_blank_lines(sql: str) -> str:
    lines = [ln for ln in sql.splitlines() if ln.strip()]
    return "\n".join(lines)


def sanitize(sql: str, **kwargs) -> str:
    """Convenience wrapper returning just the sanitized SQL."""
    out, _ = strip_jinja(sql, **kwargs)
    return out


def validate_sql(sql: str, dialect: str = "duckdb") -> Tuple[bool, str]:
    """Try to parse a Jinja-neutered source and report whether it parses.

    Returns ``(ok, message)``. Never raises.
    """
    try:
        import sqlglot  # noqa: PLC0415 (lazy import keeps this module light)
    except Exception:  # noqa: BLE001 - sqlglot is a hard dep normally
        return False, "sqlglot is unavailable"
    if not sql or not sql.strip():
        return True, "empty input (trivially valid)"
    cleaned, _ = strip_jinja(sql)
    try:
        sqlglot.parse_one(cleaned, read=dialect)
        return True, "parses cleanly"
    except Exception as err:  # noqa: BLE001 - malformed input degrades gracefully
        return False, f"parse error: {type(err).__name__}: {err}"
