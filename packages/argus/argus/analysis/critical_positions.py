"""Critical-position detection.

A critical position is a *candidate* worth a closer look — a large evaluation
swing, a mistake/blunder, a missed win, a mate appearing or vanishing, or a
major material transition. Detection is deterministic and derived only from
engine measurements plus board state.

Important honesty rule: **not every large swing is a mistake.** A swing can be
forced (the opponent played the only good move, or the position was already
lost). Candidates are therefore tagged with a *reason* and *severity*, and the
later intelligence layer decides what — if anything — they mean.
"""

from __future__ import annotations

from enum import Enum

import chess
from pydantic import BaseModel, Field

from argus.analysis.classification import MoveClassification
from argus.chess_core.models import Color

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


class CriticalReason(str, Enum):
    """Why a position is a critical candidate."""

    BLUNDER = "blunder"
    MISTAKE = "mistake"
    EVALUATION_SWING = "evaluation_swing"
    MISSED_WIN = "missed_win"
    MATE_CHANGE = "mate_change"
    MATERIAL_TRANSITION = "material_transition"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CriticalPositionPolicy(BaseModel):
    """Configurable thresholds for critical-position detection."""

    swing_medium_cp: int = 150
    swing_high_cp: int = 300
    missed_win_threshold_cp: int = 200
    winning_eval_cp: int = 250
    material_transition_min: int = 3


class CriticalPositionCandidate(BaseModel):
    """A candidate critical position (engine-derived facts only)."""

    game_id: str | None = None
    ply: int
    move_number: int
    color: Color
    san: str
    fen_before: str
    evaluation_before_white: int | None = Field(
        default=None, description="Eval before the move, White perspective"
    )
    evaluation_after_white: int | None = Field(
        default=None, description="Eval after the move, White perspective"
    )
    swing_cp: int | None = Field(
        default=None,
        description="Evaluation change from the mover's perspective (negative = lost ground)",
    )
    classification: MoveClassification | None = None
    reason: CriticalReason
    severity: Severity
    severity_score: int = Field(description="Absolute magnitude driving the severity")
    is_mate_related: bool = False
    detail: str = ""


def _material_balance(fen: str) -> int:
    """White-minus-Black material in pawns (kings excluded)."""
    board = chess.Board(fen)
    balance = 0
    for square, piece in board.piece_map().items():
        value = PIECE_VALUES[piece.piece_type]
        balance += value if piece.color == chess.WHITE else -value
    return balance


def _severity(score: int, policy: CriticalPositionPolicy) -> Severity:
    if score >= policy.swing_high_cp:
        return Severity.HIGH
    if score >= policy.swing_medium_cp:
        return Severity.MEDIUM
    return Severity.LOW


def detect_critical_positions(
    moves,  # list[AnalyzedMove] — duck-typed to avoid a circular import
    *,
    game_id: str | None = None,
    policy: CriticalPositionPolicy | None = None,
) -> list[CriticalPositionCandidate]:
    """Detect critical-position candidates from an analyzed game's moves.

    Args:
        moves: the ``AnalyzedMove`` list of a ``GameAnalysis``.
        game_id: attached to every candidate for persistence.
        policy: configurable thresholds.

    Returns:
        Candidates ordered by ply. A move may produce more than one candidate
        (e.g. a blunder that is also a large swing and a missed win), each with
        its own reason.
    """
    limits = policy or CriticalPositionPolicy()
    candidates: list[CriticalPositionCandidate] = []

    for move in moves:
        before_white = move.evaluation_before_cp
        after_white = move.evaluation_after_cp
        swing = move.evaluation_change_cp  # mover perspective
        is_white = move.color == Color.WHITE

        def to_white(value: int | None) -> int | None:
            return value if (value is None or is_white) else -value

        def add(reason: CriticalReason, score: int, detail: str, mate: bool = False) -> None:
            candidates.append(
                CriticalPositionCandidate(
                    game_id=game_id,
                    ply=move.ply,
                    move_number=move.move_number,
                    color=move.color,
                    san=move.san,
                    fen_before=move.fen_before,
                    evaluation_before_white=before_white,
                    evaluation_after_white=after_white,
                    swing_cp=swing,
                    classification=move.classification,
                    reason=reason,
                    severity=_severity(score, limits),
                    severity_score=score,
                    is_mate_related=mate,
                    detail=detail,
                )
            )

        if swing is None:
            continue

        # 1. Classified mistakes and blunders are critical by definition.
        if move.classification is MoveClassification.BLUNDER:
            add(CriticalReason.BLUNDER, abs(swing), "Engine classified the move a blunder.")
        elif move.classification is MoveClassification.MISTAKE:
            add(CriticalReason.MISTAKE, abs(swing), "Engine classified the move a mistake.")

        # 2. Large evaluation swing against the mover.
        if swing <= -limits.swing_medium_cp:
            add(
                CriticalReason.EVALUATION_SWING,
                abs(swing),
                f"Evaluation dropped {abs(swing)}cp for the mover.",
            )

        # 3. Missed win: mover was clearly winning before and is not after.
        if (
            before_white is not None
            and after_white is not None
            and (before_white if is_white else -before_white) >= limits.winning_eval_cp
            and (after_white if is_white else -after_white) < limits.winning_eval_cp / 2
        ):
            add(
                CriticalReason.MISSED_WIN,
                abs(swing),
                "A clearly winning position was not converted.",
            )

        # 4. Mate appearing or vanishing (mate distances are never cp values).
        before_mate_white = to_white(move.evaluation_before_mate if hasattr(move, "evaluation_before_mate") else None)
        after_mate_white = to_white(move.evaluation_after_mate if hasattr(move, "evaluation_after_mate") else None)
        if before_mate_white != after_mate_white and (
            before_mate_white is not None or after_mate_white is not None
        ):
            add(
                CriticalReason.MATE_CHANGE,
                limits.swing_high_cp,
                "A forced mate appeared or disappeared after this move.",
                mate=True,
            )

        # 5. Major material transition (measured from the board).
        before_material = _material_balance(move.fen_before)
        after_material = _material_balance(move.fen_after)
        if abs(after_material - before_material) >= limits.material_transition_min:
            add(
                CriticalReason.MATERIAL_TRANSITION,
                abs(after_material - before_material) * 100,
                f"Material balance changed by {abs(after_material - before_material)} pawns.",
            )

    return candidates
