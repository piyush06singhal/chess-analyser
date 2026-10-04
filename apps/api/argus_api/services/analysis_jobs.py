"""Background game-analysis engine.

Runs the Phase 3 pipeline for one game with everything the spec requires:

* **progress** — ``AnalysisSession.current_position``/``total_positions``.
* **incremental persistence** — each move is committed as it finishes, so an
  interrupted run keeps its completed plies.
* **resume** — already-analyzed plies (same analysis version) are skipped.
* **cancellation** — a cooperative token stops the engine cleanly and marks the
  run ``cancelled``; the game returns to ``paused`` so it can be resumed.
* **versioning** — the engine configuration (version, depth/time, MultiPV,
  policy) is recorded for auditability.

A single process-wide lock serializes access to the shared Stockfish process;
the structure is deliberately a thin, replaceable seam so a real queue can give
each worker its own engine later.
"""

from __future__ import annotations

import hashlib
import threading
import time

from argus_api.services.resource_gate import GATE

from argus.analysis.critical_positions import detect_critical_positions
from argus.analysis.engine.base import ChessEngine
from argus.analysis.pipeline import ANALYSIS_VERSION, AnalysisConfig, GameAnalysisPipeline
from argus.analysis.classification import MoveClassification
from argus.chess_core.models import AnalysisStatus, Color
from argus.shared.errors import AnalysisCancelledError, ArgusError
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    analyzed_plies,
    create_analysis_session,
    get_game,
    get_moves,
    get_move_analyses,
    mark_analysis_status,
    record_engine_configuration,
    replace_critical_positions,
    save_move_analysis,
    update_analysis_session,
)

logger = get_logger(__name__)

# Engine calls used to be serialised by a single process-wide lock. Phase 15 §8
# replaces that with the bounded concurrency + queue gate in
# ``services.resource_gate``: callers are admitted or refused with a real reason
# (409 / 503), and a slot is held only for the duration of a run.


class CancellationRegistry:
    """Tracks in-flight analyses so a user can cancel one by game id."""

    def __init__(self) -> None:
        self._cancelled: set[str] = set()
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def start(self, game_id: str) -> None:
        with self._lock:
            self._active.add(game_id)
            self._cancelled.discard(game_id)

    def finish(self, game_id: str) -> None:
        with self._lock:
            self._active.discard(game_id)
            self._cancelled.discard(game_id)

    def cancel(self, game_id: str) -> bool:
        with self._lock:
            if game_id in self._active:
                self._cancelled.add(game_id)
                return True
            return False

    def is_cancelled(self, game_id: str) -> bool:
        with self._lock:
            return game_id in self._cancelled

    def is_active(self, game_id: str) -> bool:
        with self._lock:
            return game_id in self._active


#: Process-wide registry (one per worker process).
REGISTRY = CancellationRegistry()


class _MoveFacts:
    """Light adapter exposing stored MoveAnalysis rows to critical detection."""

    __slots__ = (
        "ply",
        "move_number",
        "color",
        "san",
        "fen_before",
        "fen_after",
        "evaluation_before_cp",
        "evaluation_before_mate",
        "evaluation_after_cp",
        "evaluation_after_mate",
        "evaluation_change_cp",
        "classification",
    )

    def __init__(self, row) -> None:  # noqa: ANN001 — ORM row
        self.ply = row.ply
        self.move_number = row.move_number
        self.color = Color(row.mover)
        self.san = row.played_move_san
        self.fen_before = row.fen_before
        self.fen_after = row.fen_after
        self.evaluation_before_cp = row.evaluation_before_cp
        self.evaluation_before_mate = row.evaluation_before_mate
        self.evaluation_after_cp = row.evaluation_after_cp
        self.evaluation_after_mate = row.evaluation_after_mate
        self.evaluation_change_cp = row.evaluation_change_cp
        self.classification = (
            MoveClassification(row.classification) if row.classification else None
        )


def _config_hash(config: AnalysisConfig, engine_version: str | None) -> str:
    payload = f"{config.describe()}|engine={engine_version}"
    return hashlib.sha256(payload.encode()).hexdigest()


