"""Positional event detection.

Two different things are kept strictly apart here:

* **POSITIONAL FEATURE** — an objective board property (an isolated pawn exists,
  a file is open, a rook has no move, a square cannot be defended by a pawn).
  Features are recorded without any verdict.
* **POSITIONAL ERROR CANDIDATE** — the same feature, but created by a move the
  engine measured as losing at least ``error_candidate_min_cp_loss`` centipawns.
  This is a Caissa interpretation, and it is labelled as a *candidate*.

A feature that the engine does not flag stays a feature. The layer never turns
structure into a verdict on its own.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

import chess
from pydantic import BaseModel, Field

from argus.analysis.features.extractor import extract_position_features, pawn_file_map
from argus.chess_core.models import Color
from argus.intelligence.activity import PieceActivityAnalysis, activity_for
from argus.intelligence.base import (
    Certainty,
    EvidenceSource,
    MoveFact,
    PositionalPolicy,
    chess_color,
    color_label,
    severity_from_magnitude,
)
from argus.intelligence.structure import PawnStructureAnalysis, PawnStructureEvent, pawn_structure_for


class PositionalEventType(str, Enum):
    """Positional features Caissa can measure from the board."""

    ISOLATED_PAWN = "isolated_pawn"
    DOUBLED_PAWN = "doubled_pawn"
    BACKWARD_PAWN = "backward_pawn"
    PASSED_PAWN = "passed_pawn"
    PAWN_ISLAND = "pawn_island"
    WEAK_SQUARE = "weak_square"
    OPEN_FILE = "open_file"
    SEMI_OPEN_FILE = "semi_open_file"
    PAWN_BREAK = "pawn_break"
    RESTRICTED_PIECE = "restricted_piece"
    TRAPPED_PIECE = "trapped_piece"
    UNDEVELOPED_PIECE = "undeveloped_piece"
    LOST_CENTER_CONTROL = "lost_center_control"
    SPACE_DISADVANTAGE = "space_disadvantage"
    POOR_ROOK_PLACEMENT = "poor_rook_placement"


_STRUCTURE_MAP: dict[str, PositionalEventType] = {
    "isolated_pawn_created": PositionalEventType.ISOLATED_PAWN,
    "doubled_pawns_created": PositionalEventType.DOUBLED_PAWN,
    "backward_pawn_created": PositionalEventType.BACKWARD_PAWN,
    "passed_pawn_created": PositionalEventType.PASSED_PAWN,
    "pawn_island_change": PositionalEventType.PAWN_ISLAND,
    "open_file_created": PositionalEventType.OPEN_FILE,
    "semi_open_file_created": PositionalEventType.SEMI_OPEN_FILE,
    "pawn_break": PositionalEventType.PAWN_BREAK,
}


class PositionalEvent(BaseModel):
    """A positional feature, optionally flagged as an error candidate."""

    type: PositionalEventType
    ply: int
    move_number: int
    side: Color
    san: str
    classification: Literal["feature", "error_candidate"] = "feature"
    severity: str = "low"
    certainty: Certainty = Certainty.CONFIRMED
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE
    statement: str
    engine_supported: bool | None = Field(
        default=None,
        description="True when the engine's centipawn loss on this move supports the error flag",
    )
    evidence: dict = Field(default_factory=dict)


class PositionalAnalysis(BaseModel):
    """All positional features of a game, split into features and error candidates."""

    events: list[PositionalEvent] = Field(default_factory=list)
    feature_count: int = 0
    error_candidate_count: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_side: dict[str, int] = Field(default_factory=dict)
    weak_squares_white: list[str] = Field(default_factory=list)
    weak_squares_black: list[str] = Field(default_factory=list)
    final_white_islands: int | None = None
    final_black_islands: int | None = None
    note: str = (
        "A positional feature is an objective board property. It is flagged as an error "
        "candidate only when the engine's centipawn loss on the same move supports it."
    )


def weak_squares(board: chess.Board, color: chess.Color) -> list[str]:
    """Squares this side cannot defend with a pawn but the enemy attacks with one.

    A square counts when no pawn of ``color`` on an adjacent file is behind it
    (so the pawn could never advance to defend it) *and* an enemy pawn attacks
    it. Deterministic, and intentionally narrow so the output stays meaningful.
    """
    own_pawns = pawn_file_map(board, color)
    direction = 1 if color == chess.WHITE else -1
    result: list[str] = []
    for square in chess.SQUARES:
        if board.piece_at(square) is not None:
            continue
        file, rank = chess.square_file(square), chess.square_rank(square)
        defendable = False
        for df in (-1, 1):
            neighbour = file + df
            if not 0 <= neighbour <= 7:
                continue
            # A pawn defends the square one step diagonally ahead of it.
            if any(pawn_rank + direction == rank for pawn_rank in own_pawns.get(neighbour, [])):
                defendable = True
                break
        if defendable:
            continue
        attacked_by_pawn = any(
            (piece := board.piece_at(attacker)) is not None and piece.piece_type == chess.PAWN
            for attacker in board.attackers(not color, square)
        )
        if attacked_by_pawn:
            result.append(chess.square_name(square))
    return result


def _rook_mobility(board: chess.Board, color: chess.Color) -> set[int]:
    """Squares of ``color``'s rooks that have at least one legal move."""
    probe = board
    if board.turn != color:
        probe = board.copy(stack=False)
        probe.turn = color
    movable = {move.from_square for move in probe.legal_moves}
    return {
        square
        for square in chess.SquareSet(board.occupied_co[color])
        if (piece := board.piece_at(square)) is not None and piece.piece_type == chess.ROOK
    } & movable


