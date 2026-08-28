"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Snowflake connector stub for sqlean-lint.
"""

from __future__ import annotations

from typing import Any

from sqlean_lint.connectors.base import BaseConnector


class SnowflakeConnector(BaseConnector):
    """Stub connector for Snowflake.

    All methods raise ``NotImplementedError`` with installation instructions
    until the ``snowflake-connector-python`` extra is installed.
    """

    _INSTALL_MSG = (
        "Snowflake connector requires snowflake-connector-python. "
        "Install with: pip install sqlean-lint[snowflake]"
    )

    def connect(self, config: dict[str, Any]) -> None:
        """Stub – not yet implemented."""
        raise NotImplementedError(self._INSTALL_MSG)

    def execute_query(self, sql: str) -> list[dict[str, Any]]:
        """Stub – not yet implemented."""
        raise NotImplementedError(self._INSTALL_MSG)

    def get_schema(self, table_name: str) -> dict[str, Any]:
        """Stub – not yet implemented."""
        raise NotImplementedError(self._INSTALL_MSG)

    def close(self) -> None:
        """Stub – not yet implemented."""
        raise NotImplementedError(self._INSTALL_MSG)
