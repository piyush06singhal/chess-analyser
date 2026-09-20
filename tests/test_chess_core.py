"""Tests for chess-core: FEN, PGN, move validation, results, serialization.

Expected values come from known chess positions and the python-chess reference
implementation — no fabricated move data.
"""

from __future__ import annotations

import chess
import pytest

from argus.chess_core.fen import validate_fen
from argus.chess_core.models import GameResult, parse_pgn_date, parse_time_control
from argus.chess_core.moves import validate_san, validate_uci
from argus.chess_core.pgn import parse_games, validate_pgn
from argus.chess_core.positions import serialize_position
from argus.shared.errors import InvalidFenError, InvalidPgnError

from tests.conftest import OPERA_GAME_PGN, START_FEN


class TestFenValidation:
    def test_start_position_is_valid(self):
        result = validate_fen(START_FEN)
        assert result.is_valid is True
        assert result.errors == []
        assert result.fen == START_FEN

    def test_fools_mate_is_valid(self):
        # 1. f3 e5 2. g4 Qh4# — the fastest possible checkmate.
        result = validate_fen("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
        assert result.is_valid is True

    def test_empty_fen_is_invalid(self):
        assert validate_fen("").is_valid is False
        assert validate_fen("   ").is_valid is False

    def test_garbage_fen_is_invalid(self):
        result = validate_fen("this is not a fen")
        assert result.is_valid is False
        assert result.errors

    def test_position_without_king_is_rejected(self):
        # Kings removed — structurally parseable but illegal.
        result = validate_fen("rnbq1bnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQ1BNR w KQkq - 0 1")
        assert result.is_valid is False
        assert any("king" in error for error in result.errors)

    def test_pawns_on_backrank_is_rejected(self):
        result = validate_fen("rnbqkbnr/PPPPPPPP/8/8/8/8/pppppppp/RNBQKBNR w KQkq - 0 1")
        assert result.is_valid is False

    def test_invalid_fen_never_raises(self):
        # validate_fen reports failures; callers decide how to surface them.
        for bad in ["", "8/8/8", "rnbqkbnr///w"]:
            assert validate_fen(bad).is_valid is False


class TestPgnParsing:
    def test_opera_game_parses_with_correct_metadata(self):
        game = parse_games(OPERA_GAME_PGN)[0]
        assert game.white_player.name == "Paul Morphy"
        assert "Duke Karl" in game.black_player.name
        assert game.result is GameResult.WHITE_WINS
        assert game.opening.eco_code == "C41"
        assert game.date == "1858.11.02"
        assert game.date_iso == "1858-11-02"
        assert game.move_count == 33  # 17 full moves, White's 17th included

    def test_opera_game_moves_are_consistent(self):
        game = parse_games(OPERA_GAME_PGN)[0]
        first, last = game.moves[0], game.moves[-1]
        assert first.san == "e4" and first.move_number == 1 and first.color.value == "white"
        assert last.san == "Rd8#"  # the known final mate
        assert chess.Board(last.fen_after).is_checkmate() is True
        # fen_after of each move equals fen_before of the next
        for previous, current in zip(game.moves, game.moves[1:]):
            assert previous.fen_after == current.fen_before

    def test_opera_game_initial_position_is_start(self):
        game = parse_games(OPERA_GAME_PGN)[0]
        assert game.initial_position == START_FEN

    def test_multi_game_pgn_parses_all_games(self):
        multi = OPERA_GAME_PGN + "\n\n" + OPERA_GAME_PGN.replace("Paul Morphy", "Someone Else")
        games = parse_games(multi)
        assert len(games) == 2
        assert games[1].white_player.name == "Someone Else"

    def test_empty_pgn_is_rejected(self):
        with pytest.raises(InvalidPgnError):
            parse_games("")
        with pytest.raises(InvalidPgnError):
            parse_games("   ")

    def test_text_without_moves_is_rejected(self):
        with pytest.raises(InvalidPgnError):
            parse_games('[Event "Just headers"]\n[Result "*"]')

    def test_illegal_moves_are_rejected(self):
        # 2. Kd2 is illegal in the position after 1. e4 e5? No — it is legal;
        # use a genuinely illegal move: moving the pinned-to-be knight like a rook.
        illegal = OPERA_GAME_PGN.replace("2. Nf3 d6", "2. Rg1 d6")
        with pytest.raises(InvalidPgnError):
            parse_games(illegal)

    def test_validate_pgn_reports_instead_of_raising(self):
        ok = validate_pgn(OPERA_GAME_PGN)
        assert ok.is_valid is True and ok.game_count == 1
        bad = validate_pgn("nonsense")
        assert bad.is_valid is False and bad.errors

    def test_max_plies_guard(self):
        with pytest.raises(InvalidPgnError):
            parse_games(OPERA_GAME_PGN, max_plies=10)

    def test_missing_headers_are_none_not_question_marks(self):
        minimal = '[Event "Test"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 *'
        game = parse_games(minimal)[0]
        assert game.site is None
        assert game.white_rating is None
        assert game.time_control.raw is None


class TestMoveValidation:
    def test_valid_san_is_normalized(self):
        result = validate_san(START_FEN, "e4")
        assert result.is_valid is True
        assert result.normalized_san == "e4"
        assert result.normalized_uci == "e2e4"

    def test_lax_san_is_normalized_to_standard(self):
        result = validate_san(START_FEN, "E4")
        assert result.is_valid is True
        assert result.normalized_san == "e4"

    def test_illegal_san_is_reported(self):
        result = validate_san(START_FEN, "Ke2")
        assert result.is_valid is False
        assert result.error

    def test_invalid_fen_raises_for_move_validation(self):
        with pytest.raises(InvalidFenError):
            validate_san("not a fen", "e4")
        with pytest.raises(InvalidFenError):
            validate_uci("not a fen", "e2e4")

    def test_valid_uci_is_normalized(self):
        result = validate_uci(START_FEN, "g1f3")
        assert result.is_valid is True
        assert result.normalized_uci == "g1f3"
        assert result.normalized_san == "Nf3"

    def test_illegal_uci_is_reported(self):
        result = validate_uci(START_FEN, "e2e5")
        assert result.is_valid is False
        assert "Illegal" in (result.error or "")

    def test_null_move_is_rejected(self):
        result = validate_uci(START_FEN, "0000")
        assert result.is_valid is False


class TestResultAndMetadataParsing:
    def test_result_parsing(self):
        assert GameResult("1-0") is GameResult.WHITE_WINS
        assert GameResult("0-1") is GameResult.BLACK_WINS
        assert GameResult("1/2-1/2") is GameResult.DRAW
        assert GameResult("*") is GameResult.UNKNOWN

    def test_time_control_initial_plus_increment(self):
        info = parse_time_control("600+5")
        assert info.initial_seconds == 600
        assert info.increment_seconds == 5
        assert info.raw == "600+5"

    def test_time_control_plain_initial(self):
        info = parse_time_control("3000")
        assert info.initial_seconds == 3000
        assert info.increment_seconds is None

    def test_time_control_long_form(self):
        info = parse_time_control("40/7200")
        assert info.initial_seconds == 7200

    def test_time_control_placeholders(self):
        assert parse_time_control("-").initial_seconds is None
        assert parse_time_control("?").initial_seconds is None
        assert parse_time_control(None).raw is None

    def test_pgn_date_partial_returns_none(self):
        assert parse_pgn_date("2024.??.??") is None
        assert parse_pgn_date("????.??.??") is None
        assert parse_pgn_date("2024.02.03") == "2024-02-03"
        assert parse_pgn_date("2024.13.99") is None  # impossible date


class TestPositionSerialization:
    def test_start_position_serializes(self):
        snapshot = serialize_position(START_FEN)
        assert snapshot.turn.value == "white"
        assert snapshot.castling_rights == "KQkq"
        assert snapshot.halfmove_clock == 0
        assert snapshot.fullmove_number == 1
        assert snapshot.piece_placement["e1"] == "K"
        assert snapshot.piece_placement["e8"] == "k"
        assert len(snapshot.piece_placement) == 32

    def test_serialization_roundtrip_fen(self):
        fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
        snapshot = serialize_position(fen)
        assert snapshot.fen == fen

    def test_invalid_fen_raises(self):
        with pytest.raises(InvalidFenError):
            serialize_position("garbage")
