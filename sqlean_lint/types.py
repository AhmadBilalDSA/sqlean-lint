"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Domain dataclasses and enumerations shared across sqlean-lint."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

SEVERITY_ORDER: Dict[str, int] = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


class Severity(str, Enum):
    """Violation severity ladder used for filtering and quality gates."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RiskLevel(str, Enum):
    """Banded interpretation of the 0-100 cost-model risk score."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def severity_rank(severity: "Severity | str") -> int:
    """Numeric rank for a severity (LOW=1 ... CRITICAL=4). Unknown -> 1."""
    value = severity.value if isinstance(severity, Severity) else str(severity).upper()
    return SEVERITY_ORDER.get(value, 1)


def coerce_severity(value: str) -> Severity:
    """Parse user-supplied severity text, raising ValueError with valid options."""
    name = (value or "").strip().upper()
    try:
        return Severity(name)
    except ValueError:
        valid = ", ".join(s.value for s in Severity)
        raise ValueError(f"Invalid severity {value!r}; expected one of: {valid}") from None


@dataclass
class RuleViolation:
    """A single lint finding anchored to an exact source location."""

    rule_id: str
    severity: Severity
    title: str
    message: str
    line: int = 1
    col: int = 1
    snippet: str = ""
    suggested_fix: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value if isinstance(self.severity, Severity) else str(self.severity),
            "title": self.title,
            "message": self.message,
            "line": self.line,
            "col": self.col,
            "snippet": self.snippet,
            "suggested_fix": self.suggested_fix,
        }


@dataclass
class CostEstimate:
    """Deterministic plan-risk estimate produced by the cost model."""

    risk_score: int = 0
    risk_level: RiskLevel = RiskLevel.LOW
    scan_complexity: str = "O(1)"
    join_risk: int = 0
    sort_risk: int = 0
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk_score": self.risk_score,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else str(self.risk_level),
            "scan_complexity": self.scan_complexity,
            "join_risk": self.join_risk,
            "sort_risk": self.sort_risk,
            "explanation": self.explanation,
        }


@dataclass
class LintContext:
    """Ambient information handed to every rule during a check pass."""

    dialect: str = "duckdb"
    raw_sql: str = ""
    file_path: str = "<query>"


@dataclass
class LintResult:
    """Full outcome of linting one SQL source (query or file)."""

    file_path: str
    raw_sql: str
    violations: List[RuleViolation] = field(default_factory=list)
    cost: CostEstimate = field(default_factory=CostEstimate)
    optimized_sql: Optional[str] = None
    duration_ms: float = 0.0

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def has_critical(self) -> bool:
        return any(v.severity == Severity.CRITICAL for v in self.violations)

    @property
    def max_severity(self) -> Optional[Severity]:
        if not self.violations:
            return None
        return max(self.violations, key=lambda v: severity_rank(v.severity)).severity

    def to_dict(
        self,
        include_sql: bool = True,
        include_duration: bool = True,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "file_path": self.file_path,
            "violations": [v.to_dict() for v in self.violations],
            "cost": self.cost.to_dict(),
            "optimized_sql": self.optimized_sql,
        }
        if include_duration:
            payload["duration_ms"] = round(self.duration_ms, 3)
        if include_sql:
            payload["raw_sql"] = self.raw_sql
        return payload
