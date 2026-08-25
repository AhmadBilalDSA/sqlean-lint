"""Abstract base class every sqlean-lint rule implements."""
from __future__ import annotations

import abc
from typing import List, Optional

from sqlglot import exp

from ..parser import node_position, node_snippet
from ..types import LintContext, RuleViolation, Severity


class BaseRule(abc.ABC):
    """Contract for a pluggable semantic rule.

    Rules are pure inspectors: they receive one statement AST plus ambient
    context and return violations. They must never mutate the AST, touch the
    network, or raise on unexpected shapes (guard internally instead).
    """

    rule_id: str = "SQL-000-000"
    severity: Severity = Severity.MEDIUM
    title: str = "Base rule"
    description: str = ""

    @abc.abstractmethod
    def check(self, expression: exp.Expression, context: LintContext) -> List[RuleViolation]:
        """Inspect one statement AST and return all matching violations."""

    def make_violation(
        self,
        node: exp.Expression,
        context: LintContext,
        message: str,
        suggested_fix: Optional[str] = None,
        snippet: Optional[str] = None,
        anchor: Optional[exp.Expression] = None,
    ) -> RuleViolation:
        """Convenience builder producing a fully positioned violation."""
        target = anchor if anchor is not None else node
        line, col = node_position(target, context)
        return RuleViolation(
            rule_id=self.rule_id,
            severity=self.severity,
            title=self.title,
            message=message,
            line=line,
            col=col,
            snippet=node_snippet(node, context.dialect) if snippet is None else snippet,
            suggested_fix=suggested_fix,
        )
