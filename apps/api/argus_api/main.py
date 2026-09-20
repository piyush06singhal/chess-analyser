"""ARGUS Chess API application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import argus_api
from argus.shared.errors import ArgusError
from argus.shared.logging import configure_logging, get_logger
from argus_api.config import get_settings
from argus_api.db.models import Base
from argus_api.db.session import SessionFactory, build_engine, check_connection
from argus_api.routes import analysis, games, health
from argus_api.services import engine_service
from argus_api.services.redis_service import check_redis

logger = get_logger(__name__)

# HTTP status per domain error code (central mapping — no scattered handling).
_STATUS_BY_CODE = {
    "invalid_fen": 422,
    "invalid_pgn": 422,
    "invalid_move": 422,
    "validation_error": 422,
    "insufficient_data": 422,
    "not_found": 404,
    "tool_not_found": 404,
    "tool_unavailable": 501,
    "llm_not_configured": 501,
    "engine_unavailable": 503,
    "engine_timeout": 504,
    "engine_response": 500,
    "engine_error": 500,
    "analysis_error": 500,
    "repository_error": 500,
    "argus_error": 500,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize settings, database, and engine; dispose them on shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    app.state.settings = settings
    app.state.db_engine = build_engine(settings)
    app.state.session_factory = SessionFactory(app.state.db_engine)
    if app.state.session_factory.enabled:
        # Table creation for Phase 1; Alembic migrations arrive in Phase 2.
        Base.metadata.create_all(app.state.db_engine)
        logger.info(
            "Database tables ensured [dialect=%s]", app.state.db_engine.dialect.name
        )
    app.state.engine = engine_service.get_engine(settings)

    db_status = check_connection(app.state.db_engine)
    redis_status = check_redis(settings.redis_url)
    logger.info(
        "ARGUS API starting [env=%s database=%s redis=%s engine_available=%s]",
        settings.env,
        "connected" if db_status.get("connected") else db_status.get("reason", "unavailable"),
        "connected" if redis_status.get("connected") else redis_status.get("reason", "unconfigured"),
        app.state.engine.info().get("available"),
    )
    yield
    engine_service.reset_engine()
    if app.state.db_engine is not None:
        app.state.db_engine.dispose()
    logger.info("ARGUS API stopped")


def create_app() -> FastAPI:
    """Create the FastAPI application with routes and error handling."""
    app = FastAPI(
        title="ARGUS Chess API",
        version=argus_api.__version__,
        lifespan=lifespan,
        description=(
            "AI-powered chess game intelligence, analysis, and personalized "
            "coaching platform. Deterministic engine analysis (Stockfish) is "
            "kept strictly separate from probabilistic AI functionality."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(games.router)
    app.include_router(analysis.router)

    @app.exception_handler(ArgusError)
    async def handle_argus_error(_: Request, exc: ArgusError) -> JSONResponse:
        """Central domain error handler: stable codes, honest statuses."""
        return JSONResponse(
            status_code=_STATUS_BY_CODE.get(exc.code, 500),
            content={"error": exc.to_dict()},
        )

    return app


app = create_app()
