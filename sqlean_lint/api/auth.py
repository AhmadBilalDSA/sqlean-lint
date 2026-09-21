"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Identity & Access Management (IAM) middleware for the sqlean-lint PRO API.

Validates JSON Web Tokens (JWT) using Auth0/Okta OIDC flows before allowing
REST API access. Implements:

  * OIDC discovery (JWKS fetch + key rotation).
  * JWT signature verification with RS256.
  * Audience and issuer validation.
  * Role-based access control (RBAC) via token claims.
  * Caching of JWKS to minimize network round-trips.

All crypto validation is **offline** after the one-time JWKS fetch; no calls
to the identity provider occur on each request.

Privacy-first: token validation never stores or logs the raw JWT; only the
validated claims (subject, roles, scopes) are retained in memory.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("sqlean_lint.api.auth")

# FastAPI bearer token security scheme.
_bearer_scheme = HTTPBearer(auto_error=False)

# Cache for the token validator (JWKS, algorithms).
_token_validator: Optional["_TokenValidator"] = None
_validator_lock = threading.Lock()

# Default OIDC discovery URL patterns for Auth0 / Okta.
AUTH0_DISCOVERY = "https://{tenant}.auth0.com/.well-known/openid-configuration"
OKTA_DISCOVERY = "https://{org}.okta.com/.well-known/openid-configuration"


class AuthError(Exception):
    """Raised when token validation fails."""


def _discovery_url() -> Optional[str]:
    """Resolve the OIDC discovery URL from env configuration."""
    explicit = os.environ.get("OIDC_DISCOVERY_URL")
    if explicit:
        return explicit
    tenant = os.environ.get("AUTH0_TENANT")
    if tenant:
        return AUTH0_DISCOVERY.format(tenant=tenant)
    org = os.environ.get("OKTA_ORG")
    if org:
        return OKTA_DISCOVERY.format(org=org)
    domain = os.environ.get("AUTH0_DOMAIN")
    if domain:
        return f"https://{domain}/.well-known/openid-configuration"
    return None


