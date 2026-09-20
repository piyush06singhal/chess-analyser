"""Derived ML feature engineering.

Separates raw chess features (board-derived, in ``argus.analysis.features``)
from derived ML features aggregated over a game (e.g. how long a player stayed
behind on material). Raw features are never mutated; builders consume them and
emit new, named derived features.

Phase 1 ships the interface plus one verified transformation; the catalogue
grows with the ML phase.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from argus.analysis.features.models import RawPositionFeatures


class FeatureBuilder(ABC):
    """Derives ML features from raw per-position features."""

    @abstractmethod
    def feature_names(self) -> list[str]:
        """Names of the derived features this builder emits (stable order)."""

    @abstractmethod
    def build(self, raw_features: list[RawPositionFeatures]) -> dict[str, list[Any]]:
        """Derive features over an ordered sequence of game positions."""


class MaterialAdvantageDurationBuilder(FeatureBuilder):
    """Counts plies spent ahead/behind/equal on material, per side.

    Derived from the raw ``material_balance`` feature (white minus black).
    """

    def feature_names(self) -> list[str]:
        return [
            "material_advantage_plies_white",
            "material_disadvantage_plies_white",
            "material_equal_plies",
        ]

    def build(self, raw_features: list[RawPositionFeatures]) -> dict[str, list[Any]]:
        advantage = sum(1 for f in raw_features if f.material_balance > 0)
        disadvantage = sum(1 for f in raw_features if f.material_balance < 0)
        equal = sum(1 for f in raw_features if f.material_balance == 0)
        return {
            "material_advantage_plies_white": [advantage],
            "material_disadvantage_plies_white": [disadvantage],
            "material_equal_plies": [equal],
        }
