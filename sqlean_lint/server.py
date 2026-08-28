"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Localhost-only HTTP server powering the Web Studio.

Every endpoint is bound to ``127.0.0.1`` and never exposes the service to
the network.  The server is single-process with a ``ThreadingHTTPServer``
for concurrent request handling.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .engine import lint_source
from .features import is_enforced, is_pro, studio_state_fragment
from .optimizer import optimize_sql
from .transpiler import transpile_sql
from .webstudio import STUDIO_HTML

# ── Constants ──────────────────────────────────────────────────────────

PRESETS: List[Dict[str, str]] = [
    {
        "name": "Cartesian Explosion",
        "dialect": "duckdb",
        "setup": (
            "CREATE TABLE orders (id INT, customer_id INT);\n"
            "CREATE TABLE customers (id INT, name TEXT);\n"
        ),
        "sql": (
            "SELECT o.id, c.name, o.total\n"
            "FROM orders o, customers c\n"
            "WHERE o.customer_id = c.id;"
        ),
    },
    {
        "name": "SARGable Date",
        "dialect": "duckdb",
        "setup": (
            "CREATE TABLE events (id INT, ts TIMESTAMP);\n"
        ),
        "sql": (
            "SELECT *\n"
            "FROM events\n"
            "WHERE YEAR(ts) = 2026;"
        ),
    },
]


# ── History store (SQLite-backed) ─────────────────────────────────────

class HistoryStore:
    """Thread-safe SQLite store for lint session history."""

    _LOCK = threading.Lock()

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or (Path.home() / ".sqlean_lint" / "history.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._LOCK:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS history ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "ts REAL NOT NULL,"
                "dialect TEXT NOT NULL,"
                "sql TEXT NOT NULL,"
                "violations_json TEXT NOT NULL,"
                "risk_score INTEGER NOT NULL"
                ")"
            )
            conn.commit()
            conn.close()

    def add(
        self,
        dialect: str,
        sql: str,
        violations_json: str,
        risk_score: int,
    ) -> None:
        with self._LOCK:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT INTO history (ts, dialect, sql, violations_json, risk_score) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), dialect, sql, violations_json, risk_score),
            )
            conn.commit()
            conn.close()

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._LOCK:
            conn = sqlite3.connect(str(self._db_path))
            rows = conn.execute(
                "SELECT id, ts, dialect, sql, violations_json, risk_score "
                "FROM history ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
        return [
            {
                "id": row[0],
                "ts": row[1],
                "dialect": row[2],
                "sql": row[3],
                "violations_json": row[4],
                "risk_score": row[5],
            }
            for row in rows
        ]

    def clear(self) -> int:
        with self._LOCK:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM history")
            count = cursor.fetchone()[0]
            conn.execute("DELETE FROM history")
            conn.commit()
            conn.close()
        return count


_history = HistoryStore()


# ── HTTP handler ──────────────────────────────────────────────────────

class StudioHandler(BaseHTTPRequestHandler):
    """Route GET/POST requests to the Web Studio API endpoints."""

    # Silence default logging.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        pass

    # ── GET routes ─────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._serve_studio()
        elif path == "/api/state":
            self._handle_state()
        elif path == "/api/history":
            self._handle_history_get(parsed)
        elif path == "/api/presets":
            self._handle_presets()
        else:
            self._respond(404, {"error": "Not found"})

    # ── POST routes ────────────────────────────────────────────────────

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        body = self._read_body()

        if path == "/api/lint":
            self._handle_lint(body)
        elif path == "/api/transpile":
            self._handle_transpile(body)
        elif path == "/api/apply_fix":
            self._handle_apply_fix(body)
        elif path == "/api/export":
            self._handle_export(body)
        else:
            self._respond(404, {"error": "Not found"})

    # ── Helpers ────────────────────────────────────────────────────────

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _respond(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _respond_html(self, code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── Endpoint implementations ───────────────────────────────────────

    def _serve_studio(self) -> None:
        self._respond_html(200, STUDIO_HTML)

    def _handle_state(self) -> None:
        state = studio_state_fragment()
        state["presets"] = PRESETS
        self._respond(200, state)

    def _handle_presets(self) -> None:
        self._respond(200, {"presets": PRESETS})

    def _handle_lint(self, body: Dict[str, Any]) -> None:
        sql = body.get("sql", "")
        dialect = body.get("dialect", "duckdb")
        if not sql.strip():
            self._respond(400, {"error": "sql is required"})
            return

        result = lint_source(sql, dialect=dialect)
        result_dict = result.to_dict(include_sql=True, include_duration=True)

        _history.add(
            dialect=dialect,
            sql=sql,
            violations_json=json.dumps([v.to_dict() for v in result.violations]),
            risk_score=result.cost.risk_score,
        )

        self._respond(200, result_dict)

    def _handle_transpile(self, body: Dict[str, Any]) -> None:
        sql = body.get("sql", "")
        source = body.get("source_dialect", "duckdb")
        target = body.get("target_dialect", "bigquery")
        if not sql.strip():
            self._respond(400, {"error": "sql is required"})
            return
        try:
            result = transpile_sql(sql, source, target)
            self._respond(200, {"transpiled_sql": result})
        except Exception as exc:  # noqa: BLE001
            self._respond(400, {"error": str(exc)})

    def _handle_apply_fix(self, body: Dict[str, Any]) -> None:
        if is_enforced() and not is_pro():
            self._respond(402, {
                "error": "Pro license required for auto-fix.",
                "upgrade_url": "https://sqlean-lint.dev/pro",
            })
            return

        sql = body.get("sql", "")
        dialect = body.get("dialect", "duckdb")
        if not sql.strip():
            self._respond(400, {"error": "sql is required"})
            return
        result = lint_source(sql, dialect=dialect)
        self._respond(200, {
            "original_sql": sql,
            "optimized_sql": result.optimized_sql or sql,
            "changed": result.optimized_sql is not None,
            "cost": result.cost.to_dict(),
        })

    def _handle_history_get(self, parsed: Any) -> None:
        qs = parse_qs(parsed.query)
        limit = int(qs.get("limit", ["50"])[0])
        self._respond(200, {"history": _history.recent(limit)})

    def _handle_export(self, body: Dict[str, Any]) -> None:
        fmt = body.get("format", "json")
        results_json = body.get("results_json", "[]")
        try:
            results = json.loads(results_json) if isinstance(results_json, str) else results_json
        except (json.JSONDecodeError, TypeError):
            results = []

        if fmt == "json":
            self._respond(200, {"export": results, "format": "json"})
        else:
            self._respond(400, {"error": f"Unsupported export format: {fmt!r}"})


# ── Threaded server ───────────────────────────────────────────────────

class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Start the Web Studio server on *host*:*port*.

    Binds to localhost only.  If *open_browser* is *True* the default
    browser is launched after a short delay.
    """
    server = _ThreadedHTTPServer((host, port), StudioHandler)
    url = f"http://{host}:{port}"

    if open_browser:
        threading.Timer(0.6, webbrowser.open, args=[url]).start()

    print(f"Web Studio running at {url}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
