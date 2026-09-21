"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Integration adapters — sqlean-lint in external toolchains without touching the
core engine or GUI. See ``dbt_core`` for the dbt hook.

``dbt_core`` is imported lazily so ``python -m sqlean_lint.integrations.dbt_core``
runs cleanly (no eager package pre-import when a submodule is executed).
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "DBT_PROJECT_FILE",
    "DbtIntegrationError",
    "find_dbt_project",
    "lint_dbt_models",
    "run_dbt_lint",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import dbt_core

        return getattr(dbt_core, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(
        set(__all__) | {"jinja_parser", "schema_validator", "finops_cost"}
    )
