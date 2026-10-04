"""Piece-activity metrics.

Measurable, board-derived activity numbers for both sides at every ply. These
are features, not judgements: they are the raw material that the positional
layer, the report, and (later) player profiling and ML consume.

Reused from the Phase 1 feature extractor: legal mobility, center occupancy and
attack counts, and the undeveloped-piece count. Added here: attacked-square
coverage, defended own pieces, mutual coordination, and trapped/restricted
pieces (computed from the real legal-move list, so "trapped" means strictly
"this piece has no legal move in this position").
"""

from __future__ import annotations

from pydantic import BaseModel, Field

import chess

from argus.analysis.features.extractor import extract_position_features
from argus.chess_core.models import Color
from argus.intelligence.base import PIECE_POINTS, MoveFact, PositionalPolicy


class SideActivity(BaseModel):
    """Activity metrics for one side in one position."""

    mobility: int = Field(description="Legal moves available to the side")
    attacked_squares: int = Field(description="Distinct squares the side attacks")
    defended_pieces: int = Field(description="Own pieces defended by another own piece")
    coordination: int = Field(description="Own pieces that both defend and are defended")
    center_occupied: int
    center_attacked: int
    developed_pieces: int
    undeveloped_pieces: int
    trapped_pieces: list[str] = Field(default_factory=list, description="Pieces with zero legal moves")
    restricted_pieces: list[str] = Field(default_factory=list)
    piece_activity_score: int = Field(
        description="Caissa-derived aggregate: mobility + defended pieces + center control"
    )


class PieceActivitySnapshot(BaseModel):
    """Both sides' activity after one ply."""

    ply: int
    move_number: int
    side_to_move: Color
    white: SideActivity
    black: SideActivity


class PieceActivityAnalysis(BaseModel):
    """Activity history plus per-side averages."""

    snapshots: list[PieceActivitySnapshot] = Field(default_factory=list)
    average_mobility_white: float | None = None
    average_mobility_black: float | None = None
    average_activity_white: float | None = None
    average_activity_black: float | None = None
    note: str = (
        "Activity metrics are raw board measurements (legal moves, attacked and "
        "defended squares). They carry no verdict on their own."
    )


def _attacked_squares(board: chess.Board, color: chess.Color) -> int:
    squares: set[int] = set()
    for square in chess.SquareSet(board.occupied_co[color]):
        squares |= {int(target) for target in board.attacks(square)}
    return len(squares)


def _defended_pieces(board: chess.Board, color: chess.Color) -> tuple[int, int]:
    """(own pieces defended by another own piece, mutually coordinated pieces)."""
    defended = 0
    coordinated = 0
    for square in chess.SquareSet(board.occupied_co[color]):
        piece = board.piece_at(square)
        if piece is None or piece.piece_type == chess.KING:
            continue
        defenders = [
            attacker
            for attacker in board.attackers(color, square)
            if attacker != square
        ]
        if not defenders:
            continue
        defended += 1
        # Coordinated: this piece is defended by a piece that it also defends.
        for defender_square in defenders:
            defender = board.piece_at(defender_square)
            if defender is None or defender.piece_type == chess.KING:
                continue
            if square in set(board.attackers(color, defender_square)):
                coordinated += 1
                break
    return defended, coordinated


def _move_availability(
    board: chess.Board, color: chess.Color, restricted_threshold: int
) -> tuple[list[str], list[str], int]:
    """Trapped/restricted pieces and legal mobility for ``color``.

    Only pieces whose restriction is informative are reported: pawns (blocked
    pawns are a pawn-structure fact, analysed separately) and kings (a king with
    no legal move is checkmate or stalemate, not a restricted piece) are skipped.
    """
    if board.turn != color:
        board = board.copy(stack=False)
        board.turn = color
    per_square: dict[int, int] = {}
    for move in board.legal_moves:
        per_square[move.from_square] = per_square.get(move.from_square, 0) + 1
    trapped: list[str] = []
    restricted: list[str] = []
    for square in chess.SquareSet(board.occupied_co[color]):
        piece = board.piece_at(square)
        if piece is None or piece.piece_type in (chess.PAWN, chess.KING):
            continue
        count = per_square.get(square, 0)
        label = f"{piece.symbol()}{chess.square_name(square)}"
        if count == 0:
            trapped.append(label)
        elif count <= restricted_threshold:
            restricted.append(label)
    return trapped, restricted, sum(per_square.values())


def activity_for(
    board: chess.Board, color: chess.Color, *, restricted_threshold: int = 2
) -> SideActivity:
    """Activity metrics for one side in a position."""
    features = extract_position_features(board)
    is_white = color == chess.WHITE
    mobility = features.mobility_white if is_white else features.mobility_black
    trapped, restricted, _legal_mobility = _move_availability(
        board, color, restricted_threshold
    )
    defended, coordinated = _defended_pieces(board, color)
    center_occupied = (
        features.center_occupied_white if is_white else features.center_occupied_black
    )
    center_attacked = (
        features.center_attacked_white if is_white else features.center_attacked_black
    )
    undeveloped = (
        features.undeveloped_pieces_white if is_white else features.undeveloped_pieces_black
    )
    # "Developed pieces" counts non-pawn, non-king material actually on the board.
    developed = sum(
        1
        for piece in board.piece_map().values()
        if piece.color == color and piece.piece_type not in (chess.PAWN, chess.KING)
    )
    return SideActivity(
        mobility=mobility,
        attacked_squares=_attacked_squares(board, color),
        defended_pieces=defended,
        coordination=coordinated,
        center_occupied=center_occupied,
        center_attacked=center_attacked,
        developed_pieces=developed,
        undeveloped_pieces=undeveloped,
        trapped_pieces=trapped,
        restricted_pieces=restricted,
        # Legal-move mobility is used so the score cannot drift from the board.
        piece_activity_score=mobility + defended + center_occupied * 2 + center_attacked,
    )


def build_piece_activity(
    moves: list[MoveFact],
    *,
    initial_position: str,
    policy: PositionalPolicy | None = None,
) -> PieceActivityAnalysis:
    """Activity history for a game (one snapshot per ply)."""
    limits = policy or PositionalPolicy()
    if not moves:
        return PieceActivityAnalysis()

    snapshots: list[PieceActivitySnapshot] = []
    for fact in moves:
        board = chess.Board(fact.fen_after)
        white = activity_for(board, chess.WHITE, restricted_threshold=limits.restricted_mobility)
        black = activity_for(board, chess.BLACK, restricted_threshold=limits.restricted_mobility)
        snapshots.append(
            PieceActivitySnapshot(
                ply=fact.ply,
                move_number=fact.move_number,
                side_to_move=Color.WHITE if board.turn else Color.BLACK,
                white=white,
                black=black,
            )
        )

    count = len(snapshots)
    return PieceActivityAnalysis(
        snapshots=snapshots,
        average_mobility_white=round(sum(s.white.mobility for s in snapshots) / count, 2),
        average_mobility_black=round(sum(s.black.mobility for s in snapshots) / count, 2),
        average_activity_white=round(
            sum(s.white.piece_activity_score for s in snapshots) / count, 2
        ),
        average_activity_black=round(
            sum(s.black.piece_activity_score for s in snapshots) / count, 2
        ),
    )


def piece_value(piece: chess.Piece | None) -> int:
    """Material value of a piece in pawn units (0 for an empty square)."""
    if piece is None:
        return 0
    return PIECE_POINTS[piece.symbol().lower()]
