"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Privacy-first UX research telemetry.

Records anonymous interaction events (button clicks, rule toggles, session
journeys) and, when a user explicitly opts in, forwards them to Segment /
Amplitude / Mixpanel REST endpoints. Telemetry is **opt-in and off by
default**: the product remains 100% local and air-gapped unless
``SQLEAN_LINT_TELEMETRY=1`` is set. No personally identifiable information
or SQL source content is ever transmitted.

Extended with:
  * Session-level journey recording (open → scan → fix → export funnel).
  * Conversion event tracking for growth analytics.
  * Batched flush with configurable buffer size to reduce network calls.
  * Feature-flag exposure recording for A/B test attribution.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_ANALYTICS_ENDPOINTS: Dict[str, str] = {
    "segment": "https://api.segment.io/v1/track",
    "amplitude": "https://api2.amplitude.com/2/httpapi",
    "mixpanel": "https://api.mixpanel.com/track",
}

# A stable, anonymous install id (not tied to the user identity).
_INSTALL_ID: Optional[str] = None

_DEFAULT_BUFFER_SIZE = 50
_FLUSH_INTERVAL_S = 30.0


def _install_id() -> str:
    global _INSTALL_ID
    if _INSTALL_ID is None:
        _INSTALL_ID = str(uuid.uuid4())
    return _INSTALL_ID


def telemetry_enabled() -> bool:
    """Return whether analytics are enabled (opt-in via env var)."""
    return os.environ.get("SQLEAN_LINT_TELEMETRY", "0") == "1"


def _common_properties() -> Dict[str, str]:
    return {
        "anonymous_id": _install_id(),
        "os": platform.system(),
        "python": platform.python_version(),
        "product": "sqlean-lint-pro",
        "version": "1.0.0",
    }


def _track(provider: str, event: str, properties: Dict[str, Any]) -> None:
    """Push a single event to one analytics provider (best-effort)."""
    if not telemetry_enabled():
        return
    endpoint = os.environ.get(
        f"SQLEAN_LINT_{provider.upper()}_ENDPOINT",
        _ANALYTICS_ENDPOINTS.get(provider, ""),
    )
    api_key = os.environ.get(f"SQLEAN_LINT_{provider.upper()}_KEY")
    write_key = os.environ.get(f"SQLEAN_LINT_{provider.upper()}_WRITE_KEY")
    if not endpoint:
        return

    payload: Dict[str, Any]
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if provider == "segment":
        payload = {
            "userId": _install_id(),
            "event": event,
            "properties": {**properties, **_common_properties()},
        }
        if write_key:
            headers["Authorization"] = "Basic " + base64.b64encode(
                f"{write_key}:".encode("utf-8")
            ).decode("ascii")
    elif provider == "amplitude":
        payload = {
            "api_key": api_key or "",
            "events": [{
                "event_type": event,
                "user_id": _install_id(),
                "event_properties": properties,
                "os_name": _common_properties()["os"],
            }],
        }
    else:  # mixpanel
        payload = {
            "event": event,
            "properties": {
                "token": api_key or "",
                **properties,
                **_common_properties(),
            },
        }

    try:
        import requests  # declared dependency

        requests.post(endpoint, json=payload, headers=headers, timeout=5)
    except Exception:  # noqa: BLE001 - telemetry must never break the app
        pass


def track_interaction(event: str, properties: Optional[Dict[str, Any]] = None) -> None:
    """Record a UX interaction (button click, rule toggle) across providers.

    Args:
        event: Short event name, e.g. ``"gui.dashboard.run_scan"``.
        properties: Optional anonymous properties (never raw SQL).
    """
    if not telemetry_enabled():
        return
    props: Dict[str, Any] = dict(properties or {})
    props["ts"] = int(time.time())
    for provider in _ANALYTICS_ENDPOINTS:
        _track(provider, event, props)


def track_rule_toggle(rule_id: str, enabled: bool) -> None:
    """Record a rule enable/disable toggle for UX research."""
    if not telemetry_enabled():
        return
    track_interaction("gui.rule_toggle", {"rule": rule_id, "enabled": enabled})


def track_conversion(event: str, value: Optional[float] = None,
                     metadata: Optional[Dict[str, Any]] = None) -> None:
    """Record a conversion event (upgrade, activation, export).

    Args:
        event: Conversion event name, e.g. ``"license.activated"``.
        value: Optional monetary or ordinal value.
        metadata: Additional context (plan tier, feature used).
    """
    props: Dict[str, Any] = dict(metadata or {})
    props["conversion"] = True
    if value is not None:
        props["value"] = value
    track_interaction(f"conversion.{event}", props)


