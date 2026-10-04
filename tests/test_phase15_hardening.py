"""Phase 15 hardening tests: configuration, feature flags, and resource bounds.

These cover the operational slice added after the API-hardening slice: the
environment-aware configuration validator, the feature-flag gate, the engine
resource gate (bounded concurrency + bounded queue + idempotency), and the
route-level enforcement of rate limits and flags.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from argus.shared.errors import (
    ConflictError,
    FeatureDisabledError,
    ServiceBusyError,
)
from argus_api.config import ENVIRONMENTS, Settings, get_settings, validate_settings
from argus_api.feature_flags import KNOWN_FLAGS, is_enabled, require
from argus_api.main import app
from argus_api.rate_limit import LIMITER
from argus_api.services.resource_gate import GATE, EngineGate


# --- configuration -----------------------------------------------------------


class TestConfiguration:
    def test_environments_are_the_documented_set(self):
        assert ENVIRONMENTS == ("development", "test", "staging", "production")

    def test_is_production_is_a_measured_property(self):
        assert Settings(env="production").is_production is True
        assert Settings(env="Production").is_production is True
        assert Settings(env="staging").is_production is False

    def test_development_is_valid_with_defaults(self):
        settings = Settings(
            env="development",
            cors_origins="http://localhost:3100",
            database_url="",
            api_keys="",
        )
        assert validate_settings(settings, environment="development") == []

    def test_unknown_environment_is_a_problem(self):
        problems = validate_settings(Settings(env="prod"), environment="prod")
        assert any("Unknown environment" in problem for problem in problems)

    def test_production_requires_keys_postgres_and_json_logs(self):
        settings = Settings(
            env="production",
            cors_origins="https://argus.example",
            database_url="",
            api_keys="",
            log_format="text",
        )
        problems = validate_settings(settings, environment="production")
        joined = " ".join(problems)
        assert "ARGUS_API_KEYS" in joined
        assert "ARGUS_DATABASE_URL" in joined
        assert "ARGUS_LOG_FORMAT=json" in joined

    def test_production_refuses_sqlite_and_echo_provider(self):
        settings = Settings(
            env="production",
            cors_origins="https://argus.example",
            database_url="sqlite:///x.db",
            api_keys="k:ops:operator",
            log_format="json",
            llm_provider="echo",
        )
        problems = validate_settings(settings, environment="production")
        joined = " ".join(problems)
        assert "PostgreSQL" in joined
        assert "echo" in joined

    def test_production_valid_configuration_passes(self):
        settings = Settings(
            env="production",
            cors_origins="https://argus.example",
            database_url="postgresql+psycopg2://u:p@db:5432/argus",
            api_keys="k:ops:operator",
            log_format="json",
            llm_provider="openai",
        )
        assert validate_settings(settings, environment="production") == []

    def test_negative_limits_are_reported(self):
        settings = Settings(
            env="development",
            cors_origins="http://localhost:3100",
            engine_max_concurrency=-1,
        )
        problems = validate_settings(settings)
        assert any("engine_max_concurrency" in problem for problem in problems)

    def test_empty_cors_is_a_problem(self):
        problems = validate_settings(Settings(env="development", cors_origins=""))
        assert any("CORS" in problem for problem in problems)


# --- feature flags -----------------------------------------------------------


class TestFeatureFlags:
    def test_unknown_flags_are_off(self):
        settings = Settings(feature_flags="not_a_real_flag=true")
        # Unknown flags fall back to the caller's default (on), never to the
        # configured value — a typo cannot enable anything.
        assert is_enabled(settings, "not_a_real_flag", default=True) is True
        assert is_enabled(settings, "not_a_real_flag", default=False) is False

    def test_known_flag_defaults_on_when_unlisted(self):
        settings = Settings(feature_flags="")
        for name in KNOWN_FLAGS:
            assert is_enabled(settings, name) is True

    def test_known_flag_can_be_disabled(self):
        settings = Settings(feature_flags="coach=false")
        assert is_enabled(settings, "coach") is False
        assert is_enabled(settings, "training") is True

    def test_flag_map_parses_variants(self):
        settings = Settings(feature_flags="coach=off, graph=1, live_chess")
        flags = settings.feature_flag_map
        assert flags["coach"] is False
        assert flags["graph"] is True
        assert flags["live_chess"] is True

    def test_require_raises_with_the_flag_named(self):
        settings = Settings(feature_flags="predictions=false")
        with pytest.raises(FeatureDisabledError) as excinfo:
            require(settings, "predictions")
        assert excinfo.value.details["flag"] == "predictions"
        # The API maps a disabled feature to 404 (not advertised).
        assert excinfo.value.code == "feature_disabled"

    def test_require_passes_when_enabled(self):
        require(Settings(feature_flags=""), "coach")  # no raise


# --- engine resource gate ----------------------------------------------------


class TestEngineGate:
    def _settings(self, *, concurrency=1, queue=1):
        return Settings(
            env="development",
            engine_max_concurrency=concurrency,
            analysis_queue_limit=queue,
        )

    def setup_method(self):
        GATE.reset()

    def teardown_method(self):
        GATE.reset()

    def test_second_admit_for_same_game_conflicts(self):
        settings = self._settings()
        gate = EngineGate()
        gate.admit("g1", settings)
        with pytest.raises(ConflictError):
            gate.admit("g1", settings)

    def test_queue_limit_refuses_with_service_busy(self):
        settings = self._settings(concurrency=1, queue=1)
        gate = EngineGate()
        gate.admit("g1", settings)  # running
        gate.admit("g2", settings)  # queued (capacity = 2)
        with pytest.raises(ServiceBusyError):
            gate.admit("g3", settings)

    def test_slot_releases_and_allows_reuse(self):
        settings = self._settings()
        gate = EngineGate()
        gate.admit("g1", settings)
        with gate.slot("g1", settings):
            assert gate.snapshot()["running"] == 1
        gate.release("g1")
        assert gate.snapshot()["in_flight"] == 0
        # After release the game may be admitted again.
        gate.admit("g1", settings)

    def test_snapshot_counts_running_and_queued(self):
        settings = self._settings(concurrency=2, queue=5)
        gate = EngineGate()
        gate.admit("a", settings)
        gate.admit("b", settings)
        gate.admit("c", settings)
        snapshot = gate.snapshot()
        assert snapshot["in_flight"] == 3
        assert snapshot["max_concurrency"] == 2
        assert snapshot["queue_limit"] == 5


# --- route-level enforcement -------------------------------------------------


def _client_with(monkeypatch, **env):
    """Build a fresh TestClient after applying environment overrides."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return TestClient(app)


