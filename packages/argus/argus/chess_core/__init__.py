"""ARGUS chess core: pure chess data handling (FEN, PGN, moves, positions).

Deliberately free of engine, LLM, database, and UI logic so the rest of the
platform consumes standardized chess data through clean interfaces.
"""

from argus.chess_core.fen import FenValidationResult, validate_fen
from argus.chess_core.models import (
    Color,
    Game,
    GameMove,
    GameResult,
    OpeningInfo,
    PlayerInfo,
    TimeControlInfo,
)
from argus.chess_core.moves import MoveValidationResult, validate_san, validate_uci
from argus.chess_core.pgn import (
    PgnValidationResult,
    parse_first_game,
    parse_games,
    validate_pgn,
)
from argus.chess_core.positions import PositionSnapshot, serialize_position

__all__ = [
    "Color",
    "FenValidationResult",
    "Game",
    "GameMove",
    "GameResult",
    "MoveValidationResult",
    "OpeningInfo",
    "PgnValidationResult",
    "PlayerInfo",
    "PositionSnapshot",
    "TimeControlInfo",
    "parse_first_game",
    "parse_games",
    "serialize_position",
    "validate_fen",
    "validate_pgn",
    "validate_san",
    "validate_uci",
]
