"""Tests for the game-import subsystem and structured PGN validation.

Every PGN used here is a known-legal (or deliberately known-illegal) game; the
expected outcomes come from the python-chess reference implementation, not
from fabricated data.
"""

from __future__ import annotations

import pytest

from argus.chess_core.models import GameResult
from argus.importing import (
    ImportSource,
    IssueType,
    PgnImporter,
    available_sources,
    planned_sources,
    resolve_importer,
)
from argus.importing.validation import validate_pgn_detailed
from argus.shared.errors import InvalidPgnError, UnsupportedSourceError

from tests.conftest import (
    CASTLING_PGN,
    CHECK_PGN,
    DRAW_PGN,
    EN_PASSANT_PGN,
    ILLEGAL_MOVE_PGN,
    INCOMPLETE_PGN,
    MALFORMED_PGN,
    OPERA_GAME_PGN,
    PROMOTION_PGN,
    SCHOLARS_MATE_PGN,
)

VALID_GAMES = {
    "opera": OPERA_GAME_PGN,
    "castling": CASTLING_PGN,
    "en_passant": EN_PASSANT_PGN,
    "promotion": PROMOTION_PGN,
    "check": CHECK_PGN,
    "scholars_mate": SCHOLARS_MATE_PGN,
    "draw": DRAW_PGN,
}


class TestValidationAcceptsLegalGames:
    @pytest.mark.parametrize("name", list(VALID_GAMES))
    def test_legal_game_is_valid(self, name: str) -> None:
        report = validate_pgn_detailed(VALID_GAMES[name])
        assert report.is_valid is True
        assert report.game_count == 1
        assert report.ply_count > 0
        assert report.issues == []


class TestValidationRejectsBadInput:
    def test_empty_pgn(self) -> None:
        report = validate_pgn_detailed("   ")
        assert report.is_valid is False
        assert report.issues[0].type is IssueType.EMPTY_PGN

    def test_illegal_move_reports_move_number(self) -> None:
        report = validate_pgn_detailed(ILLEGAL_MOVE_PGN)
        assert report.is_valid is False
        issue = report.issues[0]
        assert issue.type is IssueType.ILLEGAL_MOVE
        assert issue.move_number == 3
        assert issue.ply == 5

    def test_header_only_game_is_incomplete(self) -> None:
        report = validate_pgn_detailed(INCOMPLETE_PGN)
        assert report.is_valid is False
        assert report.issues[0].type is IssueType.INCOMPLETE_GAME

    def test_free_text_is_malformed(self) -> None:
        report = validate_pgn_detailed(MALFORMED_PGN)
        assert report.is_valid is False
        assert report.issues[0].type in {IssueType.MALFORMED_PGN, IssueType.NO_GAMES}

    def test_result_mismatch_is_detected(self) -> None:
        # The game ends in a white checkmate but the header claims a black win.
        wrong = SCHOLARS_MATE_PGN.replace('"1-0"', '"0-1"').replace("1-0", "0-1")
        report = validate_pgn_detailed(wrong)
        assert report.is_valid is False
        assert report.issues[0].type is IssueType.RESULT_MISMATCH

    def test_overlong_game_is_rejected(self) -> None:
        report = validate_pgn_detailed(OPERA_GAME_PGN, max_plies=10)
        assert report.is_valid is False
        assert report.issues[0].type is IssueType.GAME_TOO_LONG

    def test_messages_are_human_readable(self) -> None:
        report = validate_pgn_detailed(ILLEGAL_MOVE_PGN)
        message = report.issues[0].message
        assert "move 3" in message.lower()
        # Never leak a Python exception repr.
        assert "Traceback" not in message and "Error(" not in message


class TestPgnImporter:
    def test_import_valid_game(self) -> None:
        result = PgnImporter().import_games(SCHOLARS_MATE_PGN)
        assert result.source is ImportSource.PGN_TEXT
        assert len(result.games) == 1
        assert result.games[0].result is GameResult.WHITE_WINS

    def test_import_invalid_raises_with_details(self) -> None:
        with pytest.raises(InvalidPgnError) as excinfo:
            PgnImporter().import_games(ILLEGAL_MOVE_PGN)
        issues = excinfo.value.details.get("issues")
        assert issues and issues[0]["type"] == IssueType.ILLEGAL_MOVE.value

    def test_file_source_is_tracked(self) -> None:
        result = PgnImporter(ImportSource.PGN_FILE).import_games(CHECK_PGN)
        assert result.source is ImportSource.PGN_FILE


class TestRegistry:
    def test_available_and_planned_sources(self) -> None:
        # Both platform sources are implemented: their PGN is fetched by
        # argus.importing.chesscom / argus.importing.lichess and parsed by the
        # same importer. Nothing is left declared-only.
        assert available_sources() == ["chess_com", "lichess", "pgn_file", "pgn_text"]
        assert planned_sources() == []

    def test_resolve_known_source(self) -> None:
        assert isinstance(resolve_importer("pgn_text"), PgnImporter)

    def test_chesscom_parses_pgn_with_its_own_source(self) -> None:
        importer = resolve_importer("chess_com")
        assert isinstance(importer, PgnImporter)
        result = importer.import_games(CHECK_PGN)
        assert result.source is ImportSource.CHESS_COM
        assert result.games[0].move_count > 0

    def test_lichess_parses_pgn_with_its_own_source(self) -> None:
        importer = resolve_importer("lichess")
        assert isinstance(importer, PgnImporter)
        result = importer.import_games(CHECK_PGN)
        assert result.source is ImportSource.LICHESS
        assert result.games[0].move_count > 0

    def test_resolve_unknown_source(self) -> None:
        with pytest.raises(UnsupportedSourceError):
            resolve_importer("nope")