def _rook_placement_issues(
    before: chess.Board, after: chess.Board, color: chess.Color
) -> list[str]:
    """Rooks that *became* immobile on this move (a placement fact, not a verdict).

    Comparing before and after keeps the starting position (where every rook is
    boxed in) from being reported over and over.
    """
    movable_before = _rook_mobility(before, color)
    movable_after = _rook_mobility(after, color)
    issues: list[str] = []
    for square in sorted(movable_before - movable_after):
        file = chess.FILE_NAMES[chess.square_file(square)]
        file_has_own_pawn = bool(pawn_file_map(after, color).get(chess.square_file(square)))
        issues.append(
            f"R{chess.square_name(square)} on the {file}-file lost every legal move"
            + (" behind its own pawn" if file_has_own_pawn else "")
        )
    return issues


def _structure_event_to_positional(
    event: PawnStructureEvent, facts_by_ply: dict[int, MoveFact], limits: PositionalPolicy
) -> PositionalEvent | None:
    event_type = _STRUCTURE_MAP.get(event.type)
    if event_type is None:
        return None
    fact = facts_by_ply.get(event.ply)
    error_supported = bool(
        fact is not None
        and event.side == fact.mover
        and fact.is_problem
        and (fact.centipawn_loss or 0) >= limits.error_candidate_min_cp_loss
    )
    return PositionalEvent(
        type=event_type,
        ply=event.ply,
        move_number=event.move_number,
        side=event.side,
        san=event.san,
        classification="error_candidate" if error_supported else "feature",
        severity=event.severity,
        certainty=Certainty.CANDIDATE if error_supported else Certainty.CONFIRMED,
        source=(
            EvidenceSource.ARGUS_INTERPRETATION
            if error_supported
            else EvidenceSource.ARGUS_DERIVED_FEATURE
        ),
        statement=event.statement,
        engine_supported=None if fact is None else error_supported,
        evidence={
            **event.evidence,
            "engine_context": None
            if fact is None
            else {
                "centipawn_loss": fact.centipawn_loss,
                "classification": fact.classification.value if fact.classification else None,
                "evaluation_change_cp": fact.eval_change_cp,
            },
            "error_candidate_threshold_cp": limits.error_candidate_min_cp_loss,
        },
    )


