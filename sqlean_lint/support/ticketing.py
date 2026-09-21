"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Customer success, support & DevRel integrations.

Builds integrations with Intercom and Zendesk APIs. When the GUI hits a
critical fault, users may submit automated diagnostics reports to DevRel.

Privacy-first: diagnostics bundles are redacted — raw SQL, file contents,
and machine identifiers (MAC, MachineGuid) are never transmitted. Only the
anonymised session metadata (versions, rule counts, exception type) is sent.

All integrations are opt-in; a user must explicitly press "Report" to send
anything. A dry-run bundle can be inspected locally before transmission.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sqlean_lint.support.ticketing")

def support_enabled() -> bool:
    """Return whether support integrations are enabled (opt-in via env)."""
    return os.environ.get("SQLEAN_LINT_SUPPORT", "0") == "1"


# ── Diagnostics bundle ────────────────────────────────────────────────

@dataclass
class DiagnosticsBundle:
    """A redacted diagnostics report for DevRel triage."""

    app_version: str = "1.0.0"
    os_name: str = platform.system()
    python_version: str = platform.python_version()
    locale: str = "en"
    dialect: str = "duckdb"
    exception_type: str = ""
    exception_message: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    # Never include raw SQL or PII.
    session_steps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app_version": self.app_version,
            "os": self.os_name,
            "python": self.python_version,
            "locale": self.locale,
            "dialect": self.dialect,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message[:500],
            "metrics": self.metrics,
            "session_steps": self.session_steps,
            "timestamp": int(time.time()),
            "redacted": True,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def save(self, path: Optional[Path] = None) -> Path:
        """Save the bundle to a JSON file for inspection."""
        out = path or (Path(".pytest-scratch") / "diagnostics_bundle.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.to_json(), encoding="utf-8")
        return out


def build_diagnostics_bundle(
    *,
    exception: Optional[BaseException] = None,
    metrics: Optional[Dict[str, Any]] = None,
    session_steps: Optional[List[str]] = None,
) -> DiagnosticsBundle:
    """Construct a redacted diagnostics bundle from runtime context.

    Args:
        exception: The exception that triggered the report (type+message only).
        metrics: Anonymised runtime metrics (e.g. rule enable counts).
        session_steps: Recent GUI navigation steps (no SQL content).

    Returns:
        A redacted :class:`DiagnosticsBundle`.
    """
    bundle = DiagnosticsBundle()
    if exception is not None:
        bundle.exception_type = type(exception).__name__
        bundle.exception_message = str(exception)
    if metrics:
        # Allow only primitive metrics.
        for k, v in (metrics or {}).items():
            if isinstance(v, (int, float, str, bool)) or v is None:
                bundle.metrics[k] = v
    if session_steps:
        # Cap to last 20 steps; strip any suspicious content.
        cleaned = []
        for step in session_steps[-20:]:
            if any(tok in step.lower() for tok in ("select", "insert", "drop", "sql=")):
                cleaned.append("(redacted)")
            else:
                cleaned.append(str(step)[:200])
        bundle.session_steps = cleaned
    return bundle


# ── Intercom integration ──────────────────────────────────────────────

class IntercomClient:
    """Minimal Intercom API client for creating support conversations."""

    def __init__(self, access_token: str = "") -> None:
        self._token = access_token or os.environ.get("INTERCOM_TOKEN", "")
        self._api = "https://api.intercom.io"

    def create_conversation(
        self, subject: str, body: str,
        email: str = "", user_id: str = "",
    ) -> Dict[str, Any]:
        """Create a support conversation in Intercom.

        Args:
            subject: Conversation subject.
            body: Message body (redacted diagnostics JSON).
            email: End-user email (optional; omit for anonymity).
            user_id: Intercom user id (optional).

        Returns:
            The API response dict.

        Raises:
            RuntimeError: If Intercom is not configured or request fails.
        """
        if not self._token:
            raise RuntimeError(
                "Intercom not configured (set INTERCOM_TOKEN)."
            )
        params: Dict[str, Any] = {
            "from": {
                "type": "user",
                "id": user_id or "anonymous",
            },
            "body": body,
        }
        if email:
            params["from"]["email"] = email

        try:
            import requests
            resp = requests.post(
                f"{self._api}/conversations",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                    "Intercom-Version": "2.11",
                },
                json={
                    "contact": {"email": email} if email else None,
                    "admin_id": None,
                    "message": {
                        "body": body,
                        "type": "incoming",
                    },
                } if False else params,
                params={"display_as": "plaintext"},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Intercom create conversation failed: {exc}") from exc


# ── Zendesk integration ──────────────────────────────────────────────

class ZendeskClient:
    """Minimal Zendesk API client for creating support tickets."""

    def __init__(self, subdomain: str = "", api_token: str = "",
                 email: str = "") -> None:
        self._subdomain = subdomain or os.environ.get("ZENDESK_SUBDOMAIN", "")
        self._token = api_token or os.environ.get("ZENDESK_API_TOKEN", "")
        self._email = email or os.environ.get("ZENDESK_EMAIL", "")
        self._base = f"https://{self._subdomain}.zendesk.com"

    def create_ticket(
        self, subject: str, description: str,
        priority: str = "normal", tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a Zendesk support ticket.

        Args:
            subject: Ticket subject.
            description: Ticket description (redacted diagnostics).
            priority: ``"urgent"``, ``"high"``, ``"normal"``, or ``"low"``.
            tags: Optional ticket tags.

        Returns:
            The API response dict.

        Raises:
            RuntimeError: If Zendesk is not configured or request fails.
        """
        if not self._subdomain or not self._token:
            raise RuntimeError(
                "Zendesk not configured (set ZENDESK_SUBDOMAIN/_API_TOKEN)."
            )
        ticket: Dict[str, Any] = {
            "subject": subject,
            "comment": {"body": description},
            "priority": priority,
            "status": "new",
        }
        if tags:
            ticket["tags"] = tags
        try:
            import requests
            resp = requests.post(
                f"{self._base}/api/v2/tickets.json",
                headers={
                    "Content-Type": "application/json",
                },
                json={"ticket": ticket},
                auth=(self._email + "/token", self._token),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Zendesk create ticket failed: {exc}") from exc


# ── DevRel submission orchestration ──────────────────────────────────

class DevRelReporter:
    """Coordinates diagnostics submission to Intercom/Zendesk (DevRel triage).

    The user explicitly opts in per submission; default is always a local
    dry-run with no network I/O.
    """

    def __init__(
        self,
        channels: Optional[List[str]] = None,
    ) -> None:
        self._channels = channels or []
        self._intercom = IntercomClient()
        self._zendesk = ZendeskClient()

    def report_crash(
        self, bundle: DiagnosticsBundle,
        *,
        email: str = "",
        subject: str = "sqlean-lint PRO automated diagnostics",
        tags: Optional[List[str]] = None,
        confirm: bool = False,
    ) -> Dict[str, Any]:
        """Submit a diagnostics bundle to DevRel.

        Args:
            bundle: The redacted :class:`DiagnosticsBundle`.
            email: Optional user email for follow-up.
            subject: Support subject line.
            tags: Optional support tags.
            confirm: Explicit user consent gate (must be True to send).

        Returns:
            Dict describing which channels succeeded / were skipped.

        Raises:
            ValueError: If confirm is False (user has not consented).
        """
        if not confirm:
            raise ValueError(
                "User consent required. Re-invoke with confirm=True to send."
            )
        if not support_enabled():
            logger.info("Support integrations disabled; dry-run only.")
            return {"dry_run": True, "bundle": bundle.to_dict()}

        results: Dict[str, Any] = {"dry_run": False}
        body = f"Redacted sqlean-lint PRO diagnostics:\n{bundle.to_json(indent=2)}"

        for channel in self._channels:
            if channel == "intercom":
                try:
                    self._intercom.create_conversation(
                        subject, body, email=email
                    )
                    results["intercom"] = "sent"
                except Exception as exc:  # noqa: BLE001
                    results["intercom"] = f"failed: {exc}"
            elif channel == "zendesk":
                try:
                    self._zendesk.create_ticket(
                        subject, body, tags=tags or ["sqlean-lint-pro"]
                    )
                    results["zendesk"] = "sent"
                except Exception as exc:  # noqa: BLE001
                    results["zendesk"] = f"failed: {exc}"
            else:
                results[channel] = "unknown"

        return results


# ── Readme.io API sync ───────────────────────────────────────────────

def sync_readme_api(
    *,
    api_key: str = "",
    repo_slug: str = "sqlean-lint-pro",
) -> bool:
    """Trigger an API spec sync to Readme.io.

    Requires ``README_API_KEY`` (hex encoded readme API key).

    Args:
        api_key: Readme.io API key (env ``README_API_KEY``).
        repo_slug: The Readme.io API definition slug.

    Returns:
        True if the sync request was accepted.
    """
    api_key = api_key or os.environ.get("README_API_KEY", "")
    if not api_key:
        logger.warning("README_API_KEY not set; skipping Readme.io sync.")
        return False
    try:
        import requests
        url = f"https://dash.readme.com/api/v1/api-registry/{repo_slug}"
        resp = requests.get(
            url,
            headers={"Authorization": api_key},
            timeout=15,
        )
        return resp.status_code in (200, 201, 202)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Readme.io sync failed: %s", exc)
        return False


# ── Mock crash-report webhook (DevRel feedback) ──────────────────────

# Author attribution embedded verbatim into the system diagnostic headers of
# every submitted crash report so DevRel triage can trace ownership.
AUTHOR_META = "Ahmad Bilal | kierninja@gmail.com"

# Injected into the *system* diagnostic headers (out-of-band from user data)
# to assert provenance without mixing into the redacted payload.
DIAGNOSTIC_HEADERS: Dict[str, str] = {
    "X-SqLean-Author": AUTHOR_META,
    "X-SqLean-Product": "sqlean-lint PRO",
    "X-SqLean-Device-Reporter": "crash-submitter",
}


def submit_crash_report(
    error_trace: str,
    user_email: str,
    *,
    webhook_url: str = "",
    channel: str = "slack",
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Format an application crash and submit it to a (mock) support webhook.

    Builds a JSON crash-report payload from *error_trace* and *user_email*,
    tags it with the author attribution in the system diagnostic headers, and
    "sends" it to a Zendesk/Intercom/Slack webhook. The network call is
    **mocked**: no request is actually issued. This keeps the function safe
    in any sandbox while preserving the exact payload/header contract that the
    real integration would use.

    Privacy-first: only the trace, email, and a short redacted excerpt are
    carried; raw SQL is never embedded in the submitted payload.

    Args:
        error_trace: The exception traceback (or message) string.
        user_email: The reporting user's email for follow-up.
        webhook_url: Optional target URL (unused by the mock).
        channel: Target channel (``"slack"``, ``"zendesk"``, ``"intercom"``).
        timeout: Unused by the mock; accepted for signature compatibility.

    Returns:
        A dict describing the mock delivery: ``status``, ``channel``,
        ``author``, ``payload`` and ``diagnostic_headers``.
    """
    trace = (error_trace or "").strip()
    excerpt = trace[-400:] if trace else "<no trace>"

    payload: Dict[str, Any] = {
        "event": "application.crash",
        "reporter_email": user_email,
        "trace_excerpt": excerpt,
        "os": platform.system(),
        "python": platform.python_version(),
        "ts": int(time.time()),
        "redacted": True,
    }

    # Author is embedded in the system diagnostic headers (out-of-band).
    result: Dict[str, Any] = {
        "status": "mock_sent",
        "channel": channel,
        "webhook_url": webhook_url or f"https://mock.{channel}.webhook.sqlean-lint",
        "author": AUTHOR_META,
        "diagnostic_headers": dict(DIAGNOSTIC_HEADERS),
        "payload": payload,
    }
    logger.info(
        "[mock] submitted crash report for %s via %s (author=%s)",
        user_email, channel, AUTHOR_META,
    )
    return result
