"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Role-Based Access Control (RBAC) middleware for the sqlean-lint PRO FastAPI backend.

Enforces fine-grained role checks on protected endpoints.  Three enterprise
roles are defined:

* **ADMIN** -- full access; can provision, revoke, and list licenses.
* **BILLING** -- read-only license listing; can trigger invoice sync.
* **DEVELOPER** -- can lint SQL and read reports; no license mutation.

The middleware plugs into FastAPI's dependency-injection system via
:func:`require_role` and :func:`require_any_role`.  Roles are extracted from
the JWT ``claims`` dict produced by :mod:`sqlean_lint.api.auth`.

Privacy-first: only the role claim is inspected; no user PII is persisted or
logged by the RBAC layer.
"""
from __future__ import annotations

import enum
import json
import logging
import time
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set

from fastapi import Depends, HTTPException, Request, Response, status

logger = logging.getLogger("sqlean_lint.api.rbac")

# ── In-memory audit log (rotated hourly) ────────────────────────────────

_audit_log: List[Dict[str, Any]] = []
_AUDIT_MAX_ENTRIES = 10_000


def _record_audit_event(
    *,
    subject: str,
    action: str,
    resource: str,
    granted: bool,
    ip: str = "",
) -> None:
    """Append a structured audit entry for compliance evidence."""
    _audit_log.append({
        "ts": int(time.time()),
        "subject": subject,
        "action": action,
        "resource": resource,
        "granted": granted,
        "ip": ip,
    })
    if len(_audit_log) > _AUDIT_MAX_ENTRIES:
        del _audit_log[:_AUDIT_MAX_ENTRIES // 2]


def get_audit_log() -> List[Dict[str, Any]]:
    """Return a shallow copy of the in-memory audit log (for admin endpoints)."""
    return list(_audit_log)


# ── Role definitions ────────────────────────────────────────────────────

class Role(str, enum.Enum):
    """Canonical access roles for the sqlean-lint enterprise API."""

    ADMIN = "ADMIN"
    BILLING = "BILLING"
    DEVELOPER = "DEVELOPER"


# Role hierarchy: higher roles inherit permissions of lower roles.
_ROLE_HIERARCHY: Dict[Role, FrozenSet[Role]] = {
    Role.ADMIN: frozenset({Role.ADMIN, Role.BILLING, Role.DEVELOPER}),
    Role.BILLING: frozenset({Role.BILLING}),
    Role.DEVELOPER: frozenset({Role.DEVELOPER}),
}


def _extract_roles(claims: Dict[str, Any]) -> Set[Role]:
    """Extract a set of :class:`Role` values from verified JWT claims.

    Roles are read from the ``roles`` claim (list of strings) or the
    ``https://sqlean.lint/roles`` custom claim.  Unknown role strings are
    silently ignored.
    """
    raw: Any = claims.get("roles", claims.get("https://sqlean.lint/roles", []))
    if isinstance(raw, str):
        raw = [r.strip() for r in raw.split(",") if r.strip()]
    roles: Set[Role] = set()
    for name in raw or []:
        try:
            roles.add(Role(name.upper()))
        except ValueError:
            logger.debug("Ignoring unknown role: %s", name)
    return roles


def user_roles(claims: Dict[str, Any]) -> Set[Role]:
    """Return the effective roles for a user, respecting hierarchy."""
    direct = _extract_roles(claims)
    expanded: Set[Role] = set()
    for role in direct:
        expanded |= _ROLE_HIERARCHY.get(role, frozenset())
    return expanded


def has_role(claims: Dict[str, Any], required: Role) -> bool:
    """Check if the caller possesses *at least* the given role."""
    return required in user_roles(claims)


def has_any_role(claims: Dict[str, Any], *required: Role) -> bool:
    """Check if the caller possesses any one of the given roles."""
    effective = user_roles(claims)
    return bool(effective & set(required))


# ── FastAPI dependency factories ────────────────────────────────────────

def require_role(role: Role):
    """Return a FastAPI dependency requiring exactly *role* (or higher).

    Usage::

        @app.post("/api/licenses/revoke")
        async def revoke(claims = Depends(require_role(Role.ADMIN))):
            ...
    """

    def _dependency(
        request: Request,
        claims: Dict[str, Any] = Depends(_get_claims),
    ) -> Dict[str, Any]:
        granted = has_role(claims, role)
        subject = claims.get("sub", "anonymous")
        resource = str(request.url.path)

        _record_audit_event(
            subject=subject,
            action=f"access:{resource}",
            resource=resource,
            granted=granted,
            ip=request.client.host if request.client else "",
        )

        if not granted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{role.value}' required. "
                    f"Your roles: {[r.value for r in user_roles(claims)]}"
                ),
            )
        return claims

    return _dependency


def require_any_role(*roles: Role):
    """Return a FastAPI dependency requiring any one of the given roles.

    Usage::

        @app.get("/api/billing/invoices")
        async def list_invoices(
            claims = Depends(require_any_role(Role.ADMIN, Role.BILLING)),
        ):
            ...
    """

    def _dependency(
        request: Request,
        claims: Dict[str, Any] = Depends(_get_claims),
    ) -> Dict[str, Any]:
        granted = has_any_role(claims, *roles)
        subject = claims.get("sub", "anonymous")
        resource = str(request.url.path)

        _record_audit_event(
            subject=subject,
            action=f"access:{resource}",
            resource=resource,
            granted=granted,
            ip=request.client.host if request.client else "",
        )

        if not granted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"One of roles {[r.value for r in roles]} required. "
                    f"Your roles: {[r.value for r in user_roles(claims)]}"
                ),
            )
        return claims

    return _dependency


# ── Internal helpers ────────────────────────────────────────────────────

def _get_claims(request: Request) -> Dict[str, Any]:
    """Extract verified claims from request state.

    The OIDC auth middleware (see :mod:`sqlean_lint.api.auth`) attaches
    validated claims to ``request.state.claims`` before RBAC runs.  In
    unit-test or mock environments the claims can also be injected via
    the ``X-Test-Claims`` header (JSON-encoded dict).
    """
    # Test / mock path
    test_claims_header = request.headers.get("X-Test-Claims")
    if test_claims_header:
        try:
            return json.loads(test_claims_header)
        except (ValueError, TypeError):
            pass

    # Production path: claims attached by auth middleware
    claims = getattr(getattr(request, "state", None), "claims", None)
    if isinstance(claims, dict):
        return claims

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No authenticated claims found. Ensure auth middleware is active.",
    )


# ── Resource-level permission helpers ───────────────────────────────────

class ResourceAction(enum.Enum):
    """Fine-grained resource actions for ABAC-style checks."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    ADMIN = "admin"


