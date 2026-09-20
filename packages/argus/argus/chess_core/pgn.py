"""PGN parsing and validation built on python-chess's PGN reader.

Side variations (RAVs) are not analyzed: only the main line of each game is
extracted. Games containing illegal or unreadable moves are rejected with
``InvalidPgnError`` — no partial game is presented as valid.
"""

from __future__ import annotations

import io

import chess
import chess.pgn
from pydantic import BaseModel, Field

from argus.chess_core.models import (
    Color,
    Game,
    GameMove,
    GameResult,
    OpeningInfo,
    PlayerInfo,
    parse_pgn_date,
    parse_time_control,
)
from argus.shared.errors import InvalidPgnError

DEFAULT_MAX_PLIES = 1000


class PgnValidationResult(BaseModel):
    """Outcome of validating a PGN string without keeping the parsed games."""

    is_valid: bool
    game_count: int
    errors: list[str] = Field(default_factory=list)


def parse_games(pgn_text: str, *, max_plies: int | None = DEFAULT_MAX_PLIES) -> list[Game]:
    """Parse every game in a PGN string into standardized ``Game`` objects.

    Args:
        pgn_text: Raw PGN text (may contain multiple games).
        max_plies: Safety guard against oversized games; ``None`` disables it.

    Raises:
        InvalidPgnError: when the text is empty, contains no readable game,
            includes illegal moves, or a game exceeds ``max_plies``.
    """
    if not (pgn_text or "").strip():
        raise InvalidPgnError("PGN text is empty")
    stream = io.StringIO(pgn_text)
    games: list[Game] = []
    while True:
        try:
            game = chess.pgn.read_game(stream)
        except Exception as exc:  # noqa: BLE001 — the PGN reader raises arbitrary errors on malformed input
            raise InvalidPgnError(f"Failed to parse PGN: {exc}") from exc
        if game is None:
            break
        if game.errors:
            raise InvalidPgnError(
                "PGN contains illegal or unreadable moves",
                details={"errors": [str(error) for error in game.errors[:5]]},
            )
        if not game.variations:
            # ARGUS imports games to analyze them; a game without moves cannot
            # be analyzed, so header-only PGNs are rejected explicitly.
            raise InvalidPgnError("Game contains no moves")
        games.append(_convert_game(game, max_plies))
    if not games:
        raise InvalidPgnError("No games found in PGN text")
    return games


def parse_first_game(pgn_text: str, *, max_plies: int | None = DEFAULT_MAX_PLIES) -> Game:
    """Parse and return the first game of a PGN string."""
    return parse_games(pgn_text, max_plies=max_plies)[0]


def validate_pgn(pgn_text: str) -> PgnValidationResult:
    """Validate a PGN string; returns issues instead of raising."""
    try:
        games = parse_games(pgn_text)
    except InvalidPgnError as exc:
        return PgnValidationResult(
            is_valid=False,
            game_count=0,
            errors=[exc.message, *exc.details.get("errors", [])],
        )
    return PgnValidationResult(is_valid=True, game_count=len(games))


def _convert_game(game: chess.pgn.Game, max_plies: int | None) -> Game:
    headers = game.headers

    def _header(key: str) -> str | None:
        # python-chess fills the seven-tag roster with "?" when absent.
        value = headers.get(key)
        if value is None or value == "?":
            return None
        return value

    def _rating(key: str) -> int | None:
        raw = _header(key)
        return int(raw) if raw is not None and raw.isdigit() else None

    white = PlayerInfo(name=_header("White") or "Unknown", title=_header("WhiteTitle"))
    black = PlayerInfo(name=_header("Black") or "Unknown", title=_header("BlackTitle"))

    result = GameResult.UNKNOWN
    result_raw = _header("Result")
    if result_raw is not None:
        try:
            result = GameResult(result_raw)
        except ValueError:
            result = GameResult.UNKNOWN

    board = game.board()  # honours SetUp/FEN headers when present
    initial_fen = board.fen()

    moves: list[GameMove] = []
    ply = 0
    node: chess.pgn.GameNode = game
    while node.variations:
        if max_plies is not None and ply >= max_plies:
            raise InvalidPgnError(
                f"Game exceeds the maximum supported length of {max_plies} plies"
            )
        next_node = node.variations[0]  # main line only
        move = next_node.move
        if move is None:  # defensive: a variation without a move
            break
        fen_before = board.fen()
        move_number = board.fullmove_number
        color = Color.WHITE if board.turn == chess.WHITE else Color.BLACK
        san = board.san(move)
        board.push(move)
        ply += 1
        moves.append(
            GameMove(
                ply=ply,
                move_number=move_number,
                color=color,
                san=san,
                uci=move.uci(),
                fen_before=fen_before,
                fen_after=board.fen(),
            )
        )
        node = next_node

    has_opening_headers = any(_header(key) for key in ("ECO", "Opening", "Variation"))
    opening = OpeningInfo(
        eco_code=_header("ECO"),
        name=_header("Opening"),
        variation=_header("Variation"),
        source="pgn_header" if has_opening_headers else None,
    )

    return Game(
        white_player=white,
        black_player=black,
        white_rating=_rating("WhiteElo"),
        black_rating=_rating("BlackElo"),
        result=result,
        date=_header("Date"),
        date_iso=parse_pgn_date(_header("Date")),
        event=_header("Event"),
        site=_header("Site"),
        time_control=parse_time_control(_header("TimeControl")),
        opening=opening,
        moves=moves,
        initial_position=initial_fen,
        final_position=board.fen(),
    )
