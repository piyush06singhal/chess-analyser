"""Profile assembly and versioning.

``build_profile`` is the single entry point: measured per-game inputs in, one
versioned :class:`~argus.player_intelligence.models.PlayerProfile` out. It is a
pure function, so the API service can rebuild a profile at any time and get the
same snapshot for the same games — which is what makes the stored profile
auditable and the "rebuild" operation meaningful.

Versioning exists because a profile is an *interpretation*: when the
methodology changes, old snapshots must remain explicable. Three versions are
recorded:

``profile_version``
    the shape of the profile document,
``methodology_version``
    the aggregation/threshold rules that produced these numbers,
``feature_version``
    the ML feature contract (Phase 6 consumes this).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from argus.player_intelligence import aggregate
from argus.player_intelligence.dna import build_chess_dna
from argus.player_intelligence.models import ChessDna
from argus.player_intelligence.features import FEATURE_VERSION
from argus.player_intelligence.insights import build_insights
from argus.player_intelligence.models import PlayerGameInput, PlayerProfile
from argus.player_intelligence.policy import DEFAULT_POLICY, Coverage, PlayerInsightPolicy
from argus.player_intelligence.sanity import assert_profile_consistent

#: Bumped when the *shape* of the stored profile changes (new sections, renamed
#: fields) — one snapshot row exists per (player, profile_version).
PROFILE_VERSION = "5.0"
#: Bumped when how a number is *derived* changes, even if the shape does not:
#: the derivation goes into the staleness check, so a snapshot computed by an
#: older methodology is recomputed instead of being served next to new code.
#: 5.1 — evidence rows are de-duplicated by (game, ply, label) and an insight
#: title no longer says "recurring" before the repetition thresholds are met.
METHODOLOGY_VERSION = "5.1"

#: What the UI shows when there is not even one analyzed game.
INSUFFICIENT_DATA_MESSAGE = (
    "Not enough analyzed games for player-level pattern analysis. "
    "Import and analyze games for this player and the profile will build itself."
)


def build_profile(
    inputs: Sequence[PlayerGameInput],
    *,
    player_id: str,
    display_name: str,
    platform: str | None = None,
    platform_username: str | None = None,
    imported_games: int = 0,
    excluded_game_ids: Sequence[str] = (),
    policy: PlayerInsightPolicy | None = None,
    generated_at: datetime | None = None,
) -> PlayerProfile:
    """Aggregate analyzed games into a versioned player profile.

    ``imported_games`` and ``excluded_game_ids`` make the difference between
    "the player has 40 games" and "40 games have been analyzed" explicit: games
    without a stored report are counted and named, never dropped silently.
    """
    effective = policy or DEFAULT_POLICY
    games = list(inputs)
    timestamp = generated_at or datetime.now(timezone.utc)

    profile = PlayerProfile(
        player_id=player_id,
        display_name=display_name,
        platform=platform,
        platform_username=platform_username,
        profile_version=PROFILE_VERSION,
        methodology_version=METHODOLOGY_VERSION,
        feature_version=FEATURE_VERSION,
        generated_at=timestamp,
        last_updated_at=timestamp,
        imported_games=imported_games or len(games),
        analyzed_games=len(games),
        excluded_games=len(excluded_game_ids),
        excluded_game_ids=list(excluded_game_ids),
        policy=effective.describes(),
    )

    # Below the profile minimum, the profile is *deliberately* empty rather
    # than filled with weak numbers: the UI shows the coverage state instead.
    if not effective.can_profile(len(games)):
        profile.coverage = Coverage.INSUFFICIENT
        profile.sufficient_data = False
        profile.notes.append(INSUFFICIENT_DATA_MESSAGE)
        # The DNA section explains its own emptiness too, so a UI that renders
        # sections independently still says why instead of looking broken.
        profile.chess_dna = ChessDna(
            dimensions=[], derived_from_games=len(games), notes=[INSUFFICIENT_DATA_MESSAGE]
        )
        return profile

    profile.sufficient_data = True
    profile.coverage = effective.coverage_for(len(games))
    profile.games = aggregate.analyze_games(games, effective)
    profile.by_color = aggregate.by_color(games, effective)
    profile.openings = aggregate.openings(games, effective)
    profile.phases = aggregate.phases(games, effective)
    profile.tactical = aggregate.tactical(games, effective)
    profile.positional = aggregate.positional(games, effective)
    profile.king_safety = aggregate.king_safety(games, effective)
    profile.material = aggregate.material_stats(games, effective)
    profile.conversion = aggregate.conversion(games, effective)
    profile.recovery = aggregate.recovery(games, effective)
    profile.time_controls = aggregate.time_controls(games, effective)
    profile.opponents = aggregate.opponents(games, effective)
    profile.trends = aggregate.trends(games, effective)
    profile.chess_dna = build_chess_dna(
        games,
        effective,
        games=profile.games,
        tactical=profile.tactical,
        positional=profile.positional,
        king_safety=profile.king_safety,
        material=profile.material,
        openings=profile.openings,
        phases=profile.phases,
        conversion=profile.conversion,
    )
    profile.insights = build_insights(
        profile, games, effective, methodology_version=METHODOLOGY_VERSION
    )

    if profile.coverage is not Coverage.ROBUST:
        profile.notes.append(
            f"{len(games)} analyzed games is {profile.coverage.value} coverage. "
            f"Player-level tendencies need {effective.min_games_for_tendency} analyzed games; "
            "until then everything above is an observation over this sample."
        )

    assert_profile_consistent(profile, policy=effective)
    return profile


__all__ = [
    "INSUFFICIENT_DATA_MESSAGE",
    "METHODOLOGY_VERSION",
    "PROFILE_VERSION",
    "build_profile",
]
