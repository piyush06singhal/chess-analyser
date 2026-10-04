"""Chess engine abstraction: interface + result models.

Engine-agnostic: implementations talk UCI (Stockfish today) but consumers only
see the ``ChessEngine`` base class and typed results. Engine code never
contains LLM logic and never touches databases or UI.

Mate scores are converted to centipawn equivalents (a documented convention
for comparability, not an evaluation claim) so mate and cp scores can be
compared mechanically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

import chess
from pydantic import BaseModel, Field

from argus.chess_core.models import Color, Game
from argus.shared.errors import AnalysisCancelledError, InvalidMoveError

MATE_SCORE_CEILING = 10_000


class EngineLine(BaseModel):
    """One principal-variation line returned by the engine (a MultiPV entry)."""

    index: int = Field(description="1-based MultiPV rank")
    depth: int
    move_uci: str = Field(description="First move of the line (UCI)")
    move_san: str | None = Field(default=None, description="First move of the line (SAN)")
    cp: int | None = Field(default=None, description="Centipawn score, side-to-move perspective")
    mate: int | None = Field(
        default=None,
        description="Mate distance; positive = side to move mates, negative = gets mated",
    )
    pv: list[str] = Field(default_factory=list, description="Principal variation as UCI strings")
    nodes: int | None = Field(default=None, description="Nodes searched for this line, when reported")
    nps: int | None = Field(default=None, description="Nodes per second, when reported")


class AnalyzedPosition(BaseModel):
    """Structured engine response for a single position."""

    fen: str
    depth: int
    multipv: int
    best_move_uci: str | None = None
    best_move_san: str | None = None
    lines: list[EngineLine] = Field(default_factory=list)
    is_terminal: bool = Field(
        default=False,
        description="True when the position has no legal moves (checkmate/stalemate); "
        "no search is performed",
    )
    terminal_reason: str | None = Field(
        default=None, description="'checkmate' or 'stalemate' when is_terminal is True"
    )
    nodes: int | None = Field(default=None, description="Nodes searched, when reported")
    nps: int | None = Field(default=None, description="Nodes per second, when reported")
    engine: str = "stockfish"
    engine_version: str | None = None


#: The played move's score came from the *same* search that produced the best
#: line, so best-vs-played is a like-for-like comparison with no search noise.
PLAYED_EVAL_SAME_SEARCH = "same_search"
#: The played move's score came from a separate search of the position after it.
#: Honest, but it mixes two searches, so a small difference is not a real loss.
PLAYED_EVAL_RESULTING_POSITION = "resulting_position"
#: No score for the played move at all.
PLAYED_EVAL_UNAVAILABLE = "unavailable"


class MoveComparison(BaseModel):
    """Comparison of a played move against the engine's best in the position."""

    fen: str
    played_move_uci: str
    played_move_san: str | None = None
    best_move_uci: str | None = None
    best_move_san: str | None = None
    played_cp: int | None = Field(default=None, description="Played move eval (mover perspective)")
    best_cp: int | None = Field(default=None, description="Best move eval (mover perspective)")
    played_eval_source: str = Field(
        default=PLAYED_EVAL_UNAVAILABLE,
        description=(
            "Where the played move's score came from: 'same_search' (exact, the same "
            "search that produced the best line) or 'resulting_position' (a separate "
            "search of the position after the move)."
        ),
    )
    centipawn_loss: int | None = None
    is_best_move: bool = False
    depth: int


class CandidateMove(BaseModel):
    """One MultiPV line's root move and its evaluation, mover perspective.

    Kept in a deliberately flat shape (``uci``/``cp``/``mate``) because it travels
    through the storage layer into products that reason over alternative moves —
    notably the training engine's acceptable-move set, where a move within the
    tolerance of the best must not be marked wrong for not being the engine's
    first choice.
    """

    rank: int = Field(description="1-based MultiPV rank in the search that produced it")
    uci: str
    san: str | None = None
    cp: int | None = Field(default=None, description="Score, mover perspective")
    mate: int | None = None
    pv: list[str] = Field(default_factory=list)


