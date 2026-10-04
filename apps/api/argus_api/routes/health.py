"""Health routes: liveness, readiness, and dependency status.

``/health`` reports the service and each dependency it knows about. ``/ready``
answers the different question an orchestrator asks: *can this instance serve
traffic right now?* — which needs the database and the engine, and reports the
rest (redis, the migration state, the model registry, the LLM provider) as
individual, honest fields rather than one opaque boolean.

Every check here is a real probe. Nothing is stubbed to return "ok": if the
engine is missing, ``/ready`` says so and the status is ``degraded``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import argus_api
from fastapi import APIRouter, Request
from sqlalchemy import inspect as sa_inspect

from fastapi.responses import PlainTextResponse

from argus_api.db.session import check_connection
from argus_api.observability import METRICS
from argus_api.security import is_open
from argus_api.services.redis_service import check_redis

router = APIRouter(tags=["health"])

#: Tables Caissa expects to exist once migrations have run. Checked by name, so a
#: half-migrated database is reported instead of assumed fine.
EXPECTED_TABLES = (
    "games",
    "players",
    "move_analyses",
    "training_positions",
    "training_attempts",
    "training_sessions",
    "opponent_profiles",
    "player_profiles",
    "scenarios",
    "study_collections",
    "study_items",
    "match_preparations",
    "live_games",
    "live_game_moves",
    "live_game_events",
)


def _migration_check(request: Request) -> dict:
    """Are the expected tables present? (The closest honest migration probe.)"""
    try:
        engine = request.app.state.db_engine
        names = set(sa_inspect(engine).get_table_names())
        missing = [name for name in EXPECTED_TABLES if name not in names]
        return {
            "ok": not missing,
            "expected": len(EXPECTED_TABLES),
            "present": len(EXPECTED_TABLES) - len(missing),
            "missing": missing,
            "detail": (
                "All expected tables are present."
                if not missing
                else f"Missing tables: {', '.join(missing)}"
            ),
        }
    except Exception as exc:  # noqa: BLE001 — readiness must never raise
        return {"ok": False, "detail": f"Could not inspect the schema: {exc}"}


def _registry_check(request: Request) -> dict:
    """The ML registry's real state: how many models are production-approved."""
    try:
        from argus.ml.registry import ModelRegistry

        from argus_api.services.prediction_service import models_dir_for

        # The registry lives in the *configured* model store. Loading it from the
        # default path would report "unreadable" on any deployment that moved its
        # models — a health check that lies about a correctly configured system.
        registry = ModelRegistry.load(models_dir_for(getattr(request.app.state, "settings", None)))
        summary = registry.summary()
        return {
            "ok": True,
            "registered": summary.get("registered"),
            "production_models": summary.get("production_models") or [],
            "detail": (
                "No model has passed its production gate, so predictions are "
                "unavailable by design."
                if not (summary.get("production_models") or [])
                else "A production model is available for at least one task."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"Registry unreadable: {exc}"}


def _auth_check(request: Request) -> dict:
    """Whether authentication is on, and what mode the deployment is in.

    Open mode is not an error, but it is stated as a measured fact: a production
    deployment should set ``ARGUS_API_KEYS``.
    """
    settings = request.app.state.settings
    if is_open(settings):
        return {
            "ok": True,
            "mode": "open",
            "detail": (
                "No API keys configured: every request is the 'local' caller. "
                "Set ARGUS_API_KEYS to require a key."
            ),
        }
    from argus_api.security import parse_api_keys

    count = len(parse_api_keys(settings.api_keys))
    return {
        "ok": True,
        "mode": "keyed",
        "keys": count,
        "detail": f"Authentication is on with {count} configured key(s).",
    }


def _engine_capacity(request: Request) -> dict:
    """The engine resource gate's real state: slots in use, work queued (§8).

    Non-blocking: a busy engine degrades throughput, not correctness, so it is
    reported rather than treated as unready. It is a measured fact, not a guess.
    """
    from argus_api.services.resource_gate import GATE

    snapshot = GATE.snapshot()
    return {
        "ok": True,
        "max_concurrency": snapshot["max_concurrency"],
        "queue_limit": snapshot["queue_limit"],
        "running": snapshot["running"],
        "queued": snapshot["queued"],
        "detail": (
            f"{snapshot['running']} analysis slot(s) in use, "
            f"{snapshot['queued']} queued (limit {snapshot['max_concurrency']} running, "
            f"{snapshot['queue_limit']} queued)."
        ),
    }


def _llm_check(request: Request) -> dict:
    settings = request.app.state.settings
    provider = (settings.llm_provider or "").strip()
    if not provider:
        return {
            "ok": True,
            "configured": False,
            "provider": None,
            "detail": (
                "No LLM provider configured: the coach returns an explicit 501 and "
                "every deterministic path still works."
            ),
        }
    return {
        "ok": True,
        "configured": True,
        "provider": provider,
        "detail": f"Provider '{provider}' is configured; API keys are never exposed here.",
    }


@router.get("/health")
def health(request: Request) -> dict:
    """Full health check with honest per-dependency status.

    ``status`` is "ok" only when the service itself is running; degraded
    dependencies (database, redis, engine) are reported per field so callers
    can distinguish "service up" from "all systems up".
    """
    settings = request.app.state.settings
    engine = request.app.state.engine
    database = check_connection(request.app.state.db_engine)
    redis = check_redis(settings.redis_url)
    engine_info = engine.info()
    return {
        "status": "ok",
        "service": "argus-api",
        "version": argus_api.__version__,
        "environment": settings.env,
        "engine": engine_info,
        "database": database,
        "redis": redis,
    }


def _readiness(request: Request) -> dict:
    settings = request.app.state.settings
    database = check_connection(request.app.state.db_engine)
    redis = check_redis(settings.redis_url)
    try:
        engine_info = request.app.state.engine.info()
    except Exception as exc:  # noqa: BLE001
        engine_info = {"available": False, "detail": str(exc)}
    migrations = _migration_check(request)
    registry = _registry_check(request)
    llm = _llm_check(request)
    auth = _auth_check(request)
    capacity = _engine_capacity(request)

    checks = {
        "database": {"ok": bool(database.get("connected")), "detail": database},
        "redis": {"ok": bool(redis.get("connected")), "detail": redis},
        "auth": auth,
        "stockfish": {
            "ok": bool(engine_info.get("available")),
            "name": engine_info.get("engine"),
            "version": engine_info.get("version"),
            "detail": engine_info,
        },
        "engine_capacity": capacity,
        "migrations": migrations,
        "ml_registry": registry,
        "ai_provider": llm,
    }
    # Ready means "able to do its core job": a database to read and an engine to
    # analyse with. Everything else is reported but does not block serving.
    blocking = [name for name in ("database", "stockfish") if not checks[name]["ok"]]
    return {
        "status": "ready" if not blocking else "degraded",
        "service": "argus-api",
        "version": argus_api.__version__,
        "environment": settings.env,
        "blocking": blocking,
        "checks": checks,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
def ready(request: Request) -> dict:
    """Readiness for orchestrators: real probes, honest degradation."""
    return _readiness(request)


@router.get("/health/ready")
def readiness(request: Request) -> dict:
    """Alias of ``/ready`` (kept for existing probes)."""
    return _readiness(request)


@router.get("/metrics", response_class=PlainTextResponse)
def metrics() -> PlainTextResponse:
    """Prometheus text exposition of this process's counters and histograms.

    These are *this process's* measurements and they reset when it restarts;
    they are not an uptime or a fleet total. A multi-process deployment scrapes
    each process.
    """
    return PlainTextResponse(METRICS.render(), media_type="text/plain; version=0.0.4")
