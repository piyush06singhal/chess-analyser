"""Phase 12 real-time analysis: the engine's move, and the in-game coach.

This module owns the two places an engine touches a live game, and both are
gated by the same fair-play decision the rest of the system uses:

* **the engine's own move** (§55–§58) — when a training or sandbox game has the
  engine in a seat, its move is generated here and applied through the ordinary
  validated pipeline. Its strength is controlled by *search depth*, which is a
  real, verifiable lever; Caissa does not pretend to set an Elo it cannot set.
* **the in-game coach** (§23, §26–§28) — after a human move in a game whose
  analysis mode permits it, the position is scored with the same Stockfish,
  the move is classified with the same Phase 3 policy, and the result is written
  to the event log as ``ANALYSIS_UPDATED`` / ``COACH_MESSAGE``.

Nothing here runs for a competitive game. ``state.analysis_permitted()`` is
checked before the engine is even acquired, so a competitive game can never cost
engine time — the fair-play boundary is also the resource boundary.

Analyses are stored as *events*, not in a table of their own: they are already
sequenced, already persisted, and already the thing a reconnecting client
replays. A second store would be a second thing to keep in step.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import chess

from argus.analysis.classification import (
    MoveClassification,
    MoveClassificationInput,
    classify_move,
)
from argus.analysis.engine.base import ChessEngine
from argus.live import AnalysisMode, GameMode, LiveGame, LiveGameError, LiveGameState
from argus.shared.errors import EngineUnavailableError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: The most analyses one live game may request. A game that asks for more is not
#: being coached, it is being farmed for engine time.
MAX_ANALYSES_PER_GAME = 60
#: Depth used when a game does not choose one — shallow on purpose: a live answer
#: arrives while the player is still at the board.
DEFAULT_LIVE_DEPTH = 12
#: A swing this large (centipawns) marks a critical moment even if the move was
#: not itself a mistake — the position changed character.
CRITICAL_SWING_CP = 200


class LiveAnalysisLimiter:
    """In-process engine budget for live games.

    The shared Stockfish process is a single resource, so concurrency here is
    deliberately stricter than for post-game analysis: one live search at a time,
    a per-game cap, and obsolete work cancelled the moment a newer version
    arrives. This is the same "thin, replaceable seam" the post-game runner uses;
    a real queue would replace it, not fan it out.
    """

    def __init__(self, *, max_per_game: int = MAX_ANALYSES_PER_GAME, max_concurrent: int = 1):
        self._lock = threading.Lock()
        self._max_per_game = max_per_game
        self._max_concurrent = max_concurrent
        self._in_flight = 0
        self._counts: dict[str, int] = {}
        self._latest: dict[str, int] = {}
        self._cancelled: set[tuple[str, int]] = set()
        self._rejected: dict[str, int] = {}

    def claim(self, game_id: str, version: int) -> tuple[bool, str | None]:
        """Reserve engine time for one analysis of ``game_id`` at ``version``.

        Returns ``(allowed, reason)``. A version older than the newest already
        claimed is obsolete and is refused *before* any engine work starts.
        """
        with self._lock:
            if version < self._latest.get(game_id, -1):
                return False, "obsolete"
            self._latest[game_id] = version
            if version in self._cancelled:
                self._cancelled.discard((game_id, version))
                return False, "cancelled"
            if self._counts.get(game_id, 0) >= self._max_per_game:
                self._rejected[game_id] = self._rejected.get(game_id, 0) + 1
                return False, "game_budget_exhausted"
            if self._in_flight >= self._max_concurrent:
                return False, "engine_busy"
            self._in_flight += 1
            self._counts[game_id] = self._counts.get(game_id, 0) + 1
            return True, None

    def release(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    def cancel(self, game_id: str, version: int) -> None:
        """Mark an in-flight analysis obsolete (§24)."""
        with self._lock:
            self._cancelled.add((game_id, version))

    def stats(self) -> dict:
        with self._lock:
            return {
                "in_flight": self._in_flight,
                "max_concurrent": self._max_concurrent,
                "analyses_by_game": dict(self._counts),
                "rejected_by_game": dict(self._rejected),
            }

    def reset(self) -> None:
        with self._lock:
            self._in_flight = 0
            self._counts.clear()
            self._latest.clear()
            self._cancelled.clear()
            self._rejected.clear()


#: Process-wide limiter (one per worker process), like the post-game registry.
LIMITER = LiveAnalysisLimiter()


def analysis_allowed(state: LiveGameState) -> bool:
    """Whether a live game may receive engine analysis at all (fair play §20).

    Diagnosis and training games only. A competitive game is refused here, before
    any caller can reach the engine.
    """
    return state.analysis_permitted() and state.mode in (GameMode.TRAINING, GameMode.SANDBOX)


def engine_move_uci(
    engine: ChessEngine, game: LiveGame, *, default_depth: int = DEFAULT_LIVE_DEPTH
) -> str:
    """Ask the engine for its own move in a game whose seat it holds (§55)."""
    if not game.state.is_engine_turn():
        raise LiveGameError(
            "seat_not_engine",
            f"The {game.state.side_to_move.value} seat is not played by the engine.",
        )
    board = game.board
    if board.is_game_over():
        raise LiveGameError("illegal_transition", "The game is already over.")
    depth = int(game.state.engine.get("depth") or default_depth)
    depth = max(1, min(depth, 30))
    if not engine.info().get("available"):
        raise EngineUnavailableError(
            "The engine opponent is unavailable because Stockfish is not reachable."
        )
    result = engine.analyze_position(game.state.current_fen, depth=depth, multipv=1)
    if result.is_terminal or not result.best_move_uci:
        raise LiveGameError("illegal_transition", "The engine found no move in this position.")
    return result.best_move_uci


def analyse_move(
    engine: ChessEngine,
    *,
    fen_before: str,
    played_uci: str,
    depth: int = DEFAULT_LIVE_DEPTH,
    multipv: int = 3,
) -> dict:
    """Score and classify one played move with the shared Phase 3 policy.

    The same engine call, the same comparison and the same classification policy
    as post-game analysis — so a live label and the review's label cannot
    disagree about the same move.
    """
    comparison = engine.compare_moves(fen_before, [played_uci], depth=depth)
    if not comparison:
        return {"available": False, "reason": "The engine returned no comparison."}
    result = comparison[0]
    board = chess.Board(fen_before)
    played_san = result.played_move_san
    if played_san is None:
        try:
            played_san = board.san(chess.Move.from_uci(played_uci))
        except ValueError:
            played_san = played_uci

    top = engine.analyze_position(fen_before, depth=depth, multipv=max(2, multipv))
    candidates = [
        {"uci": line.move_uci, "san": line.move_san, "cp": line.cp, "mate": line.mate}
        for line in top.lines
    ]
    second_gap = None
    if len(top.lines) >= 2 and top.lines[0].cp is not None and top.lines[1].cp is not None:
        second_gap = top.lines[0].cp - top.lines[1].cp

    classification = classify_move(
        MoveClassificationInput(
            centipawn_loss=result.centipawn_loss,
            is_best_move=result.is_best_move,
            second_best_gap=second_gap,
        )
    )
    return {
        "available": True,
        "fen": fen_before,
        "played_move_uci": played_uci,
        "played_move_san": played_san,
        "best_move_uci": result.best_move_uci,
        "best_move_san": result.best_move_san,
        "played_cp": result.played_cp,
        "best_cp": result.best_cp,
        "centipawn_loss": result.centipawn_loss,
        "is_best_move": result.is_best_move,
        "classification": classification.value if classification else None,
        "depth": result.depth,
        "candidates": candidates,
        "critical": _is_critical(classification, result.centipawn_loss),
        "engine": engine.info().get("engine", "stockfish"),
        "engine_version": engine.info().get("version"),
    }


def _is_critical(classification: MoveClassification | None, centipawn_loss: int | None) -> bool:
    """Whether the move is worth interrupting the player for (§27)."""
    if classification in (MoveClassification.MISTAKE, MoveClassification.BLUNDER):
        return True
    return centipawn_loss is not None and abs(centipawn_loss) >= CRITICAL_SWING_CP


def critical_event_kind(analysis: dict) -> str | None:
    """The §28 event name for an analysis, or ``None`` when nothing notable."""
    if not analysis.get("available") or not analysis.get("critical"):
        return None
    loss = analysis.get("centipawn_loss")
    if analysis.get("is_best_move"):
        return "strong_tactical_opportunity"
    if loss is not None and loss <= -CRITICAL_SWING_CP:
        return "strong_tactical_opportunity"
    if loss is not None and loss >= CRITICAL_SWING_CP:
        return "major_evaluation_swing"
    return "critical_position"


def coach_message(state: LiveGameState, analysis: dict, *, side: str) -> dict:
    """The coach's text for one analysed move, limited by the game's coach level.

    The level is the game's, not the request's — a competitive game reaches the
    hint branch no matter what the client asks for.
    """
    from argus.live import CoachLevel, SAFE_HINTS

    classification = analysis.get("classification")
    loss = analysis.get("centipawn_loss")
    level = state.coach_level
    if level is CoachLevel.OFF:
        return {
            "level": level.value,
            "kind": "coach_off",
            "message": "The coach is turned off for this game.",
            "engine_supported": False,
        }
    if not analysis.get("available"):
        return {
            "level": level.value,
            "kind": "unavailable",
            "message": "Caissa could not score that move.",
            "engine_supported": False,
        }
    if level is CoachLevel.HINTS:
        return {
            "level": level.value,
            "kind": "hint",
            "message": SAFE_HINTS[len(analysis["played_move_uci"]) % len(SAFE_HINTS)],
            "engine_supported": False,
        }
    if level is CoachLevel.CONCEPTUAL:
        # Board facts only: no engine number, no best move.
        return {
            "level": level.value,
            "kind": "concept",
            "message": _concept_message(analysis),
            "engine_supported": False,
        }
    # FULL_ANALYSIS — only reachable in training/sandbox games.
    detail = ""
    if analysis.get("best_move_san") and not analysis.get("is_best_move"):
        detail = f" The engine's choice was {analysis['best_move_san']}."
    if loss is not None:
        detail += f" ({loss:+d} centipawns versus the best move at depth {analysis['depth']}.)"
    label = (classification or "unscored").replace("_", " ")
    return {
        "level": level.value,
        "kind": "analysis",
        "message": f"{side.capitalize()}'s {analysis.get('played_move_san')} is {label}.{detail}",
        "engine_supported": True,
    }


def _concept_message(analysis: dict) -> str:
    """A board-grounded sentence that never quotes an engine number."""
    classification = analysis.get("classification")
    played = analysis.get("played_move_san") or analysis.get("played_move_uci")
    if classification in ("blunder", "mistake"):
        return (
            f"{played} looks to leave something loose. Before moving, check what "
            "your opponent's forcing replies attack."
        )
    if classification == "inaccurate":
        return (
            f"{played} is playable but concedes something. Look for a move that "
            "improves your worst-placed piece."
        )
    if classification == "best":
        return f"{played} keeps your position together — the engine agrees it is the move."
    return f"{played} is reasonable. Ask what your opponent threatens next."


def run_move_analysis(
    engine: ChessEngine,
    *,
    game: LiveGame,
    played_uci: str,
    fen_before: str,
    depth: int = DEFAULT_LIVE_DEPTH,
) -> dict:
    """Analyse a move under the limiter, releasing the claim in every case."""
    state = game.state
    if not analysis_allowed(state):
        return {"available": False, "reason": "analysis_not_permitted"}
    allowed, reason = LIMITER.claim(state.game_id, state.version)
    if not allowed:
        return {"available": False, "reason": reason}
    try:
        return analyse_move(engine, fen_before=fen_before, played_uci=played_uci, depth=depth)
    except EngineUnavailableError as exc:
        return {"available": False, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 — a live analysis may never break a game
        logger.warning("Live analysis failed [game=%s ply=%s]: %s", state.game_id, played_uci, exc)
        return {"available": False, "reason": "analysis_failed"}
    finally:
        LIMITER.release()


def event_payloads(
    *, game: LiveGame, analysis: dict, played_uci: str, ply: int, side: str
) -> list[tuple[str, dict]]:
    """The events a permitted analysis produces: analysis, then coach, then moment."""
    payloads: list[tuple[str, dict]] = [
        (
            "ANALYSIS_UPDATED",
            {
                "ply": ply,
                "played_move_uci": played_uci,
                "analysis": analysis,
                "analysed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    ]
    message = coach_message(game.state, analysis, side=side)
    payloads.append(
        (
            "COACH_MESSAGE",
            {"ply": ply, "side": side, "coach": message, "analysis_available": bool(analysis.get("available"))},
        )
    )
    kind = critical_event_kind(analysis)
    if kind:
        payloads.append(
            (
                "ANALYSIS_UPDATED",
                {
                    "ply": ply,
                    "critical_moment": {
                        "kind": kind,
                        "classification": analysis.get("classification"),
                        "centipawn_loss": analysis.get("centipawn_loss"),
                        "played_move_san": analysis.get("played_move_san"),
                        "best_move_san": analysis.get("best_move_san")
                        if game.state.coach_level.value == "full_analysis"
                        else None,
                    },
                },
            )
        )
    return payloads


def supports_engine_opponent(state: LiveGameState) -> bool:
    """Whether this game may have the engine in a seat at all."""
    return state.mode in (GameMode.TRAINING, GameMode.SANDBOX)


def analysis_modes_for(mode: str) -> list[str]:
    """The analysis modes a game mode may choose (the UI's honest menu)."""
    if mode in (GameMode.LOCAL.value, GameMode.PRIVATE_MATCH.value):
        return [AnalysisMode.NO_ANALYSIS.value]
    if mode == GameMode.SANDBOX.value:
        return [AnalysisMode.NO_ANALYSIS.value, AnalysisMode.SANDBOX_ANALYSIS.value]
    return [
        AnalysisMode.NO_ANALYSIS.value,
        AnalysisMode.POST_MOVE_ANALYSIS.value,
        AnalysisMode.TRAINING_ANALYSIS.value,
    ]


__all__ = [
    "CRITICAL_SWING_CP",
    "DEFAULT_LIVE_DEPTH",
    "LIMITER",
    "MAX_ANALYSES_PER_GAME",
    "LiveAnalysisLimiter",
    "analysis_allowed",
    "analysis_modes_for",
    "analyse_move",
    "coach_message",
    "critical_event_kind",
    "engine_move_uci",
    "event_payloads",
    "run_move_analysis",
    "supports_engine_opponent",
]
