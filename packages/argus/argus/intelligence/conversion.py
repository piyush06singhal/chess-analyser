"""Conversion analysis.

Answers, from the engine trajectory alone: did a side reach a large advantage
and fail to convert it? Did an equal position slide into a losing one? Did a
losing position recover?

Everything here is a **candidate statement about the game** — the equivalent of
"Caissa measured a +3.5 evaluation at ply 41 and +0.2 at ply 63" — and never a
judgement about the player. The vocabulary is deliberately mechanical
(``advantage_conversion_candidate``), because "you threw away a winning game" is
an interpretation this layer is not allowed to make.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from argus.analysis.perspective import format_evaluation
from argus.chess_core.models import Color
from argus.intelligence.advantage import AdvantageState, band
from argus.intelligence.base import (
    Certainty,
    ConversionPolicy,
    EvidenceSource,
    color_label,
)
from argus.intelligence.trajectory import GameTrajectory, TrajectoryPoint, trajectory_state_at


class ConversionEventType(str, Enum):
    """Conversion-related game states."""

    CONVERSION_CONFIRMED = "conversion_confirmed"
    ADVANTAGE_CONVERSION_CANDIDATE = "advantage_conversion_candidate"
    WINNING_BECAME_EQUAL = "winning_became_equal"
    EQUAL_BECAME_LOSING = "equal_became_losing"
    LOSING_RECOVERED = "losing_recovered"


class ConversionEvent(BaseModel):
    """One conversion-related observation, with both ends of the evidence."""

    type: ConversionEventType
    side: Color
    ply: int
    move_number: int | None = None
    peak_ply: int | None = None
    peak_evaluation_white: int | None = None
    peak_display: str | None = None
    later_ply: int | None = None
    later_evaluation_white: int | None = None
    later_display: str | None = None
    measured_plies: int = Field(default=0, description="Evaluated plies this statement rests on")
    small_sample: bool = False
    statement: str
    certainty: Certainty = Certainty.CANDIDATE
    source: EvidenceSource = EvidenceSource.ARGUS_INTERPRETATION
    evidence: dict = Field(default_factory=dict)


class ConversionAnalysis(BaseModel):
    """All conversion-related observations for a game."""

    events: list[ConversionEvent] = Field(default_factory=list)
    by_side: dict[str, int] = Field(default_factory=dict)
    peak_white_ply: int | None = None
    peak_black_ply: int | None = None
    note: str = (
        "Conversion observations are Caissa candidates: they state the measured "
        "evaluations at two plies and make no claim about intent or skill."
    )


def _side_series(
    trajectory: GameTrajectory, color: Color
) -> list[tuple[int, int | None, int | None, AdvantageState | None]]:
    """(ply, cp_white, mate_white, state) for evaluated points, from ``color``'s view."""
    series = []
    for point in trajectory.points:
        if not point.available:
            continue
        state = point.white_state if color == Color.WHITE else point.black_state
        series.append((point.ply, point.evaluation_cp_white, point.mate_white, state))
    return series