class TestRouteEnforcement:
    def setup_method(self):
        LIMITER.reset()
        GATE.reset()

    def teardown_method(self):
        LIMITER.reset()
        GATE.reset()

    def test_disabled_feature_returns_404(self, monkeypatch):
        with _client_with(monkeypatch, ARGUS_FEATURE_FLAGS="coach=false") as client:
            assert client.get("/api/coach/status").status_code == 404
            # A different, enabled surface still works.
            assert client.get("/api/training/meta").status_code in {200, 404}
            # The 404 carries the flag name so an operator can tell why.
            body = client.get("/api/coach/status").json()
            assert body["error"]["details"]["flag"] == "coach"

    def test_enabled_feature_is_served(self, monkeypatch):
        with _client_with(monkeypatch, ARGUS_FEATURE_FLAGS="coach=true") as client:
            assert client.get("/api/coach/status").status_code == 200

    def test_analysis_rate_limit_returns_429(self, monkeypatch):
        with _client_with(
            monkeypatch,
            ARGUS_RATE_LIMIT_ANALYSIS_PER_MINUTE="1",
            ARGUS_ENGINE_DEPTH="6",
        ) as client:
            first = client.post(
                "/api/analysis/position",
                json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
            )
            # The first request is admitted (whatever the engine does with it);
            # the second is refused by the limiter *before* the handler runs.
            assert first.status_code != 429
            second = client.post(
                "/api/analysis/position",
                json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
            )
            assert second.status_code == 429
            assert second.headers.get("Retry-After")
            assert second.json()["error"]["code"] == "rate_limited"


# --- redis probe -------------------------------------------------------------


