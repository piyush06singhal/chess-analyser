"""Game-import abstraction.

Every source of chess games (pasted PGN, uploaded PGN file, and future
platform APIs such as Chess.com or Lichess) implements the same
:class:`GameImporter` contract and produces the same internal ``Game``
representation. Adding a source therefore never changes the core game model
or the downstream analysis pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field

from argus.chess_core.models import Game


class ImportSource(str, Enum):
    """Where a game came from. Platform sources are declared now so the
    architecture is ready, but only the PGN sources are implemented in Phase 2.
    """

    PGN_TEXT = "pgn_text"
    PGN_FILE = "pgn_file"
    CHESS_COM = "chess_com"
    LICHESS = "lichess"


class IssueType(str, Enum):
    """Machine-readable categories of PGN validation problems."""

    EMPTY_PGN = "EMPTY_PGN"
    NO_GAMES = "NO_GAMES"
    MALFORMED_PGN = "MALFORMED_PGN"
    INVALID_MOVE = "INVALID_MOVE"
    ILLEGAL_MOVE = "ILLEGAL_MOVE"
    INCOMPLETE_GAME = "INCOMPLETE_GAME"
    RESULT_MISMATCH = "RESULT_MISMATCH"
    GAME_TOO_LONG = "GAME_TOO_LONG"
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"


class ValidationIssue(BaseModel):
    """A single, user-presentable validation problem (never a stack trace)."""

    type: IssueType
    message: str
    game_index: int | None = Field(default=None, description="0-based index of the offending game")
    move_number: int | None = None
    ply: int | None = None


class ValidationReport(BaseModel):
    """Outcome of validating a PGN payload without importing it."""

    is_valid: bool
    game_count: int = 0
    ply_count: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def error_messages(self) -> list[str]:
        return [issue.message for issue in self.issues]


class ImportResult(BaseModel):
    """A successfully imported payload: the parsed games plus any warnings."""

    source: ImportSource
    games: list[Game]
    warnings: list[str] = Field(default_factory=list)


class GameImporter(ABC):
    """Common interface for all game sources."""

    source: ImportSource

    @abstractmethod
    def validate(self, payload: str) -> ValidationReport:
        """Validate a payload without persisting or raising."""

    @abstractmethod
    def import_games(self, payload: str) -> ImportResult:
        """Parse a payload into internal games.

        Raises:
            InvalidPgnError: when the payload is not a valid, analyzable game.
        """

    def import_first(self, payload: str) -> Game:
        """Parse and return the first game of a payload."""
        return self.import_games(payload).games[0]
