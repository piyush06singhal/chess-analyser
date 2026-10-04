"""Caissa API application entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import argus_api
from argus.analysis.cache import CachingEngine, PositionAnalysisCache
from argus.shared.errors import ArgusError
from argus.shared.logging import configure_logging, get_logger
from argus_api.config import get_settings, validate_settings
from argus_api.middleware import (
    REQUEST_ID_HEADER,
    BodySizeLimitMiddleware,
    FeatureGateMiddleware,
    MetricsMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from argus_api.observability import current_request_id
from argus_api.db.models import Base
from argus_api.db.repository import backfill_player_identities, merge_duplicate_players, prune_orphan_players
from argus_api.db.schema import ensure_schema_upgrades
from argus_api.db.session import SessionFactory, build_engine, check_connection
from argus_api.routes import (
    analysis,
    coach,
    coaching,
    games,
    graph,
    health,
    intelligence,
    live,
    opponents,
    players,
    predictions,
    scenarios,
    sources,
    training,
)
from argus_api.services import engine_service
from argus_api.services.live_sweeper import sweep_loop
from argus_api.services.redis_service import check_redis

logger = get_logger(__name__)

# HTTP status per domain error code (central mapping — no scattered handling).
_STATUS_BY_CODE = {
    "invalid_fen": 422,
    "invalid_pgn": 422,
    "invalid_move": 422,
    "unsupported_source": 422,
    "upload_error": 422,
    "validation_error": 422,
    "live_game_limit": 422,
    "insufficient_data": 422,
    "not_found": 404,
    "conflict": 409,
    "source_player_not_found": 404,
    "source_response": 502,
    "source_unavailable": 503,
    "source_rate_limited": 429,
    "source_error": 502,
    "tool_not_found": 404,
    "tool_unavailable": 501,
    "llm_not_configured": 501,
    "llm_error": 502,
    "llm_request_error": 502,
    "llm_response_error": 502,
    "engine_unavailable": 503,
    "engine_timeout": 504,
    "engine_response": 500,
    "engine_error": 500,
    "analysis_cancelled": 409,
    "analysis_required": 409,
    "analysis_error": 500,
    "repository_error": 500,
    "unauthorized": 401,
    "rate_limited": 429,
    "payload_too_large": 413,
    "service_busy": 503,
    # A feature that is off in this deployment is not advertised (404), matching
    # how a game the caller may not read looks absent.
    "feature_disabled": 404,
    "configuration_error": 500,
    "argus_error": 500,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize settings, database, and engine; dispose them on shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    # Configuration is validated against the environment's rules at startup. In
    # production a misconfigured deployment must not run (an open production API
    # is exactly the failure this catches); elsewhere the problems are logged so
    # they are visible without blocking local work.
    problems = validate_settings(settings)
    if problems:
        for problem in problems:
            logger.error("Configuration problem: %s", problem)
        if settings.is_production:
            from argus.shared.errors import ConfigurationError

            raise ConfigurationError(
                f"Refusing to start: {len(problems)} configuration problem(s).",
                details={"problems": problems},
            )
    app.state.settings = settings
    app.state.db_engine = build_engine(settings)
    app.state.session_factory = SessionFactory(app.state.db_engine)
    if app.state.session_factory.enabled:
        # Table creation (Alembic is still a follow-up) plus the additive column
        # upgrades create_all cannot express.
        Base.metadata.create_all(app.state.db_engine)
        upgraded = ensure_schema_upgrades(app.state.db_engine)
        logger.info(
            "Database tables ensured [dialect=%s upgrades=%s]",
            app.state.db_engine.dialect.name,
            ", ".join(upgraded) if upgraded else "none",
        )
        # Player identity hygiene (Phase 5). Order matters: give legacy rows
        # their identity key, merge rows that normalize to the same player, then
        # drop rows that no longer have any game. Games themselves are never
        # touched by this (merging only re-points foreign keys).
        try:
            with app.state.session_factory.session_scope() as session:
                filled = backfill_player_identities(session)
                merged = merge_duplicate_players(session)
                pruned = prune_orphan_players(session)
            if filled or merged or pruned:
                logger.info(
                    "Player identity hygiene [backfilled=%d merged_groups=%d pruned_orphans=%d]",
                    filled,
                    len(merged),
                    len(pruned),
                )
        except Exception as exc:  # noqa: BLE001 — never block startup on hygiene
            logger.warning("Player identity hygiene skipped: %s", exc)
    # Validate engine configuration at startup (clear error if unavailable),
    # then wrap the shared engine with a configuration-aware position cache.
    try:
        engine_path = engine_service.validate_engine_configuration(settings)
        logger.info("Stockfish located [path=%s]", engine_path)
    except ArgusError as exc:
        logger.warning("Engine unavailable at startup: %s", exc.message)
    app.state.engine = CachingEngine(
        engine_service.get_engine(settings),
        PositionAnalysisCache(max_entries=settings.analysis_cache_entries),
    )

    db_status = check_connection(app.state.db_engine)
    redis_status = check_redis(settings.redis_url)
    logger.info(
        "Caissa API starting [env=%s database=%s redis=%s engine_available=%s]",
        settings.env,
        "connected" if db_status.get("connected") else db_status.get("reason", "unavailable"),
        "connected" if redis_status.get("connected") else redis_status.get("reason", "unconfigured"),
        app.state.engine.info().get("available"),
    )
    # Phase 12: a server-authoritative clock has to be checked by the server, so
    # a small loop flags games whose time ran out while nobody was watching.
    if app.state.session_factory.enabled:
        app.state.live_sweeper = asyncio.create_task(sweep_loop(app.state.session_factory))
    yield
    task = getattr(app.state, "live_sweeper", None)
    if task is not None and not task.done():
        task.cancel()
    engine_service.reset_engine()
    if app.state.db_engine is not None:
        app.state.db_engine.dispose()
    logger.info("Caissa API stopped")


def create_app() -> FastAPI:
    """Create the FastAPI application with routes and error handling."""
    app = FastAPI(
        title="Caissa API",
        version=argus_api.__version__,
        lifespan=lifespan,
        description=(
            "AI-powered chess game intelligence, analysis, and personalized "
            "coaching platform. Deterministic engine analysis (Stockfish) is "
            "kept strictly separate from probabilistic AI functionality."
        ),
    )
    settings = get_settings()
    # Middleware is applied outermost-last in Starlette: the last added runs
    # first. Security headers and the request context must wrap everything, so
    # they are added last. CORS is added first so a preflight is answered before
    # the body-size check. (All of these are added *before* the routers, which
    # is required — Starlette forbids adding middleware after startup.)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(FeatureGateMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(games.router)
    app.include_router(sources.router)
    app.include_router(analysis.router)
    app.include_router(intelligence.router)
    # Opponent routes are registered before the player routes so the one-segment
    # ``/api/players/opponent-meta`` cannot be shadowed by ``/{player_id}``.
    app.include_router(opponents.router)
    app.include_router(players.router)
    # Feature flags are enforced by ``FeatureGateMiddleware`` by path prefix
    # (see middleware.py), so HTTP and WebSocket routes are covered uniformly
    # without a Request-typed dependency that a WebSocket scope cannot satisfy.
    app.include_router(predictions.router)
    app.include_router(coach.router)
    app.include_router(training.router)
    app.include_router(scenarios.router)
    # Phase 11: the coaching workspace composes the phases above; it is
    # registered last and owns no analysis of its own.
    app.include_router(coaching.router)
    # Phase 12: live games. Actions are REST; the event stream is a WebSocket.
    app.include_router(live.router)
    # Phase 13: the intelligence graph sits above every phase above it and owns
    # no analysis of its own; it is registered last.
    app.include_router(graph.router)

    @app.exception_handler(ArgusError)
    async def handle_argus_error(_: Request, exc: ArgusError) -> JSONResponse:
        """Central domain error handler: stable codes, honest statuses.

        The body is the one consistent error envelope; the request id ties the
        response to the server log line. Internal details (a stack trace, a SQL
        string, a file path) never reach the client.
        """
        status = _STATUS_BY_CODE.get(exc.code, 500)
        payload = exc.to_dict()
        payload["request_id"] = current_request_id()
        headers = {}
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            headers["Retry-After"] = str(int(retry_after) if retry_after >= 1 else 1)
        return JSONResponse(status_code=status, content={"error": payload}, headers=headers or None)

    return app


app = create_app()