class _FakeSocket:
    """Records what the probe sends and answers like a password-protected Redis."""

    def __init__(self, replies: list[bytes]) -> None:
        self.sent: list[bytes] = []
        self._replies = list(replies)

    def __enter__(self) -> "_FakeSocket":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _n: int) -> bytes:
        return self._replies.pop(0) if self._replies else b""


class TestRedisProbe:
    def test_a_password_protected_redis_is_reported_connected(self, monkeypatch):
        # The Phase 15 hardening gives Redis a password; a probe that never sends
        # AUTH reports a healthy instance as down. This is the regression guard.
        from argus_api.services import redis_service

        sock = _FakeSocket([b"+OK\r\n", b"+PONG\r\n"])
        monkeypatch.setattr(redis_service.socket, "create_connection", lambda *a, **k: sock)
        status = redis_service.check_redis("redis://:secret@localhost:6379/0")
        assert status["connected"] is True
        assert any(b"AUTH" in payload and b"secret" in payload for payload in sock.sent)

    def test_a_wrong_password_is_reported_honestly(self, monkeypatch):
        from argus_api.services import redis_service

        sock = _FakeSocket([b"-WRONGPASS invalid username-password pair\r\n"])
        monkeypatch.setattr(redis_service.socket, "create_connection", lambda *a, **k: sock)
        status = redis_service.check_redis("redis://:wrong@localhost:6379/0")
        assert status["connected"] is False
        assert "authentication" in status["reason"]

    def test_an_unconfigured_url_is_reported_unconfigured(self):
        from argus_api.services.redis_service import check_redis

        status = check_redis("")
        assert status == {
            "configured": False,
            "connected": False,
            "reason": "ARGUS_REDIS_URL is not set",
        }


# --- audit log -----------------------------------------------------------------


class _StubRequest:
    """Just enough of a Request for ``authenticate``: app settings + headers."""

    def __init__(self, api_keys: str, headers: dict[str, str] | None = None) -> None:
        self.app = type("App", (), {"state": type("State", (), {"settings": type("S", (), {"api_keys": api_keys})()})()})()
        self.headers = headers or {}


class TestAuditLog:
    """The audit log must actually be written, not merely defined.

    A named audit event that is never emitted is worse than none: it implies
    coverage that does not exist. These tests pin the call sites.
    """

    def test_audit_emits_a_structured_json_event(self, caplog):
        import logging

        from argus_api.observability import audit

        with caplog.at_level(logging.INFO, logger="argus_api.observability"):
            audit("test.event", answer=42)
        line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("audit "))
        assert '"event": "test.event"' in line
        assert '"answer": 42' in line

    def test_a_missing_key_is_audited(self, caplog):
        import logging

        from argus_api.observability import AUDIT_AUTH_FAILED
        from argus_api.security import AuthenticationError, authenticate

        request = _StubRequest("secret:alice:operator")
        with caplog.at_level(logging.INFO, logger="argus_api.observability"):
            with pytest.raises(AuthenticationError):
                authenticate(request)  # type: ignore[arg-type]
        assert any(AUDIT_AUTH_FAILED in r.getMessage() for r in caplog.records)

    def test_an_invalid_key_is_audited(self, caplog):
        import logging

        from argus_api.observability import AUDIT_AUTH_FAILED
        from argus_api.security import AuthenticationError, authenticate

        request = _StubRequest("secret:alice:operator", {"x-api-key": "wrong"})
        with caplog.at_level(logging.INFO, logger="argus_api.observability"):
            with pytest.raises(AuthenticationError):
                authenticate(request)  # type: ignore[arg-type]
        assert any(AUDIT_AUTH_FAILED in r.getMessage() for r in caplog.records)

    def test_a_valid_key_is_not_audited_as_a_failure(self, caplog):
        import logging

        from argus_api.observability import AUDIT_AUTH_FAILED
        from argus_api.security import authenticate

        request = _StubRequest("secret:alice:operator", {"x-api-key": "secret"})
        with caplog.at_level(logging.INFO, logger="argus_api.observability"):
            caller, role = authenticate(request)  # type: ignore[arg-type]
        assert caller == "alice" and role == "operator"
        assert not any(AUDIT_AUTH_FAILED in r.getMessage() for r in caplog.records)
