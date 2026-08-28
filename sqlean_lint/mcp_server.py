"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Model Context Protocol server over **stdio only** (JSON-RPC 2.0).

Privacy/transport guarantees:
* No sockets, no ports, no HTTP - strictly stdin/stdout framing.
* All linting/optimization/cost work happens locally in-process.
* Diagnostics go to stderr; stdout carries JSON-RPC frames exclusively.

Supported methods: initialize, ping, tools/list, tools/call
(lint_query, optimize_query, estimate_query_cost), resources/list,
prompts/list. Notifications never receive a response."""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

from ._version import __version__
from .engine import lint_query
from .optimizer import optimize_sql
from .parser import SUPPORTED_DIALECTS, safe_parse

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "sqlean-lint-mcp"

_TOOLS = [
    {
        "name": "lint_query",
        "description": (
            "Run all semantic performance rules against one SQL statement "
            "(100% local). Returns violations with exact line/column positions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The SQL text to lint."},
                "dialect": {
                    "type": "string",
                    "enum": list(SUPPORTED_DIALECTS),
                    "description": "Target SQL dialect (default duckdb).",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "optimize_query",
        "description": (
            "Apply provably safe AST rewrites (non-SARGable date ranges, "
            "NOT IN -> NOT EXISTS, redundant CTE ORDER BY removal)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "dialect": {"type": "string", "enum": list(SUPPORTED_DIALECTS)},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "estimate_query_cost",
        "description": (
            "Deterministic 0-100 plan-risk score with complexity class "
            "(O(1)/O(N)/O(N*M)) and per-component contributions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "dialect": {"type": "string", "enum": list(SUPPORTED_DIALECTS)},
            },
            "required": ["sql"],
        },
    },
]

_TOOL_NAMES = {tool["name"] for tool in _TOOLS}


# --------------------------------------------------------------------------
# tool implementations
# --------------------------------------------------------------------------

def _tool_lint(arguments: Dict[str, Any]) -> Dict[str, Any]:
    sql = str(arguments["sql"])
    dialect = str(arguments.get("dialect", "duckdb"))
    result = lint_query(sql, dialect, optimize=True)
    return {
        "file_path": result.file_path,
        "violations": [v.to_dict() for v in result.violations],
        "cost": result.cost.to_dict(),
        "optimized_sql": result.optimized_sql,
        "duration_ms": round(result.duration_ms, 3),
    }


def _tool_optimize(arguments: Dict[str, Any]) -> Dict[str, Any]:
    sql = str(arguments["sql"])
    dialect = str(arguments.get("dialect", "duckdb"))
    parsed = safe_parse(sql, dialect)
    if not parsed.ok:
        return {"ok": False, "error": parsed.error_message}
    optimized, transformations = optimize_sql(sql, dialect)
    return {
        "ok": True,
        "optimized_sql": optimized,
        "transformations": [t.to_dict() for t in transformations],
    }


def _tool_cost(arguments: Dict[str, Any]) -> Dict[str, Any]:
    sql = str(arguments["sql"])
    dialect = str(arguments.get("dialect", "duckdb"))
    result = lint_query(sql, dialect, optimize=False)
    counts: Dict[str, int] = {}
    for violation in result.violations:
        counts[violation.rule_id] = counts.get(violation.rule_id, 0) + 1
    return {
        "risk_score": result.cost.risk_score,
        "risk_level": result.cost.risk_level.value
        if hasattr(result.cost.risk_level, "value")
        else str(result.cost.risk_level),
        "scan_complexity": result.cost.scan_complexity,
        "join_risk": result.cost.join_risk,
        "sort_risk": result.cost.sort_risk,
        "explanation": result.cost.explanation,
        "violation_count": len(result.violations),
        "by_rule": counts,
    }


_TOOL_HANDLERS = {
    "lint_query": _tool_lint,
    "optimize_query": _tool_optimize,
    "estimate_query_cost": _tool_cost,
}


# --------------------------------------------------------------------------
# JSON-RPC dispatch
# --------------------------------------------------------------------------

def _response(message_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def handle_request(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch one decoded JSON-RPC message; None means 'send nothing'."""
    method = message.get("method")
    message_id = message.get("id")

    if method == "initialize":
        return _response(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            },
        )

    if method == "ping":
        return _response(message_id, {})

    if method == "tools/list":
        return _response(message_id, {"tools": _TOOLS})

    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name"))
        arguments = params.get("arguments") or {}
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            return _response(
                message_id,
                {
                    "content": [{"type": "text", "text": json.dumps({"error": f"Unknown tool: {name}"})}],
                    "isError": True,
                },
            )
        try:
            payload = handler(arguments)
            return _response(
                message_id,
                {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False},
            )
        except Exception as err:  # noqa: BLE001 - tool errors are results, not crashes
            return _response(
                message_id,
                {
                    "content": [{"type": "text", "text": f"{type(err).__name__}: {err}"}],
                    "isError": True,
                },
            )

    if method in ("resources/list", "prompts/list"):
        return _response(message_id, {"resources": []} if method == "resources/list" else {"prompts": []})

    if method.startswith("notifications/") or message_id is None:
        return None  # notifications are acknowledged silently

    return _error(message_id, -32601, f"Method not found: {method}")


def read_loop(stream=None, out=None) -> None:
    """Line-delimited JSON-RPC over stdio until EOF."""
    stream = stream if stream is not None else sys.stdin
    out = out if out is not None else sys.stdout
    for raw_line in stream:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as err:
            frame = _error(None, -32700, f"Parse error: {err}")
        else:
            if isinstance(message, list):  # tolerate batches: process each entry
                for entry in message:
                    response = handle_request(entry) if isinstance(entry, dict) else _error(None, -32600, "Invalid Request")
                    if response:
                        out.write(json.dumps(response) + "\n")
                        out.flush()
                continue
            if not isinstance(message, dict):
                frame = _error(None, -32600, "Invalid Request: expected object")
            else:
                response = handle_request(message)
                if response is None:
                    continue
                frame = response
        out.write(json.dumps(frame) + "\n")
        out.flush()


def main() -> None:
    """Entrypoint: sqlean-lint-mcp (stdio transport, zero network ports)."""
    for stream_name in ("stdin", "stdout"):
        stream_obj = getattr(sys, stream_name, None)
        if stream_obj is not None and hasattr(stream_obj, "reconfigure"):
            try:
                stream_obj.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - best effort encoding pinning
                pass
    print(f"{SERVER_NAME} v{__version__} ready on stdio (local-only)", file=sys.stderr)
    read_loop()


if __name__ == "__main__":
    main()
