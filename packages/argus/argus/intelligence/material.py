"""Material tracking.

Material is measured from the board (python-chess), **independently of the
engine**. The module never reads an evaluation: a side can be a pawn up and
losing, and the report must be able to state both facts without conflating
them. Every value here is a statement about the board, nothing more.

Recorded facts:

* ``capture`` — a piece was captured (which piece, by which side, balance effect)
* ``exchange`` — a capture answered by a recapture on the same square
* ``promotion`` — a pawn promoted
* ``material_transition`` — the balance changed by at least
  ``MaterialPolicy.transition_min`` pawns

Material values are pawn units (N/B = 3, R = 5, Q = 9) from a single table
(:data:`argus.intelligence.base.PIECE_POINTS`) so the timeline and the report
cannot disagree about what a piece is worth.
"""

from __future__ import annotations

from typing import Literal

import chess
from pydantic import BaseModel, Field

from argus.chess_core.models import Color
from argus.intelligence.base import (
    PIECE_POINTS,
    Certainty,
    EvidenceSource,
    MaterialPolicy,
    MoveFact,
)

MaterialEventType = Literal["capture", "exchange", "promotion", "material_transition"]

_PIECE_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


class MaterialSnapshot(BaseModel):
    """Material state after one ply (kings excluded, pawn units)."""

    ply: int
    move_number: int
    white_points: int
    black_points: int
    balance: int = Field(description="White points minus Black points")
    queens: int
    rooks: int
    minors: int
    major_pieces: int = Field(description="rooks + queens on the board")


class MaterialEvent(BaseModel):
    """One material fact, tied to the ply that produced it."""

    type: MaterialEventType
    ply: int
    move_number: int
    side: Color
    san: str
    balance_before: int
    balance_after: int
    delta: int = Field(description="Balance change caused by this ply (White perspective)")
    moved_piece: str | None = None
    captured_piece: str | None = None
    promoted_to: str | None = None
    square: str | None = None
    answered_on_same_square: bool = False
    certainty: Certainty = Certainty.CONFIRMED
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE
    evidence: dict = Field(default_factory=dict)


class MaterialTimeline(BaseModel):
    """Full material history of a game, derived from the board at every ply."""

    initial_balance: int = 0
    final_balance: int = 0
    snapshots: list[MaterialSnapshot] = Field(default_factory=list)
    events: list[MaterialEvent] = Field(default_factory=list)
    transitions: list[MaterialEvent] = Field(default_factory=list)
    captures: list[MaterialEvent] = Field(default_factory=list)
    promotions: list[MaterialEvent] = Field(default_factory=list)
    peak_white_balance: int = 0
    peak_black_balance: int = 0
    peak_white_ply: int | None = None
    peak_black_ply: int | None = None
    first_capture_ply: int | None = None
    first_capture_move_number: int | None = None
    total_captures: int = 0
    exchanges: int = 0
    note: str = (
        "Material is measured from the board only. Material advantage and engine "
        "advantage are reported separately and are not interchangeable."
    )


def _points(board: chess.Board, color: chess.Color) -> int:
    total = 0
    for piece in board.piece_map().values():
        if piece.color == color:
            total += PIECE_POINTS[piece.symbol().lower()]
    return total


def material_points(board: chess.Board, color: chess.Color) -> int:
    """Public accessor: total material points for one side (kings excluded)."""
    return _points(board, color)


def _counts(board: chess.Board) -> tuple[int, int, int, int]:
    queens = rooks = minors = 0
    for piece in board.piece_map().values():
        if piece.piece_type == chess.QUEEN:
            queens += 1
        elif piece.piece_type == chess.ROOK:
            rooks += 1
        elif piece.piece_type in (chess.KNIGHT, chess.BISHOP):
            minors += 1
    return queens, rooks, minors, queens + rooks


def _captured_piece(board_before: chess.Board, move: chess.Move) -> chess.Piece | None:
    """Piece captured by ``move`` in ``board_before`` (handles en passant)."""
    target = board_before.piece_at(move.to_square)
    if target is not None:
        return target
    piece = board_before.piece_at(move.from_square)
    if piece is not None and piece.piece_type == chess.PAWN:
        # En passant: the captured pawn sits beside the destination square.
        if chess.square_file(move.from_square) != chess.square_file(move.to_square):
            return board_before.piece_at(chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square)))
    return None


def _material_event(
    fact: MoveFact,
    *,
    event_type: MaterialEventType,
    balance_before: int,
    balance_after: int,
    moved_piece: str | None = None,
    captured_piece: str | None = None,
    promoted_to: str | None = None,
    square: str | None = None,
    answered_on_same_square: bool = False,
    evidence: dict | None = None,
) -> MaterialEvent:
    return MaterialEvent(
        type=event_type,
        ply=fact.ply,
        move_number=fact.move_number,
        side=fact.mover,
        san=fact.san,
        balance_before=balance_before,
        balance_after=balance_after,
        delta=balance_after - balance_before,
        moved_piece=moved_piece,
        captured_piece=captured_piece,
        promoted_to=promoted_to,
        square=square,
        answered_on_same_square=answered_on_same_square,
        evidence={
            "fen_before": fact.fen_before,
            "fen_after": fact.fen_after,
            "uci": fact.uci,
            **(evidence or {}),
        },
    )


