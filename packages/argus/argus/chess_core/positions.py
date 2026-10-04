"""Position representation and serialization."""

from __future__ import annotations

import chess
from pydantic import BaseModel, Field

from argus.chess_core.fen import validate_fen
from argus.chess_core.models import Color, Game, GamePositionState
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


def _terminal_reason(board: chess.Board) -> str | None:
    """Return a human- and machine-readable terminal reason, or ``None``.

    Only claims that are unconditionally terminal are reported (checkmate,
    stalemate, dead position / insufficient material, 50- and 75-move rules);
    threefold repetition is claimable rather than automatic and is left out.
    """
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    if board.is_insufficient_material():
        return "insufficient_material"
    if board.is_fifty_moves():
        return "fifty_moves"
    if board.is_seventyfive_moves():
        return "seventyfive_moves"
    if board.is_fivefold_repetition():
        return "fivefold_repetition"
    return None


def _state_from_board(
    board: chess.Board,
    *,
    ply: int,
    game_id: str | None,
    san: str | None,
    uci: str | None,
    previous_fen: str | None,
) -> GamePositionState:
    reason = _terminal_reason(board)
    return GamePositionState(
        game_id=game_id,
        ply=ply,
        move_number=board.fullmove_number,
        side_to_move=Color.WHITE if board.turn == chess.WHITE else Color.BLACK,
        fen=board.fen(),
        san=san,
        uci=uci,
        previous_fen=previous_fen,
        resulting_fen=board.fen(),
        is_check=board.is_check(),
        is_checkmate=board.is_checkmate(),
        is_stalemate=board.is_stalemate(),
        is_terminal=reason is not None,
        terminal_reason=reason,
    )


def generate_game_positions(game: Game) -> list[GamePositionState]:
    """Generate the full linear position sequence of a parsed game.

    Returns ``len(game.moves) + 1`` positions: the initial position (ply 0)
    followed by one position per move. Each entry's ``fen`` is the canonical
    engine input; the model duplicates no board-state logic — it replays the
    game through python-chess exactly as the engine abstraction does.

    Raises:
        InvalidFenError: when the game's initial position cannot be parsed.
    """
    try:
        board = chess.Board(game.initial_position)
    except ValueError as exc:
        raise InvalidFenError(
            f"Game initial position is not a valid FEN: {game.initial_position}",
            details={"fen": game.initial_position},
        ) from exc

    positions = [
        _state_from_board(
            board, ply=0, game_id=game.id, san=None, uci=None, previous_fen=None
        )
    ]
    for move in game.moves:
        previous_fen = board.fen()
        try:
            board.push(chess.Move.from_uci(move.uci))
        except ValueError as exc:
            raise InvalidFenError(
                f"Stored move {move.uci!r} at ply {move.ply} is not a legal move",
                details={"ply": move.ply, "uci": move.uci},
            ) from exc
        positions.append(
            _state_from_board(
                board,
                ply=move.ply,
                game_id=game.id,
                san=move.san,
                uci=move.uci,
                previous_fen=previous_fen,
            )
        )
    return positions
