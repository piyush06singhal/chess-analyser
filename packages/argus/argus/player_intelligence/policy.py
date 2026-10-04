"""Evidence policy for player-level claims.

Phase 5 aggregates *many* games. The failure mode of every "player profile"
feature is over-claiming from too little data, so the thresholds that decide
what may be said live in one configurable, documented place — this module.

Three levels of statement are distinguished, exactly as the phase objective
requires:

``OBSERVATION``
    A count over the available games/events. Always allowed, always printed
    with its sample size. ("Two tactical errors were observed.")
``PATTERN``
    The same measurable thing recurring across independent games above the
    pattern thresholds. ("Tactical errors recurred in 4 of 31 games.")
``TENDENCY``
    A player-level claim supported by enough games and enough coverage.
    ("Across 27 analyzed games, tactical errors account for 31% of the
    player's significant mistakes.")

Nothing above ``OBSERVATION`` is emitted unless its thresholds are met; the
``Coverage`` enum is what the UI shows when they are not.

Every default here is deliberately conservative and is a *documented starting
policy*, not a statistical truth. They are configurable, and the values that
were actually used travel with every generated profile.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


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


#: Coverage bands by analyzed games. Chosen so the labels describe *data*,
#: not the player: 1 game cannot support a profile, ~5 games are limited,
#: 20+ analyzed games are the point where repeated patterns become usable.
COVERAGE_BANDS: tuple[tuple[int, Coverage], ...] = (
    (20, Coverage.ROBUST),
    (5, Coverage.MODERATE),
    (2, Coverage.LIMITED),
    (0, Coverage.INSUFFICIENT),
)


class PlayerInsightPolicy(BaseModel):
    """Configurable minimum-sample policy for player-level statements.

    The numbers are conservative starting values; changing them changes what
    the product is willing to claim, so the effective policy is stored with
    every profile and is part of the profile signature.
    """

    # --- games ---------------------------------------------------------------
    #: Below this many analyzed games, no profile sections are computed at all.
    min_games_for_profile: int = 2
    #: Below this many games, aggregate *statistics* are shown but no tendency.
    min_games_for_tendency: int = 20
    #: "Substantially larger evidence base" for a strong player-level claim.
    min_games_for_strong_claim: int = 50
    #: Per-colour claims need this many games *in that colour*.
    min_games_per_color_for_claim: int = 5
    #: Per-opening performance claims need this many games with that opening.
    min_games_per_opening_for_claim: int = 4
    #: Per-time-control claims need this many games in that class.
    min_games_per_time_control_for_claim: int = 5
    #: Per-phase claims need this many *evaluated moves* in that phase.
    min_phase_moves_for_claim: int = 20
    #: Trend analysis: recent window and baseline both need this many games.
    min_games_for_trend: int = 10
    min_recent_games_for_trend: int = 5
    #: Rating-context claims need this many games with a known opponent rating.
    min_games_with_opponent_rating: int = 5

    # --- events / patterns ---------------------------------------------------
    #: A recurring pattern needs at least this many occurrences …
    min_pattern_occurrences: int = 4
    #: … spread across at least this many distinct games …
    min_pattern_games: int = 4
    #: … covering at least this share of the player's games …
    min_pattern_coverage: float = Field(default=0.25, ge=0.0, le=1.0)
    #: … and this share of that side's relevant events (consistency).
    min_pattern_consistency: float = Field(default=0.20, ge=0.0, le=1.0)

    # --- trends --------------------------------------------------------------
    #: Rolling windows evaluated for recent-vs-historical comparison.
    trend_windows: tuple[int, ...] = (5, 10, 20)
    #: Minimum relative change before a difference is reported at all
    #: (a trend is still never called an "improvement").
    trend_min_relative_change: float = Field(default=0.05, ge=0.0)

    # --- conversion / recovery (documented evaluation thresholds) -----------
    #: Advantage band (in the Caissa advantage scale) counted as an opportunity
    #: to convert: 3 == ``winning``.
    conversion_min_band: int = 3
    #: Disadvantage band counted as a recovery situation: -3 == ``losing``.
    recovery_max_band: int = -3

    def coverage_for(self, analyzed_games: int) -> Coverage:
        """Coverage band for a number of analyzed games."""
        for threshold, coverage in COVERAGE_BANDS:
            if analyzed_games >= threshold:
                return coverage
        return Coverage.INSUFFICIENT

    def can_profile(self, analyzed_games: int) -> bool:
        return analyzed_games >= self.min_games_for_profile

    def can_claim_tendency(self, analyzed_games: int) -> bool:
        return analyzed_games >= self.min_games_for_tendency

    def pattern_is_supported(self, *, occurrences: int, games: int, coverage: float,
                             consistency: float) -> bool:
        """Whether a recurring pattern clears every documented bar."""
        return (
            occurrences >= self.min_pattern_occurrences
            and games >= self.min_pattern_games
            and coverage >= self.min_pattern_coverage
            and consistency >= self.min_pattern_consistency
        )

    def describes(self) -> dict[str, object]:
        """Reproducibility record of the policy actually used."""
        return self.model_dump(mode="json")


DEFAULT_POLICY = PlayerInsightPolicy()
