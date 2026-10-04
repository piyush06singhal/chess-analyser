"""Engine resource gate: bounded concurrency and a bounded queue (§8).

Stockfish is one process shared by the whole API process, so unbounded analysis
requests are a denial-of-service against Caissa itself: a caller can queue more
work than the machine can ever finish and starve everyone else. This module makes
the bound explicit and enforced at the request boundary:

* **A concurrency limit** (``ARGUS_ENGINE_MAX_CONCURRENCY``) — how many analyses
  may hold an engine slot at once. The engine's own lock still serialises the
  actual searches, so this is a *work-admission* bound rather than a claim of
  parallel search; that is stated rather than implied.
* **A queue limit** (``ARGUS_ANALYSIS_QUEUE_LIMIT``) — how many further analyses
  may wait for a slot before new requests are refused with a real 503 and a
  ``Retry-After``.
* **Idempotency** — a game that already has an admitted run cannot be admitted
  again; the second request is a 409, so re-clicking "analyze" cannot spawn a
  duplicate run over the same rows.

The gate holds no persistent state and is rebuilt from configuration on first
use, so it is testable in isolation and cannot drift from settings.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

from argus.shared.errors import ConflictError, ServiceBusyError


@dataclass
class _GateState:
    max_concurrency: int = 2
    queue_limit: int = 64
    lock: threading.Lock = field(default_factory=threading.Lock)
    semaphore: threading.BoundedSemaphore | None = None
    #: game_id -> True when it currently holds a slot, False when queued.
    admitted: dict[str, bool] = field(default_factory=dict)
    running: int = 0


class EngineGate:
    """Admission control for engine-using analyses (process-wide)."""

    def __init__(self) -> None:
        self._state = _GateState()

    def _configure(self, settings) -> _GateState:  # noqa: ANN001
        """Return the gate state, (re)building the semaphore when limits change.

        Tests swap settings between cases; rebuilding on a limit change keeps the
        gate consistent with configuration instead of silently honouring the
        first values it ever saw. The semaphore is only rebuilt when nothing is
        in flight, so a mid-run reload cannot corrupt the accounting.
        """
        concurrency = max(1, int(getattr(settings, "engine_max_concurrency", 2) or 1))
        queue_limit = max(0, int(getattr(settings, "analysis_queue_limit", 64) or 0))
        state = self._state
        with state.lock:
            changed = (
                state.semaphore is None
                or state.max_concurrency != concurrency
                or state.queue_limit != queue_limit
            )
            if changed and not state.admitted:
                state.max_concurrency = concurrency
                state.queue_limit = queue_limit
                state.semaphore = threading.BoundedSemaphore(concurrency)
        return state

    def admit(self, game_id: str, settings) -> None:  # noqa: ANN001
        """Reserve a place for ``game_id`` (running or queued).

        Raises:
            ConflictError: when this game already has an admitted run.
            ServiceBusyError: when the queue is full.
        """
        state = self._configure(settings)
        with state.lock:
            if game_id in state.admitted:
                raise ConflictError(
                    "This game already has an analysis run in progress.",
                    details={"game_id": game_id},
                )
            capacity = state.max_concurrency + state.queue_limit
            if len(state.admitted) >= capacity:
                raise ServiceBusyError(
                    "The analysis queue is full; try again shortly.",
                    details={
                        "max_concurrency": state.max_concurrency,
                        "queue_limit": state.queue_limit,
                        "in_flight": len(state.admitted),
                    },
                )
            state.admitted[game_id] = False  # queued until slot() acquires

    @contextmanager
    def slot(self, game_id: str, settings):  # noqa: ANN001
        """Hold an engine slot for the duration of a run, then release it.

        The caller must have called :meth:`admit` first. The semaphore enforces
        the concurrency bound; the admitted table enforces idempotency and the
        queue bound.
        """
        state = self._configure(settings)
        assert state.semaphore is not None  # guaranteed by _configure
        state.semaphore.acquire()
        with state.lock:
            state.admitted.setdefault(game_id, False)
            state.admitted[game_id] = True
            state.running += 1
        try:
            yield
        finally:
            with state.lock:
                state.running -= 1
                if game_id in state.admitted:
                    state.admitted[game_id] = False
            state.semaphore.release()

    def release(self, game_id: str) -> None:
        """Forget an admitted run (called in the runner's ``finally``)."""
        state = self._state
        with state.lock:
            state.admitted.pop(game_id, None)

    def snapshot(self) -> dict:
        """A read-only view of the gate's real state (for ``/ready`` and tests)."""
        state = self._state
        with state.lock:
            return {
                "max_concurrency": state.max_concurrency,
                "queue_limit": state.queue_limit,
                "running": state.running,
                "queued": len(state.admitted) - state.running,
                "in_flight": len(state.admitted),
            }

    def reset(self) -> None:
        """Clear all state (tests only)."""
        state = self._state
        with state.lock:
            state.admitted.clear()
            state.running = 0
            state.semaphore = None


#: Process-wide gate, matching the process-wide engine and job registry.
GATE = EngineGate()


__all__ = ["GATE", "EngineGate"]
