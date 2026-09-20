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

import chess
from pydantic import BaseModel, Field

from argus.chess_core.models import Color, Game
from argus.shared.errors import InvalidMoveError

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
    engine: str = "stockfish"
    engine_version: str | None = None


class MoveComparison(BaseModel):
    """Comparison of a played move against the engine's best in the position."""

    fen: str
    played_move_uci: str
    played_move_san: str | None = None
    best_move_uci: str | None = None
    best_move_san: str | None = None
    played_cp: int | None = Field(default=None, description="Played move eval (mover perspective)")
    best_cp: int | None = Field(default=None, description="Best move eval (mover perspective)")
    centipawn_loss: int | None = None
    is_best_move: bool = False
    depth: int


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
        default=None, description="Eval after the played move (mover perspective)"
    )
    evaluation_after_mate: int | None = None
    evaluation_change_cp: int | None = None
    centipawn_loss: int | None = None
    best_move_uci: str | None = None
    best_move_san: str | None = None
    is_best_move: bool = False
    second_best_cp: int | None = Field(
        default=None,
        description="Second-best line eval (mover perspective); None when MultiPV < 2",
    )
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


def compute_cp_loss(
    best_cp: int | None,
    best_mate: int | None,
    played_cp: int | None,
    played_mate: int | None,
) -> int | None:
    """Centipawn loss of a played move vs the best line (mover perspective).

    Returns ``None`` when either score is unavailable, and ``0`` when the
    played move itself delivers mate.
    """
    if played_mate is not None and played_mate > 0:
        return 0
    best_value = to_cp(best_cp, best_mate)
    played_value = to_cp(played_cp, played_mate)
    if best_value is None or played_value is None:
        return None
    return max(0, best_value - played_value)


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
        self, fen: str, *, depth: int | None = None, multipv: int | None = None
    ) -> AnalyzedPosition:
        """Analyze a single position and return a structured response."""

    @abstractmethod
    def compare_moves(
        self, fen: str, moves: list[str], *, depth: int | None = None
    ) -> list[MoveComparison]:
        """Compare candidate moves in the same position against the best."""

    @abstractmethod
    def close(self) -> None:
        """Release engine resources."""

    def analyze_game(
        self, game: Game, *, depth: int | None = None, multipv: int | None = None
    ) -> list[GameMoveEvaluation]:
        """Evaluate every played move of a game (main line).

        For each move: analyzes the position before the move (MultiPV) and the
        position after (to obtain the played move's eval when it is not among
        the MultiPV lines). All scores are reported from the mover's
        perspective.

        Raises:
            InvalidMoveError: when a move is illegal in its position.
            EngineError subclasses: on engine unavailability/timeout/crash.
        """
        board = chess.Board(game.initial_position)
        evaluations: list[GameMoveEvaluation] = []
        for move in game.moves:
            played = chess.Move.from_uci(move.uci)
            if played not in board.legal_moves:
                raise InvalidMoveError(
                    f"Illegal move '{move.san}' ({move.uci}) at ply {move.ply}",
                    details={"fen": move.fen_before, "move": move.uci},
                )
            before = self.analyze_position(move.fen_before, depth=depth, multipv=multipv)
            board.push(played)
            after = self.analyze_position(board.fen(), depth=depth, multipv=1)

            best_line = before.lines[0] if before.lines else None
            after_line = after.lines[0] if after.lines else None

            played_line = next(
                (line for line in before.lines if line.move_uci == move.uci), None
            )
            if played_line is not None:
                played_cp, played_mate = played_line.cp, played_line.mate
            elif after_line is not None:
                # Score of the position after the move, flipped to the mover.
                played_cp, played_mate = flip_score(after_line.cp, after_line.mate)
            else:
                played_cp, played_mate = None, None

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
                    centipawn_loss=compute_cp_loss(best_cp, best_mate, played_cp, played_mate),
                    best_move_uci=before.best_move_uci,
                    best_move_san=before.best_move_san,
                    is_best_move=bool(before.best_move_uci and move.uci == before.best_move_uci),
                    second_best_cp=(before.lines[1].cp if len(before.lines) > 1 else None),
                    depth=max(before.depth, after.depth),
                    principal_variation=best_line.pv if best_line else [],
                )
            )
        return evaluations