class GameMoveEvaluation(BaseModel):
    """Raw engine evaluation of one played move within a game.

    Produced by ``ChessEngine.analyze_game``. Enrichment (features, phase,
    classification) belongs to the GameAnalyzer, not the engine.
    """

    ply: int
    move_number: int
    color: Color
    san: str
    uci: str
    fen_before: str
    fen_after: str
    evaluation_before_cp: int | None = Field(
        default=None, description="Best-line eval before the move (mover perspective)"
    )
    evaluation_before_mate: int | None = None
    evaluation_after_cp: int | None = Field(
        default=None,
        description=(
            "Eval of the position after the played move (mover perspective), from a "
            "separate search of that position"
        ),
    )
    evaluation_after_mate: int | None = None
    evaluation_change_cp: int | None = None
    centipawn_loss: int | None = None
    #: Score of the move actually played, from the same search that produced the
    #: best line whenever the move was inside the MultiPV window. This is the
    #: only value that makes "best vs played" a like-for-like comparison, and it
    #: is what accuracy must be computed from — ``evaluation_after_cp`` mixes two
    #: different searches and therefore carries search noise both ways.
    played_eval_cp: int | None = Field(
        default=None, description="Played move's own eval, mover perspective"
    )
    played_eval_mate: int | None = None
    played_eval_source: str = Field(
        default=PLAYED_EVAL_UNAVAILABLE,
        description="PLAYED_EVAL_SAME_SEARCH | PLAYED_EVAL_RESULTING_POSITION | PLAYED_EVAL_UNAVAILABLE",
    )
    best_move_uci: str | None = None
    best_move_san: str | None = None
    is_best_move: bool = False
    second_best_cp: int | None = Field(
        default=None,
        description="Second-best line eval (mover perspective); None when MultiPV < 2",
    )
    #: The MultiPV lines themselves, so a downstream consumer can reason about
    #: *which* alternatives were near-best rather than only how near the second
    #: one was. Empty when MultiPV was 1.
    candidate_moves: list[CandidateMove] = Field(default_factory=list)
    depth: int
    principal_variation: list[str] = Field(default_factory=list)


def to_cp(cp: int | None, mate: int | None) -> int | None:
    """Convert an engine score to a centipawn-equivalent value.

    Mate scores map onto the ``MATE_SCORE_CEILING`` scale so they can be
    compared with centipawn scores. Returns ``None`` when no score exists.
    """
    if cp is not None:
        return cp
    if mate is not None:
        sign = 1 if mate > 0 else -1
        return sign * (MATE_SCORE_CEILING - abs(mate))
    return None


def flip_score(cp: int | None, mate: int | None) -> tuple[int | None, int | None]:
    """Flip a score to the opposite player's perspective."""
    return (
        None if cp is None else -cp,
        None if mate is None else -mate,
    )


def calculate_centipawn_loss(
    best_cp: int | None,
    best_mate: int | None,
    played_cp: int | None,
    played_mate: int | None,
) -> int | None:
    """Centipawn loss of a played move versus the best line, from the mover's perspective.

    Mathematical convention (documented, tested):

    1. Both scores are first normalized to the same player's perspective (the
       mover). Callers pass engine scores already expressed for the mover —
       ``flip_score`` is the tool for converting them.
    2. Mate scores are mapped onto the ``MATE_SCORE_CEILING`` scale via
       :func:`to_cp`, so a mate and a centipawn evaluation are comparable and
       a forced mate is never treated as an ordinary centipawn number.
    3. A move that itself delivers mate has zero centipawn loss (``0``).
    4. Otherwise ``loss = max(0, best_value - played_value)``. The clamp at
       zero keeps CPL non-negative; a positive value would otherwise mean the
       played move is *better* than the reported best line, which only happens
       from search noise, never from a genuinely better move.

    Returns ``None`` when either evaluation is unavailable — an unknown CPL is
    reported as unknown, never guessed as ``0``.
    """
    if played_mate is not None and played_mate > 0:
        return 0
    best_value = to_cp(best_cp, best_mate)
    played_value = to_cp(played_cp, played_mate)
    if best_value is None or played_value is None:
        return None
    return max(0, best_value - played_value)


# Backwards-compatible alias (Phase 1 name).
compute_cp_loss = calculate_centipawn_loss


