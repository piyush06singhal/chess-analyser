"""Tests for raw feature extraction against known positions."""

from __future__ import annotations

import chess

from argus.analysis.features.extractor import (
    extract_position_features,
    extract_position_features_from_fen,
    phase_piece_material,
)

from tests.conftest import START_FEN


class TestMaterialFeatures:
    def test_start_position_is_balanced(self):
        features = extract_position_features_from_fen(START_FEN)
        assert features.material_balance == 0
        assert features.material_white == features.material_black
        # Standard start: 8 pawns (800) + 2N (600) + 2B (600) + 2R (1000) + Q (900)
        assert features.material_white == 3900

    def test_material_advantage_detected(self):
        # White is missing a knight: 3900 - 300 = 3600.
        features = extract_position_features_from_fen(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/R1BQKBNR w KQkq - 0 1"
        )
        assert features.material_balance == -300

    def test_captures_change_material(self):
        board = chess.Board(START_FEN)
        board.push_san("e4")
        board.push_san("d5")
        board.push_san("exd5")
        features = extract_position_features(board)
        assert features.material_balance == 100  # White won a pawn


class TestMobilityAndSafety:
    def test_start_mobility_is_20(self):
        features = extract_position_features_from_fen(START_FEN)
        assert features.mobility_white == 20
        assert features.mobility_black == 20

    def test_pawn_shield_at_start_is_3(self):
        features = extract_position_features_from_fen(START_FEN)
        assert features.king_safety_white == 3
        assert features.king_safety_black == 3

    def test_no_hanging_pieces_at_start(self):
        features = extract_position_features_from_fen(START_FEN)
        assert features.hanging_pieces_white == 0
        assert features.hanging_pieces_black == 0
        assert features.checkers == 0
        assert features.white_in_check is False

    def test_check_is_detected(self):
        features = extract_position_features_from_fen(
            "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
        )
        assert features.white_in_check is True
        assert features.checkers == 1


class TestPawnStructure:
    def test_isolated_pawn_detected(self):
        # White pawns a2, c2 with no b2 pawn and no d2 pawn: a2 and c2 have no
        # friendly pawn on either adjacent file → both isolated (f2/g2/h2 chain is not).
        features = extract_position_features_from_fen(
            "rnbqkbnr/pppppppp/8/8/8/8/P1P1PPPP/RNBQKBNR w KQkq - 0 1"
        )
        assert features.isolated_pawns_white == 2

    def test_doubled_pawns_detected(self):
        # Two white pawns on the e-file (e2 and e4), d2 missing to keep 8 pawns.
        features = extract_position_features_from_fen(
            "rnbqkbnr/pppp1ppp/8/8/4P3/8/PPP1PPPP/RNBQKBNR w KQkq - 0 1"
        )
        assert features.doubled_pawns_white == 1

    def test_passed_pawn_detected(self):
        # White pawn on e5 with no black pawns on d/e/f files ahead of it
        # (black d7, e7, f7 removed; a7/b7/c7/g7/h7 remain).
        features = extract_position_features_from_fen(
            "rnbqkbnr/ppp3pp/8/4P3/8/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1"
        )
        assert features.passed_pawns_white == 1


class TestDevelopmentAndCenter:
    def test_undeveloped_at_start(self):
        features = extract_position_features_from_fen(START_FEN)
        # 2 knights + 2 bishops + 2 rooks + queen = 7 pieces on original squares
        assert features.undeveloped_pieces_white == 7
        assert features.undeveloped_pieces_black == 7

    def test_development_after_e4(self):
        board = chess.Board(START_FEN)
        board.push_san("e4")
        features = extract_position_features(board)
        assert features.center_occupied_white == 1


class TestPhaseInputs:
    def test_phase_material_at_start(self):
        board = chess.Board(START_FEN)
        assert phase_piece_material(board) == 2 * (2 * 3 + 2 * 3 + 2 * 5 + 9)  # 62

    def test_endgame_has_less_material(self):
        board = chess.Board("8/5k2/8/8/8/8/5K2/4R3 w - - 0 1")
        assert phase_piece_material(board) == 5

    def test_features_are_deterministic(self):
        first = extract_position_features_from_fen(START_FEN)
        second = extract_position_features_from_fen(START_FEN)
        assert first.model_dump() == second.model_dump()
