"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Pro feature gating and license enforcement for sqlean-lint.
All checks are 100% local — license state is read from a cached file on
disk and from the ``SQLEAN_LICENSE_ENFORCE`` environment variable.  No
network calls are ever made.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

# ── Feature descriptors ────────────────────────────────────────────────

FEATURE_AUTOFIX: Dict[str, str] = {
    "name": "AST Auto-Fixer",
    "tier": "pro",
}

FEATURE_STUDIO: Dict[str, str] = {
    "name": "Web Studio",
    "tier": "pro",
}

# ── Exceptions ─────────────────────────────────────────────────────────

class ProFeatureError(RuntimeError):
    """Raised when a pro-gated feature is accessed without a valid license."""


# ── Helpers ────────────────────────────────────────────────────────────

def data_dir() -> Path:
    """Return (and create if needed) the ``~/.sqlean_lint`` data directory."""
    path = Path.home() / ".sqlean_lint"
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_enforced() -> bool:
    """Return *True* when the ``SQLEAN_LICENSE_ENFORCE`` env var is truthy."""
    value = os.environ.get("SQLEAN_LICENSE_ENFORCE", "").strip().lower()
    return value in ("1", "true", "yes")


def is_pro() -> bool:
    """Return *True* when a cached pro-license marker file exists."""
    marker = data_dir() / ".pro_license"
    return marker.is_file()


def ensure_pro(feature: Dict[str, str]) -> None:
    """Gate access to *feature* behind the pro license.

    Raises :class:`ProFeatureError` when enforcement is active and the
    current installation is not licensed as pro.
    """
    if is_enforced() and not is_pro():
        raise ProFeatureError(
            f"Feature {feature['name']!r} requires a pro license. "
            "Visit https://sqlean-lint.dev/pro to upgrade."
        )


def upgrade_prompt(product_name: str) -> str:
    """Return a formatted upgrade prompt string for *product_name*."""
    return (
        f"--- Upgrade Required ---\n"
        f"{product_name} is a pro feature.\n"
        f"Visit https://sqlean-lint.dev/pro to unlock."
    )


def studio_state_fragment() -> Dict[str, Any]:
    """Return a snapshot dict describing the current studio/license state."""
    return {
        "pro": is_pro(),
        "enforced": is_enforced(),
        "tier": "pro" if is_pro() else "community",
    }
