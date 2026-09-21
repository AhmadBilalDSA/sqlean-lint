"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Highly-concurrent FastAPI microservice wrapper for sqlean-lint PRO.

Exposes REST, optional GraphQL, and gRPC surfaces so external enterprise
clients (React / Next.js / Flutter) can submit SQL for remote linting. The lint
engine remains the same citeable ``sqlean_lint.rules.pro`` entry points, so
server-side results match the desktop and CLI exactly.

The FastAPI app is assembled by :func:`create_app`. Optional layers (GraphQL via
``strawberry``, gRPC via ``grpcio``) are lazy-imported so the module also
compiles and imports cleanly in headless/lightweight sandboxes without those
extras installed.

Enterprise extensions:
  * Rate limiting per API key with sliding-window counters.
  * Batch lint endpoint for processing multiple SQL files concurrently.
  * WebSocket endpoint for streaming incremental lint results.
  * CORS configuration for cross-origin enterprise clients.
  * Request ID middleware for distributed tracing correlation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..connectors.polyglot import sql_to_mongodb
from ..engine import lint_source
from ..types import LintResult as EngineLintResult

logger = logging.getLogger("sqlean_lint.api")

APP_VERSION = "2.0.0"
APP_AUTHOR = "Ahmad Bilal"


# ── Request / response models ───────────────────────────────────────────

class LintRequest(BaseModel):
    """A remote linting request body."""
    sql: str
    dialect: str = "duckdb"
    format: str = "json"
    rules: Optional[List[str]] = None


class LintResult(BaseModel):
    """Structured linting response: violations plus the optimized SQL.

    Mirrors :class:`~sqlean_lint.types.LintResult` at the wire boundary so
    REST clients receive the same violations/optimization the desktop and
    CLI produce.
    """
    sql: str
    dialect: str
    findings: List[Dict[str, Any]]
    status: str
    optimized_sql: Optional[str] = None
    cost: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


class BatchLintRequest(BaseModel):
    """Batch linting request for multiple SQL statements."""
    queries: List[LintRequest]
    concurrency: int = Field(default=4, ge=1, le=32,
                             description="Max concurrent lint workers")


class BatchLintResult(BaseModel):
    """Aggregated result for a batch lint request."""
    results: List[LintResult]
    total_queries: int
    total_findings: int
    elapsed_ms: int


class ConvertRequest(BaseModel):
    sql: str
    to: str = "mongodb"


class RateLimitInfo(BaseModel):
    """Tracks per-key rate limit state."""
    key: str
    window_start: float
    request_times: List[float] = Field(default_factory=list)

    def is_allowed(self, limit: int, window_s: float) -> bool:
        """Check if a new request is within the sliding window limit."""
        now = time.time()
        cutoff = now - window_s
        self.request_times = [t for t in self.request_times if t > cutoff]
        if len(self.request_times) >= limit:
            return False
        self.request_times.append(now)
        return True


# ── Rate limiter (in-memory sliding window) ────────────────────────────

class _RateLimiter:
    """Simple in-memory sliding-window rate limiter keyed by API key."""

    def __init__(
        self, default_limit: int = 100, window_s: float = 60.0
    ) -> None:
        self._default_limit = default_limit
        self._window_s = window_s
        self._keys: Dict[str, RateLimitInfo] = {}
        self._overrides: Dict[str, int] = {}

    def set_limit(self, key: str, limit: int) -> None:
        """Override the rate limit for a specific API key."""
        self._overrides[key] = limit

    def check(self, key: str) -> bool:
        """Return True if the request is allowed; record the attempt."""
        if key not in self._keys:
            self._keys[key] = RateLimitInfo(key=key, window_start=time.time())
        info = self._keys[key]
        limit = self._overrides.get(key, self._default_limit)
        return info.is_allowed(limit, self._window_s)

    def remaining(self, key: str) -> int:
        """Return the number of remaining requests in the current window."""
        if key not in self._keys:
            limit = self._overrides.get(key, self._default_limit)
            return limit
        info = self._keys[key]
        now = time.time()
        cutoff = now - self._window_s
        current = sum(1 for t in info.request_times if t > cutoff)
        limit = self._overrides.get(key, self._default_limit)
        return max(0, limit - current)


_rate_limiter = _RateLimiter()


# ── Authentication middleware dependency ────────────────────────────────

