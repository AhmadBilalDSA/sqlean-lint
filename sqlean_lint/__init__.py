"""sqlean-lint: Semantic SQL Performance Linter, AST Auto-Optimizer and Cost Gate.

Privacy charter (enforced by design):
    * 100% local AST parsing, cost estimation and optimization (sqlglot + DuckDB, in-process).
    * Zero network calls, zero telemetry, zero external asset fetches.
    * Air-gapped single-file HTML artifacts (inline CSS/JS/SVG only).
    * MCP transport strictly over stdio; no local network ports are opened.
"""
from __future__ import annotations

from ._version import __version__
from .types import (
    CostEstimate,
    LintContext,
    LintResult,
    RiskLevel,
    RuleViolation,
    Severity,
    coerce_severity,
    severity_rank,
)
from .parser import (
    SUPPORTED_DIALECTS,
    normalize_dialect,
    nested_context,
    node_position,
    node_snippet,
    parse_one,
    parse_script,
    safe_parse,
)
from .rules import get_default_rules
from .optimizer import Transformation, optimize_expression, optimize_sql
from .cost_model import estimate_cost
from .engine import lint_paths, lint_query, lint_source
from .reporter import to_html, to_json, to_markdown, to_terminal

__all__ = [
    "__version__",
    "CostEstimate",
    "LintContext",
    "LintResult",
    "RiskLevel",
    "RuleViolation",
    "Severity",
    "coerce_severity",
    "severity_rank",
    "SUPPORTED_DIALECTS",
    "normalize_dialect",
    "nested_context",
    "node_position",
    "node_snippet",
    "parse_one",
    "parse_script",
    "safe_parse",
    "get_default_rules",
    "Transformation",
    "optimize_expression",
    "optimize_sql",
    "estimate_cost",
    "lint_paths",
    "lint_query",
    "lint_source",
    "to_html",
    "to_json",
    "to_markdown",
    "to_terminal",
]
