"""Engine-derived outcome forecast.

This answers "who is winning, and how likely is each result?" **without** any
machine learning and without inventing a single number. Every probability here is
a deterministic function of the evaluation Stockfish already produced, so
re-running it on the same analysis gives the same forecast, and the formula is
published right next to the numbers it produced.

Methodology
-----------

The engine evaluation (White perspective, the stored convention) is mapped to a
three-way split by two logistic curves separated by a draw offset:

``p_white_raw = L(value − D)``, ``p_black_raw = L(−value − D)``,
``p_draw_raw = 1 − p_white_raw − p_black_raw``

with ``L(x) = 1 / (1 + exp(−x / S))``.

* ``S = 300`` centipawns — **the same logistic scale Caissa accuracy uses**, so
  the two features cannot drift apart.
* ``D = 220`` centipawns — the *draw offset*. Without it a one-pawn edge would
  claim a near-certain win; with it, a level position splits roughly
  32% / 36% / 32%, which is what a level chess game actually looks like.
* ``p_draw`` is floored at 2% and the three values are renormalized, so the
  forecast never claims certainty outside a forced mate.

**Mate is handled exactly, never through the curve**: a mate in ``n`` is 100%
for the mating side and 0% for everything else. A position the engine did not
evaluate produces **no forecast** rather than a guessed one.

Honest framing
--------------

This is a Caissa interpretation of an engine fact, and it is labelled as one. It
is not a trained model, it is not calibrated against any other provider, and it
describes *the position*, never the players. A forecast of 70% does not mean
"this player wins 70% of the time"; it means the engine's evaluation maps to 70%
under a documented curve.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from argus.chess_core.models import Color
from argus.intelligence.base import (
    Certainty,
    EvidenceSource,
    Finding,
    MoveFact,
    color_label,
)

#: Logistic scale, shared with the accuracy methodology.
SCALE_CP = 300
#: Centipawn shift that keeps a small edge from reading as a won game.
DRAW_OFFSET_CP = 220
#: Draws are never impossible until mate.
MIN_DRAW = 0.02

FORECAST_METHODOLOGY = (
    "Caissa outcome forecast: logistic split of the stored engine evaluation "
    "(p_white = L(value-220), p_black = L(-value-220) with L(x) = 1/(1+exp(-x/300)), "
    "p_draw = 1-p_white-p_black floored at 2% and renormalized). Mate is handled exactly, "
    "never through the curve. It is a deterministic function of the engine evaluation, "
    "not a trained model."
)
FORECAST_DISCLAIMER = (
    "Caissa forecast is Caissa's own documented reading of the engine evaluation. It is not "
    "a trained model, is not calibrated against any other provider, and describes the "
    "position, not the players."
)


def logistic(value: float, *, scale: int = SCALE_CP) -> float:
    """Standard logistic curve used for every probability in this module."""
    try:
        return 1.0 / (1.0 + math.exp(-value / scale))
    except OverflowError:  # pragma: no cover — |value| would have to be absurd
        return 0.0 if value < 0 else 1.0


class OutcomeProbabilities(BaseModel):
    """A three-way result forecast, White perspective, summing to 1."""

    white: float = Field(ge=0.0, le=1.0)
    draw: float = Field(ge=0.0, le=1.0)
    black: float = Field(ge=0.0, le=1.0)

    @property
    def favourite(self) -> str:
        """``white`` | ``black`` | ``draw`` — the larger of the three."""
        if self.white >= self.black and self.white >= self.draw:
            return "white"
        if self.black >= self.white and self.black >= self.draw:
            return "black"
        return "draw"

    def percentages(self) -> tuple[int, int, int]:
        """Whole percentages that still add up to 100 (largest remainder)."""
        raw = [self.white * 100, self.draw * 100, self.black * 100]
        floors = [int(value) for value in raw]
        remainder = 100 - sum(floors)
        order = sorted(range(3), key=lambda i: raw[i] - floors[i], reverse=True)
        for i in range(remainder):
            floors[order[i % 3]] += 1
        return floors[0], floors[1], floors[2]


def outcome_probabilities(
    cp: int | None, mate: int | None = None
) -> OutcomeProbabilities | None:
    """Map a White-perspective evaluation to a three-way forecast.

    Returns ``None`` when the position was never evaluated — unknown stays
    unknown rather than defaulting to a coin flip.
    """
    if mate is not None:
        if mate > 0:
            return OutcomeProbabilities(white=1.0, draw=0.0, black=0.0)
        if mate < 0:
            return OutcomeProbabilities(white=0.0, draw=0.0, black=1.0)
        return None  # mate 0 is not a thing the engine reports
    if cp is None:
        return None

    value = float(cp)
    p_white = logistic(value - DRAW_OFFSET_CP)
    p_black = logistic(-value - DRAW_OFFSET_CP)
    p_draw = max(1.0 - p_white - p_black, MIN_DRAW)
    total = p_white + p_black + p_draw
    return OutcomeProbabilities(
        white=round(p_white / total, 4),
        draw=round(p_draw / total, 4),
        black=round(p_black / total, 4),
    )


def win_expectation_white(cp: int | None, mate: int | None = None) -> float | None:
    """Single-number "how much of the game White has", for plotting.

    Uses the same documented curve, so the graph line and the three-way
    forecast can never disagree.
    """
    if mate is not None:
        return 1.0 if mate > 0 else 0.0
    if cp is None:
        return None
    return round(logistic(float(cp)), 4)


class ForecastPeak(BaseModel):
    """The best chance one side ever had, and when."""

    side: Color
    probability: float
    ply: int
    move_number: int


class ResultForecast(BaseModel):
    """The game's engine-derived outcome forecast, with its methodology."""

    at_start: OutcomeProbabilities | None = None
    final: OutcomeProbabilities | None = None
    peak_white: ForecastPeak | None = None
    peak_black: ForecastPeak | None = None
    decisive_ply: int | None = Field(
        default=None, description="Largest single-ply swing in White's forecast"
    )
    decisive_swing: float | None = None
    evaluated_plies: int = 0
    unforecastable_plies: int = 0
    facts: list[Finding] = Field(default_factory=list)
    source: EvidenceSource = EvidenceSource.ARGUS_INTERPRETATION
    certainty: Certainty = Certainty.CONFIRMED
    methodology: str = FORECAST_METHODOLOGY
    disclaimer: str = FORECAST_DISCLAIMER

    @property
    def favourite(self) -> str | None:
        return self.final.favourite if self.final else None


