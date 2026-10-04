"""Outcome-forecast tests.

The forecast is a deterministic function of the stored engine evaluation, so the
tests check the properties that make it trustworthy: it is a real three-way
distribution, it is monotone in the evaluation, mate is exact rather than
approximated, unknown stays unknown, and the curve it used is always attached to
the numbers.
"""

from __future__ import annotations

import pytest

from argus.intelligence import (
    FORECAST_DISCLAIMER,
    FORECAST_METHODOLOGY,
    EvidenceSource,
    GameIntelligence,
    build_forecast,
    build_trajectory,
    outcome_probabilities,
    win_expectation_white,
)
from argus.intelligence.forecast import SCALE_CP

from tests.conftest import OPERA_GAME_PGN
from tests.test_intelligence_core import (
    context_for,
    moves_from_sans,
    quiet,
    san_list_from_pgn,
    set_white_evals,
)


def test_probabilities_are_a_real_distribution() -> None:
    for cp in (-900, -300, -50, 0, 50, 300, 900):
        forecast = outcome_probabilities(cp)
        assert forecast is not None
        assert forecast.white + forecast.draw + forecast.black == pytest.approx(1.0, abs=1e-3)
        white, draw, black = forecast.percentages()
        assert white + draw + black == 100
        assert min(white, draw, black) >= 0


def test_a_level_position_is_not_a_coin_flip_between_two_winners() -> None:
    forecast = outcome_probabilities(0)
    assert forecast is not None
    assert forecast.draw > forecast.white
    assert forecast.white == pytest.approx(forecast.black, abs=1e-6)
    # Roughly a third each: a level game with a real draw rate, not 50/50.
    assert 0.25 < forecast.white < 0.4


def test_forecast_is_monotone_in_the_evaluation() -> None:
    values = [outcome_probabilities(cp).white for cp in range(-600, 601, 50)]
    assert values == sorted(values)
    # A large but not forced edge is a strong favourite, not a formality: draws and
    # losses still get real probability until the position is actually decided.
    assert values[0] < 0.1 and values[-1] > 0.7
    assert outcome_probabilities(1500).white > 0.9
    assert outcome_probabilities(-1500).white < 0.05


def test_mate_is_exact_and_never_approximated() -> None:
    white_mates = outcome_probabilities(0, 3)
    assert white_mates == white_mates.model_copy(update={"white": 1.0, "draw": 0.0, "black": 0.0})
    black_mates = outcome_probabilities(0, -2)
    assert black_mates is not None and black_mates.black == 1.0 and black_mates.draw == 0.0


def test_no_evaluation_means_no_forecast() -> None:
    assert outcome_probabilities(None, None) is None
    assert win_expectation_white(None, None) is None
    # Mate outranks a stale centipawn value rather than being blended with it.
    assert win_expectation_white(0, 1) == 1.0


def test_win_expectation_is_the_same_curve_used_by_accuracy() -> None:
    assert win_expectation_white(0) == pytest.approx(0.5, abs=1e-6)


def test_scale_is_the_documented_one() -> None:
    assert SCALE_CP == 300
    assert "300" in FORECAST_METHODOLOGY
    assert "not a trained model" in FORECAST_DISCLAIMER


def test_build_forecast_tracks_peaks_and_the_decisive_swing() -> None:
    facts = quiet(moves_from_sans(["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "d3", "d6"]))
    # White-perspective evaluations: White builds a real edge, then throws it away.
    set_white_evals(
        facts, {1: 20, 2: 30, 3: 80, 4: 90, 5: 260, 6: 300, 7: -400, 8: -420}
    )

    forecast = build_forecast(facts)
    assert forecast.evaluated_plies == 8
    assert forecast.final is not None and forecast.final.white < 0.5
    assert forecast.peak_white is not None and forecast.peak_white.probability > 0.5
    assert forecast.peak_white.ply == 6
    assert forecast.decisive_ply == 7
    assert forecast.decisive_swing is not None and forecast.decisive_swing < 0
    assert forecast.source is EvidenceSource.ARGUS_INTERPRETATION
    assert forecast.methodology == FORECAST_METHODOLOGY
    assert any(fact.key == "forecast_swing" for fact in forecast.facts)


def test_missing_evaluations_are_counted_not_guessed() -> None:
    facts = quiet(moves_from_sans(["e4", "e5", "Nf3", "Nc6"]))
    facts[1].eval_after_cp = None
    facts[1].eval_after_mate = None
    forecast = build_forecast(facts)
    assert forecast.evaluated_plies == 3
    assert forecast.unforecastable_plies == 1
    assert any(fact.key == "forecast_missing" for fact in forecast.facts)


def test_report_section_carries_the_forecast_and_its_curve() -> None:
    facts = quiet(moves_from_sans(san_list_from_pgn(OPERA_GAME_PGN)))
    # White is winning from early on in the Opera Game.
    set_white_evals(facts, {ply: (1200 if ply >= 12 else 50) for ply in range(1, len(facts) + 1)})

    intelligence = GameIntelligence(context_for(facts, result="1-0"), facts)
    report = intelligence.build_report()
    assert report.forecast is not None
    section = report.forecast
    assert section.forecast.final is not None
    assert section.forecast.final.white > 0.9
    assert section.forecast.methodology and section.forecast.disclaimer
    assert section.facts == section.forecast.facts


def test_trajectory_points_expose_the_win_expectation() -> None:
    facts = quiet(moves_from_sans(san_list_from_pgn(OPERA_GAME_PGN)))
    trajectory = build_trajectory(facts, initial_position=facts[0].fen_before)
    assert trajectory.points
    expected = [point.white_win_expectation for point in trajectory.points if point.available]
    assert all(value is not None for value in expected)
    assert all(0.0 <= value <= 1.0 for value in expected)