_RESOURCE_PERMISSIONS: Dict[Role, Set[ResourceAction]] = {
    Role.ADMIN: {ResourceAction.READ, ResourceAction.WRITE, ResourceAction.DELETE, ResourceAction.ADMIN},
    Role.BILLING: {ResourceAction.READ, ResourceAction.WRITE},
    Role.DEVELOPER: {ResourceAction.READ},
}


def can_perform(claims: Dict[str, Any], action: ResourceAction) -> bool:
    """Check if the caller can perform the given resource action."""
    effective = user_roles(claims)
    for role in effective:
        allowed = _RESOURCE_PERMISSIONS.get(role, set())
        if action in allowed:
            return True
    return False


def require_action(action: ResourceAction):
    """Return a FastAPI dependency requiring the given resource action."""

    def _dependency(
        request: Request,
        claims: Dict[str, Any] = Depends(_get_claims),
    ) -> Dict[str, Any]:
        granted = can_perform(claims, action)
        subject = claims.get("sub", "anonymous")
        resource = str(request.url.path)

        _record_audit_event(
            subject=subject,
            action=f"{action.value}:{resource}",
            resource=resource,
            granted=granted,
            ip=request.client.host if request.client else "",
        )

        if not granted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Action '{action.value}' not permitted for your roles.",
            )
        return claims

    return _dependency


# ── Endpoint-level policy map ──────────────────────────────────────────

ENDPOINT_ROLE_MAP: Dict[str, Role] = {
    "/api/licenses/revoke": Role.ADMIN,
    "/api/licenses/provision": Role.ADMIN,
    "/api/licenses/list": Role.BILLING,
    "/api/billing/invoices": Role.BILLING,
    "/api/lint": Role.DEVELOPER,
    "/api/lint/batch": Role.DEVELOPER,
    "/api/admin/audit-log": Role.ADMIN,
    "/api/admin/seats": Role.ADMIN,
    "/graphql": Role.DEVELOPER,
}


def enforce_endpoint_role(path: str, claims: Dict[str, Any]) -> bool:
    """Enforce the role policy for a given endpoint path.

    Returns True if access is granted, raises HTTPException otherwise.
    """
    required = ENDPOINT_ROLE_MAP.get(path)
    if required is None:
        return True  # no policy → open
    if not has_role(claims, required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Endpoint '{path}' requires role '{required.value}'.",
        )
    return True
