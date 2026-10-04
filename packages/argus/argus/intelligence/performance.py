"""Phase-specific performance.

For each side and each phase (opening / middlegame / endgame) the layer reports:

* move counts and how many of them the engine actually evaluated,
* the mean centipawn loss (engine measurement),
* the mean evaluation change (mover perspective),
* classification counts,
* the number of tactical events attached to that side in that phase.

**Small samples are declared, not smoothed.** An endgame of five moves produces
statistics with ``small_sample: True`` and a note, because a mean over five
moves is indicative at best — Caissa says so instead of presenting it as a
finding.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from argus.analysis.phase import GamePhase
from argus.chess_core.models import Color
from argus.intelligence.base import (
    EvidenceSource,
    MoveFact,
    PhasePerformancePolicy,
    color_label,
)
from argus.intelligence.tactics import TacticalEvent

_SMALL_SAMPLE_NOTE = (
    "Fewer moves than Caissa's reliability threshold; treat these numbers as indicative only."
)


class PhaseSideStats(BaseModel):
    """Engine-derived statistics for one side in one phase."""

    phase: GamePhase
    side: Color
    moves: int
    evaluated_moves: int
    average_centipawn_loss: float | None = None
    average_evaluation_change_cp: float | None = None
    problem_moves: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    tactical_events: int = 0
    plies: list[int] = Field(
        default_factory=list, description="Plies of this side's moves in this phase (evidence refs)"
    )
    small_sample: bool = False
    note: str | None = None
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE


class PhasePerformance(BaseModel):
    """Phase statistics for both sides."""

    white: dict[str, PhaseSideStats] = Field(default_factory=dict)
    black: dict[str, PhaseSideStats] = Field(default_factory=dict)
    phase_move_counts: dict[str, int] = Field(default_factory=dict)
    worst_phase_white: str | None = Field(
        default=None, description="Phase with the largest mean centipawn loss for White"
    )
    worst_phase_black: str | None = None
    phase_source: str = Field(
        default="Caissa board-state detector (see phases.GamePhaseDetector)",
        description="Which phase classifier produced the per-ply labels",
    )
    note: str = (
        "Phase statistics are engine measurements grouped by phase. Sample sizes are "
        "always reported; small samples are flagged rather than smoothed over."
    )


def build_phase_performance(
    moves: list[MoveFact],
    *,
    phase_by_ply: dict[int, GamePhase],
    tactical_events: list[TacticalEvent] | None = None,
    policy: PhasePerformancePolicy | None = None,
) -> PhasePerformance:
    """Aggregate engine measurements per side and phase."""
    limits = policy or PhasePerformancePolicy()
    events = tactical_events or []
    tactical_by_ply: dict[int, int] = {}
    for event in events:
        tactical_by_ply[event.ply] = tactical_by_ply.get(event.ply, 0) + 1

    white: dict[str, PhaseSideStats] = {}
    black: dict[str, PhaseSideStats] = {}
    phase_move_counts: dict[str, int] = {}

    for phase in (GamePhase.OPENING, GamePhase.MIDDLEGAME, GamePhase.ENDGAME):
        for color, bucket in ((Color.WHITE, white), (Color.BLACK, black)):
            own = [
                fact
                for fact in moves
                if fact.mover == color and phase_by_ply.get(fact.ply) is phase
            ]
            if not own:
                continue
            losses = [fact.centipawn_loss for fact in own if fact.centipawn_loss is not None]
            changes = [fact.eval_change_cp for fact in own if fact.eval_change_cp is not None]
            counts: dict[str, int] = {}
            for fact in own:
                if fact.classification is not None:
                    counts[fact.classification.value] = (
                        counts.get(fact.classification.value, 0) + 1
                    )
            evaluated = len([fact for fact in own if fact.evaluated])
            small = len(own) < limits.reliable_min_moves or evaluated < limits.reliable_min_scored
            bucket[phase.value] = PhaseSideStats(
                phase=phase,
                side=color,
                moves=len(own),
                evaluated_moves=evaluated,
                average_centipawn_loss=round(sum(losses) / len(losses), 2) if losses else None,
                average_evaluation_change_cp=(
                    round(sum(changes) / len(changes), 2) if changes else None
                ),
                problem_moves=len([fact for fact in own if fact.is_problem]),
                counts=counts,
                tactical_events=sum(tactical_by_ply.get(fact.ply, 0) for fact in own),
                plies=[fact.ply for fact in own],
                small_sample=small,
                note=_SMALL_SAMPLE_NOTE if small else None,
            )

    for fact in moves:
        phase = phase_by_ply.get(fact.ply)
        if phase is None:
            continue
        phase_move_counts[phase.value] = phase_move_counts.get(phase.value, 0) + 1

    def worst(bucket: dict[str, PhaseSideStats]) -> str | None:
        scored = [
            (stats.average_centipawn_loss, stats.phase.value)
            for stats in bucket.values()
            if stats.average_centipawn_loss is not None
        ]
        return max(scored)[1] if scored else None

    return PhasePerformance(
        white=white,
        black=black,
        phase_move_counts=phase_move_counts,
        worst_phase_white=worst(white),
        worst_phase_black=worst(black),
    )


def phase_statement(performance: PhasePerformance, color: Color) -> str:
    """A factual sentence about one side's worst phase by mean centipawn loss."""
    bucket = performance.white if color == Color.WHITE else performance.black
    worst = (
        performance.worst_phase_white if color == Color.WHITE else performance.worst_phase_black
    )
    if worst is None or worst not in bucket:
        return f"{color_label(color)} has no phase with enough evaluated moves to compare."
    stats = bucket[worst]
    sample = " (small sample)" if stats.small_sample else ""
    return (
        f"{color_label(color)}'s largest mean centipawn loss came in the {worst} "
        f"({stats.average_centipawn_loss}cp over {stats.moves} moves{sample})."
    )
