"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Billing & monetization for sqlean-lint PRO.

Implements a Stripe/Chargebee webhook listener. Processes ``invoice.paid``
and ``customer.subscription.created`` events to automatically provision
``sqlean-lint PRO`` enterprise license files (``pro_license.json``).

Privacy-first: license files contain only non-identifying fields (customer
id, plan, expiry) that are required to validate the licensed binary. No
payment data, card numbers, or user content is ever persisted locally.

The webhook handlers are pure-data transformers; network I/O happens only
when the caller invokes the corresponding service methods.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sqlean_lint.finance.billing")

# Stripe event type constants.
STRIPE_INVOICE_PAID = "invoice.paid"
STRIPE_SUBSCRIPTION_CREATED = "customer.subscription.created"
STRIPE_SUBSCRIPTION_UPDATED = "customer.subscription.updated"
STRIPE_SUBSCRIPTION_DELETED = "customer.subscription.deleted"
STRIPE_CUSTOMER_CREATED = "customer.created"

# Chargebee event type prefixes.
CHARGEBEE_INVOICE_PAID = "invoice_generated"
CHARGEBEE_SUBSCRIPTION_CREATED = "subscription_created"

# Addressable license output location (default app config dir).
def _default_license_dir() -> Path:
    h = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME") or "."
    return Path(h) / "sqlean-lint"


def _license_secret() -> str:
    """Return the HMAC secret used to sign license files.

    Falls back to a stored secret file so the system works even when the
    env var is not set. In production set ``SQLEAN_LICENSE_HMAC_SECRET``.
    """
    from_env = os.environ.get("SQLEAN_LICENSE_HMAC_SECRET", "")
    if from_env:
        return from_env
    secret = os.environ.get("SQLEAN_LICENSE_SECRET", "")
    return secret or "dev-only-insecure-secret"


def _sign(payload: int) -> str:
    """Return an HMAC-SHA256 signature for a payload integer."""
    import hmac

    data = payload.to_bytes(8, "big")
    return hmac.new(
        _license_secret().encode(), data, hashlib.sha256
    ).hexdigest()


def _timestamp_days_from_now(days: int) -> int:
    return int(time.time()) + (days * 86400)


def generate_license_file(
    *,
    customer_id: str,
    plan: str = "enterprise",
    seats: int = 1,
    duration_days: int = 365,
    tenant: str = "",
    output_dir: Optional[Path] = None,
) -> Path:
    """Generate and persist an enterprise ``pro_license.json`` file.

    Args:
        customer_id: Stripe/Chargebee customer identifier.
        plan: License plan (``"enterprise"``, ``"team"``, ``"pro"``).
        seats: Number of licensed seats.
        duration_days: License duration in days from now.
        tenant: Optional enterprise tenant/org identifier.
        output_dir: Directory to write the license file (default app dir).

    Returns:
        Path to the written ``pro_license.json`` file.

    Raises:
        ValueError: If customer_id is empty or seats < 1.
    """
    if not customer_id:
        raise ValueError("customer_id is required.")
    if seats < 1:
        raise ValueError("seats must be >= 1.")

    out_dir = output_dir or _default_license_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    license_id = str(uuid.uuid4())
    issued_at = int(time.time())
    expires_at = _timestamp_days_from_now(duration_days)
    nonce = int.from_bytes(uuid.uuid4().bytes[:8], "big")

    payload: Dict[str, Any] = {
        "version": 2,
        "license_id": license_id,
        "customer_id": customer_id,
        "plan": plan,
        "tier": {"enterprise": 3, "team": 2, "pro": 1}.get(plan, 0),
        "seats": seats,
        "tenant": tenant,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "activation_grace_days": 30,
        "offline_lease_days": 30,
        "signature": _sign(nonce),
        "nonce_b64": nonce.to_bytes(8, "big").hex(),
    }

    out_path = out_dir / "pro_license.json"
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    logger.info("Provisioned %s license for customer %s (seats=%d)",
                plan, customer_id, seats)
    return out_path


