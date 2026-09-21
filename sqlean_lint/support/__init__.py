# Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.
"""Customer success, support & DevRel layer for sqlean-lint PRO."""
from __future__ import annotations

from .ticketing import (
    DevRelReporter,
    DiagnosticsBundle,
    IntercomClient,
    ZendeskClient,
    build_diagnostics_bundle,
    support_enabled,
    sync_readme_api,
)

__all__ = [
    "DevRelReporter",
    "DiagnosticsBundle",
    "IntercomClient",
    "ZendeskClient",
    "build_diagnostics_bundle",
    "support_enabled",
    "sync_readme_api",
]
