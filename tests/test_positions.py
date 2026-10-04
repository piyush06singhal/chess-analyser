"""Tests for canonical game-position generation.

Position generation must be chess-correct for the full rule set (castling,
en passant, promotion, captures, check, checkmate, stalemate/draws) and must
produce a sequence that the Stockfish abstraction can consume directly.
"""

from __future__ import annotations

import chess
import pytest

from argus.chess_core.models import Color
from argus.chess_core.pgn import parse_first_game
from argus.chess_core.positions import generate_game_positions

from tests.conftest import (
    CASTLING_PGN,
    CHECK_PGN,
    DRAW_PGN,
    EN_PASSANT_PGN,
    OPERA_GAME_PGN,
    PROMOTION_PGN,
    SCHOLARS_MATE_PGN,
    START_FEN,
)

ALL_PGNS = [
    OPERA_GAME_PGN,
    CASTLING_PGN,
    EN_PASSANT_PGN,
    PROMOTION_PGN,
    CHECK_PGN,
    SCHOLARS_MATE_PGN,
    DRAW_PGN,
]


class TestPositionSequence:
    @pytest.mark.parametrize("pgn", ALL_PGNS)
    def test_sequence_length_is_moves_plus_one(self, pgn: str) -> None:
        game = parse_first_game(pgn)
        positions = generate_game_positions(game)
        assert len(positions) == game.move_count + 1

    def test_ply_zero_is_the_initial_position(self) -> None:
        game = parse_first_game(OPERA_GAME_PGN)
        first = generate_game_positions(game)[0]
        assert first.ply == 0
        assert first.san is None
        assert first.uci is None
        assert first.previous_fen is None
        assert first.fen == START_FEN == game.initial_position

    def test_positions_are_contiguous_and_chain_correctly(self) -> None:
        game = parse_first_game(OPERA_GAME_PGN)
        positions = generate_game_positions(game)
        for previous, current in zip(positions, positions[1:]):
            assert current.ply == previous.ply + 1
            assert current.previous_fen == previous.resulting_fen
            assert current.fen == current.resulting_fen

    def test_side_to_move_alternates(self) -> None:
        game = parse_first_game(OPERA_GAME_PGN)
        positions = generate_game_positions(game)
        assert positions[0].side_to_move is Color.WHITE
        assert positions[1].side_to_move is Color.BLACK


class TestChessRulesRuleCorrectness:
    def test_castling_moves_the_king(self) -> None:
        game = parse_first_game(CASTLING_PGN)
        positions = generate_game_positions(game)
        kingside = next(p for p in positions if p.san == "O-O")
        board = chess.Board(kingside.fen)
        assert board.piece_at(chess.G1) == chess.Piece(chess.KING, chess.WHITE)
        assert board.piece_at(chess.F1) == chess.Piece(chess.ROOK, chess.WHITE)
        # White lost the castling right after moving the king.
        assert "K" not in board.castling_xfen()

    def test_en_passant_capture_removes_the_pawn(self) -> None:
        game = parse_first_game(EN_PASSANT_PGN)
        positions = generate_game_positions(game)
        ep = next(p for p in positions if p.san == "exd6")
        board = chess.Board(ep.fen)
        assert board.piece_at(chess.D6) == chess.Piece(chess.PAWN, chess.WHITE)
        # The captured black pawn was on d5 and is gone (en passant is not a
        # capture onto the destination square).
        assert board.piece_at(chess.D5) is None

    def test_promotion_creates_a_queen(self) -> None:
        game = parse_first_game(PROMOTION_PGN)
        positions = generate_game_positions(game)
        promo = next(p for p in positions if p.san == "bxa8=Q")
        board = chess.Board(promo.fen)
        assert board.piece_at(chess.A8) == chess.Piece(chess.QUEEN, chess.WHITE)

    def test_check_position_flags_check(self) -> None:
        game = parse_first_game(CHECK_PGN)
        positions = generate_game_positions(game)
        check = next(p for p in positions if p.san == "Bb5+")
        assert check.is_check is True
        assert check.is_checkmate is False

    def test_checkmate_is_terminal(self) -> None:
        game = parse_first_game(SCHOLARS_MATE_PGN)
        final = generate_game_positions(game)[-1]
        assert final.is_checkmate is True
        assert final.is_terminal is True
        assert final.terminal_reason == "checkmate"

    def test_drawn_result_is_preserved(self) -> None:
        game = parse_first_game(DRAW_PGN)
        positions = generate_game_positions(game)
        assert positions[-1].is_terminal is False  # resigned/drawn early, not terminal on board


class TestEngineConsumability:
    def test_every_fen_is_parseable_by_python_chess(self) -> None:
        game = parse_first_game(OPERA_GAME_PGN)
        for position in generate_game_positions(game):
            board = chess.Board(position.fen)  # raises on an invalid FEN
            assert board.fen() == position.fen
