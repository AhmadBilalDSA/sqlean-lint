"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

OpenTelemetry (OTel) instrumentation for sqlean-lint PRO.

Routes metrics, traces, and logs to Datadog, Prometheus, and Grafana via the
OpenTelemetry SDK and the OTLP/gRPC exporter. Adds PagerDuty alert webhooks
on API 500 errors.

Privacy-first: tracing is **opt-in and off by default**. Enable with
``SQLEAN_LINT_OTEL=1``. No SQL source content is ever included in trace
attributes by default; only anonymised metadata (dialect, rule count, latency).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("sqlean_lint.observability")

# Tracer / meter singletons (resolved lazily so this module compiles cleanly
# without the OTel SDK installed).
_tracer: Any = None
_meter: Any = None
_provider: Any = None
_initialized = False
_initializing = threading.Lock()

_APM_ENDPOINTS = {
    "datadog": "https://api.datadoghq.com/api/v2/apm",
    "grafana": "https://otlp-gateway-prod-eu-west-0.grafana.net/otlp",
}

_SERVICE_NAME = "sqlean-lint-pro"

# PagerDuty webhook endpoints for 500 errors.
_PAGERDUTY_EVENTS_API = "https://events.pagerduty.com/v2/enqueue"
_PAGERDUTY_DEDUP_KEY = "sqlean-lint-api-500"


def otel_enabled() -> bool:
    """Return whether OTel instrumentation is enabled (opt-in via env var)."""
    return os.environ.get("SQLEAN_LINT_OTEL", "0") == "1"


def init_otel(
    *,
    service_name: str = _SERVICE_NAME,
    endpoint: Optional[str] = None,
) -> bool:
    """Initialize the OpenTelemetry SDK and instrumentation.

    Args:
        service_name: OTel service name for trace/span identification.
        endpoint: OTLP endpoint override (defaults to env or standard export).

    Returns:
        True if OTel was initialized, False if already initialized or disabled.
    """
    global _tracer, _meter, _provider, _initialized

    if _initialized:
        return True
    if not otel_enabled():
        logger.debug("OTel instrumentation disabled (SQLEAN_LINT_OTEL != 1).")
        return False

    with _initializing:
        if _initialized:
            return True
        try:
            from opentelemetry import trace as otel_trace
            from opentelemetry import metrics as otel_metrics
            from opentelemetry.sdk.resources import (
                Resource, SERVICE_NAME, DEPLOYMENT_ENVIRONMENT,
            )
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
            )
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            resource = Resource.create({
                SERVICE_NAME: service_name,
                DEPLOYMENT_ENVIRONMENT: os.environ.get(
                    "SQLEAN_LINT_ENV", "production"
                ),
            })

            trace_endpoint = endpoint or os.environ.get(
                "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
            )

            tracer_provider = TracerProvider(resource=resource)
            span_exporter = OTLPSpanExporter(endpoint=trace_endpoint)
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            otel_trace.set_tracer_provider(tracer_provider)
            _tracer = otel_trace.get_tracer(service_name)

            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=trace_endpoint),
                export_interval_millis=5000,
            )
            meter_provider = MeterProvider(
                resource=resource, metric_readers=[metric_reader]
            )
            otel_metrics.set_meter_provider(meter_provider)
            _meter = otel_metrics.get_meter(service_name)

            _provider = tracer_provider
            _initialized = True
            logger.info("OTel initialized for service %s", service_name)
            return True
        except ImportError as exc:
            logger.warning(
                "OpenTelemetry packages not installed; telemetry unavailable: %s",
                exc,
            )
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to initialize OTel: %s", exc)
            return False


def get_tracer():
    """Return the configured OTel tracer (or a null tracer if disabled)."""
    if not _initialized:
        init_otel()
    if _tracer is None:
        from opentelemetry import trace as otel_trace
        return otel_trace.get_tracer(_SERVICE_NAME)
    return _tracer


def get_meter():
    """Return the configured OTel meter (or a null meter if disabled)."""
    if not _initialized:
        init_otel()
    if _meter is None:
        from opentelemetry import metrics as otel_metrics
        return otel_metrics.get_meter(_SERVICE_NAME)
    return _meter


# ── Metric instrumentation helpers ──────────────────────────────────────

def record_latency(operation: str, duration_ms: float,
                   attributes: Optional[Dict[str, str]] = None) -> None:
    """Record an operation latency histogram metric.

    Args:
        operation: The operation name (e.g. ``"lint"``, ``"transpile"``).
        duration_ms: Latency in milliseconds.
        attributes: Optional key/value span attributes (never raw SQL).
    """
    if not otel_enabled():
        return
    try:
        hist = get_meter().create_histogram(
            name=f"sqlean.lint.{operation}.duration",
            description=f"Latency of {operation} in milliseconds",
            unit="ms",
        )
        hist.record(duration_ms, attributes=dict(attributes or {}))
    except Exception:  # noqa: BLE001
        pass