class AnalysisJobRunner:
    """Runs one game analysis with persisted lifecycle state."""

    def __init__(self, session_factory, engine: ChessEngine, settings) -> None:  # noqa: ANN001
        self._session_factory = session_factory
        self._engine = engine
        self._settings = settings

    # --- public API -----------------------------------------------------------

    def run(self, game_id: str, *, config: AnalysisConfig, resume: bool = True) -> None:
        """Analyze a game, updating status around the engine run.

        Designed for FastAPI background tasks: it owns its database sessions and
        never raises into the response path.
        """
        REGISTRY.start(game_id)
        session_id: int | None = None
        started = time.perf_counter()
        try:
            with self._session_factory.session_scope() as session:
                mark_analysis_status(session, game_id, AnalysisStatus.ANALYZING.value, depth=config.depth)

            with self._session_factory.session_scope() as session:
                orm_game = get_game(session, game_id)
                moves = get_moves(session, game_id)
                game = self._chess_game(orm_game, moves)
                engine_info = self._engine.info()
                engine_version = engine_info.get("version")

                # One generation for the whole run: the rows, the resume check and
                # the session record all use the config's version, so a caller that
                # overrides it cannot leave the stored rows and the recorded
                # methodology disagreeing.
                generation = config.analysis_version or ANALYSIS_VERSION
                done_plies = (
                    analyzed_plies(session, game_id, generation) if resume else set()
                )
                start_ply = None
                if done_plies:
                    start_ply = next(
                        (p for p in range(1, len(moves) + 1) if p not in done_plies), None
                    )
                # Nothing left to search (every ply is already stored at this
                # analysis version) is not the same as "nothing left to do":
                # the derived data and the run record are rebuilt either way, so
                # a re-analysis can never leave the game half-updated while
                # reporting success.
                engine_required = not done_plies or start_ply is not None

                record_engine_configuration(
                    session,
                    config_hash=_config_hash(config, engine_version),
                    engine=engine_info.get("engine", "stockfish"),
                    engine_version=engine_version,
                    analysis_version=config.analysis_version,
                    profile=config.profile.value,
                    depth=config.depth,
                    movetime_ms=config.movetime_ms,
                    multipv=config.multipv,
                    threads=self._settings.engine_threads,
                    hash_mb=self._settings.engine_hash_mb,
                    policy=config.classification_policy,
                )
                run = create_analysis_session(
                    session,
                    game_id,
                    engine=engine_info.get("engine", "stockfish"),
                    engine_version=engine_version,
                    depth=config.depth,
                    multipv=config.multipv,
                    movetime_ms=config.movetime_ms,
                    profile=config.profile.value,
                    analysis_version=config.analysis_version,
                    engine_config=config.describe(),
                    policy=config.classification_policy,
                    total_positions=len(moves),
                )
                session_id = run.id

                pipeline = GameAnalysisPipeline(self._engine)

                def on_move(enriched) -> None:  # noqa: ANN001
                    with self._session_factory.session_scope() as inner:
                        save_move_analysis(
                            inner,
                            game_id,
                            enriched,
                            analysis_version=generation,
                            engine=engine_info.get("engine", "stockfish"),
                            engine_version=engine_version,
                        )

                def on_progress(completed: int, total: int) -> None:
                    with self._session_factory.session_scope() as inner:
                        update_analysis_session(
                            inner,
                            run.id,
                            current_position=len(done_plies) + completed,
                            positions_analyzed=len(done_plies) + completed,
                        )

                if engine_required:
                    # Hold a bounded engine slot for the run. The gate is what
                    # makes a queue of analyses a real, finite thing instead of
                    # an unbounded pile of background tasks.
                    with GATE.slot(game_id, self._settings):
                        pipeline.analyze(
                            game,
                            config,
                            on_move=on_move,
                            on_progress=on_progress,
                            should_cancel=lambda: REGISTRY.is_cancelled(game_id),
                            start_ply=start_ply,
                        )

                # Critical positions are derived from the full stored set, so a
                # resumed run still produces complete results.
                stored = get_move_analyses(session, game_id)
                candidates = detect_critical_positions(
                    [_MoveFacts(row) for row in stored], game_id=game_id
                )
                replace_critical_positions(
                    session, game_id, candidates, analysis_version=generation
                )

                duration = time.perf_counter() - started
                # The engine version is only known once Stockfish has started,
                # so it is recorded after the run (still before completion).
                final_version = self._engine.info().get("version")
                update_analysis_session(
                    session,
                    run.id,
                    status="completed",
                    positions_analyzed=len(stored),
                    current_position=len(moves),
                    duration_seconds=duration,
                    engine_version=final_version,
                )
                mark_analysis_status(session, game_id, AnalysisStatus.ANALYZED.value, depth=config.depth)

            logger.info(
                "Analysis completed for %s [moves=%d critical=%d duration=%.1fs]",
                game_id,
                len(stored),
                len(candidates),
                duration,
            )
        except AnalysisCancelledError:
            logger.info("Analysis cancelled for %s", game_id)
            self._finish(game_id, session_id, status="cancelled", game_status=AnalysisStatus.READY.value)
        except ArgusError as exc:
            logger.warning("Analysis failed for %s: %s", game_id, exc.message)
            self._finish(game_id, session_id, status="failed", game_status=AnalysisStatus.FAILED.value, error=exc.message)
        except Exception as exc:  # noqa: BLE001 — never let a background task vanish
            logger.exception("Unexpected analysis error for %s", game_id)
            self._finish(game_id, session_id, status="failed", game_status=AnalysisStatus.FAILED.value, error=str(exc))
        finally:
            GATE.release(game_id)
            REGISTRY.finish(game_id)

    # --- helpers --------------------------------------------------------------

    def _finish(
        self,
        game_id: str,
        session_id: int | None,
        *,
        status: str,
        game_status: str,
        error: str | None = None,
    ) -> None:
        try:
            with self._session_factory.session_scope() as session:
                if session_id is not None:
                    update_analysis_session(session, session_id, status=status, error=error)
                mark_analysis_status(session, game_id, game_status, error=error)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to record analysis outcome for %s", game_id)

    @staticmethod
    def _chess_game(orm_game, moves):  # noqa: ANN001
        from argus_api.routes.games import _chess_game_from_orm

        return _chess_game_from_orm(orm_game, moves)
