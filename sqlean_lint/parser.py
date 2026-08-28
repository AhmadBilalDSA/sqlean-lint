"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Robust, 100% local AST parsing built on sqlglot.

Everything in this module executes in-process. There are no network calls of
any kind; dialect support is provided entirely by sqlglot's local grammars."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple, Union

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel

from .types import LintContext, RuleViolation, Severity

SUPPORTED_DIALECTS: Tuple[str, ...] = (
    "snowflake",
    "bigquery",
    "postgres",
    "duckdb",
    "databricks",
    "mysql",
    "sqlite",
    "tsql",
)

_DIALECT_ALIASES = {
    "postgresql": "postgres",
    "pg": "postgres",
    "postgres15": "postgres",
    "bq": "bigquery",
    "mssql": "tsql",
    "t-sql": "tsql",
    "transactsql": "tsql",
    "spark": "databricks",
    "sparksql": "databricks",
}

_LINE_COL_RE = re.compile(r"Line\s+(\d+)[,\s]+Col(?:umn)?:?\s*(\d+)", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_dialect(dialect: str) -> str:
    """Validate/normalize a user-supplied dialect name."""
    name = (dialect or "").strip().lower()
    name = _DIALECT_ALIASES.get(name, name)
    if name not in SUPPORTED_DIALECTS:
        raise ValueError(
            f"Unsupported dialect {dialect!r}; choose from: {', '.join(SUPPORTED_DIALECTS)}"
        )
    return name


def _parse_options() -> dict:
    # RAISE forces sqlglot to surface structural errors instead of silently
    # degrading to Command nodes.
    return {"error_level": ErrorLevel.RAISE}


def parse_one(sql: str, dialect: str) -> exp.Expression:
    """Parse a single statement; raises on syntax errors (never networks)."""
    tree = sqlglot.parse_one(sql, read=normalize_dialect(dialect), **_parse_options())
    if tree is None:
        raise ValueError("Empty SQL input: nothing to parse.")
    return tree


def parse_script(sql: str, dialect: str) -> List[exp.Expression]:
    """Parse a multi-statement script into a list of AST roots."""
    statements = sqlglot.parse(sql, read=normalize_dialect(dialect), **_parse_options())
    return [statement for statement in statements if statement is not None]


@dataclass
class SafeParseResult:
    """Structured outcome of :func:`safe_parse` (never raises for bad SQL)."""

    ok: bool
    statements: List[exp.Expression] = field(default_factory=list)
    error_message: Optional[str] = None
    error_line: int = 1
    error_col: int = 1


def safe_parse(sql: str, dialect: str) -> SafeParseResult:
    """Parse defensively, converting any parser failure into structured data.

    Line/column extraction prefers sqlglot's own attributes, then the
    ``Line N, Col M`` text embedded in the error message, and finally
    defaults to (1, 1).
    """
    try:
        return SafeParseResult(ok=True, statements=parse_script(sql, dialect))
    except Exception as err:  # noqa: BLE001 - parser crashes become findings
        line = getattr(err, "line", None)
        col = getattr(err, "col", None)
        if not isinstance(line, int) or line <= 0:
            match = _LINE_COL_RE.search(str(err))
            if match:
                line, col = int(match.group(1)), int(match.group(2))
            else:
                line, col = 1, 1
        if not isinstance(col, int) or col <= 0:
            col = 1
        first_line = str(err).strip().splitlines() or ["unknown parse failure"]
        return SafeParseResult(
            ok=False,
            error_message=f"{type(err).__name__}: {first_line[0]}",
            error_line=line,
            error_col=col,
        )


def parser_failure_violation(parsed: SafeParseResult, context: LintContext) -> RuleViolation:
    """Build the PARSER-001 violation describing an unparseable source."""
    detail = parsed.error_message or "unknown parse failure"
    snippet = context.raw_sql.strip()
    if len(snippet) > 72:
        snippet = snippet[:72] + "..."
    return RuleViolation(
        rule_id="PARSER-001",
        severity=Severity.HIGH,
        title="SQL failed to parse",
        message=(
            f"The statement could not be parsed with dialect "
            f"'{context.dialect}': {detail}"
        ),
        line=parsed.error_line,
        col=parsed.error_col,
        snippet=snippet,
        suggested_fix="Fix the syntax error before performance rules can run.",
    )


# --------------------------------------------------------------------------
# Shared AST utilities used by rules and the optimizer.
# --------------------------------------------------------------------------

def node_snippet(node: exp.Expression, dialect: Optional[str] = None, limit: int = 72) -> str:
    """Render a compact single-line snippet for a node (ASCII ellipsis)."""
    try:
        text = node.sql(dialect=dialect) if dialect else node.sql()
    except Exception:  # noqa: BLE001 - rendering must never crash a rule
        try:
            text = node.sql()
        except Exception:  # noqa: BLE001
            text = ""
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def node_position(node: exp.Expression, context: LintContext) -> Tuple[int, int]:
    """Best-effort exact (line, col) for a node within ``context.raw_sql``.

    sqlglot attaches token positions to leaf nodes via ``meta``; container
    nodes inherit the minimum position of their subtree. When no metadata is
    available we fall back to locating the rendered snippet in the source.
    """
    best: Optional[Tuple[int, int]] = None
    try:
        for sub in node.walk():
            meta = getattr(sub, "meta", None) or {}
            line = meta.get("line")
            if isinstance(line, int) and line > 0:
                col = meta.get("col")
                candidate = (line, col if isinstance(col, int) else 1)
                if best is None or candidate < best:
                    best = candidate
    except Exception:  # noqa: BLE001 - position hints are best effort
        best = None
    if best is not None:
        return best

    snippet = node_snippet(node, context.dialect)[:48]
    idx = context.raw_sql.find(snippet) if snippet else -1
    if idx >= 0:
        line = context.raw_sql.count("\n", 0, idx) + 1
        col = idx - (context.raw_sql.rfind("\n", 0, idx) + 1) + 1
        return line, col
    return 1, 1


def nested_context(select: exp.Select) -> Optional[str]:
    """Classify a SELECT as living inside a CTE ('cte') or subquery ('subquery')."""
    parent = select.parent
    while parent is not None:
        if isinstance(parent, exp.CTE):
            return "cte"
        if isinstance(parent, exp.Subquery):
            return "subquery"
        parent = parent.parent
    return None


def get_with_key(select: exp.Select) -> Optional[str]:
    """Return the arg key holding CTEs across sqlglot versions ('with_')."""
    for key in ("with_", "with"):
        if select.arg_types.get(key):
            return key
    return None


def find_from(select: Union[exp.Select, exp.Expression]) -> Optional[exp.From]:
    """Locate the FROM clause regardless of sqlglot arg naming."""
    from_clause = select.args.get("from_") or select.args.get("from")
    if isinstance(from_clause, exp.From):
        return from_clause
    found = select.find(exp.From)
    return found if isinstance(found, exp.From) else None


def is_iso_date(text: str) -> bool:
    return bool(_ISO_DATE_RE.match(text or ""))
