"""Game trajectory.

The trajectory is the sequence of engine evaluations of the game (White
perspective, the stored convention), one point per ply, with the advantage state
of both sides at that point.

Two rules matter:

* **No interpolation.** A ply the engine did not evaluate is emitted with
  ``available: False`` and no evaluation. Missing data is never filled in, and
  the report says how many plies were missing.
* The trajectory is the *input* to the analytical states below, which are
  Caissa interpretations over engine facts and are labelled as such:
  ``advantage_creation``, ``advantage_loss``, ``comeback``, ``collapse``,
  ``stabilization`` and ``conversion``.

The states describe the game, not the player's character: "the evaluation went
from +3.0 to 0.0" is a fact; "the player is a choker" is not something this
layer will ever say.
"""

from __future__ import annotations

from enum import Enum

import chess
from pydantic import BaseModel, Field

from argus.analysis.perspective import format_evaluation
from argus.chess_core.models import Color
from argus.intelligence.material import material_points
from argus.intelligence.advantage import AdvantageState, band, state_for
from argus.intelligence.forecast import win_expectation_white
from argus.intelligence.base import (
    AdvantagePolicy,
    Certainty,
    EvidenceSource,
    MoveFact,
    color_label,
)

#: A stretch this long with an unchanged state counts as stabilization.
STABILIZATION_PLIES = 8
#: Window within which a band change is treated as one event.
TRANSITION_WINDOW_PLIES = 12
#: Band change that counts as a real shift rather than noise.
MATERIAL_BAND_DELTA = 2


class TrajectoryEventType(str, Enum):
    """Analytical states derived from the trajectory (Caissa interpretation)."""

    ADVANTAGE_CREATION = "advantage_creation"
    ADVANTAGE_LOSS = "advantage_loss"
    COMEBACK = "comeback"
    COLLAPSE = "collapse"
    STABILIZATION = "stabilization"
    CONVERSION = "conversion"


class TrajectoryPoint(BaseModel):
    """The engine's view of the game after one ply (plus the initial position)."""

    ply: int
    move_number: int
    san: str | None = None
    side_to_move: Color | None = None
    evaluation_cp_white: int | None = Field(
        default=None, description="Engine evaluation in centipawns, White perspective"
    )
    mate_white: int | None = Field(
        default=None, description="Mate distance, White perspective (positive = White mates)"
    )
    available: bool = Field(description="False when the engine produced no evaluation")
    white_state: AdvantageState | None = None
    black_state: AdvantageState | None = None
    material_balance: int | None = Field(default=None, description="Pawns, White perspective")
    classification: str | None = None
    evaluation_display: str = "—"
    white_win_expectation: float | None = Field(
        default=None,
        description=(
            "Documented logistic reading of the evaluation, 0..1 for White "
            "(see argus.intelligence.forecast). None when the ply was not evaluated."
        ),
    )


class TrajectorySegment(BaseModel):
    """A run of consecutive plies in which the state did not change."""

    state: AdvantageState | None
    start_ply: int
    end_ply: int
    length: int
    stable: bool = Field(description="True when the run is long enough to count as stabilization")


class TrajectoryEvent(BaseModel):
    """An analytical state change derived from the engine trajectory."""

    type: TrajectoryEventType
    side: Color
    start_ply: int
    end_ply: int
    statement: str
    source: EvidenceSource = EvidenceSource.ARGUS_INTERPRETATION
    certainty: Certainty = Certainty.CONFIRMED
    evidence: dict = Field(default_factory=dict)


class GameTrajectory(BaseModel):
    """Full evaluation trajectory of a game with its analytical states."""

    points: list[TrajectoryPoint] = Field(default_factory=list)
    segments: list[TrajectorySegment] = Field(default_factory=list)
    events: list[TrajectoryEvent] = Field(default_factory=list)
    evaluated_plies: int = 0
    missing_plies: int = 0
    #: Plies whose engine evaluation is a mate score (``#n``/``#-n``).
    mate_plies: int = 0
    #: Range over plies with a **centipawn** evaluation. Mate plies are excluded:
    #: a mate is displayed as ``#n``, and folding its ``MATE_SCORE_CEILING``
    #: mapping into a centipawn range would report a fake centipawn number.
    min_evaluation_white: int | None = None
    max_evaluation_white: int | None = None
    final_evaluation_white: int | None = None
    #: Display form of the final evaluation (``+0.42`` or ``#3``), never a
    #: centipawn number for a mate.
    final_evaluation_display: str | None = None
    states_seen: list[str] = Field(default_factory=list)
    note: str = (
        "Points are engine measurements at each ply. Plies without an engine "
        "evaluation are reported as unavailable and are never interpolated. "
        "The centipawn range excludes mate plies, which are displayed as #n."
    )


def _point_state_label(point: TrajectoryPoint, color: Color) -> AdvantageState | None:
    return point.white_state if color == Color.WHITE else point.black_state


