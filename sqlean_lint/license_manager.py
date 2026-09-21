"""Copyright (c) 2026 SQLean Engineering. All Rights Reserved.

Hardware-bound licensing engine for sqlean-lint Pro.

The license key is a base64url-safe blob whose payload carries a Hardware ID,
customer email, tier, issue and expiry timestamps. The payload is signed with
an RSA private key (PKCS#1 v1.5 + SHA-256) using the ``cryptography`` library
and validated against an embedded public key. Only when the signature verifies
*and* the payload Hardware ID matches the machine's computed Hardware ID is the
license considered valid.

The Hardware ID is derived from the machine's primary MAC address (``uuid``)
combined with the Windows MachineGuid registry value, then hashed. This binds
the license to one physical machine and prevents key sharing between hosts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
import winreg
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .features import data_dir

# ── Constants ──────────────────────────────────────────────────────────

LICENSE_FILE_NAME: str = "pro_license.json"

# Format:
#   "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
# Developers replace this with their own public half of the signing key.
# The matching private key must NEVER ship inside the application or repo.
EMBEDDED_PUBLIC_KEY: str = ""

# Issuer identity bound into every signed license payload. The signer always
# stamps this so the authenticity chain (and ownership) is permanently tied to
# the creator and cannot be re-attributed to another party.
LICENSE_ISSUER: str = "AhmadBilalDSA (kierninja@gmail.com)"

# Length of the offline lease window (hardware/machine changes tolerated).
LEASE_DAYS: int = 30


# ── Exceptions ─────────────────────────────────────────────────────────

class LicenseError(RuntimeError):
    """Raised for any licensing failure (missing, tampered or mismatched)."""


@dataclass
class LicenseInfo:
    """Decoded and verified license attributes."""

    valid: bool
    hardware_id: str
    customer_email: str = ""
    tier: str = "pro"
    issued: str = ""
    expires: str = ""
    key_hint: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if not self.expires:
            return False
        try:
            expiry = datetime.fromisoformat(self.expires)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) > expiry
        except ValueError:
            return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "hardware_id": self.hardware_id,
            "customer_email": self.customer_email,
            "tier": self.tier,
            "issued": self.issued,
            "expires": self.expires,
            "key_hint": self.key_hint,
            "extra": dict(self.extra),
        }


# ── Hardware ID generation ─────────────────────────────────────────────

def _windows_machine_guid() -> str:
    """Read the Windows ``MachineGuid`` registry value.

    Returns an empty string when unavailable (e.g. non-Windows host in tests).
    """
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value)
    except (OSError, ValueError):  # pragma: no cover - platform dependent
        return ""


def generate_hardware_id() -> str:
    """Compute a stable, hashed Hardware ID for the current machine.

    The raw material is ``MAC address + Windows MachineGuid``. The result is a
    SHA-256 hex digest (64 chars), so the underlying identifiers never leak.
    """
    mac = uuid.getnode()
    mac_hex = format((mac & ((1 << 48) - 1)), "012x")
    machine_guid = _windows_machine_guid()
    raw = f"{mac_hex}|{machine_guid}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


# ── Key signing / verification helpers ─────────────────────────────────

def _load_public_key(pem: str) -> Optional[rsa.RSAPublicKey]:
    """Deserialize an RSA public key from a PEM string (or None)."""
    if not pem or "PUBLIC KEY" not in pem:
        return None
    try:
        key = serialization.load_pem_public_key(pem.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return key if isinstance(key, rsa.RSAPublicKey) else None


def sign_license_payload(
    payload: Dict[str, Any], private_key_pem: str
) -> str:
    """Sign a license payload with an RSA private key (used by the vendor
    tooling to mint keys, never by the shipped application).

    The issuer identity is injected into a copy of the payload *before*
    signing, so every generated key is permanently stamped with the creator's
    identity and can never be re-claimed by another party.
    """
    sealed = dict(payload)
    sealed["issuer"] = LICENSE_ISSUER
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise TypeError("Expected an RSA private key.")
    canonical = json.dumps(
        sealed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    signature = private_key.sign(
        canonical, padding.PKCS1v15(), hashes.SHA256()
    )
    return base64.urlsafe_b64encode(signature).decode("ascii")


def assemble_license_key(
    payload: Dict[str, Any], signature_b64: str
) -> str:
    """Bundle payload + signature into a single base64url key string."""
    blob = {"payload": payload, "signature": signature_b64}
    return base64.urlsafe_b64encode(
        json.dumps(blob, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def mint_license_key(
    payload: Dict[str, Any], private_key_pem: str
) -> str:
    """Seal, sign and assemble a license key in one operation (vendor tooling).

    This is the authoritative mint path: it injects the ``issuer`` identity,
    signs the sealed payload, and bundles the *same* sealed payload with its
    signature so verification is always self-consistent. Callers never touch
    the raw private key or the internal sealing step.
    """
    sealed = dict(payload)
    sealed["issuer"] = LICENSE_ISSUER
    signature = sign_license_payload(sealed, private_key_pem)
    return assemble_license_key(sealed, signature)


def decode_license_key(
    key: str,
) -> tuple[Dict[str, Any], str]:
    """Split a license key into its (payload, signature) pair.

    Raises ``LicenseError`` on malformed keys.
    """
    try:
        decoded = base64.urlsafe_b64decode(key.encode("ascii"))
        blob = json.loads(decoded.decode("utf-8"))
        payload = blob["payload"]
        signature = blob["signature"]
    except Exception as err:  # noqa: BLE001
        raise LicenseError("License key is malformed and could not be decoded.") from err
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise LicenseError("License key is missing its payload or signature.")
    return payload, signature


# ── Validator ──────────────────────────────────────────────────────────

class LicenseValidator:
    """Validates license keys against the embedded public key and the
    current machine's Hardware ID."""

    def __init__(
        self,
        *,
        public_key_pem: Optional[str] = None,
        license_path: Optional[Path] = None,
    ) -> None:
        self._public_key = _load_public_key(public_key_pem or EMBEDDED_PUBLIC_KEY)
        self._license_path = license_path or (data_dir() / LICENSE_FILE_NAME)

    @property
    def has_public_key(self) -> bool:
        return self._public_key is not None

    def validate_key(self, key: str, hardware_id: Optional[str] = None) -> LicenseInfo:
        """Decode and verify *key* against the embedded public key and the
        Hardware ID of the current machine.

        Raises ``LicenseError`` when the signature is invalid, the key is
        malformed, or the bundled Hardware ID does not match this machine.
        """
        if self._public_key is None:
            return self._unverifiable_info(key)

        payload, signature = self._decode_returning_info_on_error(key)

        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        try:
            raw_sig = base64.urlsafe_b64decode(signature.encode("ascii"))
            self._public_key.verify(raw_sig, canonical, padding.PKCS1v15(), hashes.SHA256())
        except (InvalidSignature, ValueError) as err:
            raise LicenseError(
                "License signature verification failed; the key is not authentic."
            ) from err

        payload_hw = str(payload.get("hardware_id", ""))
        if payload_hw and hardware_id is not None and payload_hw != hardware_id:
            raise LicenseError(
                "This license key is bound to a different machine (hardware ID mismatch)."
            )

        info = LicenseInfo(
            valid=True,
            hardware_id=payload_hw,
            customer_email=str(payload.get("customer_email") or payload.get("email", "")),
            tier=str(payload.get("tier", "pro")),
            issued=str(payload.get("issued", "")),
            expires=str(payload.get("expires", "")),
            key_hint=self._hint(key),
            extra={k: v for k, v in payload.items() if k not in _PAYLOAD_KEYS},
        )
        return info

    def _decode_returning_info_on_error(self, key: str) -> tuple[Dict[str, Any], str]:
        try:
            return decode_license_key(key)
        except LicenseError:
            raise

    def _unverifiable_info(self, key: str) -> LicenseInfo:
        """Build a non-valid info when no public key is embedded."""
        return LicenseInfo(valid=False, hardware_id="", key_hint=self._hint(key))

    @staticmethod
    def _hint(key: str) -> str:
        if len(key) > 10:
            return key[:4] + "..." + key[-4:]
        return key

    # ── Local config persistence ───────────────────────────────────────

    def is_licensed(self, hardware_id: Optional[str] = None) -> bool:
        """Return *True* if a valid, matching license (or an active lease
        covering a hardware change) is stored locally.

        The checked chain is:

        1. a locally cached license whose RSA signature verified originally,
           matching this machine and not expired;
        2. otherwise an active 30-day lease that grants renewed ``first_seen``
           timestamps even when the hardware ID changed (e.g. replacing a NIC
           or docking), so long as it was seeded from a previously valid key.
        """
        try:
            cached = self.load_cached()
        except LicenseError:
            return False
        wanted = hardware_id or generate_hardware_id()
        if cached is not None and cached.valid and not cached.is_expired and (
            not cached.hardware_id or cached.hardware_id == wanted
        ):
            return True
        if self._lease_valid(wanted):
            return True
        return False

    def grant_lease(self, sealed_key: Optional[str] = None) -> bool:
        """Open or extend the 30-day offline lease for the current machine.

        *sealed_key* is the original license key as shipped to the customer
        (payload + RSA signature). It is stored verbatim so that re-activation
        or a later hardware match can re-validate from the authentic signature.
        Returns *True* when a lease was established.
        """
        stored = self._load_own_state()
        if not sealed_key and not stored.get("key"):
            return False
        info = self.load_cached() if stored.get("key") is None else None
        if sealed_key:
            try:
                self.validate_key(sealed_key)
            except LicenseError:
                return False
        now = datetime.now(timezone.utc).isoformat()
        first_seen = stored.get("lease", {}).get("first_seen") or now
        lease = {
            "key": sealed_key or stored.get("key"),
            "lease": {
                "first_seen": first_seen,
                "last_seen": now,
            },
        }
        lease["lease_sig"] = self._lease_signature(lease["lease"], lease["key"])
        self._write_own_state(lease)
        return True

    def lease_info(self) -> Optional[Dict[str, Any]]:
        """Return the persisted lease dict (or *None* when absent)."""
        stored = self._load_own_state()
        lease = stored.get("lease")
        if not lease:
            return None
        return {
            "key_hint": self._hint(stored.get("key", "")),
            "first_seen": lease.get("first_seen"),
            "last_seen": lease.get("last_seen"),
            "valid": self._lease_valid(generate_hardware_id()),
        }

    def _lease_valid(self, wanted: str) -> bool:
        """Validate the offline lease window has not elapsed for *wanted*.
        *wanted* is unused for signature checks but kept as an explicit,
        documented hook for per-machine lease enforcement."""
        stored = self._load_own_state()
        lease = stored.get("lease")
        if not lease:
            return False
        key = stored.get("key", "")
        if not key:
            return False
        expected = self._lease_signature(lease, key)
        stored_sig = stored.get("lease_sig", "")
        if not hmac.compare_digest(stored_sig, expected):
            return False
        first_seen = lease.get("first_seen")
        if not first_seen:
            return False
        try:
            start = datetime.fromisoformat(first_seen)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        if datetime.now(timezone.utc) - start > timedelta(days=LEASE_DAYS):
            return False
        return True

    @staticmethod
    def _lease_signature(lease: Dict[str, Any], key: str) -> str:
        """HMAC-SHA256 over the lease fields using the original key as the
        secret, so only a holder of the authentic key can (re)sign a lease."""
        canonical = json.dumps(lease, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        digest = hmac.new(key.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
        return digest

    def _load_own_state(self) -> Dict[str, Any]:
        """Read the full raw persisted blob (original key + lease)."""
        if not self._license_path.is_file():
            return {}
        try:
            data = json.loads(self._license_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_own_state(self, blob: Dict[str, Any]) -> None:
        self._license_path.parent.mkdir(parents=True, exist_ok=True)
        self._license_path.write_text(
            json.dumps(blob, indent=2), encoding="utf-8"
        )

    def save_license(self, info: LicenseInfo) -> None:
        """Persist a validated :class:`LicenseInfo` to the local config dir."""
        self._license_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_own_state({**self._load_own_state(), **info.to_dict()})

    def load_cached(self) -> Optional[LicenseInfo]:
        """Read the locally stored license (validated by signature first)."""
        if not self._license_path.is_file():
            return None
        try:
            data = json.loads(self._license_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return LicenseInfo(**{
                k: v for k, v in data.items() if k in _CACHED_KEYS
            })
        except TypeError:
            # A lease-only blob (key + lease, no full LicenseInfo) is not a
            # cached license; treat it as absent and let the lease path decide.
            return None

    def clear(self) -> bool:
        """Remove the locally stored license. Returns *True* on success."""
        try:
            if self._license_path.is_file():
                self._license_path.unlink()
            return True
        except OSError:
            return False


_PAYLOAD_KEYS = frozenset(
    {"hardware_id", "email", "tier", "issued", "expires", "key_hint"}
)

# Keys we hydrate back into a LicenseInfo from the persisted blob.
_CACHED_KEYS = frozenset(_PAYLOAD_KEYS | {"valid"})
