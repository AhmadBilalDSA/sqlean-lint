"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Pluggable rule registry for sqlean-lint."""
from __future__ import annotations

from typing import List

from .base import BaseRule
from .cartesian import CartesianJoinRule
from .join_type_cast import JoinTypeCastRule
from .leading_wildcard import LeadingWildcardRule
from .null_in_subquery import NullInSubqueryRule
from .pro import (
    DEFAULT_RULES,
    PRO_RULE_CLASSES,
    SARGableDateRule,
    CartesianProductRule,
    WildcardSelectRule,
    LintViolation,
    apply_fixes,
    ast_cache_size,
    cached_parse,
    clear_ast_cache,
    line_col_of,
    render,
    run_all,
    lint_sql,
    fix_sql,
    lint_files,
    collect_sql_files,
)
from .sargable import NON_SARGABLE_FUNCTIONS, SargableRule
from .select_star import SelectStarRule
from .unbounded_sort import UnboundedSortRule

__all__ = [
    "BaseRule",
    "CartesianJoinRule",
    "JoinTypeCastRule",
    "LeadingWildcardRule",
    "NullInSubqueryRule",
    "SargableRule",
    "SelectStarRule",
    "UnboundedSortRule",
    "NON_SARGABLE_FUNCTIONS",
    "get_default_rules",
    "RULE_CLASSES",
    # sqlean-lint Pro rule engine
    "LintViolation",
    "WildcardSelectRule",
    "CartesianProductRule",
    "SARGableDateRule",
    "DEFAULT_RULES",
    "PRO_RULE_CLASSES",
    "cached_parse",
    "clear_ast_cache",
    "ast_cache_size",
    "run_all",
    "apply_fixes",
    "line_col_of",
    "render",
    "lint_sql",
    "fix_sql",
    "lint_files",
    "collect_sql_files",
]

RULE_CLASSES = (
    CartesianJoinRule,
    SargableRule,
    NullInSubqueryRule,
    UnboundedSortRule,
    SelectStarRule,
    JoinTypeCastRule,
    LeadingWildcardRule,
)


def get_default_rules() -> List[BaseRule]:
    """Fresh rule instances per run (rules keep no cross-run state)."""
    return [rule_class() for rule_class in RULE_CLASSES]