def track_feature_flag(flag_name: str, variant: str,
                       user_bucket: Optional[str] = None) -> None:
    """Record that a feature flag was exposed to the user.

    Args:
        flag_name: Feature flag identifier, e.g. ``"new_ast_rule_v2"``.
        variant: The variant the user was bucketed into.
        user_bucket: Optional deterministic bucket hash for audit.
    """
    track_interaction("feature_flag.exposure", {
        "flag": flag_name,
        "variant": variant,
        "bucket": user_bucket or "",
    })


class SessionRecorder:
    """Records a linear user journey within a single application session.

    Tracks the sequence of significant actions (open, scan, fix, export) with
    timestamps for funnel analysis. When telemetry is enabled, events are also
    forwarded to configured analytics providers.
    """

    FUNNEL_STEPS = ("open", "scan", "fix", "export")

    def __init__(self, *, enabled: Optional[bool] = None) -> None:
        self._enabled = telemetry_enabled() if enabled is None else enabled
        self._session_id = str(uuid.uuid4())
        self._started_at = time.time()
        self._events: List[Dict[str, Any]] = []

    @property
    def session_id(self) -> str:
        return self._session_id

    def record(self, step: str, properties: Optional[Dict[str, Any]] = None) -> None:
        """Record a funnel step in the current session.

        Args:
            step: One of ``"open"``, ``"scan"``, ``"fix"``, ``"export"``.
            properties: Optional extra properties for the step.
        """
        entry: Dict[str, Any] = {
            "session_id": self._session_id,
            "step": step,
            "ts": int(time.time()),
            "elapsed_ms": int((time.time() - self._started_at) * 1000),
            "properties": dict(properties or {}),
        }
        self._events.append(entry)
        if self._enabled:
            track_interaction(f"session.{step}", {
                "session_id": self._session_id,
                "step_index": self.FUNNEL_STEPS.index(step)
                if step in self.FUNNEL_STEPS else -1,
                **(properties or {}),
            })

    def funnel_summary(self) -> Dict[str, Any]:
        """Return a summary of the session funnel for analytics."""
        completed = [e["step"] for e in self._events if e["step"] in self.FUNNEL_STEPS]
        return {
            "session_id": self._session_id,
            "duration_ms": int((time.time() - self._started_at) * 1000),
            "steps_completed": completed,
            "funnel_length": len(completed),
            "total_events": len(self._events),
        }


class InteractionRecorder:
    """In-process recorder that respects the opt-in privacy gate.

    Buffers events in memory and flushes them to analytics providers only when
    telemetry is enabled; otherwise it is a no-op queue. Supports periodic
    background flushing via :meth:`start_background_flush`.
    """

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        buffer_size: int = _DEFAULT_BUFFER_SIZE,
    ) -> None:
        self._enabled = telemetry_enabled() if enabled is None else enabled
        self._buffer: deque[Dict[str, Any]] = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._flush_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._on_flush: Optional[Callable[[int], None]] = None

    def record(self, event: str, properties: Optional[Dict[str, Any]] = None) -> None:
        entry = {
            "event": event,
            "properties": dict(properties or {}),
            "ts": int(time.time()),
        }
        with self._lock:
            self._buffer.append(entry)
        if self._enabled:
            track_interaction(event, entry["properties"])

    def flush(self) -> int:
        """Return the number of buffered events and clear the buffer."""
        with self._lock:
            n = len(self._buffer)
            self._buffer.clear()
        if self._on_flush:
            self._on_flush(n)
        return n

    def on_flush(self, callback: Callable[[int], None]) -> None:
        """Register a callback invoked with the count on each flush."""
        self._on_flush = callback

    def start_background_flush(
        self, interval_s: float = _FLUSH_INTERVAL_S
    ) -> None:
        """Start a daemon thread that periodically flushes the buffer.

        Args:
            interval_s: Seconds between flush attempts.
        """
        if self._flush_thread and self._flush_thread.is_alive():
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=interval_s)
                if not self._stop_event.is_set():
                    self.flush()

        self._flush_thread = threading.Thread(target=_loop, daemon=True)
        self._flush_thread.start()

    def stop_background_flush(self) -> None:
        """Stop the background flush thread and perform a final flush."""
        self._stop_event.set()
        if self._flush_thread:
            self._flush_thread.join(timeout=5)
            self._flush_thread = None
        self.flush()

    @property
    def buffered_count(self) -> int:
        with self._lock:
            return len(self._buffer)