def build_forecast(moves: list[MoveFact]) -> ResultForecast:
    """Build the forecast from the stored per-move evaluations."""
    forecast = ResultForecast()
    previous: float | None = None
    first_ply: int | None = None

    for fact in moves:
        probabilities = outcome_probabilities(fact.eval_after_white, fact.mate_after_white)
        if probabilities is None:
            forecast.unforecastable_plies += 1
            continue
        forecast.evaluated_plies += 1

        if first_ply is None:
            first_ply = fact.ply
            forecast.at_start = probabilities

        if forecast.peak_white is None or probabilities.white > forecast.peak_white.probability:
            forecast.peak_white = ForecastPeak(
                side=Color.WHITE,
                probability=probabilities.white,
                ply=fact.ply,
                move_number=fact.move_number,
            )
        if forecast.peak_black is None or probabilities.black > forecast.peak_black.probability:
            forecast.peak_black = ForecastPeak(
                side=Color.BLACK,
                probability=probabilities.black,
                ply=fact.ply,
                move_number=fact.move_number,
            )

        if previous is not None:
            swing = probabilities.white - previous
            if forecast.decisive_swing is None or abs(swing) > abs(forecast.decisive_swing):
                forecast.decisive_swing = round(swing, 4)
                forecast.decisive_ply = fact.ply
        previous = probabilities.white
        forecast.final = probabilities

    forecast.facts = _forecast_facts(forecast, moves)
    return forecast


def _forecast_facts(forecast: ResultForecast, moves: list[MoveFact]) -> list[Finding]:
    """Factual statements carrying the numbers they were derived from."""
    facts: list[Finding] = []
    if forecast.at_start is not None:
        facts.append(
            Finding(
                key="forecast_start",
                statement=(
                    "From the first evaluated position the engine's forecast was "
                    f"White {_pct(forecast.at_start.white)}, draw {_pct(forecast.at_start.draw)}, "
                    f"Black {_pct(forecast.at_start.black)}."
                ),
                evidence={"probabilities": forecast.at_start.model_dump()},
            )
        )
    if forecast.final is not None:
        facts.append(
            Finding(
                key="forecast_final",
                statement=(
                    "After the last evaluated move the forecast was "
                    f"White {_pct(forecast.final.white)}, draw {_pct(forecast.final.draw)}, "
                    f"Black {_pct(forecast.final.black)}."
                ),
                evidence={"probabilities": forecast.final.model_dump()},
            )
        )
    if forecast.peak_white is not None and forecast.peak_white.probability > 0.5:
        facts.append(
            Finding(
                key="forecast_peak_white",
                statement=(
                    f"White's best chance was {_pct(forecast.peak_white.probability)} after "
                    f"move {forecast.peak_white.move_number}."
                ),
                ply=forecast.peak_white.ply,
                move_number=forecast.peak_white.move_number,
                side=Color.WHITE,
                evidence={"probability": forecast.peak_white.probability},
            )
        )
    if forecast.peak_black is not None and forecast.peak_black.probability > 0.5:
        facts.append(
            Finding(
                key="forecast_peak_black",
                statement=(
                    f"Black's best chance was {_pct(forecast.peak_black.probability)} after "
                    f"move {forecast.peak_black.move_number}."
                ),
                ply=forecast.peak_black.ply,
                move_number=forecast.peak_black.move_number,
                side=Color.BLACK,
                evidence={"probability": forecast.peak_black.probability},
            )
        )
    if forecast.decisive_ply is not None and forecast.decisive_swing is not None:
        fact = next((m for m in moves if m.ply == forecast.decisive_ply), None)
        if fact is not None:
            change = forecast.decisive_swing * 100
            facts.append(
                Finding(
                    key="forecast_swing",
                    statement=(
                        f"The forecast moved most on move {fact.move_number} "
                        f"({fact.san}): {change:+.1f} percentage points for "
                        f"{color_label(Color.WHITE)}."
                    ),
                    ply=fact.ply,
                    move_number=fact.move_number,
                    side=fact.mover,
                    evidence={
                        "swing_points": round(change, 2),
                        "fen_before": fact.fen_before,
                    },
                )
            )
    if forecast.unforecastable_plies:
        facts.append(
            Finding(
                key="forecast_missing",
                statement=(
                    f"{forecast.unforecastable_plies} move(s) had no engine evaluation and are "
                    "excluded from the forecast."
                ),
                evidence={"unforecastable_plies": forecast.unforecastable_plies},
            )
        )
    return facts


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


__all__ = [
    "DRAW_OFFSET_CP",
    "FORECAST_DISCLAIMER",
    "FORECAST_METHODOLOGY",
    "MIN_DRAW",
    "SCALE_CP",
    "ForecastPeak",
    "OutcomeProbabilities",
    "ResultForecast",
    "build_forecast",
    "logistic",
    "outcome_probabilities",
    "win_expectation_white",
]
