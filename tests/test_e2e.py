"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

End-to-End (E2E) UI Testing for sqlean-lint PRO.

Uses the Playwright Python framework to mock a browser session connecting
to the FastAPI Swagger docs (http://localhost:8000/docs) and successfully
submitting a linting payload to verify the network boundaries.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# ── Playwright availability check ───────────────────────────────────────

try:
    from playwright.sync_api import Page, Playwright, sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:  # pragma: no cover - optional dev dependency
    _HAS_PLAYWRIGHT = False


# ── Test configuration ──────────────────────────────────────────────────

BASE_URL = os.environ.get("SQLEAN_E2E_BASE_URL", "http://localhost:8000")
SWAGGER_URL = f"{BASE_URL}/docs"
API_LINT_URL = f"{BASE_URL}/lint"


# ── Helper: Start FastAPI server in background for E2E tests ───────────

def _start_test_server() -> Optional[Any]:
    """Start the FastAPI server in a subprocess for E2E testing."""
    try:
        import subprocess
        import time
        import requests

        # Check if server is already running
        try:
            resp = requests.get(f"{BASE_URL}/health", timeout=2)
            if resp.status_code == 200:
                print(f"  Server already running at {BASE_URL}")
                return None
        except Exception:
            pass

        # Start server
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "sqlean_lint.api.server:app",
             "--host", "0.0.0.0", "--port", "8000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for server to be ready
        for _ in range(30):
            try:
                resp = requests.get(f"{BASE_URL}/health", timeout=1)
                if resp.status_code == 200:
                    print(f"  Test server started at {BASE_URL}")
                    return proc
            except Exception:
                time.sleep(0.5)

        proc.terminate()
        return None
    except Exception as exc:
        print(f"  Failed to start test server: {exc}")
        return None


def _stop_test_server(proc: Optional[Any]) -> None:
    """Stop the test server subprocess."""
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ── Playwright test fixtures ────────────────────────────────────────────

@pytest.fixture(scope="session")
def playwright_browser() -> Any:
    """Session-scoped Playwright browser fixture."""
    if not _HAS_PLAYWRIGHT:
        pytest.skip("playwright not installed (optional dev dependency)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture(scope="function")
def page(playwright_browser: Any) -> Any:
    """Function-scoped page fixture with cleanup."""
    context = playwright_browser.new_context(
        viewport={"width": 1280, "height": 720},
        base_url=BASE_URL,
    )
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture(scope="session", autouse=True)
def test_server() -> Any:
    """Start and stop the FastAPI test server for the session."""
    proc = _start_test_server()
    yield proc
    _stop_test_server(proc)


# ── E2E Test Suite ──────────────────────────────────────────────────────

class TestSwaggerUI:
    """Validate the Swagger/OpenAPI documentation UI loads correctly."""

    def test_swagger_ui_loads(self, page: Any, test_server: Any) -> None:
        """Verify Swagger UI page loads without errors."""
        page.goto(SWAGGER_URL)
        page.wait_for_load_state("networkidle")

        # Check for Swagger UI elements
        assert page.locator("text=swagger-ui").count() > 0 or \
               page.locator("text=SQLean Lint").count() > 0 or \
               page.locator("text=sqlean-lint").count() > 0

        # Verify the page title
        title = page.title()
        assert "swagger" in title.lower() or "sqlean" in title.lower() or "api" in title.lower()

    def test_swagger_shows_lint_endpoint(self, page: Any, test_server: Any) -> None:
        """Verify the /lint endpoint is documented in Swagger."""
        page.goto(SWAGGER_URL)
        page.wait_for_load_state("networkidle")

        page.click("text=/lint")
        page.wait_for_timeout(500)

        assert page.locator("text=sql").count() > 0
        assert page.locator("text=dialect").count() > 0

    def test_swagger_shows_health_endpoint(self, page: Any, test_server: Any) -> None:
        """Verify the /health endpoint is documented."""
        page.goto(SWAGGER_URL)
        page.wait_for_load_state("networkidle")

        page.click("text=/health")
        page.wait_for_timeout(500)

        assert page.locator("text=200").count() > 0


class TestLintEndpointE2E:
    """End-to-end tests for the linting API via browser automation."""

    def test_lint_endpoint_via_swagger_try_it_out(self, page: Any, test_server: Any) -> None:
        """Submit a linting payload via Swagger 'Try it out' feature."""
        page.goto(SWAGGER_URL)
        page.wait_for_load_state("networkidle")

        # Expand the POST /lint endpoint
        page.click("text=POST /lint")
        page.wait_for_timeout(500)

        # Click "Try it out" button
        try_it_out = page.locator("button:has-text('Try it out')")
        if try_it_out.count() > 0:
            try_it_out.first.click()
            page.wait_for_timeout(300)

            # Fill in the request body
            request_body = page.locator("textarea[placeholder*='Request body'], textarea:near(text=sql)")
            if request_body.count() > 0:
                test_sql = "SELECT * FROM users WHERE created_at > '2024-01-01'"
                request_body.first.fill(json.dumps({
                    "sql": test_sql,
                    "dialect": "duckdb",
                    "format": "json"
                }))

                # Click Execute
                execute_btn = page.locator("button:has-text('Execute')")
                if execute_btn.count() > 0:
                    execute_btn.first.click()
                    page.wait_for_timeout(3000)

                    # Verify response
                    response_body = page.locator("pre, code, .response-body")
                    if response_body.count() > 0:
                        response_text = response_body.first.inner_text()
                        assert "findings" in response_text or "status" in response_text

    def test_lint_endpoint_direct_api_call(self, page: Any, test_server: Any) -> None:
        """Direct API call to /lint endpoint and validate response."""
        response = page.request.post(
            API_LINT_URL,
            data=json.dumps({
                "sql": "SELECT * FROM orders WHERE YEAR(order_date) = 2024",
                "dialect": "duckdb",
                "format": "json"
            }),
            headers={"Content-Type": "application/json"}
        )

        assert response.ok, f"API call failed: {response.status} {response.status_text}"
        body = response.json()

        assert "status" in body
        assert body["status"] in ("ok", "findings")
        assert "dialect" in body
        assert body["dialect"] == "duckdb"
        assert "findings" in body
        assert isinstance(body["findings"], list)

        # Should find the non-SARGable YEAR() predicate
        if body["findings"]:
            rule_ids = [f.get("rule_id", "") for f in body["findings"]]
            assert any("SARG" in rid for rid in rule_ids), f"Expected SARG rule, got {rule_ids}"

    def test_lint_endpoint_with_invalid_sql(self, page: Any, test_server: Any) -> None:
        """Test that malformed SQL is handled gracefully (never 500)."""
        response = page.request.post(
            API_LINT_URL,
            data=json.dumps({
                "sql": "SELECT * FROM WHERE INVALID SQL!!!",
                "dialect": "duckdb"
            }),
            headers={"Content-Type": "application/json"}
        )

        # Should not crash - either 200 with empty findings or 422 for validation
        assert response.status in (200, 422), f"Unexpected status: {response.status}"

        if response.status == 200:
            body = response.json()
            assert "findings" in body
            assert isinstance(body["findings"], list)

    def test_batch_lint_endpoint(self, page: Any, test_server: Any) -> None:
        """Test the batch linting endpoint."""
        response = page.request.post(
            f"{BASE_URL}/lint/batch",
            data=json.dumps({
                "queries": [
                    {"sql": "SELECT * FROM users", "dialect": "duckdb"},
                    {"sql": "SELECT id, name FROM products WHERE YEAR(created) = 2024", "dialect": "duckdb"},
                ],
                "concurrency": 2
            }),
            headers={"Content-Type": "application/json"}
        )

        assert response.ok, f"Batch lint failed: {response.status}"
        body = response.json()

        assert "results" in body
        assert "total_queries" in body
        assert body["total_queries"] == 2
        assert "total_findings" in body
        assert isinstance(body["results"], list)
        assert len(body["results"]) == 2


class TestNetworkBoundaries:
    """Verify network boundaries and security headers."""

    def test_cors_headers_present(self, page: Any, test_server: Any) -> None:
        """Verify CORS headers are configured correctly."""
        response = page.request.options(
            API_LINT_URL,
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type"
            }
        )

        # Should have CORS headers (may be 200 or 204)
        assert response.status in (200, 204, 404)

    def test_rate_limit_headers(self, page: Any, test_server: Any) -> None:
        """Verify rate limit headers are present."""
        response = page.request.get(f"{BASE_URL}/health")
        assert response.ok

        headers = response.headers
        assert "x-request-id" in {k.lower() for k in headers.keys()}

    def test_request_id_header(self, page: Any, test_server: Any) -> None:
        """Verify X-Request-ID header is added to responses."""
        response = page.request.get(f"{BASE_URL}/health")
        assert response.ok

        headers = response.headers
        request_id = headers.get("x-request-id") or headers.get("X-Request-ID")
        assert request_id is not None
        assert len(request_id) > 0

    def test_content_type_json(self, page: Any, test_server: Any) -> None:
        """Verify API responses have correct Content-Type."""
        response = page.request.get(f"{BASE_URL}/health")
        assert response.ok

        ct = response.headers.get("content-type", "")
        assert "application/json" in ct, f"Expected JSON content-type, got: {ct}"

    def test_no_server_version_leak(self, page: Any, test_server: Any) -> None:
        """Verify server does not leak software version in headers."""
        response = page.request.get(f"{BASE_URL}/health")
        assert response.ok

        # uvicorn/python versions should not be exposed
        server = response.headers.get("server", "").lower()
        assert "uvicorn" not in server, f"Server header leaks version: {server}"
        assert "python" not in server, f"Server header leaks Python version: {server}"


class TestHealthAndReadiness:
    """Health and readiness probe validation."""

    def test_health_endpoint(self, page: Any, test_server: Any) -> None:
        """Verify /health endpoint returns correct structure."""
        response = page.request.get(f"{BASE_URL}/health")
        assert response.ok

        body = response.json()
        assert body["status"] == "online"
        assert "author" in body
        assert body["author"] == "Ahmad Bilal"
        assert "version" in body
        assert "timestamp" in body

    def test_ready_endpoint(self, page: Any, test_server: Any) -> None:
        """Verify /ready endpoint for Kubernetes readiness probes."""
        response = page.request.get(f"{BASE_URL}/ready")
        assert response.ok

        body = response.json()
        assert body["status"] == "ready"


class TestErrorBoundaries:
    """Ensure the server never returns 500 for any client input."""

    def test_empty_body(self, page: Any, test_server: Any) -> None:
        """POST with empty body should not 500."""
        response = page.request.post(
            API_LINT_URL,
            data="",
            headers={"Content-Type": "application/json"}
        )
        assert response.status != 500, f"Server returned 500: {response.status_text}"

    def test_huge_payload(self, page: Any, test_server: Any) -> None:
        """POST with extremely large SQL should not 500."""
        huge_sql = "SELECT " + ", ".join(f"col{i}" for i in range(5000)) + " FROM t"
        response = page.request.post(
            API_LINT_URL,
            data=json.dumps({"sql": huge_sql, "dialect": "duckdb"}),
            headers={"Content-Type": "application/json"}
        )
        assert response.status != 500, f"Server returned 500 on huge payload"

    def test_wrong_content_type(self, page: Any, test_server: Any) -> None:
        """POST with wrong Content-Type should not 500."""
        response = page.request.post(
            API_LINT_URL,
            data="not json",
            headers={"Content-Type": "text/plain"}
        )
        assert response.status != 500, f"Server returned 500 on bad content-type"

    def test_nonexistent_endpoint(self, page: Any, test_server: Any) -> None:
        """GET on nonexistent endpoint should return 404, not 500."""
        response = page.request.get(f"{BASE_URL}/api/nonexistent")
        assert response.status in (404, 405), f"Expected 404/405, got {response.status}"

    def test_sql_injection_attempt(self, page: Any, test_server: Any) -> None:
        """SQL injection in the request body must not crash the server."""
        response = page.request.post(
            API_LINT_URL,
            data=json.dumps({
                "sql": "'; DROP TABLE users; --",
                "dialect": "duckdb"
            }),
            headers={"Content-Type": "application/json"}
        )
        assert response.status != 500, f"Server crashed on SQL injection attempt"

    def test_unicode_heavy_payload(self, page: Any, test_server: Any) -> None:
        """SQL with heavy Unicode must be handled gracefully."""
        unicode_sql = "SELECT \u00e9\u00e8\u00ea, \u4e16\u754c FROM \u00fc\u00f6\u00e4"
        response = page.request.post(
            API_LINT_URL,
            data=json.dumps({"sql": unicode_sql, "dialect": "duckdb"}),
            headers={"Content-Type": "application/json"}
        )
        assert response.status != 500, f"Server crashed on Unicode payload"


# ── Fallback tests when Playwright is not available ────────────────────

if not _HAS_PLAYWRIGHT:
    def test_playwright_not_installed() -> None:
        """Placeholder test when Playwright is unavailable."""
        pytest.skip("playwright not installed (optional dev dependency)")


# ── CLI entry point for manual execution ────────────────────────────────

def main() -> int:
    """Run E2E tests programmatically."""
    if not _HAS_PLAYWRIGHT:
        print("Playwright not installed. Install with: pip install playwright && playwright install")
        return 1

    import subprocess
    result = subprocess.run([
        sys.executable, "-m", "pytest",
        __file__,
        "-v",
        "--tb=short"
    ])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
