"""Engine service: process-wide Stockfish lifecycle for the API.

The engine is created lazily from configuration and shared across requests.
All blocking engine calls are executed via ``asyncio.to_thread`` in the route
handlers (the engine itself is thread-safe behind a lock). Engine failures
surface as :class:`EngineError` subclasses and are mapped centrally to HTTP
responses.

Configuration is validated at startup: :func:`validate_engine_configuration`
raises a clear domain error when no engine binary can be located, instead of
failing mysteriously on the first analysis.
"""

from __future__ import annotations

from argus.analysis.engine.base import ChessEngine
from argus.analysis.engine.stockfish import (
    StockfishEngine,
    StockfishSettings,
    locate_stockfish,
)
from argus_api.config import Settings
from argus.shared.errors import EngineNotFoundError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

_engine: ChessEngine | None = None


def build_engine(settings: Settings) -> ChessEngine:
    """Build an engine from settings (used by tests to isolate state)."""
    return StockfishEngine(
        StockfishSettings(
            path=settings.stockfish_path or None,
            depth=settings.engine_depth,
            multipv=settings.engine_multipv,
            timeout_seconds=settings.engine_timeout_seconds,
            threads=settings.engine_threads,
            hash_mb=settings.engine_hash_mb,
            movetime_ms=settings.engine_movetime_ms,
        )
    )


def validate_engine_configuration(settings: Settings) -> str:
    """Validate that an engine binary is available.

    Returns the resolved path.

    Raises:
        EngineNotFoundError: when no Stockfish binary can be found.
    """
    path = locate_stockfish(settings.stockfish_path or None)
    if path is None:
        raise EngineNotFoundError(
            "Stockfish binary not found; set ARGUS_STOCKFISH_PATH or install Stockfish",
            details={"configured_path": settings.stockfish_path or None},
        )
    return path


def get_engine(settings: Settings) -> ChessEngine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = build_engine(settings)
        logger.info("Engine service initialized")
    return _engine


def reset_engine() -> None:
    """Dispose the shared engine (used by tests)."""
    global _engine
    if _engine is not None:
        _engine.close()
        _engine = None