class _TokenValidator:
    """Validates JWTs against an OIDC issuer's JWKS.

    Fetches the issuer's JWKS once (cached), and validates tokens offline
    thereafter, rotating keys automatically on failure.
    """

    def __init__(self, issuer: str, audience: str, jwks_uri: str) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_uri = jwks_uri
        self._jwks_cache: Optional[Dict[str, Any]] = None
        self._jwks_fetched_at: float = 0.0
        self._jwks_ttl_s: float = 3600.0
        self._algorithms: List[str] = ["RS256"]
        self._lock = threading.Lock()

    def _fetch_jwks(self, force: bool = False) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            if (
                not force
                and self._jwks_cache is not None
                and (now - self._jwks_fetched_at) < self._jwks_ttl_s
            ):
                return self._jwks_cache
            try:
                with urllib.request.urlopen(
                    self._jwks_uri, timeout=10
                ) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._jwks_cache = data
                self._jwks_fetched_at = now
                if "keys" in data:
                    key_ids = [k.get("kid", "") for k in data["keys"]]
                    logger.info(
                        "Fetched JWKS from %s (keys: %s)", self._jwks_uri, key_ids
                    )
                return data
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to fetch JWKS from %s: %s",
                               self._jwks_uri, exc)
                return self._jwks_cache

    def _get_public_key(self, kid: str) -> Optional[Any]:
        """Resolve the RSA public key for a given key ID."""
        jwks = self._fetch_jwks()
        if not jwks:
            return None
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.backends import default_backend
            import base64

            for key in jwks.get("keys", []):
                if key.get("kid") != kid:
                    continue
                n = self._b64url_to_int(key.get("n", ""))
                e = self._b64url_to_int(key.get("e", ""))
                return rsa.RSAPublicNumbers(e, n).public_key(default_backend())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to construct public key: %s", exc)
        return None

    @staticmethod
    def _b64url_to_int(value: str) -> int:
        import base64
        padded = value + "=" * (-len(value) % 4)
        return int.from_bytes(
            base64.urlsafe_b64decode(padded), byteorder="big"
        )

    def validate(self, token: str) -> Dict[str, Any]:
        """Validate a JWT and return its verified claims.

        Args:
            token: The raw JWT bearer token.

        Returns:
            The decoded and verified claims dict.

        Raises:
            AuthError: If the token is invalid, expired, or signature fails.
        """
        try:
            import jwt  # PyJWT (installed with python-jose or standalone)
        except ImportError:  # pragma: no cover
            try:
                from jose import jwt  # python-jose fallback  # type: ignore
            except ImportError as exc:
                raise AuthError(
                    "JWT library not installed. "
                    "Install with: pip install PyJWT"
                ) from exc

        parts = token.split(".")
        if len(parts) != 3:
            raise AuthError("Malformed JWT: expected header.payload.signature.")

        try:
            header = json.loads(
                jwt.get_unverified_header(token) if hasattr(jwt, "get_unverified_header")
                else self._decode_header(parts[0])
            )
        except Exception:  # noqa: BLE001
            header = {}

        kid = header.get("kid")
        public_key = self._get_public_key(kid) if kid else None
        if public_key is None:
            raise AuthError("Unknown or missing signing key id.")

        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except Exception as exc:  # noqa: BLE001
            if "Signature verification failed" in str(exc):
                # Key may have rotated; force a JWKS refresh and retry once.
                self._fetch_jwks(force=True)
                kid = header.get("kid")
                public_key = self._get_public_key(kid) if kid else None
                if public_key is not None:
                    try:
                        claims = jwt.decode(
                            token, public_key, algorithms=self._algorithms,
                            audience=self._audience, issuer=self._issuer,
                        )
                        return claims
                    except Exception as retry_exc:  # noqa: BLE001
                        raise AuthError(
                            f"Token validation failed after key rotation: {retry_exc}"
                        ) from retry_exc
            raise AuthError(f"Token validation failed: {exc}")
        return claims

    @staticmethod
    def _decode_header(segment: str) -> Dict[str, Any]:
        import base64
        padded = segment + "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))

    def extract_claims_summary(
        self, claims: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Return a privacy-safe subset of claims for RBAC decisions."""
        return {
            "sub": claims.get("sub", ""),
            "email": claims.get("email", ""),
            "roles": claims.get(
                "roles",
                claims.get("https://sqlean.lint/roles", []),
            ),
            "scope": claims.get("scope", "").split(),
            "org": claims.get("org_id", claims.get("org", "")),
            "tier": claims.get("https://sqlean.lint/tier", "free"),
        }


_instance: Optional[_TokenValidator] = None


def _get_validator() -> _TokenValidator:
    global _instance
    if _instance is not None:
        return _instance
    discovery = _discovery_url()
    audience = os.environ.get("AUTH0_AUDIENCE", os.environ.get("OKTA_AUDIENCE", ""))
    if not discovery:
        logger.warning(
            "OIDC not configured (no discovery URL). Set OIDC_DISCOVERY_URL, "
            "AUTH0_TENANT, or OKTA_ORG."
        )
        raise AuthError("OIDC discovery not configured.")
    if not audience:
        raise AuthError("No OIDC audience configured (set AUTH0_AUDIENCE).")

    with _validator_lock:
        if _instance is None:
            try:
                with urllib.request.urlopen(discovery, timeout=10) as resp:
                    meta = json.loads(resp.read().decode("utf-8"))
                _instance = _TokenValidator(
                    issuer=meta.get("issuer", ""),
                    audience=audience,
                    jwks_uri=meta.get("jwks_uri", ""),
                )
            except Exception as exc:  # noqa: BLE001
                raise AuthError(f"OIDC discovery failed: {exc}") from exc
    return _instance


def oidc_required(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Dict[str, Any]:
    """FastAPI dependency that enforces JWT authentication via OIDC.

    Args:
        credentials: The HTTP Authorization header credentials.

    Returns:
        The validated privacy-safe claims summary.

    Raises:
        HTTPException: 401 if credentials are missing or invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        validator = _get_validator()
        claims = validator.validate(token)
        return validator.extract_claims_summary(claims)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authentication unavailable.",
        ) from exc


def require_roles(*roles: str) -> Any:
    """Return a FastAPI dependency that enforces required roles.

    Args:
        *roles: One or more roles the caller must possess.

    Returns:
        A FastAPI dependency callable.
    """
    def dependency(claims: Dict[str, Any] = Depends(oidc_required)) -> Dict[str, Any]:
        user_roles = set(claims.get("roles", []))
        if not user_roles.intersection(roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(roles)}",
            )
        return claims
    return dependency


def require_scope(scope: str) -> Any:
    """Return a FastAPI dependency that enforces a required OAuth scope.

    Args:
        scope: The OAuth scope the caller must hold.

    Returns:
        A FastAPI dependency callable.
    """
    def dependency(claims: Dict[str, Any] = Depends(oidc_required)) -> Dict[str, Any]:
        scopes = set(claims.get("scope", []))
        if scope not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope: {scope}",
            )
        return claims
    return dependency


def user_tier(claims: Dict[str, Any]) -> str:
    """Convenience accessor for the caller's subscription tier."""
    return claims.get("tier", "free")


def verify_jwt_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Dict[str, Any]:
    """FastAPI dependency that decodes and verifies a JSON Web Token (JWT).

    Protects enterprise endpoints such as ``POST /lint`` from unauthorized
    usage. Browses the bearer credential, verifies the JWT signature (RS256
    against the configured OIDC issuer's JWKS), validates audience/issuer and
    expiry, then returns the privacy-safe claims summary for RBAC decisions.

    Args:
        credentials: The HTTP ``Authorization: Bearer <token>`` credentials.

    Returns:
        The validated, privacy-filtered claims summary (sub, email, roles,
        scopes, org, tier). Raw token material is never returned or logged.

    Raises:
        HTTPException: 401 when the token is missing or fails verification.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        validator = _get_validator()
        claims = validator.validate(token)
        return validator.extract_claims_summary(claims)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authentication unavailable or token invalid.",
        ) from exc
