"""Evidence policy for opponent intelligence (Phase 9).

Phase 9 answers a narrow, honest question:

    What can the *available game data* tell me about this opponent's chess
    tendencies, repertoire, recurring patterns, and preparation opportunities?

It is an **analytics** system. It does not profile psychology, it does not
predict results, and it never states more than the stored games support. Every
threshold that decides *how strong a statement may be* lives here, in one
configurable, documented place, so a report always carries the policy that
produced it.

Four sample-size gates are named by the spec and implemented here:

``min_games_for_repertoire_insight``
    Below this many games *as a colour*, no repertoire move is called a
    characteristic choice — the distribution is printed as raw counts only.
``min_occurrences_for_tendency``
    A recurring behaviour needs this many independent observations before it is
    promoted above an observation.
``min_positions_for_structure_insight``
    A structural/positional claim needs this many matching positions.
``min_games_for_phase_comparison``
    A phase comparison (e.g. "worse in the endgame than the middlegame") needs
    this many games contributing measured moves.

Nothing above ``OBSERVATION`` is emitted unless its gate is met; below the gate
the claim level is ``INSUFFICIENT`` and the UI says so instead of smoothing.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ClaimLevel(str, Enum):
    """How strong a statement the evidence supports."""

    INSUFFICIENT = "insufficient"
    OBSERVATION = "observation"
    PATTERN = "pattern"
    TENDENCY = "tendency"


class Coverage(str, Enum):
    """Data-coverage band shown to the user (never a judgement of play)."""

    INSUFFICIENT = "insufficient"
    LIMITED = "limited"
    MODERATE = "moderate"
    ROBUST = "robust"


#: Coverage bands by analyzed games *for the opponent*. Deliberately identical
#: in spirit to Phase 5's player bands: describe the data, never the player.
OPPONENT_COVERAGE_BANDS: tuple[tuple[int, Coverage], ...] = (
    (20, Coverage.ROBUST),
    (5, Coverage.MODERATE),
    (2, Coverage.LIMITED),
)


class OpponentInsightPolicy(BaseModel):
    """The documented, configurable thresholds of opponent intelligence."""

    # -- the four named sample-size gates ------------------------------------
    min_games_for_repertoire_insight: int = 3
    min_occurrences_for_tendency: int = 3
    min_positions_for_structure_insight: int = 3
    min_games_for_phase_comparison: int = 3

    # -- reinforcement above the gate ----------------------------------------
    #: A repertoire move must hold at least this share *of that colour's games*
    #: to be called a characteristic choice rather than one option among many.
    repertoire_share_for_pattern: float = 0.30
    #: A tendency whose share is at least this is a TENDENCY; below it, only a
    #: PATTERN, even when the occurrence gate is met.
    tendency_share_for_tendency: float = 0.40
    #: How many plies from the start count as the opening for repertoire
    #: purposes (analysis time and memory are bounded deliberately).
    repertoire_max_ply: int = 24
    #: A move repeated at least this many times is reported as an opening node
    #: regardless of the colour-game gate.
    min_repetitions_for_node: int = 2

    # -- measured thresholds for tendencies ----------------------------------
    #: Centipawn loss at or above this is a "significant mistake" for the
    #: opponent's error tendency.
    significant_loss_cp: int = 100
    #: An advantage (mover perspective) at or above this is "winning".
    winning_cp: int = 200
    #: An advantage at or below this is "losing".
    losing_cp: int = -200

    def gates(self) -> dict[str, int]:
        """The named sample-size gates, as they will be stored in a report."""
        return {
            "min_games_for_repertoire_insight": self.min_games_for_repertoire_insight,
            "min_occurrences_for_tendency": self.min_occurrences_for_tendency,
            "min_positions_for_structure_insight": self.min_positions_for_structure_insight,
            "min_games_for_phase_comparison": self.min_games_for_phase_comparison,
        }

    def to_dict(self) -> dict[str, float | int]:
        return {
            **self.gates(),
            "repertoire_share_for_pattern": self.repertoire_share_for_pattern,
            "tendency_share_for_tendency": self.tendency_share_for_tendency,
            "repertoire_max_ply": self.repertoire_max_ply,
            "min_repetitions_for_node": self.min_repetitions_for_node,
            "significant_loss_cp": self.significant_loss_cp,
            "winning_cp": self.winning_cp,
            "losing_cp": self.losing_cp,
        }


DEFAULT_POLICY = OpponentInsightPolicy()


def coverage_for(analyzed_games: int) -> Coverage:
    """Map an analyzed-game count onto a coverage band."""
    for minimum, band in OPPONENT_COVERAGE_BANDS:
        if analyzed_games >= minimum:
            return band
    return Coverage.INSUFFICIENT


__all__ = [
    "DEFAULT_POLICY",
    "OPPONENT_COVERAGE_BANDS",
    "ClaimLevel",
    "Coverage",
    "OpponentInsightPolicy",
    "coverage_for",
]
