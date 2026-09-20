"""Configurable move classification.

Classification maps engine measurements (centipawn loss, best-move match,
second-best gap) onto move quality labels. Thresholds are configurable and
deliberately conservative — they are starting heuristics, to be tuned with
real data in later phases, never presented as ground truth.

Honesty rules:
- Moves with unavailable evaluations are NOT classified (returns ``None``).
- "Brilliant" requires positive proof: the played move must be the engine's
  best, sacrifice material, and beat the second-best line by a clear margin.
  When any of that evidence is missing, the move is never labelled brilliant.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field


class MoveClassification(str, Enum):
    """Move quality labels used across reports and the UI."""

    BRILLIANT = "brilliant"
    BEST = "best"
    GOOD = "good"
    INACCURATE = "inaccurate"
    MISTAKE = "mistake"
    BLUNDER = "blunder"


@dataclass(frozen=True)
class ClassificationThresholds:
    """Centipawn-loss boundaries for classification (configurable).

    Defaults are simple, documented starting points. They will be calibrated
    against real datasets and engine depths in later phases.
    """

    best_max_cp_loss: int = 10
    good_max_cp_loss: int = 50
    inaccurate_max_cp_loss: int = 100
    mistake_max_cp_loss: int = 300
    brilliant_min_second_best_gap: int = 150


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


def classify_move(
    move_input: MoveClassificationInput,
    thresholds: ClassificationThresholds | None = None,
) -> MoveClassification | None:
    """Classify a move from engine evidence.

    Returns ``None`` when the evaluation is unavailable — an unclassified move
    is more honest than a guessed label.
    """
    limits = thresholds or ClassificationThresholds()
    cpl = move_input.centipawn_loss
    if cpl is None:
        return None

    if (
        move_input.is_best_move
        and move_input.sacrifices_material is True
        and move_input.second_best_gap is not None
        and move_input.second_best_gap >= limits.brilliant_min_second_best_gap
    ):
        return MoveClassification.BRILLIANT
    if cpl <= limits.best_max_cp_loss:
        return MoveClassification.BEST
    if cpl <= limits.good_max_cp_loss:
        return MoveClassification.GOOD
    if cpl <= limits.inaccurate_max_cp_loss:
        return MoveClassification.INACCURATE
    if cpl <= limits.mistake_max_cp_loss:
        return MoveClassification.MISTAKE
    return MoveClassification.BLUNDER
