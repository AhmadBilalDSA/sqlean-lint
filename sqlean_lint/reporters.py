"""Copyright (c) 2026 SQLean Engineering. All Rights Reserved.

Reporting backends for headless CI/CD ingestion.

``to_sarif`` emits a strict **OASIS SARIF v2.1.0** log (``version``:
``"2.1.0"``, a ``$schema`` referencing the 2.1.0 schema URL) that any SARIF
viewer (GitHub code scanning, Azure DevOps, SARIF Viewer for VS Code) can
consume directly.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping

from .rules import DEFAULT_RULES, LintViolation

SARIF_SCHEMA_URL = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/cos02/schemas/sarif-schema-2.1.0.json"
)
SARIF_VERSION = "2.1.0"

_TOOL_NAME = "sqlean-lint-pro"

_SEVERITY_TO_LEVEL = {
    "CRITICAL": "error",
    "HIGH": "error",
    "MEDIUM": "warning",
    "LOW": "note",
}


def _rule_descriptions() -> Dict[str, Dict[str, Any]]:
    """Index the Pro rules by id for SARIF ``rules`` metadata."""
    return {
        rule.rule_id: {
            "id": rule.rule_id,
            "name": rule.title,
            "shortDescription": {"text": rule.title},
            "fullDescription": {"text": rule.description},
            "properties": {"severity": rule.severity},
        }
        for rule in DEFAULT_RULES
    }


def to_sarif(file_violations: Mapping[str, Iterable[LintViolation]]) -> str:
    """Serialize violations grouped by file path into a SARIF v2.1.0 log.

    Parameters
    ----------
    file_violations:
        Mapping of ``file path -> iterable of LintViolation``.
    """
    rule_index = _rule_descriptions()
    results: List[Dict[str, Any]] = []
    for file_path, violations in file_violations.items():
        for violation in violations:
            level = _SEVERITY_TO_LEVEL.get(violation.severity.upper(), "warning")
            region: Dict[str, int] = {
                "startLine": violation.line,
                "startColumn": violation.col,
            }
            message = violation.message
            snippet = violation.snippet
            result: Dict[str, Any] = {
                "ruleId": violation.rule_id,
                "level": level,
                "message": {"text": message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": file_path or "<query>"},
                            "region": region,
                        }
                    }
                ],
                "partialFingerprints": {
                    "primaryLocationLineHash": f"{violation.rule_id}:{file_path}:{violation.line}:{violation.col}"
                },
            }
            if snippet:
                result["locations"][0]["physicalLocation"]["region"]["snippet"] = {
                    "text": snippet
                }
            results.append(result)

    sarif_log: Dict[str, Any] = {
        "$schema": SARIF_SCHEMA_URL,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": _TOOL_NAME,
                        "informationUri": "https://sqlean-lint.dev",
                        "semanticVersion": "1.0.0",
                        "rules": list(rule_index.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif_log, indent=2, ensure_ascii=False)


def to_text(file_violations: Mapping[str, Iterable[LintViolation]]) -> str:
    """Human-readable text report intended for terminals."""
    lines: List[str] = []
    total = 0
    for file_path, violations in file_violations.items():
        for violation in violations:
            total += 1
            lines.append(
                f"{file_path}:{violation.line}:{violation.col}: "
                f"{violation.severity.upper()}: {violation.rule_id} {violation.message}"
            )
    header = "SQLEAN-LINT PRO CHECK"
    if total == 0:
        return f"{header}\nNo violations found."
    return "\n".join([header, *lines, f"{total} finding(s)."])


def to_json(file_violations: Mapping[str, Iterable[LintViolation]]) -> str:
    """Compact machine-readable JSON report of all findings."""
    payload: Dict[str, Any] = {
        "tool": _TOOL_NAME,
        "summary": {
            "total_files": len(file_violations),
            "total_violations": sum(len(list(vs)) for vs in file_violations.values()),
        },
        "files": {
            path: [v.to_dict() for v in violations]
            for path, violations in file_violations.items()
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render(
    file_violations: Mapping[str, Iterable[LintViolation]],
    fmt: str = "text",
) -> str:
    """Dispatch to the requested output format (text|json|sarif)."""
    normalized = fmt.strip().lower()
    if normalized == "json":
        return to_json(file_violations)
    if normalized == "sarif":
        return to_sarif(file_violations)
    return to_text(file_violations)