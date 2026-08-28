"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Origin watermark and provenance verification for sqlean-lint.
A deterministic SHA-256 is computed over a canonical JSON manifest at
import time.  Any tampering with the author, project, license or salt
constants will cause :func:`verify_origin` to return *False* and
:func:`assert_origin` to raise :class:`SecurityError`.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

# ── Constants ──────────────────────────────────────────────────────────

AUTHOR: str = "Ahmad Bilal (AhmadBilalDSA)"
PROJECT: str = "sqlean-lint"
LICENSE_SLUG: str = "BSL-1.1-PolyForm-NC"
_WATERMARK_SALT: str = "sqlean-origin-watermark-v1"

# ── Exceptions ─────────────────────────────────────────────────────────

class SecurityError(RuntimeError):
    """Raised when origin verification fails."""


# ── Manifest helpers ───────────────────────────────────────────────────

def origin_manifest() -> Dict[str, str]:
    """Return the canonical origin manifest dictionary."""
    return {
        "author": AUTHOR,
        "project": PROJECT,
        "license": LICENSE_SLUG,
        "salt": _WATERMARK_SALT,
    }


def compute_origin_sha256() -> str:
    """Compute a deterministic SHA-256 hex digest over the canonical manifest.

    The manifest is serialised as compact, sorted-key JSON so the hash is
    byte-identical across platforms and Python versions.
    """
    manifest = origin_manifest()
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


ORIGIN_SHA256: str = compute_origin_sha256()


# ── Verification API ──────────────────────────────────────────────────

def verify_origin(expected: str = ORIGIN_SHA256) -> bool:
    """Return *True* if the current manifest matches *expected*."""
    return compute_origin_sha256() == expected


def assert_origin(expected: str = ORIGIN_SHA256) -> None:
    """Raise :class:`SecurityError` if origin verification fails."""
    if not verify_origin(expected):
        raise SecurityError(
            "Origin watermark mismatch — source may have been tampered with."
        )


def watermark_line() -> str:
    """Return the ``# __ORIGIN__:<sha256>`` comment string."""
    return f"# __ORIGIN__:{ORIGIN_SHA256}"
