"""Health routes: liveness, readiness, and dependency status."""

from __future__ import annotations

import argus_api
from fastapi import APIRouter, Request

from argus_api.db.session import check_connection
from argus_api.services.redis_service import check_redis

router = APIRouter(tags=["health"])


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


@router.get("/health/ready")
def readiness() -> dict:
    """Liveness probe for orchestrators."""
    return {"status": "ready"}