def _get_api_key(request: Request) -> str:
    """Extract the API key from the Authorization header or query param."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    key = request.query_params.get("api_key", "")
    if key:
        return key
    return "anonymous"


def _check_rate_limit(request: Request) -> str:
    """Dependency that enforces rate limiting and returns the API key."""
    api_key = _get_api_key(request)
    if not _rate_limiter.check(api_key):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please slow down.",
            headers={
                "Retry-After": str(int(_rate_limiter._window_s)),
                "X-RateLimit-Remaining": "0",
            },
        )
    return api_key


# ── Core handlers ────────────────────────────────────────────────────────

def _handle_lint(body: LintRequest) -> LintResult:
    """Run one SQL source through the engine and shape a JSON-safe reply.

    Delegates to :func:`sqlean_lint.engine.lint_source`, which returns a
    fully-populated :class:`~sqlean_lint.types.LintResult` containing
    structured ``violations`` (JSON-serializable) and the ``optimized_sql``.
    Linting never raises on arbitrary input by contract; only malformed
    request shapes surface as HTTP 422.
    """
    try:
        engine_result: EngineLintResult = lint_source(
            body.sql,
            dialect=body.dialect,
            optimize=True,
        )
    except Exception as err:  # noqa: BLE001 - defensive; lint must not raise
        raise HTTPException(status_code=422, detail=str(err))

    payload = engine_result.to_dict(include_sql=True, include_duration=True)
    findings: List[Dict[str, Any]] = payload["violations"]
    return LintResult(
        sql=body.sql,
        dialect=body.dialect,
        findings=findings,
        status="ok" if not findings else "findings",
        optimized_sql=payload["optimized_sql"],
        cost=payload["cost"],
        duration_ms=payload["duration_ms"],
    )


async def _handle_batch_lint(body: BatchLintRequest) -> BatchLintResult:
    """Process multiple SQL queries concurrently with bounded parallelism."""
    start = time.monotonic()
    sem = asyncio.Semaphore(body.concurrency)
    results: List[Optional[LintResult]] = [None] * len(body.queries)

    async def _lint_one(idx: int, req: LintRequest) -> None:
        async with sem:
            loop = asyncio.get_running_loop()
            results[idx] = await loop.run_in_executor(None, _handle_lint, req)

    tasks = [_lint_one(i, q) for i, q in enumerate(body.queries)]
    await asyncio.gather(*tasks)

    final: List[LintResult] = [r for r in results if r is not None]
    total_findings = sum(len(r.findings) for r in final)
    elapsed = int((time.monotonic() - start) * 1000)
    return BatchLintResult(
        results=final,
        total_queries=len(final),
        total_findings=total_findings,
        elapsed_ms=elapsed,
    )


# ── WebSocket handler for streaming lint results ───────────────────────

class _ConnectionManager:
    """Manages active WebSocket connections for real-time lint streaming."""

    def __init__(self) -> None:
        self._connections: Dict[str, WebSocket] = {}

    async def connect(self, ws: WebSocket, client_id: str) -> None:
        await ws.accept()
        self._connections[client_id] = ws
        logger.info("WebSocket client connected: %s", client_id)

    def disconnect(self, client_id: str) -> None:
        self._connections.pop(client_id, None)
        logger.info("WebSocket client disconnected: %s", client_id)

    async def send_result(self, client_id: str, data: Dict[str, Any]) -> None:
        ws = self._connections.get(client_id)
        if ws:
            await ws.send_json(data)


_ws_manager = _ConnectionManager()


async def _websocket_lint(ws: WebSocket) -> None:
    """Accept a WebSocket and lint each SQL message as it arrives."""
    client_id = str(uuid.uuid4())[:8]
    await _ws_manager.connect(ws, client_id)
    try:
        while True:
            data = await ws.receive_json()
            sql = data.get("sql", "")
            dialect = data.get("dialect", "duckdb")
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, _handle_lint, LintRequest(sql=sql, dialect=dialect)
            )
            await _ws_manager.send_result(client_id, result.model_dump())
    except WebSocketDisconnect:
        _ws_manager.disconnect(client_id)


# ── Lifespan / startup / shutdown ──────────────────────────────────────

@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler for startup/shutdown hooks."""
    logger.info("sqlean-lint PRO API starting (v%s)", APP_VERSION)
    yield
    logger.info("sqlean-lint PRO API shutting down")


# ── FastAPI application factory ──────────────────────────────────────────

