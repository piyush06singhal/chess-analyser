"""Advantage states.

A normalized vocabulary for "who is better and by how much", derived from the
engine evaluation (White perspective) using **configurable, documented Caissa
thresholds**:

===========================  ============================
state                        centipawn range (mover view)
===========================  ============================
forced_mate                  engine reports mate for the side
winning                      >= 300
advantage                    150 .. 299
slight_advantage             60 .. 149
equal                        -59 .. 59
slight_disadvantage          -149 .. -60
disadvantage                 -299 .. -150
losing                       <= -300
===========================  ============================

These are **Caissa analytical categories**, not universal chess truths: another
system with different thresholds would label the same position differently. The
thresholds live in :class:`argus.intelligence.base.AdvantagePolicy` so a report
can always state which bands were used.
"""

from __future__ import annotations

from enum import Enum

from argus.chess_core.models import Color
from argus.intelligence.base import AdvantagePolicy


class AdvantageState(str, Enum):
    """Normalized advantage categories (from one side's point of view)."""

    FORCED_MATE = "forced_mate"
    WINNING = "winning"
    ADVANTAGE = "advantage"
    SLIGHT_ADVANTAGE = "slight_advantage"
    EQUAL = "equal"
    SLIGHT_DISADVANTAGE = "slight_disadvantage"
    DISADVANTAGE = "disadvantage"
    LOSING = "losing"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


#: Ordinal band for a state, from the point of view of the side it describes.
BANDS: dict[AdvantageState, int] = {
    AdvantageState.FORCED_MATE: 4,
    AdvantageState.WINNING: 3,
    AdvantageState.ADVANTAGE: 2,
    AdvantageState.SLIGHT_ADVANTAGE: 1,
    AdvantageState.EQUAL: 0,
    AdvantageState.SLIGHT_DISADVANTAGE: -1,
    AdvantageState.DISADVANTAGE: -2,
    AdvantageState.LOSING: -3,
}


def band(state: AdvantageState | None) -> int | None:
    """Ordinal band of a state (``None`` when the state is unknown)."""
    return None if state is None else BANDS[state]


def state_for(
    cp_white: int | None,
    mate_white: int | None,
    color: Color,
    policy: AdvantagePolicy | None = None,
) -> AdvantageState | None:
    """Advantage state from ``color``'s point of view.

    ``cp_white``/``mate_white`` are White-perspective engine values (the stored
    Caissa convention). Returns ``None`` when the engine produced no evaluation —
    an unknown position is reported as unknown, never as "equal".
    """
    limits = policy or AdvantagePolicy()
    if mate_white is not None:
        mates_for_white = mate_white > 0
        mates_for_color = mates_for_white if color == Color.WHITE else not mates_for_white
        return AdvantageState.FORCED_MATE if mates_for_color else AdvantageState.LOSING
    if cp_white is None:
        return None
    value = cp_white if color == Color.WHITE else -cp_white
    if value >= limits.winning_threshold:
        return AdvantageState.WINNING
    if value >= limits.advantage_threshold:
        return AdvantageState.ADVANTAGE
    if value >= limits.slight_threshold:
        return AdvantageState.SLIGHT_ADVANTAGE
    if value > -limits.slight_threshold:
        return AdvantageState.EQUAL
    if value > -limits.advantage_threshold:
        return AdvantageState.SLIGHT_DISADVANTAGE
    if value > -limits.winning_threshold:
        return AdvantageState.DISADVANTAGE
    return AdvantageState.LOSING


def state_label_for(state: AdvantageState | None, color: Color) -> str:
    """Display label of a state from a side's point of view."""
    if state is None:
        return "unknown"
    prefix = "White" if color == Color.WHITE else "Black"
    return f"{prefix} {state.label}"


def describe_policy(policy: AdvantagePolicy | None = None) -> dict:
    """The exact thresholds a report used (kept in the report for auditability)."""
    limits = policy or AdvantagePolicy()
    return {
        "slight_threshold_cp": limits.slight_threshold,
        "advantage_threshold_cp": limits.advantage_threshold,
        "winning_threshold_cp": limits.winning_threshold,
        "decisive_threshold_cp": limits.decisive_threshold,
        "note": (
            "Caissa analytical bands, not universal chess truths. "
            "forced_mate is reported only when the engine evaluation contains a mate score."
        ),
    }
