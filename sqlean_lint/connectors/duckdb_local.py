"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Local DuckDB connector for sqlean-lint.
"""

from __future__ import annotations

from typing import Any

import duckdb

from sqlean_lint.connectors.base import BaseConnector


class DuckDBLocalConnector(BaseConnector):
    """Connector for local DuckDB databases (in-memory or file-based).

    Uses the ``duckdb`` Python library to interact with DuckDB instances.
    """

    def __init__(self) -> None:
        self._connection: duckdb.DuckDBPyConnection | None = None

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
        return [dict(zip(columns, row)) for row in rows]

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
