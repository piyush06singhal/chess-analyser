"""Tests for evaluation perspective normalization and centipawn loss.

These are the highest-risk pieces of chess analysis: a sign error flips every
evaluation in the product. The convention under test is:

    stored/displayed evaluation: POSITIVE = WHITE IS BETTER
    engine native evaluation:    side-to-move perspective
"""

from __future__ import annotations

import pytest

from argus.analysis.engine.base import (
    MATE_SCORE_CEILING,
    calculate_centipawn_loss,
    flip_score,
    to_cp,
)
from argus.analysis.perspective import (
    flip,
    for_color,
    format_evaluation,
    side_to_move,
    to_mover_perspective,
    to_white_perspective,
)

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
BLACK_TO_MOVE_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


class TestSideToMove:
    def test_white_to_move(self):
        assert side_to_move(START_FEN).value == "white"

    def test_black_to_move(self):
        assert side_to_move(BLACK_TO_MOVE_FEN).value == "black"


class TestPerspectiveConversion:
    def test_white_to_move_positive_is_white_advantage(self):
        assert to_white_perspective(200, None, side="white") == (200, None)

    def test_black_to_move_positive_is_black_advantage(self):
        # +2.0 for the side to move (Black) must read as -2.0 for White.
        assert to_white_perspective(200, None, side="black") == (-200, None)

    def test_for_color_mirrors_for_black(self):
        # The same White-perspective +2.0 is -2.0 from Black's point of view.
        assert for_color(200, None, "white") == (200, None)
        assert for_color(200, None, "black") == (-200, None)

    def test_round_trip_white_perspective(self):
        for side in ("white", "black"):
            cp, mate = to_white_perspective(123, -4, side=side)
            assert to_mover_perspective(cp, mate, side=side) == (123, -4)

    def test_mate_signs_flip_too(self):
        assert to_white_perspective(0, 3, side="black") == (0, -3)

    def test_none_scores_stay_none(self):
        assert to_white_perspective(None, None, side="black") == (None, None)


class TestFormatting:
    def test_centipawn_formatting(self):
        assert format_evaluation(172, None) == "+1.72"
        assert format_evaluation(-250, None) == "-2.50"
        assert format_evaluation(0, None) == "+0.00"

    def test_mate_is_never_a_centipawn_number(self):
        assert format_evaluation(None, 3) == "#3"
        assert format_evaluation(None, -2) == "#-2"

    def test_missing_evaluation_is_honest(self):
        assert format_evaluation(None, None) == "—"


class TestMateToCp:
    def test_mate_maps_to_ceiling_scale(self):
        assert to_cp(None, 1) == MATE_SCORE_CEILING - 1
        assert to_cp(None, -1) == -(MATE_SCORE_CEILING - 1)
        # A faster mate scores higher (better) than a slower one.
        assert to_cp(None, 1) > to_cp(None, 5)

    def test_cp_passthrough(self):
        assert to_cp(42, None) == 42
        assert to_cp(None, None) is None


class TestFlipScore:
    def test_flip_negates_both(self):
        assert flip_score(10, 3) == (-10, -3)
        assert flip(10, 3) == (-10, -3)

    def test_flip_preserves_none(self):
        assert flip_score(None, None) == (None, None)


class TestCentipawnLoss:
    def test_best_move_has_zero_loss(self):
        assert calculate_centipawn_loss(50, None, 50, None) == 0

    def test_loss_is_best_minus_played(self):
        assert calculate_centipawn_loss(50, None, -150, None) == 200

    def test_loss_never_negative(self):
        # A "played move better than best" is search noise, clamped to zero.
        assert calculate_centipawn_loss(0, None, 120, None) == 0

    def test_delivering_mate_is_zero_loss(self):
        assert calculate_centipawn_loss(-500, None, None, 2) == 0

    def test_missing_scores_are_none_not_zero(self):
        assert calculate_centipawn_loss(None, None, 0, None) is None
        assert calculate_centipawn_loss(0, None, None, None) is None

    def test_blundering_a_mate_uses_mate_scale(self):
        # Best line mates in 1; the played move squanders it for -3.0.
        loss = calculate_centipawn_loss(None, 1, -300, None)
        assert loss == MATE_SCORE_CEILING - 1 + 300

    @pytest.mark.parametrize(
        "cp,expected_sign",
        [(100, 1), (-100, -1), (0, 0)],
    )
    def test_sign_convention(self, cp, expected_sign):
        value = to_white_perspective(cp, None, side="white")
        assert (value[0] > 0) - (value[0] < 0) == expected_sign
