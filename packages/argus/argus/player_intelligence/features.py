"""ML-ready player features — definitions and provenance, no models.

Phase 5 does **not** train anything. It does, however, define the feature
contract a later phase can consume, together with the metadata that makes a
dataset honest: what each feature means, which version produced it, over what
date range, and from how many games.

Two properties matter more than the values themselves:

``USER-SPECIFIC DATA``
    Every feature here is derived from one player's private games. The record
    carries ``user_specific=True`` and ``training_eligible=False`` so a future
    global dataset builder has to make an explicit, deliberate decision before
    any user data could ever enter a shared training set (spec §34).
``SAMPLE SIZE``
    A feature without a sample size is indistinguishable from a guess, so the
    sample travels with every value.

This is the boundary the Phase 6 dataset-engineering work will build on.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from argus.player_intelligence.models import PlayerGameInput, PlayerProfile

#: Bumped whenever a feature's *meaning* changes — never silently reused.
FEATURE_VERSION = "5.0"


class PlayerFeature(BaseModel):
    """One named player-level feature with its methodology attached."""

    name: str
    value: float | None = None
    unit: str = ""
    definition: str
    sample_size: int = 0
    source: str = "player_intelligence"
    feature_version: str = FEATURE_VERSION
    #: This value came from one player's private games.
    user_specific: bool = True
    #: It must not be pooled into a global training set without an explicit
    #: decision (privacy separation, spec §34).
    training_eligible: bool = False
    note: str | None = None


class PlayerFeatureSet(BaseModel):
    """A player's feature vector plus the metadata a dataset needs."""

    player_id: str
    feature_version: str = FEATURE_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    games_analyzed: int = 0
    data_range: tuple[str | None, str | None] = (None, None)
    features: list[PlayerFeature] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict[str, float | None]:
        """Flat ``name → value`` mapping (missing values stay ``None``)."""
        return {feature.name: feature.value for feature in self.features}


#: Feature name → (unit, definition). The definition is the contract.
FEATURE_DEFINITIONS: dict[str, tuple[str, str]] = {
    "games_analyzed": ("games", "Number of games with stored engine analysis included in the profile."),
    "average_cpl": ("centipawns", "Mean of the player's per-game average centipawn loss (Caissa definition)."),
    "median_cpl": ("centipawns", "Median of the player's per-game average centipawn loss."),
    "average_accuracy": ("percent", "Mean of the player's per-game Caissa accuracy, where scored."),
    "blunders_per_game": ("moves", "Engine-classified blunders per analyzed game."),
    "mistakes_per_game": ("moves", "Engine-classified mistakes per analyzed game."),
    "tactical_error_rate": ("errors per game", "Confirmed tactical error candidates per analyzed game."),
    "positional_error_rate": ("errors per game", "Positional error candidates per analyzed game."),
    "opening_deviation_rate": ("share of games", "Share of games where the player left the Caissa opening table."),
    "conversion_rate": ("share of opportunities", "Share of games with a winning evaluation that were converted."),
    "recovery_rate": ("share of situations", "Share of losing positions where the evaluation improved."),
    "endgame_error_rate": ("centipawns", "Average centipawn loss in detected endgames."),
    "king_safety_error_rate": ("events per game", "King-safety events recorded against the player per game."),
    "average_opponent_rating": ("elo", "Mean opponent rating across games with a known rating."),
    "rating_difference": ("elo", "Mean of player rating minus opponent rating."),
    "time_control_distribution": ("share of games", "Share of analyzed games in the most frequent time class."),
    "opening_diversity": ("distinct openings per game", "Distinct openings divided by analyzed games."),
    "material_imbalance_tendency": ("share of games", "Share of games where material left equality."),
}


def extract_features(
    profile: PlayerProfile,
    inputs: list[PlayerGameInput],
    *,
    games_analyzed: int,
) -> PlayerFeatureSet:
    """Build the feature set from a computed profile (pure; no training)."""
    games = profile.games
    tactical_errors = sum(
        1 for game in inputs for error in game.errors if "tactical" in error.categories
    )
    positional_errors = sum(
        1 for game in inputs for event in game.positional_events if event.kind == "error_candidate"
    )
    king_safety_against = sum(
        1 for game in inputs for event in game.king_safety_events if event.direction == "allowed"
    )
    endgame_entries = [entry for entry in profile.phases.phases if entry.phase == "endgame"]
    time_shares = {entry.time_class: entry.games for entry in profile.time_controls.entries}

    def feature(name: str, value: float | None, sample: int, note: str | None = None) -> PlayerFeature:
        unit, definition = FEATURE_DEFINITIONS[name]
        return PlayerFeature(
            name=name, value=value, unit=unit, definition=definition,
            sample_size=sample, note=note,
        )

    per_game = lambda total: round(total / games_analyzed, 4) if games_analyzed else None  # noqa: E731
    # Look dimensions up by key: a positional index would silently attach the
    # wrong dimension to a feature if the DNA ordering ever changed.
    dna_by_key = {dimension.key: dimension for dimension in profile.chess_dna.dimensions}

    features = [
        feature("games_analyzed", float(games_analyzed), games_analyzed),
        feature("average_cpl", games.average_centipawn_loss, games.accuracy_sample),
        feature("median_cpl", games.median_centipawn_loss, games.accuracy_sample),
        feature("average_accuracy", games.average_accuracy, games.accuracy_sample),
        feature("blunders_per_game", games.blunders_per_game, games_analyzed),
        feature("mistakes_per_game", games.mistakes_per_game, games_analyzed),
        feature("tactical_error_rate", per_game(tactical_errors), games_analyzed),
        feature("positional_error_rate", per_game(positional_errors), games_analyzed),
        feature("opening_deviation_rate", profile.openings.deviation_rate, games_analyzed),
        feature("conversion_rate", profile.conversion.conversion_rate, profile.conversion.opportunities),
        feature("recovery_rate", profile.recovery.improvement_rate, profile.recovery.situations),
        feature(
            "endgame_error_rate",
            endgame_entries[0].average_centipawn_loss if endgame_entries else None,
            sum(entry.evaluated_moves for entry in endgame_entries),
        ),
        feature("king_safety_error_rate", per_game(king_safety_against), games_analyzed),
        feature("average_opponent_rating", profile.opponents.average_opponent_rating,
                profile.opponents.games_with_rating),
        feature("rating_difference", profile.opponents.average_rating_difference,
                profile.opponents.games_with_rating),
        feature(
            "time_control_distribution",
            (max(time_shares.values()) / games_analyzed) if (time_shares and games_analyzed) else None,
            games_analyzed,
            note=(
                "Most frequent time class: "
                f"{max(time_shares, key=time_shares.get).value}" if time_shares else None
            ),
        ),
        feature(
            "opening_diversity",
            dna_by_key["opening_diversity"].value if "opening_diversity" in dna_by_key else None,
            games_analyzed,
        ),
        feature("material_imbalance_tendency", profile.material.imbalance_share, games_analyzed),
    ]

    return PlayerFeatureSet(
        player_id=profile.player_id,
        games_analyzed=games_analyzed,
        data_range=games.time_span,
        features=features,
        notes=[
            "Every feature is derived from this player's own analyzed games "
            "(user-specific data) and is not eligible for a global training set by default.",
            f"Feature version {FEATURE_VERSION}. Features without a sample size are None by design.",
        ],
    )


__all__ = ["FEATURE_DEFINITIONS", "FEATURE_VERSION", "PlayerFeature", "PlayerFeatureSet", "extract_features"]