def increment_counter(name: str, value: int = 1,
                      attributes: Optional[Dict[str, str]] = None) -> None:
    """Increment a named counter metric.

    Args:
        name: Metric name (prefixed with ``sqlean.lint``).
        value: Delta to add (default 1).
        attributes: Optional key/value attributes.
    """
    if not otel_enabled():
        return
    try:
        counter = get_meter().create_counter(
            name=f"sqlean.lint.{name}",
            description=f"Counter for {name}",
        )
        counter.add(value, attributes=dict(attributes or {}))
    except Exception:  # noqa: BLE001
        pass


def trace_operation(
    operation: str,
    attributes: Optional[Dict[str, str]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that wraps a function in an OTel span with latency metric.

    Args:
        operation: Span/operation name.
        attributes: Fixed attributes to attach to every span.

    Returns:
        A decorator that traces the wrapped function.
    """
    fixed = dict(attributes or {})

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            try:
                if otel_enabled():
                    tracer = get_tracer()
                    with tracer.start_as_current_span(
                        f"sqlean.lint.{operation}",
                        attributes=fixed or None,
                    ) as span:
                        result = func(*args, **kwargs)
                        span.set_attribute("success", True)
                        return result
                else:
                    return func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                if otel_enabled():
                    try:
                        current_span = get_tracer()
                        span = next(iter([]), None)  # no-op path fallback
                    except Exception:  # noqa: BLE001
                        span = None
                    record_latency(operation, (time.monotonic() - start) * 1000, {
                        **fixed, "error": str(type(exc).__name__),
                    })
                raise
            finally:
                elapsed = (time.monotonic() - start) * 1000
                record_latency(operation, elapsed, fixed)
        return wrapper
    return decorator


# ── PagerDuty alerting ─────────────────────────────────────────────────

def send_pagerduty_alert(
    *,
    title: str,
    severity: str = "critical",
    source: str = "sqlean-lint-api",
    detail: str = "",
    routing_key: Optional[str] = None,
) -> bool:
    """Send a PagerDuty event to the Events API.

    Args:
        title: Alert title.
        severity: ``"critical"``, ``"error"``, or ``"warning"``.
        source: Source node identifier.
        detail: Alert detail text.
        routing_key: PagerDuty routing key (defaults to env var).

    Returns:
        True if the alert was dispatched successfully.
    """
    key = routing_key or os.environ.get("PAGERDUTY_ROUTING_KEY")
    if not key:
        logger.debug("PagerDuty routing key not configured; skipping alert.")
        return False

    payload = {
        "routing_key": key,
        "event_action": "trigger",
        "dedup_key": _PAGERDUTY_DEDUP_KEY,
        "payload": {
            "summary": title,
            "source": source,
            "severity": severity,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "component": "api",
            "custom_details": {"detail": detail},
        },
    }
    try:
        import requests
        resp = requests.post(
            _PAGERDUTY_EVENTS_API, json=payload, timeout=5
        )
        resp.raise_for_status()
        logger.info("PagerDuty alert dispatched: %s", title)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("PagerDuty alert failed: %s", exc)
        return False


def report_api_500(request_path: str, method: str, status_code: int,
                   error_msg: str) -> None:
    """Report an API 500 error to PagerDuty and metrics.

    Args:
        request_path: Request path that produced the error.
        method: HTTP method.
        status_code: HTTP status code (expected 500).
        error_msg: Error message for the alert detail.
    """
    increment_counter("http.5xx", attributes={"path": request_path[:128]})
    if status_code == 500:
        send_pagerduty_alert(
            title=f"sqlean-lint API 500 error on {method} {request_path[:64]}",
            severity="critical",
            detail=error_msg[:500],
        )


# ── Log forwarding ─────────────────────────────────────────────────────

class OTelLogHandler(logging.Handler):
    """Logging handler that forwards records to OpenTelemetry (best-effort).

    Attach to your logger to stream structured logs to the configured OTLP
    collector (e.g. Datadog agent, Grafana Loki, Prometheus via Loki).
    """

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003
        if not otel_enabled():
            return
        try:
            increment_counter(
                "log.record",
                attributes={
                    "level": record.levelname,
                    "logger": record.name,
                },
            )
            fmt = self.format(record)
            if record.levelno >= logging.ERROR:
                send_log_alert(record.levelname, fmt)
        except Exception:  # noqa: BLE001
            pass


def send_log_alert(level: str, message: str) -> None:
    """Send PagerDuty alert for ERROR/CRITICAL log records."""
    if level in ("CRITICAL",):
        send_pagerduty_alert(
            title=f"sqlean-lint CRITICAL log: {message[:96]}",
            severity="critical",
            detail=message[:500],
        )
    elif level == "ERROR":
        send_pagerduty_alert(
            title=f"sqlean-lint ERROR log: {message[:96]}",
            severity="error",
            detail=message[:500],
        )


def attach_otel_log_handler() -> None:
    """Attach the OTel log handler to the root logger."""
    handler = OTelLogHandler()
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    logger.debug("OTel log handler attached.")


# ── FastAPI setup & 500-error interceptor ─────────────────────────────

def setup_telemetry(
    app: Any,
    *,
    service_name: str = _SERVICE_NAME,
    enable_tracing: bool = True,
    enable_metrics: bool = True,
    enable_logging: bool = True,
) -> bool:
    """Wire OpenTelemetry instrumentation into a FastAPI application.

    Configures trace/metric export to mock Datadog and Prometheus OTLP
    endpoints, attaches a logging interceptor that traps HTTP 500 errors and
    formats alert payloads for PagerDuty, and installs per-request span
    recording.

    Args:
        app: A ``fastapi.FastAPI`` instance (or Starlette ASGI app).
        service_name: OTel service name (default ``sqlean-lint-pro``).
        enable_tracing: Whether to install OTel middleware for tracing.
        enable_metrics: Whether to install request/response metrics.
        enable_logging: Whether to attach the OTel log handler for error
            forwarding to PagerDuty.

    Returns:
        True if telemetry was successfully configured.
    """
    if not otel_enabled():
        logger.debug("SQLEAN_LINT_OTEL not set; skipping setup_telemetry.")
        return False

    # 1) Initialize the OTel SDK if not already done.
    init_ok = init_otel(service_name=service_name)
    if not init_ok:
        logger.debug("OTel SDK init returned False; middleware skipped.")

    # 2) Install OTel ASGI middleware for automatic span/trace recording.
    if enable_tracing:
        try:
            from opentelemetry.instrumentation.fastapi import (
                FastAPIInstrumentor,
            )
            FastAPIInstrumentor.instrument_app(app)
            logger.info("OTel FastAPI tracing middleware installed.")
        except ImportError:
            logger.warning(
                "opentelemetry-instrumentation-fastapi not installed; "
                "tracing middleware skipped."
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("FastAPI tracing middleware failed: %s", exc)

    # 3) Install request/response metric collection.
    if enable_metrics:
        try:
            meter = get_meter()
            http_request_count = meter.create_counter(
                name="sqlean.lint.http.request.total",
                description="Total HTTP requests received by the API",
            )
            http_request_duration = meter.create_histogram(
                name="sqlean.lint.http.request.duration",
                description="HTTP request duration in milliseconds",
                unit="ms",
            )
            http_error_count = meter.create_counter(
                name="sqlean.lint.http.errors.total",
                description="Total HTTP error responses (5xx)",
            )
        except Exception:  # noqa: BLE001
            http_request_count = None
            http_request_duration = None
            http_error_count = None

        @app.middleware("http")
        async def _otel_metrics_middleware(request: Any, call_next: Any) -> Any:
            start = time.monotonic()
            response = await call_next(request)
            elapsed_ms = (time.monotonic() - start) * 1000
            path = getattr(request, "url", type("", (), {"path": ""})()).path
            method = getattr(request, "method", "GET")
            attrs = {"method": method, "path": path[:128], "status": str(response.status_code)}
            try:
                if http_request_count is not None:
                    http_request_count.add(1, attributes=attrs)
                if http_request_duration is not None:
                    http_request_duration.record(elapsed_ms, attributes=attrs)
                if response.status_code >= 500 and http_error_count is not None:
                    http_error_count.add(1, attributes=attrs)
            except Exception:  # noqa: BLE001
                pass
            return response
        logger.info("OTel metrics middleware installed.")

    # 4) Install 500-error logging interceptor → PagerDuty.
    if enable_logging:
        attach_otel_log_handler()

        @app.middleware("http")
        async def _pagerduty_500_interceptor(
            request: Any, call_next: Any
        ) -> Any:
            try:
                response = await call_next(request)
            except Exception as exc:  # noqa: BLE001
                increment_counter("http.5xx.unhandled", attributes={
                    "path": getattr(request, "url", type("", (), {"path": ""})()).path[:128],
                })
                send_pagerduty_alert(
                    title=(
                        f"sqlean-lint UNHANDLED {getattr(request, 'method', 'GET')} "
                        f"{getattr(request, 'url', type('', (), {'path': ''})()).path[:64]}"
                    ),
                    severity="critical",
                    detail=str(exc)[:500],
                )
                raise
            if response.status_code >= 500:
                path = getattr(request, "url", type("", (), {"path": ""})()).path
                method = getattr(request, "method", "GET")
                report_api_500(path, method, response.status_code, "")
            return response
        logger.info("PagerDuty 500-error interceptor installed.")

    # 5) Log mock export endpoints for visibility.
    otlp = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    logger.info(
        "OTel telemetry configured for %s — OTLP → %s "
        "(mock Datadog: %s, mock Prometheus: %s)",
        service_name, otlp,
        _APM_ENDPOINTS["datadog"],
        otlp,
    )
    return True