class ChessEngine(ABC):
    """Abstract chess engine.

    Subclasses implement the low-level engine primitives (UCI communication
    for Stockfish); ``analyze_game`` is a shared template method built on
    ``analyze_position`` so game-level logic is not duplicated.
    """

    @abstractmethod
    def info(self) -> dict:
        """Return engine availability metadata (available, path, version)."""

    @abstractmethod
    def analyze_position(
        self,
        fen: str,
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
    ) -> AnalyzedPosition:
        """Analyze a single position and return a structured response.

        Exactly one search limit governs the search: ``movetime_ms`` when set
        (time-based), otherwise ``depth`` (depth-based).
        """

    @abstractmethod
    def compare_moves(
        self,
        fen: str,
        moves: list[str],
        *,
        depth: int | None = None,
        movetime_ms: int | None = None,
    ) -> list[MoveComparison]:
        """Compare candidate moves in the same position against the best."""

    @abstractmethod
    def close(self) -> None:
        """Release engine resources."""

    # --- named operations (thin, domain-level wrappers) -------------------------

    def analyze_position_multipv(
        self,
        fen: str,
        *,
        multipv: int = 3,
        depth: int | None = None,
        movetime_ms: int | None = None,
    ) -> AnalyzedPosition:
        """MultiPV analysis: the top ``multipv`` lines for one position."""
        return self.analyze_position(
            fen, depth=depth, multipv=max(1, multipv), movetime_ms=movetime_ms
        )

    def analyze_move(
        self,
        fen: str,
        move_uci: str,
        *,
        depth: int | None = None,
        movetime_ms: int | None = None,
    ) -> MoveComparison:
        """Evaluate one candidate move against the engine's best in ``fen``."""
        comparisons = self.compare_moves(
            fen, [move_uci], depth=depth, movetime_ms=movetime_ms
        )
        return comparisons[0]

    def analyze_game(
        self,
        game: Game,
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_move: Callable[[GameMoveEvaluation], None] | None = None,
        start_ply: int | None = None,
        end_ply: int | None = None,
    ) -> list[GameMoveEvaluation]:
        """Evaluate played moves of a game (main line).

        For each move: analyzes the position before the move (MultiPV) and the
        position after (to obtain the played move's eval when it is not among
        the MultiPV lines). All scores are reported from the mover's
        perspective.

        ``start_ply``/``end_ply`` (1-based, inclusive) bound the range, which
        lets a caller resume a partially completed analysis without redoing
        finished plies. ``on_move`` is invoked for each completed evaluation so
        a caller can persist incrementally instead of holding everything in
        memory.

        Raises:
            InvalidMoveError: when a move is illegal in its position.
            AnalysisCancelledError: when ``should_cancel`` returns True.
            EngineError subclasses: on engine unavailability/timeout/crash.
        """
        first_index = max(0, (start_ply or 1) - 1)
        last_index = len(game.moves) if end_ply is None else min(end_ply, len(game.moves))
        if first_index > 0:
            # Start from the position before the first move we still need, so a
            # resumed run does not replay the whole game.
            board = chess.Board(game.moves[first_index].fen_before)
        else:
            board = chess.Board(game.initial_position)

        evaluations: list[GameMoveEvaluation] = []
        total = max(0, last_index - first_index)
        done = 0
        for index in range(first_index, last_index):
            move = game.moves[index]
            if should_cancel is not None and should_cancel():
                raise AnalysisCancelledError(
                    "Game analysis was cancelled",
                    details={"completed_plies": index},
                )
            played = chess.Move.from_uci(move.uci)
            if played not in board.legal_moves:
                raise InvalidMoveError(
                    f"Illegal move '{move.san}' ({move.uci}) at ply {move.ply}",
                    details={"fen": move.fen_before, "move": move.uci},
                )
            before = self.analyze_position(
                move.fen_before, depth=depth, multipv=multipv, movetime_ms=movetime_ms
            )
            board.push(played)
            after = self.analyze_position(
                board.fen(), depth=depth, multipv=1, movetime_ms=movetime_ms
            )

            best_line = before.lines[0] if before.lines else None
            after_line = after.lines[0] if after.lines else None

            played_line = next(
                (line for line in before.lines if line.move_uci == move.uci), None
            )
            if played_line is not None:
                # Exact: the played move was inside the MultiPV window, so its
                # score and the best score come from one and the same search.
                played_cp, played_mate = played_line.cp, played_line.mate
                played_source = PLAYED_EVAL_SAME_SEARCH
            elif after_line is not None:
                # Approximate: score of the position after the move, flipped to
                # the mover. Recorded as such so nothing downstream treats it as
                # the exact value.
                played_cp, played_mate = flip_score(after_line.cp, after_line.mate)
                played_source = PLAYED_EVAL_RESULTING_POSITION
            else:
                played_cp, played_mate = None, None
                played_source = PLAYED_EVAL_UNAVAILABLE

            eval_after_cp, eval_after_mate = (
                flip_score(after_line.cp, after_line.mate) if after_line else (None, None)
            )
            best_cp = best_line.cp if best_line else None
            best_mate = best_line.mate if best_line else None

            evaluations.append(
                GameMoveEvaluation(
                    ply=move.ply,
                    move_number=move.move_number,
                    color=move.color,
                    san=move.san,
                    uci=move.uci,
                    fen_before=move.fen_before,
                    fen_after=move.fen_after,
                    evaluation_before_cp=best_cp,
                    evaluation_before_mate=best_mate,
                    evaluation_after_cp=eval_after_cp,
                    evaluation_after_mate=eval_after_mate,
                    evaluation_change_cp=(
                        None
                        if eval_after_cp is None or best_cp is None
                        else eval_after_cp - best_cp
                    ),
                    played_eval_cp=played_cp,
                    played_eval_mate=played_mate,
                    played_eval_source=played_source,
                    centipawn_loss=compute_cp_loss(best_cp, best_mate, played_cp, played_mate),
                    best_move_uci=before.best_move_uci,
                    best_move_san=before.best_move_san,
                    is_best_move=bool(before.best_move_uci and move.uci == before.best_move_uci),
                    second_best_cp=(before.lines[1].cp if len(before.lines) > 1 else None),
                    candidate_moves=[
                        CandidateMove(
                            rank=index + 1,
                            uci=line.move_uci,
                            san=line.move_san,
                            cp=line.cp,
                            mate=line.mate,
                            pv=list(line.pv),
                        )
                        for index, line in enumerate(before.lines)
                    ],
                    depth=max(before.depth, after.depth),
                    principal_variation=best_line.pv if best_line else [],
                )
            )
            done += 1
            if on_move is not None:
                on_move(evaluations[-1])
            if on_progress is not None:
                on_progress(done, total)
        return evaluations
