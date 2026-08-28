"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Query DAG builder - visualises the logical pipeline of a SQL statement.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .parser import parse_one

RISK_COLORS: Dict[str, str] = {
    "CRITICAL": "#f85149",
    "HIGH": "#d29922",
    "MEDIUM": "#58a6ff",
    "LOW": "#7ee787",
}

_PIPELINE_KINDS: list[str] = [
    "FROM",
    "JOIN",
    "WHERE",
    "GROUP BY",
    "HAVING",
    "SELECT",
    "ORDER BY",
    "LIMIT",
]


def _extract_parts(sql: str, dialect: str) -> Dict[str, Optional[str]]:
    """Extract human-readable fragments from each pipeline stage."""
    tree = parse_one(sql, dialect)
    parts: Dict[str, Optional[str]] = {}

    from_clause = tree.args.get("from_") or tree.args.get("from")
    parts["FROM"] = from_clause.sql(dialect=dialect) if from_clause else None

    joins = tree.args.get("joins")
    if joins:
        parts["JOIN"] = " ".join(j.sql(dialect=dialect) for j in joins)
    else:
        parts["JOIN"] = None

    where = tree.args.get("where")
    parts["WHERE"] = where.sql(dialect=dialect) if where else None

    group = tree.args.get("group")
    parts["GROUP BY"] = group.sql(dialect=dialect) if group else None

    having = tree.args.get("having")
    parts["HAVING"] = having.sql(dialect=dialect) if having else None

    projections = tree.expressions
    if projections:
        parts["SELECT"] = ", ".join(p.sql(dialect=dialect) for p in projections)
    else:
        parts["SELECT"] = None

    order = tree.args.get("order")
    parts["ORDER BY"] = order.sql(dialect=dialect) if order else None

    limit = tree.args.get("limit")
    parts["LIMIT"] = limit.sql(dialect=dialect) if limit else None

    return parts


def _line_of(sql: str, fragment: Optional[str]) -> int:
    """Best-effort line number where fragment appears in sql."""
    if not fragment:
        return 0
    idx = sql.find(fragment.split("\n")[0])
    if idx < 0:
        return 0
    return sql.count("\n", 0, idx) + 1


def _severity_at_line(
    violations: List[Dict[str, Any]],
    target_line: int,
    radius: int = 5,
) -> Optional[Dict[str, Any]]:
    """Return the highest-severity violation within radius lines of target_line."""
    best: Optional[Dict[str, Any]] = None
    for v in violations:
        v_line = v.get("line", 0)
        if v_line == 0:
            continue
        if abs(v_line - target_line) <= radius:
            if best is None or v.get("severity", "") > best.get("severity", ""):
                best = v
    return best


def build_dag(
    sql: str,
    dialect: str = "duckdb",
    violations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Parse sql and build a node/edge graph of the query pipeline.

    Each node represents a logical stage (FROM, JOIN, WHERE, ...).  Risk
    colors from violations are attached by line-number proximity.

    Returns {"nodes": [...], "edges": [...]} where every node has
    id, label, kind, detail, and optionally severity, color, and message.
    """
    violations = violations or []
    parts = _extract_parts(sql, dialect)

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    for kind in _PIPELINE_KINDS:
        detail = parts.get(kind)
        if detail is None:
            continue

        node_id = f"stage_{kind.replace(' ', '_').lower()}"
        frag_line = _line_of(sql, detail)
        risk = _severity_at_line(violations, frag_line)

        node: Dict[str, Any] = {
            "id": node_id,
            "label": kind,
            "kind": kind,
            "detail": detail,
        }
        if risk:
            sev = risk.get("severity", "LOW")
            node["severity"] = sev
            node["color"] = RISK_COLORS.get(sev, "#7ee787")
            node["message"] = risk.get("message", "")

        nodes.append(node)

    for idx in range(len(nodes) - 1):
        edges.append(
            {"source": nodes[idx]["id"], "target": nodes[idx + 1]["id"]}
        )

    return {"nodes": nodes, "edges": edges}
