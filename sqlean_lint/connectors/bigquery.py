"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

BigQuery connector stub for sqlean-lint.
"""

from __future__ import annotations

from typing import Any

from sqlean_lint.connectors.base import BaseConnector


class BigQueryConnector(BaseConnector):
    """Stub connector for Google BigQuery.

    All methods raise ``NotImplementedError`` with installation instructions
    until the ``google-cloud-bigquery`` extra is installed.
    """

    _INSTALL_MSG = (
        "BigQuery connector requires google-cloud-bigquery. "
        "Install with: pip install sqlean-lint[bigquery]"
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
