"""Configurable move classification.

Classification maps engine measurements (centipawn loss, best-move match,
second-best gap, material sacrifice) onto move-quality labels. Thresholds live
in a single :class:`MoveClassificationPolicy` and are deliberately conservative
starting heuristics — to be calibrated with real data later, and **never**
presented as a universal ground truth.

Honesty rules:
- Moves with unavailable evaluations are NOT classified (returns ``None``).
- ``brilliant`` requires positive proof: the played move must be the engine's
  best, give up material, and beat the second-best line by a clear margin.
  Any missing evidence means the move is never labelled brilliant.
- ``excellent`` is separated from ``best``/``good`` so a near-best move is not
  overstated as the engine's top choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field


class MoveClassification(str, Enum):
    """Move quality labels used across reports and the UI (best → worst)."""

    BRILLIANT = "brilliant"
    BEST = "best"
    EXCELLENT = "excellent"
    GOOD = "good"
    INACCURATE = "inaccurate"
    MISTAKE = "mistake"
    BLUNDER = "blunder"


#: Severity ordering for aggregations and "worst move" logic (higher = worse).
#: ``brilliant`` is a *quality* label rather than a severity, so it sits with
#: the good side of the scale.
SEVERITY: dict[MoveClassification, int] = {
    MoveClassification.BRILLIANT: -1,
    MoveClassification.BEST: 0,
    MoveClassification.EXCELLENT: 1,
    MoveClassification.GOOD: 2,
    MoveClassification.INACCURATE: 3,
    MoveClassification.MISTAKE: 4,
    MoveClassification.BLUNDER: 5,
}

#: Classifications that generally warrant a second look in a game report.
PROBLEM_CLASSIFICATIONS = {
    MoveClassification.INACCURATE,
    MoveClassification.MISTAKE,
    MoveClassification.BLUNDER,
}


@dataclass(frozen=True)
class MoveClassificationPolicy:
    """Centipawn-loss boundaries and contextual rules for classification.

    All values are configurable so the policy can be tuned per depth/dataset
    without touching code. Defaults are documented starting heuristics.
    """

    best_max_cp_loss: int = 10
    excellent_max_cp_loss: int = 25
    good_max_cp_loss: int = 50
    inaccurate_max_cp_loss: int = 100
    mistake_max_cp_loss: int = 300
    # Brilliancy is opt-in and evidence-gated (see module docstring).
    brilliant_enabled: bool = True
    brilliant_min_second_best_gap: int = 150
    brilliant_requires_best_move: bool = True
    brilliant_requires_sacrifice: bool = True

    def describe(self) -> dict:
        """Reproducibility record of the policy actually used."""
        return {
            "best_max_cp_loss": self.best_max_cp_loss,
            "excellent_max_cp_loss": self.excellent_max_cp_loss,
            "good_max_cp_loss": self.good_max_cp_loss,
            "inaccurate_max_cp_loss": self.inaccurate_max_cp_loss,
            "mistake_max_cp_loss": self.mistake_max_cp_loss,
            "brilliant_enabled": self.brilliant_enabled,
            "brilliant_min_second_best_gap": self.brilliant_min_second_best_gap,
        }


#: Backwards-compatible alias (Phase 1 name).
ClassificationThresholds = MoveClassificationPolicy


class MoveClassificationInput(BaseModel):
    """Engine evidence available for classifying a single move."""

    centipawn_loss: int | None = None
    is_best_move: bool = False
    second_best_gap: int | None = Field(
        default=None,
        description="best_cp minus second-best cp (mover perspective); None when MultiPV < 2",
    )
    sacrifices_material: bool | None = Field(
        default=None,
        description="True when the move gives up material; None when unknown",
    )


def _is_brilliant(
    move_input: MoveClassificationInput, policy: MoveClassificationPolicy
) -> bool:
    """Brilliancy gate: every required piece of evidence must be present."""
    if not policy.brilliant_enabled:
        return False
    if policy.brilliant_requires_best_move and not move_input.is_best_move:
        return False
    if policy.brilliant_requires_sacrifice and move_input.sacrifices_material is not True:
        return False
    return (
        move_input.second_best_gap is not None
        and move_input.second_best_gap >= policy.brilliant_min_second_best_gap
    )


def classify_move(
    move_input: MoveClassificationInput,
    policy: MoveClassificationPolicy | None = None,
) -> MoveClassification | None:
    """Classify a move from engine evidence.

    Returns ``None`` when the evaluation is unavailable — an unclassified move
    is more honest than a guessed label.
    """
    limits = policy or MoveClassificationPolicy()
    cpl = move_input.centipawn_loss
    if cpl is None:
        return None

    if _is_brilliant(move_input, limits):
        return MoveClassification.BRILLIANT
    if move_input.is_best_move or cpl <= limits.best_max_cp_loss:
        return MoveClassification.BEST
    if cpl <= limits.excellent_max_cp_loss:
        return MoveClassification.EXCELLENT
    if cpl <= limits.good_max_cp_loss:
        return MoveClassification.GOOD
    if cpl <= limits.inaccurate_max_cp_loss:
        return MoveClassification.INACCURATE
    if cpl <= limits.mistake_max_cp_loss:
        return MoveClassification.MISTAKE
    return MoveClassification.BLUNDER