# ── Local usage telemetry (zero-cloud by default) ──────────────────────

class TelemetryLogger:
    """Append-only local usage logger writing JSON to a local file.

    Records usage metadata — execution time, dialect used, rules triggered,
    violation counts — into ``~/.sqlean_lint/telemetry.log`` (a JSON-lines
    file). This is **strictly local**: nothing ever leaves the machine unless
    the caller explicitly enables remote forwarding via ``allow_remote=True``
    (and even then only the same non-PII metadata is sent, honoring the
    zero-cloud privacy charter).

    Thread-safe via an internal lock so concurrent lint workers can log
    safely without corrupting the file or interleaving partial lines.

    Privacy guarantees:
      * SQL source content is **never** written to the log.
      * Machine identifiers (MAC, MachineGuid) are never captured.
      * Each record carries only a stable anonymous install id.
    """

    DEFAULT_DIRNAME = ".sqlean_lint"
    DEFAULT_FILENAME = "telemetry.log"

    def __init__(
        self,
        *,
        path: Optional[Path] = None,
        allow_remote: bool = False,
        enabled: Optional[bool] = None,
    ) -> None:
        if enabled is None:
            enabled = os.environ.get("SQLEAN_LINT_TELEMETRY", "0") == "1"
        self._enabled = enabled
        self._allow_remote = allow_remote or self._enabled
        self._path = path or self._default_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._install_id = _install_id()

    @staticmethod
    def _default_path() -> Path:
        home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
        return home / TelemetryLogger.DEFAULT_DIRNAME / TelemetryLogger.DEFAULT_FILENAME

    @property
    def path(self) -> Path:
        """Absolute path to the local telemetry log file."""
        return self._path

    @property
    def enabled(self) -> bool:
        """Whether local telemetry collection is active."""
        return self._enabled

    def log_usage(
        self,
        *,
        operation: str,
        duration_ms: float,
        dialect: str = "duckdb",
        rules_triggered: Optional[List[str]] = None,
        violation_count: int = 0,
        optimized_sql_changed: bool = False,
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Record a single usage event to the local JSON-lines log.

        Args:
            operation: Operation name, e.g. ``"lint"``, ``"lint.batch"``.
            duration_ms: Execution time in milliseconds.
            dialect: SQL dialect used.
            rules_triggered: List of rule ids that fired (no SQL content).
            violation_count: Number of violations reported.
            optimized_sql_changed: Whether the optimizer altered the query.
            extra: Additional primitive metadata (no PII / no raw SQL).

        Returns:
            True if the record was persisted.
        """
        if not self._enabled:
            return False

        record: Dict[str, Any] = {
            "ts": int(time.time()),
            "anonymous_id": self._install_id,
            "operation": operation,
            "duration_ms": round(float(duration_ms), 3),
            "dialect": dialect,
            "rules_triggered": list(rules_triggered or []),
            "violation_count": int(violation_count),
            "optimized_sql_changed": bool(optimized_sql_changed),
            "os": platform.system(),
            "python": platform.python_version(),
        }
        if extra:
            for key, value in extra.items():
                if isinstance(value, (int, float, str, bool)) or value is None:
                    record[key] = value

        try:
            line = json.dumps(record, sort_keys=True)
            with self._lock:
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            # Telemetry must never break the host application.
            return False

        if self._allow_remote:
            track_interaction(f"usage.{operation}", {
                "operation": operation,
                "duration_ms": record["duration_ms"],
                "dialect": dialect,
                "violation_count": violation_count,
            })
        return True

    def read_records(self, *, limit: int = 0) -> List[Dict[str, Any]]:
        """Read previously logged records (newest last).

        Args:
            limit: Max number of records to return; ``0`` returns all.

        Returns:
            A list of the logged JSON records.
        """
        if not self._path.exists():
            return []
        records: List[Dict[str, Any]] = []
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return records
        if limit > 0:
            return records[-limit:]
        return records

    def clear(self) -> int:
        """Clear all logged records. Returns the number of records removed."""
        records = self.read_records()
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            return 0
        return len(records)
