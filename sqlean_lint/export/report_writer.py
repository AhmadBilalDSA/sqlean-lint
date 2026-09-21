# Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.
"""Markdown & JSON audit report writers for sqlean-lint PRO.

Complements the PDF/XLSX executive exporters (``export.executive``) with
machine-friendly, CI/CD-consumable output:

* ``report_markdown`` — human-readable ``.md`` tables for lint findings, DAG
  lineage summaries and DuckDB result sets.
* ``report_json`` — a structured ``.json`` audit artifact that conforms to a
  stable, versioned schema for downstream log aggregation and querying.
* ``lint_report`` — a convenience wrapper that renders a full lint findings
  report in ``md`` or ``json`` and optionally writes it to a file.

Every function is headless, offline and never raises on arbitrary input
(zero-crash execution policy): malformed payloads degrade to empty/escaped
output rather than bubbling an exception.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlean_lint.rules import LintViolation

REPORT_SCHEMA_VERSION = "1.0.0"

_OWNER = "Ahmad Bilal (AhmadBilalDSA)"
_EMAIL = "kierninja@gmail.com"


# ── Violation payload plumbing ─────────────────────────────────────────

def _violation_dict(v: Any) -> Dict[str, Any]:
    """Normalize a LintViolation (or any dict-like) into a plain dict."""
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, LintViolation):
        return v.to_dict()
    try:
        to_dict = getattr(v, "to_dict")
        result = to_dict()
        if isinstance(result, dict):
            return result
    except Exception:  # noqa: BLE001
        pass
    return {
        "rule_id": getattr(v, "rule_id", ""),
        "message": getattr(v, "message", ""),
        "severity": getattr(v, "severity", "MEDIUM"),
        "line": getattr(v, "line", 1),
        "col": getattr(v, "col", 1),
        "snippet": getattr(v, "snippet", ""),
        "fixable": bool(getattr(v, "fixable", False)),
    }


def _csv_cell(value: Any, limit: int = 40) -> str:
    """Render *value* as a single-line, pipe-escaped Markdown table cell."""
    text = str(value if value is not None else "")
    text = " ".join(text.split())
    text = text[:limit]
    return text.replace("|", "\\|").replace("\n", " ")


# ── Markdown renderer ─────────────────────────────────────────────────

def markdown_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    title: str = "",
) -> str:
    """Render *rows* under *headers* as a GitHub-flavored Markdown table."""
    lines: List[str] = []
    if title:
        lines.append(f"### {title}\n")
    if not headers:
        return "\n".join(lines).strip()
    lines.append("| " + " | ".join(str(h) for h in headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        cells = [_csv_cell(c) for c in row]
        cells = cells + [""] * (len(headers) - len(cells))
        lines.append("| " + " | ".join(cells[: len(headers)]) + " |")
    return "\n".join(lines)


def report_markdown(
    findings: Optional[Sequence[Any]] = None,
    *,
    dialect: str = "duckdb",
    title: str = "SQL Lint & Audit Report",
    dag: Optional[Sequence[Dict[str, Any]]] = None,
    result_rows: Optional[Sequence[Dict[str, Any]]] = None,
    summary: Optional[Dict[str, Any]] = None,
) -> str:
    """Render findings (plus optional DAG / result-set data) as Markdown."""
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"*Generated {datetime.datetime.now().isoformat(timespec='seconds')} "
                 f"by sqlean-lint PRO - {_OWNER} <{_EMAIL}>*")
    lines.append("")
    lines.append(f"**Dialect:** `{dialect}`")

    if summary:
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [[str(k), str(v)] for k, v in summary.items()],
        ))

    findings = list(findings or [])
    lines.append("")
    lines.append("## Lint Findings")
    lines.append("")
    if not findings:
        lines.append("_No findings - the source is clean._")
    else:
        lines.append(markdown_table(
            ["Rule", "Severity", "Line", "Col", "Fixable", "Message"],
            [
                [
                    _violation_dict(v).get("rule_id", ""),
                    _violation_dict(v).get("severity", ""),
                    _violation_dict(v).get("line", 1),
                    _violation_dict(v).get("col", 1),
                    "yes" if _violation_dict(v).get("fixable") else "no",
                    _violation_dict(v).get("message", ""),
                ]
                for v in findings
            ],
        ))

    if dag:
        lines.append("")
        lines.append("## DAG Lineage")
        lines.append("")
        lines.append(markdown_table(
            ["Table", "Alias", "Join", "Problem", "Note"],
            [
                [
                    n.get("name", ""),
                    n.get("alias", ""),
                    n.get("join_kind", ""),
                    "yes" if n.get("problem") else "no",
                    n.get("note", ""),
                ]
                for n in dag
            ],
        ))

    if result_rows:
        lines.append("")
        lines.append("## Query Result Set")
        lines.append("")
        rows = list(result_rows)
        headers = list(rows[0].keys()) if rows else []
        lines.append(markdown_table(
            headers,
            [[r.get(c, "") for c in headers] for r in rows],
        ))

    return "\n".join(lines) + "\n"


# ── JSON renderer ─────────────────────────────────────────────────────

def report_json(
    findings: Optional[Sequence[Any]] = None,
    *,
    dialect: str = "duckdb",
    tool: str = "sqlean-lint-pro",
    dag: Optional[Sequence[Dict[str, Any]]] = None,
    result_rows: Optional[Sequence[Dict[str, Any]]] = None,
    summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a versioned audit dict suitable for ``json.dumps``."""
    findings = [_violation_dict(v) for v in (findings or [])]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": {
            "name": tool,
            "version": "1.0.0",
            "author": _OWNER,
            "author_email": _EMAIL,
        },
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "dialect": dialect,
        "summary": dict(summary or {}),
        "findings": findings,
        "dag": [dict(n) for n in (dag or [])],
        "result_rows": [dict(r) for r in (result_rows or [])],
    }


# ── Convenience helpers ───────────────────────────────────────────────

def lint_report(
    findings: Optional[Sequence[Any]] = None,
    *,
    output: Optional[Path] = None,
    fmt: str = "md",
    **kwargs: Any,
) -> str:
    """Render a lint report in ``md`` or ``json``; write it if *output* set.

    Returns the rendered text (JSON serialized to a string). Never raises.
    """
    fmt = (fmt or "md").lower()
    if fmt in ("json", "sarif"):
        data = report_json(findings, **kwargs)
        text = json.dumps(data, indent=2, ensure_ascii=False)
    else:
        text = report_markdown(findings, **kwargs)

    if output is not None:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
        except OSError:  # noqa: BLE001 - writing is best-effort
            pass
    return text


def render_markdown_report(*args: Any, **kwargs: Any) -> str:
    """Alias for :func:`report_markdown` (explicit, discoverable name)."""
    return report_markdown(*args, **kwargs)


def render_json_report(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Alias for :func:`report_json` (explicit, discoverable name)."""
    return report_json(*args, **kwargs)