def build_material_timeline(
    moves: list[MoveFact],
    *,
    initial_position: str,
    policy: MaterialPolicy | None = None,
) -> MaterialTimeline:
    """Build the material timeline and its events for a game."""
    limits = policy or MaterialPolicy()
    if not moves:
        return MaterialTimeline()

    snapshots: list[MaterialSnapshot] = []
    events: list[MaterialEvent] = []

    start_board = chess.Board(initial_position)
    initial_balance = _points(start_board, chess.WHITE) - _points(start_board, chess.BLACK)
    previous_balance = initial_balance

    for fact in moves:
        try:
            move = chess.Move.from_uci(fact.uci)
        except ValueError:  # pragma: no cover — stored moves are validated
            break
        board_before = chess.Board(fact.fen_before)
        after_board = chess.Board(fact.fen_after)
        white_points = _points(after_board, chess.WHITE)
        black_points = _points(after_board, chess.BLACK)
        balance = white_points - black_points

        queens, rooks, minors, major_pieces = _counts(after_board)
        snapshots.append(
            MaterialSnapshot(
                ply=fact.ply,
                move_number=fact.move_number,
                white_points=white_points,
                black_points=black_points,
                balance=balance,
                queens=queens,
                rooks=rooks,
                minors=minors,
                major_pieces=major_pieces,
            )
        )

        captured = _captured_piece(board_before, move)
        moving = board_before.piece_at(move.from_square)
        if captured is not None:
            events.append(
                _material_event(
                    fact,
                    event_type="capture",
                    balance_before=previous_balance,
                    balance_after=balance,
                    moved_piece=_PIECE_NAMES[moving.piece_type] if moving else None,
                    captured_piece=_PIECE_NAMES[captured.piece_type],
                    square=chess.square_name(move.to_square),
                    evidence={"captured_value": PIECE_POINTS[captured.symbol().lower()]},
                )
            )
        if move.promotion is not None:
            events.append(
                _material_event(
                    fact,
                    event_type="promotion",
                    balance_before=previous_balance,
                    balance_after=balance,
                    moved_piece="pawn",
                    promoted_to=_PIECE_NAMES[move.promotion],
                    square=chess.square_name(move.to_square),
                )
            )
        if abs(balance - previous_balance) >= limits.transition_min:
            events.append(
                _material_event(
                    fact,
                    event_type="material_transition",
                    balance_before=previous_balance,
                    balance_after=balance,
                    moved_piece=_PIECE_NAMES[moving.piece_type] if moving else None,
                    captured_piece=_PIECE_NAMES[captured.piece_type] if captured else None,
                    evidence={
                        "change_pawns": abs(balance - previous_balance),
                        "threshold_pawns": limits.transition_min,
                    },
                )
            )
        previous_balance = balance

    # An exchange is a capture whose destination square is recaptured on the
    # immediately following ply — a board fact, not an interpretation.
    for index, event in enumerate(events):
        if event.type != "capture":
            continue
        following = next(
            (other for other in events if other.ply == event.ply + 1 and other.type == "capture"),
            None,
        )
        if following is not None and following.square == event.square:
            event.answered_on_same_square = True
            following.answered_on_same_square = True
            events.append(
                MaterialEvent(
                    type="exchange",
                    ply=event.ply,
                    move_number=event.move_number,
                    side=event.side,
                    san=event.san,
                    balance_before=event.balance_before,
                    balance_after=following.balance_after,
                    delta=following.balance_after - event.balance_before,
                    moved_piece=event.moved_piece,
                    captured_piece=event.captured_piece,
                    square=event.square,
                    answered_on_same_square=True,
                    evidence={
                        "recapture_ply": following.ply,
                        "recapture_san": following.san,
                        "recaptured_piece": following.moved_piece,
                    },
                )
            )
    events.sort(key=lambda item: (item.ply, item.type))

    captures = [event for event in events if event.type == "capture"]
    transitions = [event for event in events if event.type == "material_transition"]
    promotions = [event for event in events if event.type == "promotion"]
    peak_white = max((snapshot.balance for snapshot in snapshots), default=initial_balance)
    peak_black = min((snapshot.balance for snapshot in snapshots), default=initial_balance)
    white_ply = next(
        (snapshot.ply for snapshot in snapshots if snapshot.balance == peak_white), None
    )
    black_ply = next(
        (snapshot.ply for snapshot in snapshots if snapshot.balance == peak_black), None
    )

    return MaterialTimeline(
        initial_balance=initial_balance,
        final_balance=snapshots[-1].balance,
        snapshots=snapshots,
        events=events,
        transitions=transitions,
        captures=captures,
        promotions=promotions,
        peak_white_balance=peak_white,
        peak_black_balance=peak_black,
        peak_white_ply=white_ply,
        peak_black_ply=black_ply,
        first_capture_ply=captures[0].ply if captures else None,
        first_capture_move_number=captures[0].move_number if captures else None,
        total_captures=len(captures),
        exchanges=len([event for event in events if event.type == "exchange"]),
    )


def material_balance_at(timeline: MaterialTimeline, ply: int) -> int | None:
    """Material balance (White perspective) after ``ply``, or ``None``."""
    for snapshot in timeline.snapshots:
        if snapshot.ply == ply:
            return snapshot.balance
    return None


def side_balance(timeline: MaterialTimeline, ply: int, color: Color) -> int | None:
    """Material balance from ``color``'s point of view after ``ply``."""
    balance = material_balance_at(timeline, ply)
    if balance is None:
        return None
    return balance if color == Color.WHITE else -balance
