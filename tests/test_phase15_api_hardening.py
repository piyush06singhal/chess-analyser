"""Phase 15: API hardening — auth, rate limits, request ids, headers, metrics.

These exercise the *middleware and seam* directly, so the behaviour is proven
without a full deployment: an open deployment authenticates as ``local``, a keyed
deployment refuses a missing/incorrect key, a limit produces a real 429 with a
``Retry-After``, and every response carries a request id and the security headers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from argus_api.config import get_settings
from argus_api.main import app
from argus_api.observability import METRICS
from argus_api.rate_limit import LIMITER, RateLimiter
from argus_api.security import (
    LOCAL_CALLER,
    AuthenticationError,
    RateLimitedError,
    authenticate,
    is_open,
    parse_api_keys,
)


# --- API-key parsing ----------------------------------------------------------


def test_api_keys_parse_caller_and_role() -> None:
    keys = parse_api_keys("abc:alice:operator,def:bob:viewer")
    assert keys["abc"] == {"caller": "alice", "role": "operator"}
    assert keys["def"] == {"caller": "bob", "role": "viewer"}


def test_api_keys_default_role_and_skip_malformed() -> None:
    keys = parse_api_keys("abc:alice, broken, :x:, def:bob:admin")
    assert keys["abc"]["role"] == "operator"
    assert "def" in keys
    # A malformed entry is skipped, never crashes startup.
    assert "" not in keys


def test_empty_keys_mean_open() -> None:
    assert parse_api_keys("") == {}
    assert parse_api_keys("   ") == {}


# --- authentication -----------------------------------------------------------


class _Settings:
    def __init__(self, keys: str = "") -> None:
        self.api_keys = keys


class _Request:
    """A minimal stand-in exposing the two attributes ``authenticate`` reads."""

    def __init__(self, *, keys: str, headers: dict[str, str] | None = None) -> None:
        self.app = type("App", (), {"state": type("State", (), {"settings": _Settings(keys)})()})()
        self.headers = headers or {}


def test_open_deployment_returns_local_caller() -> None:
    caller, role = authenticate(_Request(keys=""))
    assert caller == LOCAL_CALLER
    assert role == "operator"


def test_keyed_deployment_refuses_missing_key() -> None:
    with pytest.raises(AuthenticationError) as excinfo:
        authenticate(_Request(keys="abc:alice"))
    assert excinfo.value.code == "unauthorized"


def test_keyed_deployment_accepts_the_right_key() -> None:
    caller, role = authenticate(_Request(keys="abc:alice:viewer", headers={"x-api-key": "abc"}))
    assert caller == "alice"
    assert role == "viewer"


def test_keyed_deployment_accepts_a_bearer_token() -> None:
    caller, _ = authenticate(
        _Request(keys="abc:alice", headers={"authorization": "Bearer abc"})
    )
    assert caller == "alice"


def test_keyed_deployment_refuses_a_wrong_key() -> None:
    with pytest.raises(AuthenticationError):
        authenticate(_Request(keys="abc:alice", headers={"x-api-key": "nope"}))


def test_is_open_reflects_configuration() -> None:
    assert is_open(_Settings("")) is True
    assert is_open(_Settings("abc:alice")) is False


# --- rate limiter -------------------------------------------------------------


def test_rate_limiter_allows_up_to_the_limit_then_refuses() -> None:
    limiter = RateLimiter()
    for _ in range(3):
        limiter.check(bucket="analysis", key="alice", limit=3, window_seconds=60)
    with pytest.raises(RateLimitedError) as excinfo:
        limiter.check(bucket="analysis", key="alice", limit=3, window_seconds=60)
    assert excinfo.value.retry_after > 0


def test_rate_limiter_is_per_caller() -> None:
    limiter = RateLimiter()
    for _ in range(2):
        limiter.check(bucket="analysis", key="alice", limit=2, window_seconds=60)
    # A different caller has their own budget.
    limiter.check(bucket="analysis", key="bob", limit=2, window_seconds=60)


def test_rate_limiter_zero_limit_is_unlimited() -> None:
    limiter = RateLimiter()
    for _ in range(100):
        limiter.check(bucket="analysis", key="alice", limit=0, window_seconds=60)


def test_rate_limiter_window_resets() -> None:
    clock = [0.0]
    limiter = RateLimiter(clock=lambda: clock[0])
    limiter.check(bucket="analysis", key="alice", limit=1, window_seconds=60)
    with pytest.raises(RateLimitedError):
        limiter.check(bucket="analysis", key="alice", limit=1, window_seconds=60)
    clock[0] = 61.0
    limiter.check(bucket="analysis", key="alice", limit=1, window_seconds=60)


def test_rate_limited_error_maps_to_429() -> None:
    from argus_api.main import _STATUS_BY_CODE

    assert _STATUS_BY_CODE["rate_limited"] == 429
    assert _STATUS_BY_CODE["unauthorized"] == 401
    assert _STATUS_BY_CODE["payload_too_large"] == 413


# --- middleware over a real app ----------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    LIMITER.reset()
    METRICS.reset()
    with TestClient(app) as test_client:
        yield test_client


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")


def test_a_proposed_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "trace-123"})
    assert response.headers["X-Request-ID"] == "trace-123"


def test_an_oversized_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "x" * 500})
    assert response.headers["X-Request-ID"] != "x" * 500


def test_security_headers_are_present(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_metrics_endpoint_reports_real_counters(client: TestClient) -> None:
    client.get("/health")
    body = client.get("/metrics").text
    assert "argus_http_requests_total" in body
    assert "argus_http_request_duration_seconds_count" in body


def test_oversized_body_is_refused_before_reading(client: TestClient, monkeypatch) -> None:
    # The middleware reads the live setting, so a tiny limit can be applied here.
    monkeypatch.setattr(get_settings(), "max_request_bytes", 10, raising=False)
    response = client.post("/api/games/import", json={"pgn_text": "x" * 1000})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_errors_use_one_envelope_with_a_request_id(client: TestClient) -> None:
    response = client.get("/api/games/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"]


def test_authenticated_deployment_refuses_a_missing_key(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_API_KEYS", "secret:alice")
    get_settings.cache_clear()
    try:
        with TestClient(app) as keyed:
            assert keyed.get("/api/games").status_code == 401
            # /metrics is not a probe path, so it still proves the key works.
            ok = keyed.get("/metrics", headers={"X-API-Key": "secret"})
            assert ok.status_code == 200
    finally:
        get_settings.cache_clear()


def test_liveness_and_readiness_probes_need_no_key(monkeypatch) -> None:
    """A healthcheck cannot present a credential, so these paths must not need one.

    Requiring a key on them made every keyed deployment report *itself* unhealthy:
    the Docker ``HEALTHCHECK``, the ``http_service.checks`` in ``fly.toml``, a
    compose ``condition: service_healthy`` and an orchestrator's readiness probe
    all issue a bare request and cannot add a header. The service was fine; the
    probes were refused.
    """
    monkeypatch.setenv("ARGUS_API_KEYS", "secret:alice")
    get_settings.cache_clear()
    try:
        with TestClient(app) as keyed:
            for path in ("/health", "/ready", "/health/ready"):
                assert keyed.get(path).status_code == 200, path
            # The exemption is exactly these paths: operational counters stay
            # keyed, and no data route is opened up by it.
            assert keyed.get("/metrics").status_code == 401
            assert keyed.get("/api/games").status_code == 401
    finally:
        get_settings.cache_clear()


def test_cors_preflight_is_not_blocked_by_authentication(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_API_KEYS", "secret:alice")
    get_settings.cache_clear()
    try:
        with TestClient(app) as keyed:
            response = keyed.options(
                "/api/games",
                headers={
                    "Origin": "http://localhost:3100",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert response.status_code in (200, 204)
    finally:
        get_settings.cache_clear()
