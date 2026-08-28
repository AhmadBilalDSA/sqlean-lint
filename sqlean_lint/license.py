"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

License activation with online-then-offline caching for sqlean-lint Pro.

All validation is local-first: the server issues a signed blob that is
cached to disk and verified offline on subsequent runs via HMAC-SHA256.
Network calls are attempted only during :func:`activate` and are never
made for read operations.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .features import data_dir

# ── Constants ──────────────────────────────────────────────────────────

SIG_FILE_NAME: str = "license.sig"
MACHINE_FILE_NAME: str = "machine_id"
DEFAULT_VALIDATE_ENDPOINT: str = "https://api.polar.sh/v1/licenses/validate"
_PEPPER: bytes = b"sqlean-lint-offline-license-v1"


# ── Exceptions ─────────────────────────────────────────────────────────

class LicenseError(RuntimeError):
    """Raised when license activation, validation or caching fails."""


# ── Dataclasses ────────────────────────────────────────────────────────

@dataclass
class LicenseInfo:
    """Describes the current license state of the installation."""

    key_hint: str = ""
    tier: str = "pro"
    issued: str = ""
    expires: str = ""
    customer_email: str = ""
    valid: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_hint: bool = True) -> Dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        result: Dict[str, Any] = {
            "tier": self.tier,
            "issued": self.issued,
            "expires": self.expires,
            "customer_email": self.customer_email,
            "valid": self.valid,
            "extra": dict(self.extra),
        }
        if include_hint:
            result["key_hint"] = self.key_hint
        return result


# ── Internal helpers ───────────────────────────────────────────────────

def _machine_id_path() -> Path:
    return data_dir() / MACHINE_FILE_NAME


def _license_path() -> Path:
    return data_dir() / SIG_FILE_NAME


def _machine_fingerprint() -> str:
    """Compute ``SHA256(machine_id | username)`` as a hex string.

    The machine_id is read from ``data_dir() / MACHINE_FILE_NAME`` and is
    generated from ``uuid.getnode()`` if it does not yet exist.
    """
    mid_path = _machine_id_path()
    if mid_path.is_file():
        raw = mid_path.read_text(encoding="utf-8").strip()
    else:
        raw = str(uuid.getnode())
        mid_path.write_text(raw, encoding="utf-8")

    username = os.getenv("USERNAME") or os.getenv("USER") or "unknown"
    digest = hashlib.sha256(f"{raw}|{username}".encode("utf-8")).hexdigest()
    return digest


def _sign_payload(data_dict: Dict[str, Any]) -> str:
    """Return a base64 HMAC-SHA256 signature over the canonical JSON of *data_dict*."""
    canonical = json.dumps(data_dict, sort_keys=True, separators=(",", ":"))
    sig = hmac.new(_PEPPER, canonical.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(sig).decode("ascii")


def _verify_payload(data_dict: Dict[str, Any], signature_b64: str) -> bool:
    """Constant-time comparison of *data_dict* against *signature_b64*."""
    expected_b64 = _sign_payload(data_dict)
    try:
        expected_bytes = base64.b64decode(expected_b64)
        actual_bytes = base64.b64decode(signature_b64)
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(expected_bytes, actual_bytes)


def _cache_license(info: LicenseInfo) -> None:
    """Write the signed license blob to disk."""
    blob: Dict[str, Any] = {
        "key_hint": info.key_hint,
        "tier": info.tier,
        "issued": info.issued,
        "expires": info.expires,
        "customer_email": info.customer_email,
        "valid": info.valid,
        "extra": info.extra,
    }
    signature = _sign_payload(blob)
    payload = {"blob": blob, "signature": signature}
    _license_path().write_text(json.dumps(payload), encoding="utf-8")


def _offline_validate(key: str) -> Optional[LicenseInfo]:
    """Attempt offline validation using the cached license blob.

    Returns a :class:`LicenseInfo` if the cached signature matches and the
    key hint is a prefix of the supplied key; otherwise ``None``.
    """
    lic_path = _license_path()
    if not lic_path.is_file():
        return None
    try:
        payload = json.loads(lic_path.read_text(encoding="utf-8"))
        blob = payload["blob"]
        signature = payload["signature"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None

    if not _verify_payload(blob, signature):
        return None

    hint = blob.get("key_hint", "")
    if hint and not key.startswith(hint):
        return None

    return LicenseInfo(
        key_hint=hint,
        tier=blob.get("tier", "pro"),
        issued=blob.get("issued", ""),
        expires=blob.get("expires", ""),
        customer_email=blob.get("customer_email", ""),
        valid=blob.get("valid", True),
        extra=blob.get("extra", {}),
    )


# ── Public API ─────────────────────────────────────────────────────────

def activate(key: str) -> LicenseInfo:
    """Activate a license key.

    First attempts online validation against Polar.sh, then falls back
    to offline validation of the cached blob.  Raises :class:`LicenseError`
    when both paths fail.
    """
    if not key or not key.strip():
        raise LicenseError("License key must not be empty.")

    fingerprint = _machine_fingerprint()
    request_body = json.dumps({
        "license_key": key,
        "machine_id": fingerprint,
    }).encode("utf-8")

    # Try online validation first.
    try:
        req = Request(
            DEFAULT_VALIDATE_ENDPOINT,
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:  # noqa: S310 - trusted endpoint
            body = json.loads(resp.read().decode("utf-8"))

        info = LicenseInfo(
            key_hint=key[:8] + "..." if len(key) > 8 else key,
            tier=body.get("tier", "pro"),
            issued=body.get("issued", ""),
            expires=body.get("expires", ""),
            customer_email=body.get("customer_email", ""),
            valid=body.get("valid", True),
            extra=body.get("extra", {}),
        )
        _cache_license(info)
        return info
    except (URLError, OSError, json.JSONDecodeError, KeyError, ValueError):
        pass  # Fall through to offline validation.

    # Offline fallback.
    cached = _offline_validate(key)
    if cached is not None and cached.valid:
        return cached

    raise LicenseError(
        "License validation failed (network unavailable and no valid cached license)."
    )


def load_cached() -> Optional[LicenseInfo]:
    """Read and verify the cached license.  Returns ``None`` when unavailable."""
    lic_path = _license_path()
    if not lic_path.is_file():
        return None
    try:
        payload = json.loads(lic_path.read_text(encoding="utf-8"))
        blob = payload["blob"]
        signature = payload["signature"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None

    if not _verify_payload(blob, signature):
        return None

    return LicenseInfo(
        key_hint=blob.get("key_hint", ""),
        tier=blob.get("tier", "pro"),
        issued=blob.get("issued", ""),
        expires=blob.get("expires", ""),
        customer_email=blob.get("customer_email", ""),
        valid=blob.get("valid", True),
        extra=blob.get("extra", {}),
    )


def deactivate() -> bool:
    """Remove the cached license file.  Returns *True* on success."""
    lic_path = _license_path()
    if lic_path.is_file():
        try:
            lic_path.unlink()
            return True
        except OSError:
            return False
    return False
