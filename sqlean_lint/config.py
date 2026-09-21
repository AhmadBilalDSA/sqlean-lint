"""Copyright (c) 2026 SQLean Engineering. All Rights Reserved.

LinterConfig: runtime configuration for the sqlean-lint Pro desktop edition.

Holds the selected SQL dialect, path-exclusion patterns and per-rule severity
overrides. Configuration may be supplied in-memory, from keyword arguments,
from a TOML file (via ``tomli``) or from an environment-set config path.
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - py<3.11
    import tomli as _toml

from .types import Severity, coerce_severity

SUPPORTED_DIALECTS: tuple = (
    "duckdb", "postgres", "snowflake", "tsql", "mysql",
    "bigquery", "sqlite", "databricks",
)

_DEFAULT_EXCLUDES: tuple = (
    "**/node_modules/**",
    "**/.git/**",
    "**/target/**",
    "**/.dbt/**",
    "**/build/**",
    "**/dist/**",
)


@dataclass
class LinterConfig:
    """Encapsulates every user-tunable knob of the desktop linter."""

    dialect: str = "duckdb"
    excludes: List[str] = field(default_factory=lambda: list(_DEFAULT_EXCLUDES))
    severities: Dict[str, str] = field(default_factory=dict)
    # Rule enabled/disabled switch. Keys are rule IDs, values are booleans.
    enabled_rules: Dict[str, bool] = field(default_factory=dict)
    # UI preferences are stored alongside lint config so they survive restarts.
    theme: str = "dark"  # "dark" | "light"

    def __post_init__(self) -> None:
        if self.dialect not in SUPPORTED_DIALECTS:
            raise ValueError(
                f"Unsupported dialect {self.dialect!r}; "
                f"choose from {', '.join(SUPPORTED_DIALECTS)}"
            )

    # ── Lookup helpers ─────────────────────────────────────────────────

    def is_rule_enabled(self, rule_id: str) -> bool:
        """Return *True* when the rule is not explicitly disabled."""
        return self.enabled_rules.get(rule_id, True)

    def severity_for(self, rule_id: str) -> Severity:
        """Return the configured severity for a rule (or MEDIUM default)."""
        override = self.severities.get(rule_id)
        if override:
            return coerce_severity(override)
        return Severity.MEDIUM

    def is_excluded(self, path: str) -> bool:
        """Return *True* when *path* matches any configured exclusion glob."""
        normalized = Path(path).as_posix()
        for pattern in self.excludes:
            if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(
                Path(path).name, pattern
            ):
                return True
        return False

    # ── Serialization / persistence ────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dialect": self.dialect,
            "excludes": list(self.excludes),
            "severities": dict(self.severities),
            "enabled_rules": dict(self.enabled_rules),
            "theme": self.theme,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "LinterConfig":
        data = data or {}
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        return cls(**clean)

    @classmethod
    def from_toml(cls, path) -> "LinterConfig":
        """Load ``LinterConfig`` from a TOML file via tomli/tomllib."""
        source = _toml.loads(Path(path).read_text(encoding="utf-8"))
        config_section = source.get("linter", source)
        return cls.from_dict(config_section)

    @classmethod
    def from_env_or_default(cls) -> "LinterConfig":
        """Read ``SQLEAN_PRO_CONFIG`` TOML path if set, otherwise defaults."""
        configured = os.environ.get("SQLEAN_PRO_CONFIG")
        if configured and Path(configured).is_file():
            return cls.from_toml(configured)
        return cls()
