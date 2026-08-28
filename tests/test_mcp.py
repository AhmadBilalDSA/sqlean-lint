"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Stdio JSON-RPC 2.0 MCP handshake and tool execution tests."""
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_session(messages):
    """Send newline-delimited JSON-RPC messages; return parsed responses + raw lines."""
    stdin_text = "".join(json.dumps(message) + "\n" for message in messages)
    proc = subprocess.run(
        [sys.executable, "-m", "sqlean_lint.mcp_server"],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=120,
    )
    frames = [
        json.loads(line)
        for line in proc.stdout.splitlines()
        if line.strip()
    ]
    return proc, frames


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
}
INITIALIZED_NOTIFICATION = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_initialize_handshake():
    proc, frames = run_session([INITIALIZE])
    assert proc.returncode == 0
    assert len(frames) == 1
    result = frames[0]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "sqlean-lint-mcp"
    assert "tools" in result["capabilities"]


def test_tools_list_exposes_three_tools():
    _, frames = run_session(
        [INITIALIZE, INITIALIZED_NOTIFICATION, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    )
    tools_list = [f for f in frames if f.get("id") == 2][0]
    names = {tool["name"] for tool in tools_list["result"]["tools"]}
    assert {"lint_query", "optimize_query", "estimate_query_cost"} <= names


def test_notifications_never_get_responses():
    proc, frames = run_session(
        [INITIALIZE, INITIALIZED_NOTIFICATION, {"jsonrpc": "2.0", "id": 9, "method": "ping"}]
    )
    ids = {frame.get("id") for frame in frames}
    assert ids == {1, 9}  # the notification produced no frame


def test_tool_call_lint_query():
    request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "lint_query",
            "arguments": {"sql": "SELECT * FROM a CROSS JOIN b", "dialect": "duckdb"},
        },
    }
    _, frames = run_session([INITIALIZE, INITIALIZED_NOTIFICATION, request])
    response = [f for f in frames if f.get("id") == 3][0]
    assert response["result"]["isError"] is False
    payload = json.loads(response["result"]["content"][0]["text"])
    assert any(v["rule_id"] == "SQL-CART-001" for v in payload["violations"])
    assert payload["cost"]["risk_score"] >= 50


def test_tool_call_optimize_query():
    request = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "optimize_query",
            "arguments": {
                "sql": "SELECT * FROM o WHERE k NOT IN (SELECT id FROM blocked)",
                "dialect": "duckdb",
            },
        },
    }
    _, frames = run_session([INITIALIZE, INITIALIZED_NOTIFICATION, request])
    response = [f for f in frames if f.get("id") == 4][0]
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert "NOT EXISTS" in payload["optimized_sql"].upper()


def test_tool_call_estimate_query_cost():
    request = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "estimate_query_cost",
            "arguments": {"sql": "SELECT a FROM t WHERE a > 1"},
        },
    }
    _, frames = run_session([INITIALIZE, INITIALIZED_NOTIFICATION, request])
    response = [f for f in frames if f.get("id") == 5][0]
    payload = json.loads(response["result"]["content"][0]["text"])
    assert isinstance(payload["risk_score"], int)
    assert payload["scan_complexity"] in {"O(1)", "O(N)", "O(N*M)"}


def test_unknown_method_returns_minus_32601():
    request = {"jsonrpc": "2.0", "id": 6, "method": "does/not/exist"}
    _, frames = run_session([INITIALIZE, INITIALIZED_NOTIFICATION, request])
    response = [f for f in frames if f.get("id") == 6][0]
    assert response["error"]["code"] == -32601


def test_malformed_json_yields_parse_error():
    proc = subprocess.run(
        [sys.executable, "-m", "sqlean_lint.mcp_server"],
        input="{not json}\n",
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=60,
    )
    frame = json.loads(proc.stdout.splitlines()[0])
    assert frame["error"]["code"] == -32700
