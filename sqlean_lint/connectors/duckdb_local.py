"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Local DuckDB connector for sqlean-lint.

In addition to plain query execution this connector exposes explicit
transaction blocks (``begin_transaction`` / ``commit`` / ``rollback``) and a
best-effort ``execute_query_safe`` that returns a structured error dictionary
instead of raising, so the GUI status bar can surface bad SQL gracefully
(zero-crash execution policy).
"""

from __future__ import annotations

import threading
from typing import Any, Dict

import duckdb

from sqlean_lint.connectors.base import BaseConnector


class DuckDBLocalConnector(BaseConnector):
    """Connector for local DuckDB databases (in-memory or file-based).

    Uses the ``duckdb`` Python library to interact with DuckDB instances.
    """

    def __init__(self) -> None:
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._last_types: dict[str, str] = {}
        self._tx_depth: int = 0
        self._tx_lock = threading.Lock()

    def connect(self, config: dict[str, Any]) -> None:
        """Open a DuckDB connection.

        If ``config`` contains a ``path`` key, a file-based connection is
        opened.  Otherwise an in-memory database is used.

        Args:
            config: May contain:
                - ``path`` (str | None): File path for a persistent database.
                  ``None`` or omitted for in-memory.
        """
        path = config.get("path")
        if path is not None:
            self._connection = duckdb.connect(database=path)
        else:
            self._connection = duckdb.connect(database=":memory:")

    def _column_types(self, cursor) -> list[tuple[str, str]]:
        """Return ``[(name, type)]`` pairs from a DuckDB cursor.

        DuckDB exposes physical types via ``cursor.description`` on recent
        versions; when unavailable we fall back to the column names only.
        """
        if cursor is None or cursor.description is None:
            return []
        return [
            (desc[0], str(desc[1]))
            for desc in cursor.description
        ]

    def execute_query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SQL query and return results as a list of dictionaries.

        Args:
            sql: The SQL query to execute.

        Returns:
            A list of row dictionaries.

        Raises:
            RuntimeError: If no connection has been established.
        """
        if self._connection is None:
            raise RuntimeError("Not connected. Call connect() first.")
        cursor = self._connection.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        self._last_types = dict(self._column_types(cursor))
        return [dict(zip(columns, row)) for row in rows]

    def execute_query_typed(
        self, sql: str
    ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        """Execute *sql* and return ``(columns, types, rows)``.

        ``types`` holds the DuckDB type name (e.g. ``"INT64"``,
        ``"VARCHAR"``, ``"TIMESTAMP"``) for each column, in column order.

        Raises:
            RuntimeError: If no connection has been established.
        """
        if self._connection is None:
            raise RuntimeError("Not connected. Call connect() first.")
        cursor = self._connection.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        type_map = dict(self._column_types(cursor))
        types = [type_map.get(c, "") for c in columns]
        return (columns, types, [dict(zip(columns, row)) for row in rows])

    def get_schema(self, table_name: str) -> dict[str, Any]:
        """Return column information for *table_name* using ``DESCRIBE``.

        Args:
            table_name: The table to inspect.

        Returns:
            A dictionary with a ``columns`` key containing a list of
            column-info dictionaries (``name`` and ``type``).
        """
        if self._connection is None:
            raise RuntimeError("Not connected. Call connect() first.")
        cursor = self._connection.execute(f"DESCRIBE {table_name}")
        columns = cursor.fetchall()
        return {
            "columns": [{"name": col[0], "type": col[1]} for col in columns],
        }

    def close(self) -> None:
        """Close the DuckDB connection."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # ── Explicit transaction safety ────────────────────────────────────

    def _require_connection(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._connection

    def begin_transaction(self) -> None:
        """Open a transaction context (SAVEPOINT-nested, re-entrant safe).

        Explicit transactions are tracked with the DuckDB ``BEGIN`` statement.
        Nested calls use SAVEPOINTs so they compose safely with the GUI worker.
        """
        conn = self._require_connection()
        with self._tx_lock:
            if self._tx_depth == 0:
                conn.execute("BEGIN TRANSACTION")
            else:
                conn.execute(f"SAVEPOINT tx_{self._tx_depth}")
            self._tx_depth += 1

    def commit(self) -> None:
        """Commit the innermost active transaction."""
        conn = self._require_connection()
        with self._tx_lock:
            if self._tx_depth <= 0:
                return
            self._tx_depth -= 1
            if self._tx_depth == 0:
                conn.execute("COMMIT")
            else:
                conn.execute(f"RELEASE SAVEPOINT tx_{self._tx_depth}")

    def rollback(self) -> None:
        """Roll back the innermost active transaction (or all if none)."""
        conn = self._require_connection()
        with self._tx_lock:
            if self._tx_depth <= 0:
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001 - best-effort
                    pass
                return
            self._tx_depth -= 1
            if self._tx_depth == 0:
                conn.execute("ROLLBACK")
            else:
                conn.execute(f"ROLLBACK TO SAVEPOINT tx_{self._tx_depth}")

    def in_transaction(self) -> bool:
        return self._tx_depth > 0

    def execute_query_safe(
        self, sql: str, *, auto_rollback: bool = True
    ) -> Dict[str, Any]:
        """Execute *sql* and never raise; return a structured result dict.

        Returns:
            On success: ``{"ok": True, "rows": [...], "columns": [...],
            "types": [...], "row_count": int, "error": None}``.
            On failure: ``{"ok": False, "rows": [], "error": str, "error_type":
            str, "row_count": 0}`` (a structured error for the GUI status bar).

        When *auto_rollback* is true and the connector is inside an explicit
        transaction, a failed statement rolls the transaction back, satisfying
        the transaction-safety contract.
        """
        try:
            if self._connection is None:
                raise RuntimeError("Not connected. Call connect() first.")
            cursor = self._connection.execute(sql)
            columns = (
                [desc[0] for desc in cursor.description]
                if cursor.description and cursor.description is not None
                else []
            )
            typed = self._column_types(cursor)
            rows = cursor.fetchall()
            self._last_types = dict(typed)
            return {
                "ok": True,
                "columns": columns,
                "types": [dict(typed).get(c, "") for c in columns],
                "rows": [dict(zip(columns, row)) for row in rows],
                "row_count": len(rows),
                "error": None,
                "error_type": None,
            }
        except Exception as err:  # noqa: BLE001 - bad SQL degrades gracefully
            if auto_rollback and self._tx_depth > 0:
                try:
                    self.rollback()
                    self._tx_depth = 0
                except Exception:  # noqa: BLE001 - rollback is best-effort
                    pass
            return {
                "ok": False,
                "columns": [],
                "types": [],
                "rows": [],
                "row_count": 0,
                "error": str(err),
                "error_type": type(err).__name__,
            }
