"""FEN validation."""

from __future__ import annotations

import chess
from pydantic import BaseModel, Field

_STATUS_MESSAGES = {
    chess.STATUS_NO_WHITE_KING: "white king missing",
    chess.STATUS_NO_BLACK_KING: "black king missing",
    chess.STATUS_TOO_MANY_KINGS: "too many kings",
    chess.STATUS_TOO_MANY_WHITE_PAWNS: "too many white pawns",
    chess.STATUS_TOO_MANY_BLACK_PAWNS: "too many black pawns",
    chess.STATUS_PAWNS_ON_BACKRANK: "pawns on back rank",
    chess.STATUS_TOO_MANY_WHITE_PIECES: "too many white pieces",
    chess.STATUS_TOO_MANY_BLACK_PIECES: "too many black pieces",
    chess.STATUS_BAD_CASTLING_RIGHTS: "castling rights do not match the board",
    chess.STATUS_INVALID_EP_SQUARE: "invalid en-passant square",
    chess.STATUS_OPPOSITE_CHECK: "the side not to move is in check",
    chess.STATUS_EMPTY: "the board is empty",
    chess.STATUS_IMPOSSIBLE_CHECK: "impossible check configuration",
    chess.STATUS_TOO_MANY_CHECKERS: "too many checkers",
}


class FenValidationResult(BaseModel):
    """Outcome of validating a FEN string."""

    fen: str
    is_valid: bool
    errors: list[str] = Field(default_factory=list)


def validate_fen(fen: str) -> FenValidationResult:
    """Validate a FEN string.

    Returns a result with the normalized (canonical) FEN when valid; otherwise
    a list of rule violations. Never raises for invalid input — callers decide
    how to surface failures.
    """
    text = (fen or "").strip()
    if not text:
        return FenValidationResult(fen=fen, is_valid=False, errors=["FEN is empty"])
    try:
        board = chess.Board(text)
    except ValueError as exc:
        return FenValidationResult(fen=fen, is_valid=False, errors=[f"Invalid FEN: {exc}"])
    status = board.status()
    if status == chess.STATUS_VALID:
        return FenValidationResult(fen=board.fen(), is_valid=True, errors=[])
    errors = [
        message for flag, message in _STATUS_MESSAGES.items() if status & flag
    ]
    if not errors:
        errors = ["invalid position"]
    return FenValidationResult(fen=text, is_valid=False, errors=errors)