def validate_license_file(path: Optional[Path] = None) -> Dict[str, Any]:
    """Validate an existing license file and return its state.

    Args:
        path: Path to the license file (default app dir).

    Returns:
        A dict with ``valid``, ``expired``, and license metadata fields.
    """
    license_path = path or (_default_license_dir() / "pro_license.json")
    if not license_path.exists():
        return {"valid": False, "expired": False, "reason": "not_found"}

    try:
        data = json.loads(license_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"valid": False, "expired": False, "reason": "corrupt"}

    now = int(time.time())
    expires_at = int(data.get("expires_at", 0))
    expired = expires_at > 0 and now > expires_at

    # Recompute the signature from the nonce.
    try:
        nonce = int(data.get("nonce_b64", ""), 16)
        expected = _sign(nonce)
        sig_ok = data.get("signature") == expected
    except (ValueError, TypeError):
        sig_ok = False

    return {
        "valid": sig_ok and not expired,
        "expired": expired,
        "signature_ok": sig_ok,
        "plan": data.get("plan", ""),
        "seats": data.get("seats", 0),
        "tenant": data.get("tenant", ""),
        "expires_at": expires_at,
        "customer_id": data.get("customer_id", ""),
    }


# ── Stripe webhook processing ─────────────────────────────────────────

def verify_stripe_signature(
    payload: bytes, signature: str, secret: Optional[str] = None
) -> bool:
    """Verify a Stripe webhook signature using ``stripe.Webhook``.

    Args:
        payload: Raw request body bytes.
        signature: The ``Stripe-Signature`` header value.
        secret: The ``whsec_`` signing secret (env ``STRIPE_WEBHOOK_SECRET``).

    Returns:
        True if the signature is valid.
    """
    secret = secret or os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        logger.warning("No Stripe webhook secret configured.")
        return False
    try:
        import stripe
        stripe.Webhook.construct_event(payload, signature, secret)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stripe signature verification failed: %s", exc)
        return False


def process_stripe_event(
    event: Dict[str, Any], *, output_dir: Optional[Path] = None,
    validate_signature: bool = True, raw_payload: Optional[bytes] = None,
    signature_header: Optional[str] = None,
) -> Dict[str, Any]:
    """Process a Stripe webhook event and provision licenses as needed.

    Args:
        event: The Stripe event dict.
        output_dir: Directory for license output.
        validate_signature: Whether to validate the event signature.
        raw_payload: Raw body (required only when validating signature).
        signature_header: Stripe-Signature header (required for validation).

    Returns:
        A dict describing the result: ``status``, ``event_type``,
        ``provisioned``, ``license_path`` (or ``license_provisioned`` bool).

    Raises:
        ValueError: If signature validation is requested and fails.
    """
    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if validate_signature:
        if not raw_payload or not signature_header:
            raise ValueError(
                "Signature validation requires raw_payload and signature_header."
            )
        if not verify_stripe_signature(raw_payload, signature_header):
            raise ValueError("Invalid Stripe webhook signature.")

    result: Dict[str, Any] = {
        "status": "ignored",
        "event_type": event_type,
        "provisioned": False,
        "license_path": None,
    }

    if event_type in (STRIPE_INVOICE_PAID,):
        # An invoice was paid — provision/refresh the license.
        customer_id = data.get("customer", "")
        sub = data.get("subscription")
        subscription_id = sub if isinstance(sub, str) else (sub or {}).get("id", "")

        # Determine plan/seats from line items where possible.
        plan = _plan_from_lines(data) or "enterprise"
        seats = _seats_from_lines(data) or 1

        result["status"] = "provisioned"
        result["provisioned"] = True
        result["plan"] = plan
        result["seats"] = seats
        result["customer_id"] = customer_id
        result["subscription_id"] = subscription_id
        license_path = generate_license_file(
            customer_id=customer_id or "unknown",
            plan=plan, seats=seats,
            output_dir=output_dir,
        )
        result["license_path"] = str(license_path)

    elif event_type == STRIPE_SUBSCRIPTION_CREATED:
        customer_id = data.get("customer", "")
        items = data.get("items", {}).get("data", [])
        plan = "enterprise"
        seats = 1
        for item in items:
            price = item.get("price") or {}
            metadata = price.get("metadata") or {}
            if metadata.get("plan"):
                plan = metadata["plan"]
            if metadata.get("seats"):
                try:
                    seats = int(metadata["seats"])
                except (TypeError, ValueError):
                    pass
        result["status"] = "provisioned"
        result["provisioned"] = True
        result["plan"] = plan
        result["seats"] = seats
        license_path = generate_license_file(
            customer_id=customer_id or "unknown",
            plan=plan, seats=seats,
            output_dir=output_dir,
        )
        result["license_path"] = str(license_path)

    elif event_type == STRIPE_SUBSCRIPTION_UPDATED:
        result["status"] = "updated"
        result["provisioned"] = False

    elif event_type == STRIPE_SUBSCRIPTION_DELETED:
        result["status"] = "revoked"
        result["provisioned"] = False

    elif event_type == STRIPE_CUSTOMER_CREATED:
        result["status"] = "customer_registered"
        result["provisioned"] = False

    return result