def build_trajectory(
    moves: list[MoveFact],
    *,
    initial_position: str,
    material_balances: dict[int, int] | None = None,
    policy: AdvantagePolicy | None = None,
    result: str = "*",
) -> GameTrajectory:
    """Build the evaluation trajectory (no engine calls — stored data only)."""
    if not moves:
        return GameTrajectory()

    balances = material_balances or {}
    points: list[TrajectoryPoint] = []

    # The initial position is a real engine measurement when the first move has
    # a "before" evaluation (that evaluation *is* the initial position).
    first = moves[0]
    if first.evaluated:
        initial_cp = first.eval_before_white
        initial_mate = first.mate_before_white
        start_board = chess.Board(initial_position)
        points.append(
            TrajectoryPoint(
                ply=0,
                move_number=1,
                san=None,
                side_to_move=Color.WHITE if start_board.turn else Color.BLACK,
                evaluation_cp_white=initial_cp,
                mate_white=initial_mate,
                available=True,
                white_state=state_for(initial_cp, initial_mate, Color.WHITE, policy),
                black_state=state_for(initial_cp, initial_mate, Color.BLACK, policy),
                material_balance=initial_balance_from_fen(initial_position),
                evaluation_display=format_evaluation(initial_cp, initial_mate),
                white_win_expectation=win_expectation_white(initial_cp, initial_mate),
            )
        )

    for fact in moves:
        available = fact.eval_after_cp is not None or fact.eval_after_mate is not None
        cp_white = fact.eval_after_white
        mate_white = fact.mate_after_white
        points.append(
            TrajectoryPoint(
                ply=fact.ply,
                move_number=fact.move_number,
                san=fact.san,
                side_to_move=(
                    Color.BLACK if fact.mover == Color.WHITE else Color.WHITE
                ),
                evaluation_cp_white=cp_white,
                mate_white=mate_white,
                available=available,
                white_state=state_for(cp_white, mate_white, Color.WHITE, policy),
                black_state=state_for(cp_white, mate_white, Color.BLACK, policy),
                material_balance=balances.get(fact.ply),
                classification=fact.classification.value if fact.classification else None,
                evaluation_display=(
                    format_evaluation(cp_white, mate_white) if available else "—"
                ),
                white_win_expectation=(
                    win_expectation_white(cp_white, mate_white) if available else None
                ),
            )
        )

    evaluated = [point for point in points if point.available and point.evaluation_cp_white is not None]
    mate_plies = [point for point in evaluated if point.mate_white is not None]
    values = [
        point.evaluation_cp_white
        for point in evaluated
        if point.mate_white is None and point.evaluation_cp_white is not None
    ]

    segments = _segments(points)
    events = _events(points, result=result)
    states_seen: list[str] = []
    for point in points:
        state = point.white_state
        if state is not None and state.value not in states_seen:
            states_seen.append(state.value)

    final_point = next(
        (point for point in reversed(points) if point.available), None
    )
    return GameTrajectory(
        points=points,
        segments=segments,
        events=events,
        evaluated_plies=len(evaluated),
        missing_plies=len([p for p in points if not p.available]),
        mate_plies=len(mate_plies),
        min_evaluation_white=min(values) if values else None,
        max_evaluation_white=max(values) if values else None,
        final_evaluation_white=final_point.evaluation_cp_white if final_point else None,
        final_evaluation_display=final_point.evaluation_display if final_point else None,
        states_seen=states_seen,
    )


def initial_balance_from_fen(fen: str) -> int:
    """Material balance (pawns, White perspective) of a FEN."""
    board = chess.Board(fen)
    return material_points(board, chess.WHITE) - material_points(board, chess.BLACK)


def _segments(points: list[TrajectoryPoint]) -> list[TrajectorySegment]:
    """Group consecutive plies sharing the same White-perspective state."""
    segments: list[TrajectorySegment] = []
    current_state: AdvantageState | None = None
    start_ply: int | None = None
    last_ply: int | None = None

    for point in points:
        state = point.white_state if point.available else None
        if state is None:
            if start_ply is not None and last_ply is not None:
                segments.append(
                    TrajectorySegment(
                        state=current_state,
                        start_ply=start_ply,
                        end_ply=last_ply,
                        length=last_ply - start_ply + 1,
                        stable=(last_ply - start_ply + 1) >= STABILIZATION_PLIES,
                    )
                )
            current_state, start_ply, last_ply = None, None, None
            continue
        if state != current_state:
            if start_ply is not None and last_ply is not None:
                segments.append(
                    TrajectorySegment(
                        state=current_state,
                        start_ply=start_ply,
                        end_ply=last_ply,
                        length=last_ply - start_ply + 1,
                        stable=(last_ply - start_ply + 1) >= STABILIZATION_PLIES,
                    )
                )
            current_state, start_ply = state, point.ply
        last_ply = point.ply

    if start_ply is not None and last_ply is not None:
        segments.append(
            TrajectorySegment(
                state=current_state,
                start_ply=start_ply,
                end_ply=last_ply,
                length=last_ply - start_ply + 1,
                stable=(last_ply - start_ply + 1) >= STABILIZATION_PLIES,
            )
        )
    return segments


