"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

sqlean-lint: Semantic SQL Performance Linter, AST Auto-Optimizer and Cost Gate.

Privacy charter (enforced by design):
    * 100% local AST parsing, cost estimation and optimization (sqlglot + DuckDB, in-process).
    * Zero network calls, zero telemetry, zero external asset fetches.
    * Air-gapped single-file HTML artifacts (inline CSS/JS/SVG only).
    * MCP transport strictly over stdio; no local network ports are opened."""
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
from .cost_model import estimate_cost, estimate_cloud_costs
from .engine import lint_dbt, lint_paths, lint_query, lint_source
from .reporter import to_html, to_json, to_markdown, to_terminal

# Phase 2 surface (additive; nothing existing moved or renamed)
from .autofix import FixResult, Patch, fix_source, unified_diff, verify_equivalence
from .transpiler import (
    TRANSPILE_DIALECTS,
    TranspileError,
    df_to_sql,
    json_to_sql,
    preview_query,
    sql_to_format,
    transpile_sql,
)
from .ai import AISuggestion, optimize_with_ai
from .updater import self_update
from .profiler import ProfileReport, peak_rss_mb
from .dag import build_dag
from .security import ORIGIN_SHA256, origin_manifest, verify_origin, watermark_line

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
    # Phase 2
    "lint_dbt",
    "estimate_cloud_costs",
    "FixResult",
    "Patch",
    "fix_source",
    "unified_diff",
    "verify_equivalence",
    "TRANSPILE_DIALECTS",
    "TranspileError",
    "transpile_sql",
    "json_to_sql",
    "df_to_sql",
    "sql_to_format",
    "preview_query",
    "AISuggestion",
    "optimize_with_ai",
    "self_update",
    "ProfileReport",
    "peak_rss_mb",
    "build_dag",
    "ORIGIN_SHA256",
    "origin_manifest",
    "verify_origin",
    "watermark_line",
]
