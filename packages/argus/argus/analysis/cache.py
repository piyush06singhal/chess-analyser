"""Position-analysis caching.

Analyzing the same position twice with the same engine configuration is wasted
work. The cache key therefore includes everything that materially changes the
result:

    FEN + engine version + search limit (depth or movetime) + MultiPV

If any of those differ, the cached entry is **not** returned — a stale result
from a different depth or engine version would be misleading.

The default store is a bounded in-memory LRU with a lock. It is deliberately
swappable (Redis later) behind the same interface.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

from argus.analysis.engine.base import AnalyzedPosition, ChessEngine


@dataclass(frozen=True)
class CacheKey:
    """Everything that materially affects a position analysis result."""

    fen: str
    engine_version: str | None
    depth: int | None
    movetime_ms: int | None
    multipv: int

    @classmethod
    def build(
        cls,
        fen: str,
        *,
        engine_version: str | None,
        depth: int | None,
        movetime_ms: int | None,
        multipv: int,
    ) -> "CacheKey":
        return cls(
            fen=fen,
            engine_version=engine_version,
            depth=depth,
            movetime_ms=movetime_ms,
            multipv=multipv,
        )


class PositionAnalysisCache:
    """Bounded LRU cache of analyzed positions, keyed by :class:`CacheKey`."""

    def __init__(self, max_entries: int = 2048) -> None:
        self._max_entries = max_entries
        self._store: OrderedDict[CacheKey, AnalyzedPosition] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: CacheKey) -> AnalyzedPosition | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return entry

    def put(self, key: CacheKey, value: AnalyzedPosition) -> None:
        with self._lock:
            self._store[key] = value
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._store),
                "max_entries": self._max_entries,
                "hits": self.hits,
                "misses": self.misses,
            }


class CachingEngine(ChessEngine):
    """Decorates a :class:`ChessEngine` with position-analysis caching."""

    def __init__(self, engine: ChessEngine, cache: PositionAnalysisCache | None = None) -> None:
        self._engine = engine
        self._cache = cache or PositionAnalysisCache()

    def info(self) -> dict:
        return self._engine.info()

    def analyze_position(
        self,
        fen: str,
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
    ) -> AnalyzedPosition:
        effective_depth = depth
        effective_time = movetime_ms
        effective_multipv = multipv or 1
        key = CacheKey.build(
            fen,
            engine_version=self._engine.info().get("version"),
            depth=effective_depth if not effective_time else None,
            movetime_ms=effective_time,
            multipv=effective_multipv,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._engine.analyze_position(
            fen, depth=depth, multipv=multipv, movetime_ms=movetime_ms
        )
        # Store with the MultiPV count actually used, so a later multi-line
        # request is not served a single-line result.
        key = CacheKey.build(
            fen,
            engine_version=result.engine_version,
            depth=result.depth if not effective_time else None,
            movetime_ms=effective_time,
            multipv=result.multipv,
        )
        self._cache.put(key, result)
        return result

    def compare_moves(self, fen, moves, *, depth=None, movetime_ms=None):  # noqa: ANN001
        return self._engine.compare_moves(fen, moves, depth=depth, movetime_ms=movetime_ms)

    def close(self) -> None:
        self._engine.close()
