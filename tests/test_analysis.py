"""Tests for game-phase classification, classification, reports, ML, and agent tools."""

from __future__ import annotations

import chess

from argus.analysis.classification import (
    ClassificationThresholds,
    MoveClassification,
    MoveClassificationInput,
    classify_move,
)
from argus.analysis.phase import (
    GamePhase,
    PhaseThresholds,
    classify_position,
    classify_position_fen,
)

from tests.conftest import START_FEN

# --- phase classification -------------------------------------------------------


class TestGamePhase:
    def test_start_position_is_opening(self):
        assert classify_position_fen(START_FEN) is GamePhase.OPENING

    def test_queenless_minor_piece_position_is_middlegame(self):
        # Queens traded, pieces developed: middlegame by material, not move count.
        fen = "1r4k1/pppp1ppp/2n2n2/4p3/2B1P3/2N2N2/PPPP1PPP/4K1R1 w - - 4 6"
        assert classify_position_fen(fen) is GamePhase.MIDDLEGAME

    def test_sparse_endgame_is_endgame(self):
        fen = "8/5k2/8/8/8/8/5K2/4R3 w - - 0 1"
        assert classify_position_fen(fen) is GamePhase.ENDGAME

    def test_interface_accepts_custom_thresholds(self):
        board = chess.Board(START_FEN)
        # A loose endgame boundary (32 points) reclassifies the start (31
        # points) as an endgame: thresholds are configurable, not hardcoded.
        loose = PhaseThresholds(endgame_max_phase_material=63)
        assert classify_position(board, loose) is GamePhase.ENDGAME


# --- classification --------------------------------------------------------------


def _evaluation(cpl: float | None, *, sacrifice: bool = False) -> MoveClassificationInput:
    """Build a minimal classification input for classification tests."""
    return MoveClassificationInput(
        centipawn_loss=0 if cpl is None else int(cpl),
        is_best_move=cpl is None,
        sacrifices_material=sacrifice,
        second_best_gap=None,
    )


class TestClassification:
    def test_thresholds_defaults_exist(self):
        thresholds = ClassificationThresholds()
        # Defaults are configurable, not invented constants hidden in code.
        assert thresholds.mistake_max_cp_loss is not None
        assert thresholds.brilliant_min_second_best_gap is not None

    def test_no_loss_is_best(self):
        result = classify_move(_evaluation(None), ClassificationThresholds())
        assert result is MoveClassification.BEST

    def test_small_loss_is_excellent(self):
        # Phase 3 splits a near-best move (EXCELLENT, <= 25cp) from GOOD.
        result = classify_move(_evaluation(15), ClassificationThresholds())
        assert result is MoveClassification.EXCELLENT

    def test_moderate_loss_is_good(self):
        result = classify_move(_evaluation(40), ClassificationThresholds())
        assert result is MoveClassification.GOOD

    def test_sacrifice_with_small_loss_is_brilliant(self):
        # Brilliant requires positive proof: best move + sacrifice + a clear
        # gap over the second-best line.
        result = classify_move(
            MoveClassificationInput(
                centipawn_loss=10,
                is_best_move=True,
                sacrifices_material=True,
                second_best_gap=200,
            ),
            ClassificationThresholds(),
        )
        assert result is MoveClassification.BRILLIANT

    def test_large_loss_is_blunder(self):
        result = classify_move(_evaluation(400), ClassificationThresholds())
        assert result is MoveClassification.BLUNDER

    def test_thresholds_are_respected(self):
        # A custom, stricter configuration changes the label for the same move:
        # 40 cp is GOOD with the defaults (good_max 50) but INACCURATE here.
        strict = ClassificationThresholds(
            best_max_cp_loss=10,
            good_max_cp_loss=30,
            inaccurate_max_cp_loss=60,
            mistake_max_cp_loss=100,
        )
        assert classify_move(_evaluation(40), strict) is MoveClassification.INACCURATE
