"""PGN importer: the concrete :class:`GameImporter` for PGN text and files."""

from __future__ import annotations

from argus.chess_core.pgn import parse_games
from argus.importing.base import GameImporter, ImportResult, ImportSource, ValidationReport
from argus.importing.validation import validate_pgn_detailed
from argus.shared.errors import InvalidPgnError


class PgnImporter(GameImporter):
    """Imports games from raw PGN text (pasted or read from a file)."""

    def __init__(self, source: ImportSource = ImportSource.PGN_TEXT) -> None:
        self.source = source

    def validate(self, payload: str) -> ValidationReport:
        return validate_pgn_detailed(payload)

    def import_games(self, payload: str) -> ImportResult:
        report = self.validate(payload)
        if not report.is_valid:
            raise InvalidPgnError(
                report.issues[0].message if report.issues else "Invalid PGN",
                details={
                    "issues": [issue.model_dump() for issue in report.issues],
                },
            )
        return ImportResult(source=self.source, games=parse_games(payload))
