"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Minimal JSON-RPC / stdio Language Server Protocol (LSP) stub for sqlean-lint.

External editors (VS Code, Cursor, Neovim) can launch this process and talk to
it over stdin/stdout using the standard LSP protocol to obtain live SQL
diagnostics and one-shot code actions built on the core sqlean-lint engine.

Implemented message types (a curated LSP subset):
    initialize            -> server capabilities + serverInfo
    initialized           -> no-op acknowledgement
    shutdown / exit       -> clean shutdown
    textDocument/didOpen  -> publishDiagnostics on the document
    textDocument/didChange-> re-publish diagnostics for the new text
    textDocument/didSave  -> re-publish diagnostics for the saved text
    textDocument/codeAction -> return a quickfix-ish CodeAction that invokes
                               ``fix_sql`` when a rule is fixable

The server is fully offline (no telemetry, no phone-home). Malformed JSON-RPC
or unknown methods are answered with a JSON-RPC error rather than crashing
(zero-crash execution policy).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from .rules import LintViolation, fix_sql, lint_sql

# Per-document state keyed by the document URI.
_DOCUMENTS: Dict[str, Dict[str, Any]] = {}

SUPPORTED_DIALECTS = (
    "duckdb", "postgres", "mysql", "sqlite", "snowflake",
    "bigquery", "databricks", "tsql",
)


def _dialect_for_uri(uri: str) -> str:
    """Heuristic dialect pick from the file name; defaults to duckdb."""
    name = (uri or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    for candidate in ("snowflake", "bigquery", "tsql", "mysql",
                      "postgres", "sqlite", "databricks", "duckdb"):
        if candidate in name:
            return candidate
    return "duckdb"


# ── JSON-RPC plumbing ─────────────────────────────────────────────────

def _read_message() -> Optional[str]:
    """Read one LSP (Content-Length framed) message body from stdin."""
    try:
        headers: Dict[str, str] = {}
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                break
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return None
        return sys.stdin.buffer.read(length).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - corrupt stream degrades gracefully
        return None


def _write_message(obj: Dict[str, Any]) -> None:
    body = json.dumps(obj)
    payload = body.encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode())
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _send(method: Optional[str], params: Optional[Any], id: Optional[Any]) -> None:
    msg: Dict[str, Any] = {"jsonrpc": "2.0"}
    if method is not None:
        msg["method"] = method
    if params is not None:
        msg["params"] = params
    if id is not None:
        msg["id"] = id
    _write_message(msg)


def _error(id: Optional[Any], code: int, message: str) -> None:
    _write_message({
        "jsonrpc": "2.0",
        "id": id,
        "error": {"code": code, "message": message},
    })


# ── LSP handlers ──────────────────────────────────────────────────────

def _initialize(params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "capabilities": {
            "textDocumentSync": {
                "openClose": True,
                "change": 1,  # full document sync (simplest)
                "save": {"includeText": True},
            },
            "codeActionProvider": True,
        },
        "serverInfo": {
            "name": "sqlean-lint-pro",
            "version": "1.0.0",
        },
    }


def _position_to_line_col(position: Dict[str, Any]) -> tuple[int, int]:
    return int(position.get("line", 0)) + 1, int(position.get("character", 0)) + 1


def _lint_document(uri: str, text: str, version: Optional[int] = None) -> None:
    dialect = _dialect_for_uri(uri)
    violations = lint_sql(text, dialect)
    diagnostics = []
    for v in violations:
        diagnostics.append({
            "range": {
                "start": {"line": max(0, v.line - 1), "character": max(0, v.col - 1)},
                "end": {"line": max(0, v.line - 1), "character": max(0, v.col - 1)},
            },
            "severity": _severity_map(v.severity),
            "code": v.rule_id,
            "source": "sqlean-lint",
            "message": v.message,
            "data": {"fixable": v.fixable},
        })
    _send("textDocument/publishDiagnostics", {
        "uri": uri,
        "diagnostics": diagnostics,
        "version": version,
    })


