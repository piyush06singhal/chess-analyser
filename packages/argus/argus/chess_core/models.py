"""Core chess domain models shared across the platform.

Fields the source PGN does not provide stay ``None`` — no values are ever
fabricated.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

import chess
from pydantic import BaseModel, Field


class GameResult(str, Enum):
    """Standard chess results as they appear in PGN files."""

    WHITE_WINS = "1-0"
    BLACK_WINS = "0-1"
    DRAW = "1/2-1/2"
    UNKNOWN = "*"


class Color(str, Enum):
    """Side to move / side that played a move."""

    WHITE = "white"
    BLACK = "black"


class PlayerInfo(BaseModel):
    """Player identity from PGN headers."""

    name: str
    title: str | None = None


class TimeControlInfo(BaseModel):
    """Time-control metadata parsed from the PGN ``TimeControl`` header."""

    raw: str | None = None
    initial_seconds: int | None = None
    increment_seconds: int | None = None


class OpeningInfo(BaseModel):
    """Opening metadata.

    Phase 1: detected from PGN headers when present. Opening detection from
    the move sequence itself is a placeholder for the analysis phase — the
    ``source`` field distinguishes the two once a detector exists.
    """

    eco_code: str | None = None
    name: str | None = None
    variation: str | None = None
    source: str | None = None


class GameMove(BaseModel):
    """A single move of a game with its surrounding positions."""

    ply: int = Field(ge=1, description="1-based ply index across the whole game")
    move_number: int = Field(ge=1, description="Full-move number as used in PGN notation")
    color: Color
    san: str
    uci: str
    fen_before: str
    fen_after: str


class Game(BaseModel):
    """A parsed, validated chess game with standardized metadata."""

    id: str | None = None
    white_player: PlayerInfo
    black_player: PlayerInfo
    white_rating: int | None = None
    black_rating: int | None = None
    result: GameResult = GameResult.UNKNOWN
    date: str | None = Field(default=None, description="Raw PGN date (may be partial)")
    date_iso: str | None = Field(default=None, description="ISO date when fully specified")
    event: str | None = None
    site: str | None = None
    time_control: TimeControlInfo = Field(default_factory=TimeControlInfo)
    opening: OpeningInfo = Field(default_factory=OpeningInfo)
    moves: list[GameMove] = Field(default_factory=list)
    initial_position: str = chess.STARTING_FEN
    final_position: str = chess.STARTING_FEN

    @property
    def move_count(self) -> int:
        return len(self.moves)


def parse_time_control(raw: str | None) -> TimeControlInfo:
    """Parse a PGN ``TimeControl`` header value.

    Supports the common ``initial+increment`` form (e.g. ``600+5``), a plain
    initial time (``3000``), the long ``moves/seconds`` form (``40/7200``) and
    dash/``?`` placeholders. The raw value is always preserved; unparseable
    values simply leave the numeric fields ``None``.
    """
    if not raw:
        return TimeControlInfo()
    info = TimeControlInfo(raw=raw)
    text = raw.strip()
    if text in {"-", "?"}:
        return info
    if "/" in text:
        first_segment = text.split(":")[0]
        seconds_part = first_segment.partition("/")[2]
        if seconds_part.isdigit():
            info.initial_seconds = int(seconds_part)
        return info
    if "+" in text:
        initial, increment = text.split("+", 1)
        if initial.isdigit():
            info.initial_seconds = int(initial)
        if increment.isdigit():
            info.increment_seconds = int(increment)
        return info
    if text.isdigit():
        info.initial_seconds = int(text)
    return info


def parse_pgn_date(raw: str | None) -> str | None:
    """Return an ISO date (YYYY-MM-DD) when the PGN date is fully specified.

    PGN dates may be partial (``2024.??.??``); partial dates return ``None``
    rather than an invented value.
    """
    if not raw:
        return None
    parts = raw.strip().split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        year, month, day = (int(part) for part in parts)
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None
    return None
