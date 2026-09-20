"""Position representation and serialization."""

from __future__ import annotations

import chess
from pydantic import BaseModel, Field

from argus.chess_core.fen import validate_fen
from argus.chess_core.models import Color
from argus.shared.errors import InvalidFenError


class PositionSnapshot(BaseModel):
    """Serializable snapshot of a chess position."""

    fen: str
    turn: Color
    castling_rights: str = Field(description="Castling availability, e.g. 'KQkq' or '-'")
    ep_square: str | None = Field(default=None, description="En-passant target square if any")
    halfmove_clock: int
    fullmove_number: int
    piece_placement: dict[str, str] = Field(
        description="Map of square name (e.g. 'e4') to piece symbol ('P', 'n', ...)"
    )


def serialize_position(fen: str) -> PositionSnapshot:
    """Serialize a FEN position into a structured snapshot.

    Raises:
        InvalidFenError: when the FEN cannot be parsed or is illegal.
    """
    validation = validate_fen(fen)
    if not validation.is_valid:
        raise InvalidFenError(
            f"Cannot serialize invalid FEN: {'; '.join(validation.errors)}",
            details={"fen": fen, "errors": validation.errors},
        )
    board = chess.Board(validation.fen)
    placement = {
        chess.square_name(square): piece.symbol()
        for square, piece in board.piece_map().items()
    }
    return PositionSnapshot(
        fen=validation.fen,
        turn=Color.WHITE if board.turn == chess.WHITE else Color.BLACK,
        castling_rights=board.castling_xfen() or "-",
        ep_square=chess.square_name(board.ep_square) if board.ep_square is not None else None,
        halfmove_clock=board.halfmove_clock,
        fullmove_number=board.fullmove_number,
        piece_placement=placement,
    )