def build_positional_analysis(
    moves: list[MoveFact],
    *,
    initial_position: str,
    structure: PawnStructureAnalysis | None = None,
    activity: PieceActivityAnalysis | None = None,
    policy: PositionalPolicy | None = None,
) -> PositionalAnalysis:
    """Build positional features (and error candidates) for a game."""
    limits = policy or PositionalPolicy()
    if not moves:
        return PositionalAnalysis()

    if structure is None:
        from argus.intelligence.structure import build_pawn_structure

        structure = build_pawn_structure(moves, initial_position=initial_position)
    facts_by_ply = {fact.ply: fact for fact in moves}
    events: list[PositionalEvent] = []

    for structure_event in structure.events:
        converted = _structure_event_to_positional(structure_event, facts_by_ply, limits)
        if converted is not None:
            events.append(converted)

    if activity is None:
        from argus.intelligence.activity import build_piece_activity

        activity = build_piece_activity(
            moves, initial_position=initial_position, policy=limits
        )

    activity_by_ply = {snapshot.ply: snapshot for snapshot in activity.snapshots}
    weak_white: list[str] = []
    weak_black: list[str] = []

    for fact in moves:
        board_before = chess.Board(fact.fen_before)
        board_after = chess.Board(fact.fen_after)
        mover_white = fact.mover == Color.WHITE
        before_activity = activity_for(
            board_before,
            chess_color(fact.mover),
            restricted_threshold=limits.restricted_mobility,
        )
        snapshot = activity_by_ply.get(fact.ply)
        if snapshot is None:  # pragma: no cover — snapshots cover every ply
            continue
        after_activity = snapshot.white if mover_white else snapshot.black
        opponent_activity = snapshot.black if mover_white else snapshot.white

        error_supported = bool(
            fact.is_problem
            and (fact.centipawn_loss or 0) >= limits.error_candidate_min_cp_loss
        )

        def add(
            event_type: PositionalEventType,
            statement: str,
            *,
            severity: str,
            evidence: dict,
            is_error: bool = False,
        ) -> None:
            events.append(
                PositionalEvent(
                    type=event_type,
                    ply=fact.ply,
                    move_number=fact.move_number,
                    side=fact.mover,
                    san=fact.san,
                    classification="error_candidate" if is_error else "feature",
                    severity=severity,
                    certainty=Certainty.CANDIDATE if is_error else Certainty.CONFIRMED,
                    source=(
                        EvidenceSource.ARGUS_INTERPRETATION
                        if is_error
                        else EvidenceSource.ARGUS_DERIVED_FEATURE
                    ),
                    statement=statement,
                    engine_supported=error_supported,
                    evidence={
                        "fen_before": fact.fen_before,
                        "fen_after": fact.fen_after,
                        "engine_context": {
                            "centipawn_loss": fact.centipawn_loss,
                            "classification": fact.classification.value
                            if fact.classification
                            else None,
                        },
                        **evidence,
                    },
                )
            )

        new_trapped = [p for p in after_activity.trapped_pieces if p not in before_activity.trapped_pieces]
        if new_trapped:
            add(
                PositionalEventType.TRAPPED_PIECE,
                f"{color_label(fact.mover)}'s {', '.join(new_trapped)} has no legal move after {fact.san}.",
                severity="medium",
                evidence={"pieces": new_trapped},
                is_error=error_supported,
            )
        new_restricted = [
            piece
            for piece in after_activity.restricted_pieces
            if piece not in before_activity.restricted_pieces
        ]
        if new_restricted:
            add(
                PositionalEventType.RESTRICTED_PIECE,
                (
                    f"After {fact.san}, {color_label(fact.mover)}'s "
                    f"{', '.join(new_restricted)} is restricted to "
                    f"{limits.restricted_mobility} or fewer moves."
                ),
                severity="low",
                evidence={"pieces": new_restricted, "max_moves": limits.restricted_mobility},
                is_error=error_supported,
            )

        center_loss = before_activity.center_attacked - after_activity.center_attacked
        if center_loss >= limits.lost_center_min:
            add(
                PositionalEventType.LOST_CENTER_CONTROL,
                (
                    f"{color_label(fact.mover)} lost control of {center_loss} central square(s) "
                    f"after {fact.san}."
                ),
                severity=severity_from_magnitude(center_loss, medium=2, high=3),
                evidence={
                    "before": before_activity.center_attacked,
                    "after": after_activity.center_attacked,
                },
                is_error=error_supported,
            )

        mobility_gap = opponent_activity.mobility - after_activity.mobility
        if mobility_gap >= 5:
            add(
                PositionalEventType.SPACE_DISADVANTAGE,
                (
                    f"{color_label(fact.mover)} has {after_activity.mobility} legal moves against "
                    f"{opponent_activity.mobility} for the opponent after {fact.san}."
                ),
                severity=severity_from_magnitude(mobility_gap, medium=6, high=10),
                evidence={"mover_mobility": after_activity.mobility, "opponent_mobility": opponent_activity.mobility},
            )

        if fact.ply >= limits.undeveloped_after_ply and after_activity.undeveloped_pieces >= 2:
            add(
                PositionalEventType.UNDEVELOPED_PIECE,
                (
                    f"{color_label(fact.mover)} still has {after_activity.undeveloped_pieces} "
                    f"undeveloped pieces on move {fact.move_number}."
                ),
                severity="low",
                evidence={
                    "undeveloped": after_activity.undeveloped_pieces,
                    "developed": after_activity.developed_pieces,
                },
                is_error=error_supported,
            )

        for issue in _rook_placement_issues(board_before, board_after, chess_color(fact.mover)):
            add(
                PositionalEventType.POOR_ROOK_PLACEMENT,
                f"{issue} after {fact.san}.",
                severity="low",
                evidence={"issue": issue},
                is_error=error_supported,
            )

    final_board = chess.Board(moves[-1].fen_after)
    weak_white = weak_squares(final_board, chess.WHITE)
    weak_black = weak_squares(final_board, chess.BLACK)

    by_type: dict[str, int] = {}
    by_side: dict[str, int] = {}
    for event in events:
        by_type[event.type.value] = by_type.get(event.type.value, 0) + 1
        by_side[event.side.value] = by_side.get(event.side.value, 0) + 1

    white_structure = pawn_structure_for(final_board, chess.WHITE)
    black_structure = pawn_structure_for(final_board, chess.BLACK)
    return PositionalAnalysis(
        events=events,
        feature_count=len([e for e in events if e.classification == "feature"]),
        error_candidate_count=len([e for e in events if e.classification == "error_candidate"]),
        by_type=by_type,
        by_side=by_side,
        weak_squares_white=weak_white,
        weak_squares_black=weak_black,
        final_white_islands=white_structure.islands,
        final_black_islands=black_structure.islands,
    )


def events_by_ply(analysis: PositionalAnalysis) -> dict[int, list[PositionalEvent]]:
    """Group positional events by ply (used by the category classifier)."""
    grouped: dict[int, list[PositionalEvent]] = {}
    for event in analysis.events:
        grouped.setdefault(event.ply, []).append(event)
    return grouped


def extract_features(fen: str):
    """Thin re-export so callers do not need the analysis.features import path."""
    return extract_position_features(chess.Board(fen))