def analyse_conversion(
    trajectory: GameTrajectory,
    *,
    result: str,
    policy: ConversionPolicy | None = None,
    move_number_by_ply: dict[int, int] | None = None,
) -> ConversionAnalysis:
    """Detect conversion candidates, slides and comebacks from the trajectory."""
    limits = policy or ConversionPolicy()
    moves_by_ply = move_number_by_ply or {}
    events: list[ConversionEvent] = []

    if not trajectory.points:
        return ConversionAnalysis()

    peak_ply_by_side: dict[Color, int | None] = {Color.WHITE: None, Color.BLACK: None}

    for color in (Color.WHITE, Color.BLACK):
        series = _side_series(trajectory, color)
        if len(series) < 2:
            continue

        # --- equal slid into losing, and losing recovered ---------------------
        # These scans are independent of whether a winning position was ever
        # reached, so they run first.
        for index in range(1, len(series)):
            ply, cp_white, _mate, state = series[index]
            previous_ply, _previous_cp, _previous_mate, previous_state = series[index - 1]
            previous_band, current_band = band(previous_state), band(state)
            if previous_band is None or current_band is None:
                continue
            if -1 <= previous_band <= 1 and current_band <= -2:
                events.append(
                    ConversionEvent(
                        type=ConversionEventType.EQUAL_BECAME_LOSING,
                        side=color,
                        ply=ply,
                        move_number=moves_by_ply.get(ply),
                        later_ply=ply,
                        later_evaluation_white=cp_white,
                        later_display=format_evaluation(cp_white, None),
                        measured_plies=len(series),
                        statement=(
                            f"{color_label(color)}'s evaluation moved from an equal position at "
                            f"ply {previous_ply} to {format_evaluation(cp_white, None)} at ply {ply}."
                        ),
                        evidence={"band_before": previous_band, "band_after": current_band},
                    )
                )
                break
        for index in range(1, len(series)):
            ply, cp_white, _mate, state = series[index]
            previous_ply, _previous_cp, _previous_mate, previous_state = series[index - 1]
            previous_band, current_band = band(previous_state), band(state)
            if previous_band is None or current_band is None:
                continue
            if previous_band <= -2 and current_band >= 0:
                events.append(
                    ConversionEvent(
                        type=ConversionEventType.LOSING_RECOVERED,
                        side=color,
                        ply=ply,
                        move_number=moves_by_ply.get(ply),
                        peak_ply=previous_ply,
                        later_ply=ply,
                        later_evaluation_white=cp_white,
                        later_display=format_evaluation(cp_white, None),
                        measured_plies=len(series),
                        statement=(
                            f"{color_label(color)} recovered from a losing evaluation at ply "
                            f"{previous_ply} to {format_evaluation(cp_white, None)} at ply {ply}."
                        ),
                        evidence={"band_before": previous_band, "band_after": current_band},
                    )
                )
                break

        # --- winning position was reached (and held for at least N plies) -----
        winning_run: list[tuple[int, int | None]] = []
        winning_start: int | None = None
        for ply, cp_white, _mate, state in series:
            if band(state) is not None and band(state) >= 3:
                if winning_start is None:
                    winning_start = ply
                winning_run.append((ply, cp_white))
            else:
                if (
                    winning_start is not None
                    and len(winning_run) >= limits.min_winning_plies
                ):
                    break
                winning_start, winning_run = None, []
        if winning_start is None or len(winning_run) < limits.min_winning_plies:
            continue

        peak_ply, peak_cp = max(
            winning_run, key=lambda item: (item[1] if item[1] is not None else -10_000)
        )
        peak_ply_by_side[color] = winning_start
        won = (result == "1-0" and color == Color.WHITE) or (
            result == "0-1" and color == Color.BLACK
        )

        if won:
            events.append(
                ConversionEvent(
                    type=ConversionEventType.CONVERSION_CONFIRMED,
                    side=color,
                    ply=winning_start,
                    move_number=moves_by_ply.get(winning_start),
                    peak_ply=peak_ply,
                    peak_evaluation_white=peak_cp,
                    peak_display=format_evaluation(peak_cp, None),
                    measured_plies=len(series),
                    small_sample=len(series) < 20,
                    statement=(
                        f"{color_label(color)} first reached a winning evaluation at ply "
                        f"{winning_start} and the game ended {result}."
                    ),
                    certainty=Certainty.CONFIRMED,
                    evidence={"winning_start_ply": winning_start, "result": result},
                )
            )
            continue

        later = next(
            (
                (ply, cp_white, state)
                for ply, cp_white, _mate, state in series
                if ply > peak_ply
                and band(state) is not None
                and band(state) <= 1
            ),
            None,
        )
        if later is None:
            continue
        later_ply, later_cp, later_state = later
        events.append(
            ConversionEvent(
                type=ConversionEventType.ADVANTAGE_CONVERSION_CANDIDATE,
                side=color,
                ply=later_ply,
                move_number=moves_by_ply.get(later_ply),
                peak_ply=peak_ply,
                peak_evaluation_white=peak_cp,
                peak_display=format_evaluation(peak_cp, None),
                later_ply=later_ply,
                later_evaluation_white=later_cp,
                later_display=format_evaluation(later_cp, None),
                measured_plies=len(series),
                small_sample=len(series) < 20,
                statement=(
                    f"{color_label(color)} held a winning evaluation from ply {winning_start} "
                    f"(peak {format_evaluation(peak_cp, None)} at ply {peak_ply}) and the "
                    f"evaluation was {format_evaluation(later_cp, None)} by ply {later_ply}."
                ),
                evidence={
                    "winning_start_ply": winning_start,
                    "peak_ply": peak_ply,
                    "later_ply": later_ply,
                    "later_state": later_state.value if later_state else None,
                    "result": result,
                },
            )
        )
        if band(later_state) is not None and -1 <= band(later_state) <= 1:
            events.append(
                ConversionEvent(
                    type=ConversionEventType.WINNING_BECAME_EQUAL,
                    side=color,
                    ply=later_ply,
                    move_number=moves_by_ply.get(later_ply),
                    peak_ply=peak_ply,
                    peak_evaluation_white=peak_cp,
                    peak_display=format_evaluation(peak_cp, None),
                    later_ply=later_ply,
                    later_evaluation_white=later_cp,
                    later_display=format_evaluation(later_cp, None),
                    measured_plies=len(series),
                    statement=(
                        f"{color_label(color)}'s evaluation fell from a winning "
                        f"{format_evaluation(peak_cp, None)} at ply {peak_ply} to "
                        f"{format_evaluation(later_cp, None)} at ply {later_ply}."
                    ),
                    evidence={"peak_ply": peak_ply, "later_ply": later_ply, "result": result},
                )
            )


    events.sort(key=lambda event: (event.ply, event.type.value))
    by_side: dict[str, int] = {}
    for event in events:
        by_side[event.side.value] = by_side.get(event.side.value, 0) + 1

    return ConversionAnalysis(
        events=events,
        by_side=by_side,
        peak_white_ply=peak_ply_by_side[Color.WHITE],
        peak_black_ply=peak_ply_by_side[Color.BLACK],
    )


def state_at_ply(trajectory: GameTrajectory, ply: int, color: Color) -> AdvantageState | None:
    """Re-exported convenience for report builders."""
    return trajectory_state_at(trajectory, ply, color)


def point_at_ply(trajectory: GameTrajectory, ply: int) -> TrajectoryPoint | None:
    """The trajectory point at ``ply``, or ``None``."""
    for point in trajectory.points:
        if point.ply == ply:
            return point
    return None