def _plan_from_lines(data: Dict[str, Any]) -> Optional[str]:
    """Extract a plan name from invoice line items."""
    for line in data.get("lines", {}).get("data", []):
        price = line.get("price") or {}
        metadata = price.get("metadata") or {}
        if metadata.get("plan"):
            return metadata["plan"]
    return None


def _seats_from_lines(data: Dict[str, Any]) -> Optional[int]:
    """Extract a seat count from invoice line items."""
    for line in data.get("lines", {}).get("data", []):
        price = line.get("price") or {}
        metadata = price.get("metadata") or {}
        if metadata.get("seats"):
            try:
                return int(metadata["seats"])
            except (TypeError, ValueError):
                pass
    return None


# ── Chargebee webhook processing ─────────────────────────────────────

def process_chargebee_event(
    event: Dict[str, Any], *, output_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """Process a Chargebee webhook event and provision licenses.

    Args:
        event: The Chargebee webhook payload.
        output_dir: License output directory.

    Returns:
        Result dict with status and optional license path.
    """
    event_type = event.get("event_type", "")
    content = event.get("content", {})
    customer = content.get("customer", {})
    subscription = content.get("subscription", {})
    customer_id = customer.get("id", "") or subscription.get("customer_id", "")

    result: Dict[str, Any] = {
        "status": "ignored",
        "event_type": event_type,
        "provisioned": False,
        "license_path": None,
    }

    if event_type in (CHARGEBEE_INVOICE_PAID, CHARGEBEE_SUBSCRIPTION_CREATED):
        plan = subscription.get("plan_id", "enterprise")
        seats = 1
        metadatas = subscription.get("addons", [])
        for addon in metadatas:
            if addon.get("addon_id") == "seats":
                seats = max(seats, int(addon.get("quantity", 1)))

        result["status"] = "provisioned"
        result["provisioned"] = True
        result["plan"] = plan
        result["seats"] = seats
        license_path = generate_license_file(
            customer_id=customer_id or "unknown",
            plan=plan, seats=seats,
            output_dir=output_dir,
        )
        result["license_path"] = str(license_path)

    return result


def provision_from_webhook(
    provider: str,
    payload: bytes,
    signature_header: str = "",
    *, output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """High-level entry point to provision licenses from a webhook.

    Args:
        provider: ``"stripe"`` or ``"chargebee"``.
        payload: Raw request body bytes.
        signature_header: Provider signature header (for Stripe).
        output_dir: License output directory.

    Returns:
        Result dict describing the provisioning outcome.
    """
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid webhook payload: {exc}") from exc

    if provider == "stripe":
        return process_stripe_event(
            event,
            output_dir=output_dir,
            validate_signature=bool(signature_header),
            raw_payload=payload,
            signature_header=signature_header or None,
        )
    if provider == "chargebee":
        return process_chargebee_event(event, output_dir=output_dir)
    raise ValueError(f"Unsupported billing provider: {provider}")


# ── Stripe checkout → cryptographic Pro license bridge ───────────────

STRIPE_CHECKOUT_SESSION_COMPLETED = "checkout.session.completed"


def _mint_crypto_license(*, customer_email: str, plan: str, seats: int) -> Dict[str, Any]:
    """Bridge to :mod:`sqlean_lint.license_manager` to mint an RSA-signed key.

    Reads the vendor RSA private key from ``SQLEAN_LICENSE_PRIVATE_KEY`` and
    mints a signed :class:`~sqlean_lint.license_manager.LicenseInfo`, persisting
    the resulting ``pro_license.json`` into the app data directory via the
    :class:`~sqlean_lint.license_manager.LicenseValidator`. Falls back to the
    HMAC-signed license format only when no RSA private key is configured so the
    integration degrades cleanly in a dev sandbox.

    Privacy-first: only the email, hardware binding, tier and expiry are
    persisted; no card or payment data is ever written locally.
    """
    from sqlean_lint.license_manager import (
        LICENSE_FILE_NAME,
        LicenseInfo,
        LicenseValidator,
        generate_hardware_id,
        mint_license_key,
    )

    private_key_pem = os.environ.get("SQLEAN_LICENSE_PRIVATE_KEY", "")
    hardware_id = generate_hardware_id()
    now = int(time.time())

    if private_key_pem:
        payload: Dict[str, Any] = {
            "hardware_id": hardware_id,
            "customer_email": customer_email,
            "email": customer_email,
            "tier": "pro" if plan == "pro" else "enterprise",
            "plan": plan,
            "seats": seats,
            "issued": __import__("datetime").datetime.fromtimestamp(
                now, __import__("datetime").timezone.utc
            ).isoformat(),
            "expires": __import__("datetime").datetime.fromtimestamp(
                now + (365 * 86400), __import__("datetime").timezone.utc
            ).isoformat(),
        }
        sealed_key = mint_license_key(payload, private_key_pem)

        validator = LicenseValidator()
        validator.validate_key(sealed_key, hardware_id)
        info = LicenseInfo(
            valid=True,
            hardware_id=hardware_id,
            customer_email=customer_email,
            tier=payload["tier"],
            issued=payload["issued"],
            expires=payload["expires"],
            key_hint=(
                sealed_key[:4] + "..." + sealed_key[-4:] if len(sealed_key) > 10
                else sealed_key
            ),
            extra={"plan": plan, "seats": seats},
        )
        validator.save_license(info)
        validator.grant_lease(sealed_key)
        return {
            "format": "rsa-signed",
            "license_path": str(validator._license_path),
            "key_hint": info.key_hint,
            "hardware_id": info.hardware_id,
        }

    license_path = generate_license_file(
        customer_id=customer_email or "stripe-checkout",
        plan=plan,
        seats=seats,
        output_dir=None,
    )
    return {
        "format": "hmac-signed",
        "license_path": str(license_path),
    }


def process_subscription_event(
    payload: bytes,
    sig_header: str,
    endpoint_secret: str,
    *,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Process a verified Stripe subscription webhook and mint a Pro license.

    Uses the official ``stripe`` Python package to construct (and thereby
    cryptographically verify) the event from ``payload`` + ``sig_header`` +
    ``endpoint_secret``. When the resulting event type is
    ``checkout.session.completed``, a new cryptographic ``pro_license.json`` is
    minted locally for the paying customer.

    Args:
        payload: Raw webhook request body (bytes).
        sig_header: The ``Stripe-Signature`` header value.
        endpoint_secret: The ``whsec_`` signing secret.
        output_dir: Optional directory for the license output.

    Returns:
        A result dict: ``verified``, ``event_type``, and on success
        ``provisioned``, ``customer_email``, ``plan``, ``seats`` and
        ``license_keys`` describing the minted file.

    Raises:
        ValueError: If the Stripe signature cannot be verified.
    """
    import stripe

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Stripe signature verification failed: {exc}") from exc

    event_type = event.get("type", "")
    result: Dict[str, Any] = {
        "verified": True,
        "event_type": event_type,
        "provisioned": False,
        "license_keys": [],
    }

    if event_type == STRIPE_CHECKOUT_SESSION_COMPLETED:
        data = event.get("data", {}).get("object", {})
        email = data.get("customer_email") or data.get("customer_details", {}).get(
            "email", ""
        )
        metadata = data.get("metadata") or {}
        plan = metadata.get("plan", "pro")
        try:
            seats = int(metadata.get("seats", 1))
        except (TypeError, ValueError):
            seats = 1

        license_info = _mint_crypto_license(
            customer_email=email, plan=plan, seats=seats
        )

        result["provisioned"] = True
        result["customer_email"] = email
        result["plan"] = plan
        result["seats"] = seats
        result["license_keys"] = [license_info]
        result["status"] = "provisioned"
        logger.info(
            "checkout.session.completed → minted %s license for %s (seats=%d)",
            plan, email or "customer", seats,
        )

    return result
