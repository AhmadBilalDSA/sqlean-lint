"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Abstract base connector for sqlean-lint.
"""

from __future__ import annotations

import abc
from typing import Any


class BaseConnector(abc.ABC):
    """Abstract base class for database connectors.

    All connector implementations must subclass this and provide concrete
    implementations for each abstract method.
    """

    @abc.abstractmethod
    def connect(self, config: dict[str, Any]) -> None:
        """Establish a connection to the database.

        Args:
            config: Connection configuration parameters (e.g. host, port,
                credentials, database path).
        """
        ...

    @abc.abstractmethod
    def execute_query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SQL query and return results as a list of dictionaries.

        Args:
            sql: The SQL query to execute.

        Returns:
            A list of dictionaries, where each dictionary represents a row
            with column names as keys.
        """
        ...

    @abc.abstractmethod
    def get_schema(self, table_name: str) -> dict[str, Any]:
        """Retrieve schema information for a given table.

        Args:
            table_name: The name of the table to inspect.

        Returns:
            A dictionary containing schema metadata such as column names,
            data types, and constraints.
        """
        ...

    @abc.abstractmethod
    def close(self) -> None:
        """Close the database connection."""
        ...
