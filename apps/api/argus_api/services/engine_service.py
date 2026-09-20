"""Engine service: process-wide Stockfish lifecycle for the API.

The engine is created lazily from configuration and shared across requests.
All blocking engine calls are executed via ``asyncio.to_thread`` in the route
handlers (the engine itself is thread-safe behind a lock). Engine failures
surface as :class:`EngineError` subclasses and are mapped centrally to HTTP
responses.
"""

from __future__ import annotations

from argus.analysis.engine.base import ChessEngine
from argus.analysis.engine.stockfish import StockfishEngine, StockfishSettings
from argus_api.config import Settings
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
        )
    )


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