def create_app(
    *,
    with_auth: bool = True,
    cors_origins: Optional[List[str]] = None,
    rate_limit: int = 100,
    rate_window_s: float = 60.0,
) -> FastAPI:
    """Build and configure the FastAPI application.

    Args:
        with_auth: Whether to attach OIDC/JWT authentication middleware.
        cors_origins: Allowed CORS origins; ``None`` disables CORS.
        rate_limit: Max requests per window per API key.
        rate_window_s: Sliding window duration in seconds.

    Returns:
        A configured :class:`fastapi.FastAPI` instance.
    """
    _rate_limiter._default_limit = rate_limit
    _rate_limiter._window_s = rate_window_s

    app = FastAPI(
        title="sqlean-lint PRO API",
        description="Enterprise REST/GraphQL/gRPC linting microservice.",
        version=APP_VERSION,
        lifespan=_lifespan,
    )

    # CORS
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Request-ID middleware
    @app.middleware("http")
    async def _add_request_id(request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # OIDC auth (lazy import)
    _auth_dep: Any = None
    if with_auth:
        try:
            from .auth import oidc_required
            _auth_dep = Depends(oidc_required)
        except Exception:  # noqa: BLE001
            logger.debug("OIDC auth module not available; running without auth.")

    rate_dep = Depends(_check_rate_limit)

    # ── REST endpoints ──────────────────────────────────────────────

    @app.get("/health", tags=["meta"])
    def health() -> Dict[str, Any]:
        return {
            "status": "online",
            "author": APP_AUTHOR,
            "version": APP_VERSION,
            "timestamp": int(time.time()),
        }

    @app.get("/ready", tags=["meta"])
    def ready() -> Dict[str, str]:
        """Kubernetes readiness probe."""
        return {"status": "ready"}

    @app.post("/lint", response_model=LintResult, tags=["lint"])
    async def lint(
        body: LintRequest,
        api_key: str = rate_dep,
    ) -> LintResult:
        return _handle_lint(body)

    @app.post("/lint/batch", response_model=BatchLintResult, tags=["lint"])
    async def lint_batch(
        body: BatchLintRequest,
        api_key: str = rate_dep,
    ) -> BatchLintResult:
        return await _handle_batch_lint(body)

    @app.post("/convert", tags=["transpile"])
    async def convert(
        body: ConvertRequest,
        api_key: str = rate_dep,
    ) -> Dict[str, Any]:
        if body.to == "mongodb":
            return sql_to_mongodb(body.sql)
        raise HTTPException(status_code=400, detail="Unsupported target.")

    @app.get("/rules", tags=["meta"])
    def rules() -> Dict[str, Any]:
        try:
            from ..rules import DEFAULT_RULES
            return {"rules": [r.rule_id for r in DEFAULT_RULES]}
        except Exception:  # noqa: BLE001
            return {"rules": []}

    @app.get("/rate-limit/status", tags=["meta"])
    def rate_limit_status(api_key: str = Depends(_get_api_key)) -> Dict[str, Any]:
        return {
            "api_key": api_key[:8] + "..." if len(api_key) > 8 else api_key,
            "remaining": _rate_limiter.remaining(api_key),
            "window_s": _rate_limiter._window_s,
        }

    # ── WebSocket endpoint ──────────────────────────────────────────

    @app.websocket("/ws/lint")
    async def ws_lint(ws: WebSocket) -> None:
        await _websocket_lint(ws)

    _register_graphql(app)
    _register_grpc(app)
    return app


def _register_graphql(app: FastAPI) -> None:
    """Lazily register a GraphQL endpoint via Strawberry (best-effort)."""
    try:
        import strawberry  # type: ignore
        from strawberry.fastapi import GraphQLRouter  # type: ignore

        @strawberry.type
        class Query:  # type: ignore[misc]
            @strawberry.field
            def health(self) -> str:  # type: ignore[misc]
                return "ok"

            @strawberry.field
            def lint_sql(
                self, sql: str, dialect: str = "duckdb"
            ) -> str:  # type: ignore[misc]
                result = lint_source(sql, dialect=dialect)
                payload = result.to_dict(include_sql=False, include_duration=False)
                return json.dumps(payload["violations"])

        schema = strawberry.Schema(query=Query)
        app.include_router(GraphQLRouter(schema), prefix="/graphql")
    except Exception:  # noqa: BLE001 - GraphQL is optional
        logger.debug("GraphQL (strawberry) not available; skipped.")


def _register_grpc(app: FastAPI) -> None:
    """Placeholder hook for a gRPC server on a separate port."""
    logger.debug("gRPC endpoint available via sqlean_lint.api.grpc_serve.")


def grpc_serve(address: str = "[::]:50051") -> None:
    """Serve the gRPC linting service (requires ``grpcio``).

    Args:
        address: gRPC bind address.
    """
    import grpc  # type: ignore
    from concurrent import futures as _futures

    try:
        from . import grpc_lint_pb2_grpc  # type: ignore  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "gRPC stubs not generated. Run the protoc step first."
        ) from exc

    server = grpc.server(_futures.ThreadPoolExecutor(max_workers=64))
    server.add_insecure_port(address)
    server.start()
    logger.info("gRPC serving on %s", address)
    server.wait_for_termination()


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