def _events(points: list[TrajectoryPoint], *, result: str) -> list[TrajectoryEvent]:
    """Derive analytical states from the trajectory."""
    events: list[TrajectoryEvent] = []
    usable = [point for point in points if point.available and point.white_state is not None]
    if len(usable) < 2:
        return events

    for color in (Color.WHITE, Color.BLACK):
        series = [
            (point.ply, band(_point_state_label(point, color))) for point in usable
        ]
        series = [(ply, value) for ply, value in series if value is not None]
        if len(series) < 2:
            continue

        # Track exclusive peak/valley for this side and the transitions between.
        for index in range(1, len(series)):
            ply, current = series[index]
            previous_ply, previous = series[index - 1]
            if current == previous:
                continue
            delta = current - previous
            if delta >= MATERIAL_BAND_DELTA:
                events.append(
                    TrajectoryEvent(
                        type=TrajectoryEventType.ADVANTAGE_CREATION,
                        side=color,
                        start_ply=previous_ply,
                        end_ply=ply,
                        statement=(
                            f"{color_label(color)}'s advantage moved from band {previous} to "
                            f"band {current} between ply {previous_ply} and ply {ply}."
                        ),
                        evidence={"band_before": previous, "band_after": current},
                    )
                )
            elif delta <= -MATERIAL_BAND_DELTA:
                events.append(
                    TrajectoryEvent(
                        type=TrajectoryEventType.ADVANTAGE_LOSS,
                        side=color,
                        start_ply=previous_ply,
                        end_ply=ply,
                        statement=(
                            f"{color_label(color)}'s advantage moved from band {previous} to "
                            f"band {current} between ply {previous_ply} and ply {ply}."
                        ),
                        evidence={"band_before": previous, "band_after": current},
                    )
                )
            if previous <= -2 and current >= 0:
                events.append(
                    TrajectoryEvent(
                        type=TrajectoryEventType.COMEBACK,
                        side=color,
                        start_ply=previous_ply,
                        end_ply=ply,
                        statement=(
                            f"{color_label(color)} returned to at least an equal position at "
                            f"ply {ply} after being two bands or more behind."
                        ),
                        evidence={"band_before": previous, "band_after": current},
                    )
                )
            if previous >= 2 and current <= -2:
                events.append(
                    TrajectoryEvent(
                        type=TrajectoryEventType.COLLAPSE,
                        side=color,
                        start_ply=previous_ply,
                        end_ply=ply,
                        statement=(
                            f"{color_label(color)}'s position fell from band {previous} to band "
                            f"{current} between ply {previous_ply} and ply {ply}."
                        ),
                        evidence={"band_before": previous, "band_after": current},
                    )
                )

        # Conversion: the side was winning and the scoreline matches.
        peak_ply, peak_band = max(series, key=lambda item: item[1])
        if peak_band >= 3:
            won = (result == "1-0" and color == Color.WHITE) or (
                result == "0-1" and color == Color.BLACK
            )
            if won:
                events.append(
                    TrajectoryEvent(
                        type=TrajectoryEventType.CONVERSION,
                        side=color,
                        start_ply=peak_ply,
                        end_ply=series[-1][0],
                        statement=(
                            f"{color_label(color)} reached a winning evaluation at ply {peak_ply} "
                            f"and won the game."
                        ),
                        evidence={"peak_ply": peak_ply, "peak_band": peak_band, "result": result},
                    )
                )

    # Stabilization: long stretches without a state change.
    for segment in _segments(points):
        if segment.stable:
            events.append(
                TrajectoryEvent(
                    type=TrajectoryEventType.STABILIZATION,
                    side=Color.WHITE,
                    start_ply=segment.start_ply,
                    end_ply=segment.end_ply,
                    statement=(
                        f"The evaluation stayed in the '{segment.state.label if segment.state else 'unknown'}' "
                        f"band for {segment.length} plies from ply {segment.start_ply}."
                    ),
                    evidence={"length": segment.length, "state": segment.state.value if segment.state else None},
                )
            )

    events.sort(key=lambda event: (event.start_ply, event.type.value))
    return _dedupe(events)


def _dedupe(events: list[TrajectoryEvent]) -> list[TrajectoryEvent]:
    """Drop repeated events of the same type and side inside one window."""
    kept: list[TrajectoryEvent] = []
    for event in events:
        duplicate = next(
            (
                other
                for other in kept
                if other.type is event.type
                and other.side is event.side
                and abs(other.end_ply - event.end_ply) <= TRANSITION_WINDOW_PLIES
            ),
            None,
        )
        if duplicate is None:
            kept.append(event)
    return kept


def trajectory_state_at(trajectory: GameTrajectory, ply: int, color: Color) -> AdvantageState | None:
    """Advantage state for ``color`` at ``ply`` (``None`` when unavailable)."""
    for point in trajectory.points:
        if point.ply == ply:
            return _point_state_label(point, color)
    return None