def _severity_map(severity: str) -> int:
    return {
        "CRITICAL": 1,
        "HIGH": 1,
        "MEDIUM": 2,
        "LOW": 3,
        "ERROR": 1,
    }.get(severity.upper(), 2)


def _on_did_open(params: Dict[str, Any]) -> None:
    doc = params.get("textDocument", {})
    uri = doc.get("uri", "")
    text = doc.get("text", "")
    _DOCUMENTS[uri] = {"text": text, "version": doc.get("version")}
    _lint_document(uri, text, doc.get("version"))


def _on_did_change(params: Dict[str, Any]) -> None:
    doc = params.get("textDocument", {})
    uri = doc.get("uri", "")
    changes = params.get("contentChanges", [])
    if changes:
        text = changes[-1].get("text", "")
    else:
        text = _DOCUMENTS.get(uri, {}).get("text", "")
    _DOCUMENTS[uri] = {"text": text, "version": doc.get("version")}
    _lint_document(uri, text, doc.get("version"))


def _on_did_save(params: Dict[str, Any]) -> None:
    doc = params.get("textDocument", {})
    uri = doc.get("uri", "")
    text = params.get("text") or _DOCUMENTS.get(uri, {}).get("text", "")
    _DOCUMENTS[uri] = {"text": text, "version": doc.get("version")}
    _lint_document(uri, text, doc.get("version"))


def _on_code_action(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a CodeAction that runs ``fix_sql`` when a finding is fixable."""
    uri = params.get("textDocument", {}).get("uri", "")
    dialect = _dialect_for_uri(uri)
    text = _DOCUMENTS.get(uri, {}).get("text", "")
    actions: List[Dict[str, Any]] = []
    if not text:
        return actions
    try:
        violations = lint_sql(text, dialect)
    except Exception:  # noqa: BLE001
        return actions
    if any(v.fixable for v in violations):
        fixed = fix_sql(text, dialect)
        if fixed != text:
            actions.append({
                "title": "sqlean-lint: Apply safe auto-fixes",
                "kind": "quickfix",
                "edit": {
                    "changes": {
                        uri: [{
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": text.count("\n") + 1, "character": 0},
                            },
                            "newText": fixed,
                        }],
                    }
                },
            })
    return actions


# ── dispatch loop ─────────────────────────────────────────────────────

HANDLERS: Dict[str, Any] = {
    "initialize": _initialize,
    "initialized": lambda _p: None,
    "textDocument/didOpen": _on_did_open,
    "textDocument/didChange": _on_did_change,
    "textDocument/didSave": _on_did_save,
    "textDocument/codeAction": _on_code_action,
}


def _dispatch(msg: Dict[str, Any]) -> bool:
    """Handle one request/notification; return False when the server should
    exit (shutdown followed by exit)."""
    method = msg.get("method")
    rpc_id = msg.get("id")
    params = msg.get("params", {})
    if method == "exit":
        return False
    handler = HANDLERS.get(method)
    if handler is None:
        if rpc_id is not None:
            _error(rpc_id, -32601, f"Method not found: {method}")
        return True
    try:
        result = handler(params)
        if rpc_id is not None and result is not None:
            _send(None, result, rpc_id)
    except Exception as err:  # noqa: BLE001 - never let a handler kill the loop
        if rpc_id is not None:
            _error(rpc_id, -32603, f"Internal error: {err}")
    return True


def run() -> int:
    """Run the LSP stdio loop until EOF or an exit notification."""
    while True:
        raw = _read_message()
        if raw is None:
            break
        if not raw.strip():
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:  # noqa: BLE001
            # Not addressable to a request id; ignore malformed frames.
            continue
        keep = _dispatch(msg)
        if not keep:
            break
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    # Disable buffering surprises on Windows stdio.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    return run()


if __name__ == "__main__":
    sys.exit(main())
