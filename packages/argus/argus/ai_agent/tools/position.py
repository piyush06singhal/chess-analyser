"""Position tools: what the board actually contains.

``inspect_position`` computes board facts with python-chess — whose move it is,
how many legal moves exist, material, checks, castling rights. These are *derived
features*, not engine output, and the evidence they produce is tagged as such.
The distinction matters: "White has two extra pawns" is arithmetic on a FEN, while
"White is winning" is an evaluation, and only an engine may say the second one.

``get_current_position`` is the board-awareness primitive. It answers "where am I
looking?" from the context — the active game and the selected ply — so the user
never has to paste a FEN (spec §7). This is what makes "why is this bad?" work.
"""

from __future__ import annotations

from typing import Any

import chess

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders
from argus.shared.errors import InvalidFenError, NotFoundError

#: Material values in centipawns — used only for *arithmetic*, never an evaluation.
_PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

_PIECE_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


def parse_board(fen: str) -> chess.Board:
    """Parse a FEN, raising a domain error rather than a chess-library error."""
    try:
        return chess.Board(fen)
    except ValueError as exc:
        raise InvalidFenError(f"'{fen}' is not a valid FEN: {exc}") from exc


def board_facts(fen: str) -> dict[str, Any]:
    """Deterministic, engine-free facts about a position."""
    board = parse_board(fen)
    legal = list(board.legal_moves)
    placement: dict[str, dict[str, int]] = {"white": {}, "black": {}}
    material = {"white": 0, "black": 0}
    for _square, piece in board.piece_map().items():
        colour = "white" if piece.color == chess.WHITE else "black"
        name = _PIECE_NAMES[piece.piece_type]
        placement[colour][name] = placement[colour].get(name, 0) + 1
        material[colour] += _PIECE_VALUES[piece.piece_type]
    facts: dict[str, Any] = {
        "fen": board.fen(),
        "side_to_move": "white" if board.turn == chess.WHITE else "black",
        "fullmove_number": board.fullmove_number,
        "legal_move_count": len(legal),
        "is_check": board.is_check(),
        "is_checkmate": board.is_checkmate(),
        "is_stalemate": board.is_stalemate(),
        "is_game_over": board.is_game_over(),
        "white_can_castle": board.has_kingside_castling_rights(chess.WHITE)
        or board.has_queenside_castling_rights(chess.WHITE),
        "black_can_castle": board.has_kingside_castling_rights(chess.BLACK)
        or board.has_queenside_castling_rights(chess.BLACK),
        "piece_counts": placement,
        # Arithmetic on the board, not an evaluation — hence the explicit name.
        "material_balance_cp_white_minus_black": material["white"] - material["black"],
    }
    if board.is_checkmate():
        facts["terminal_reason"] = "checkmate"
    elif board.is_stalemate():
        facts["terminal_reason"] = "stalemate"
    return facts


def summarize_facts(facts: dict[str, Any]) -> str:
    """A one-line factual summary of a position, with no chess judgement."""
    balance = facts["material_balance_cp_white_minus_black"]
    if balance == 0:
        material = "material level"
    else:
        side = "White" if balance > 0 else "Black"
        material = f"{side} up {abs(balance) / 100:.1f} in material value"
    text = (
        f"{facts['side_to_move'].capitalize()} to move, "
        f"{facts['legal_move_count']} legal move(s), {material}"
    )
    if facts["is_check"]:
        text += ", in check"
    if facts.get("terminal_reason"):
        text += f", game over ({facts['terminal_reason']})"
    return text + "."


def build_position_tools(providers: AgentProviders) -> list[Tool]:
    """Position tools.

    ``get_current_position`` resolves the board from the *context*: it prefers the
    FEN the user is looking at, then the stored position at the selected ply, so a
    question about "this move" needs no pasted data.
    """

    def inspect_position(_context: AgentContext, fen: str) -> dict[str, Any]:
        facts = board_facts(fen)
        return {"facts": facts, "summary": summarize_facts(facts)}

    def get_current_position(context: AgentContext) -> dict[str, Any]:
        if context.current_fen:
            facts = board_facts(context.current_fen)
            return {
                "fen": context.current_fen,
                "game_id": context.active_game_id,
                "ply": context.selected_ply,
                "move": context.selected_move_san,
                "source": "the position the user is viewing",
                "facts": facts,
                "summary": summarize_facts(facts),
            }
        if not context.active_game_id:
            raise NotFoundError(
                "No active game or position in this conversation. Open a game and "
                "select a move, or give me a FEN."
            )
        if providers.move_analysis is None or context.selected_ply is None:
            raise NotFoundError(
                "No move is selected in this game, so Caissa does not know which "
                "position you mean. Select a move, or give me a FEN."
            )
        analysis = providers.move_analysis(context.active_game_id, context.selected_ply)
        if not analysis:
            raise NotFoundError(
                f"Caissa has no stored position for ply {context.selected_ply} of this game."
            )
        fen = str(analysis.get("fen_before") or "")
        if not fen:
            raise NotFoundError("That move row has no stored position.")
        facts = board_facts(fen)
        return {
            "fen": fen,
            "game_id": context.active_game_id,
            "ply": context.selected_ply,
            "move": analysis.get("san") or context.selected_move_san,
            "source": f"the position before ply {context.selected_ply}",
            "facts": facts,
            "summary": summarize_facts(facts),
        }

    return [
        Tool(
            name="inspect_position",
            description=(
                "Deterministic board facts for a FEN: side to move, legal move count, "
                "material counts, check / castle / game-over state. NOT an engine "
                "evaluation — use analyze_position for that."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"fen": {"type": "string", "minLength": 1}},
                    "required": ["fen"],
                },
                outputs=("facts", "summary"),
            ),
            permission=ToolPermission.ANY,
            handler=inspect_position,
            tags=("position",),
        ),
        Tool(
            name="get_current_position",
            description=(
                "The position the user is currently looking at, resolved from the "
                "conversation context (active game plus selected move). Call this "
                "BEFORE asking the user for a FEN."
            ),
            schema=ToolSchema(
                parameters={"type": "object", "properties": {}, "required": []},
                outputs=("fen", "game_id", "ply", "move", "facts", "summary"),
            ),
            permission=ToolPermission.GAME_CONTEXT,
            handler=get_current_position,
            tags=("position", "context"),
        ),
    ]


__all__ = ["board_facts", "build_position_tools", "parse_board", "summarize_facts"]
