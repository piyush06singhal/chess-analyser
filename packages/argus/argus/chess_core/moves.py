"""Move validation (SAN and UCI) against a given position."""

from __future__ import annotations

import chess
from pydantic import BaseModel

from argus.chess_core.fen import validate_fen
from argus.shared.errors import InvalidFenError


class MoveValidationResult(BaseModel):
    """Outcome of validating a move against a position."""

    fen: str
    move_input: str
    is_valid: bool
    normalized_san: str | None = None
    normalized_uci: str | None = None
    error: str | None = None


def _board_for(fen: str) -> chess.Board:
    validation = validate_fen(fen)
    if not validation.is_valid:
        raise InvalidFenError(
            f"Invalid FEN: {'; '.join(validation.errors)}",
            details={"fen": fen, "errors": validation.errors},
        )
    return chess.Board(validation.fen)


def validate_san(fen: str, san: str) -> MoveValidationResult:
    """Validate a SAN move against ``fen``.

    Returns the normalized SAN and its UCI form when valid. Common lax forms
    (e.g. ``E4`` or ``nf3``) are normalized to standard SAN before parsing;
    illegal moves remain invalid under every normalization attempt.
    """
    board = _board_for(fen)
    text = san.strip()
    move, error = _parse_san_lenient(board, text)
    if move is None:
        return MoveValidationResult(
            fen=fen, move_input=san, is_valid=False, error=f"Invalid SAN move: {error}"
        )
    return MoveValidationResult(
        fen=fen,
        move_input=san,
        is_valid=True,
        normalized_san=board.san(move),
        normalized_uci=move.uci(),
    )


def _parse_san_lenient(board: chess.Board, text: str):
    """Parse SAN, retrying common lax forms; returns (move, error_message)."""
    candidates = [text]
    if text and text[0] in "KQRBN":
        candidates.append(text[0] + text[1:].lower())
    elif text and text[0].lower() in "kqrbn":
        candidates.append(text[0].upper() + text[1:].lower())
    else:
        candidates.append(text.lower())
    last_error: ValueError | None = None
    for candidate in candidates:
        try:
            return board.parse_san(candidate), None
        except ValueError as exc:
            last_error = exc
    return None, last_error


def validate_uci(fen: str, uci: str) -> MoveValidationResult:
    """Validate a UCI move against ``fen``.

    Returns the normalized UCI and its SAN form when valid. Null moves are
    rejected.
    """
    board = _board_for(fen)
    text = uci.strip()
    try:
        move = chess.Move.from_uci(text)
    except ValueError as exc:
        return MoveValidationResult(
            fen=fen, move_input=uci, is_valid=False, error=f"Invalid UCI move: {exc}"
        )
    if move not in board.legal_moves:
        return MoveValidationResult(
            fen=fen, move_input=uci, is_valid=False, error=f"Illegal move '{text}' in position"
        )
    return MoveValidationResult(
        fen=fen,
        move_input=uci,
        is_valid=True,
        normalized_san=board.san(move),
        normalized_uci=move.uci(),
    )
