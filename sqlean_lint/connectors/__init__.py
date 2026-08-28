"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Connectors package for sqlean-lint.
"""

from sqlean_lint.connectors.base import BaseConnector
from sqlean_lint.connectors.bigquery import BigQueryConnector
from sqlean_lint.connectors.duckdb_local import DuckDBLocalConnector
from sqlean_lint.connectors.snowflake import SnowflakeConnector

CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {
    "duckdb": DuckDBLocalConnector,
    "bigquery": BigQueryConnector,
    "snowflake": SnowflakeConnector,
}


def get_connector(name: str) -> BaseConnector:
    """Return a connector instance for the given name.

    Args:
        name: The name of the connector (e.g. 'duckdb', 'bigquery', 'snowflake').

    Returns:
        An instance of the requested connector.

    Raises:
        ValueError: If the connector name is not found in the registry.
    """
    connector_class = CONNECTOR_REGISTRY.get(name)
    if connector_class is None:
        raise ValueError(
            f"Unknown connector '{name}'. Available connectors: "
            f"{', '.join(CONNECTOR_REGISTRY.keys())}"
        )
    return connector_class()
