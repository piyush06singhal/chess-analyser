"""Game import subsystem: source-agnostic ingestion of chess games."""

from argus.importing.base import (
    GameImporter,
    ImportResult,
    ImportSource,
    IssueType,
    ValidationIssue,
    ValidationReport,
)
from argus.importing.chesscom import (
    ChessComArchive,
    ChessComClient,
    ChessComGame,
    ChessComMonth,
    ChessComProfile,
    normalize_username,
)
from argus.importing.lichess import (
    LichessClient,
    LichessGame,
    LichessMonth,
    LichessMonthGames,
    LichessProfile,
    month_bounds_ms,
    months_for_profile,
)
from argus.importing.pgn_importer import PgnImporter
from argus.importing.registry import available_sources, planned_sources
from argus.importing.registry import get_importer as resolve_importer
from argus.importing.validation import validate_pgn_detailed

__all__ = [
    "ChessComArchive",
    "ChessComClient",
    "ChessComGame",
    "ChessComMonth",
    "ChessComProfile",
    "GameImporter",
    "ImportResult",
    "ImportSource",
    "IssueType",
    "LichessClient",
    "LichessGame",
    "LichessMonth",
    "LichessMonthGames",
    "LichessProfile",
    "PgnImporter",
    "ValidationIssue",
    "ValidationReport",
    "available_sources",
    "month_bounds_ms",
    "months_for_profile",
    "normalize_username",
    "planned_sources",
    "resolve_importer",
    "validate_pgn_detailed",
]
