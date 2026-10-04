"""Statistical sanity checks for a player profile.

An aggregate that does not add up is worse than no aggregate: it looks like a
measurement. These invariants are asserted on every profile the service
produces, and the test suite exercises them directly.

They are deliberately *structural* checks (do the sums reconcile, are rates in
range, can a claim outrun its sample) rather than statistical tests of
significance, which this phase explicitly does not attempt.
"""

from __future__ import annotations

from argus.player_intelligence.models import PlayerProfile
from argus.player_intelligence.policy import ClaimLevel, Coverage, PlayerInsightPolicy


class ProfileSanityError(ValueError):
    """Raised when a generated profile violates a documented invariant."""


def _check_rate(issues: list[str], name: str, value: float | None) -> None:
    if value is None:
        return
    if not 0.0 <= value <= 1.0:
        issues.append(f"{name} must be a share in [0, 1], got {value}")


def validate_profile(profile: PlayerProfile, *, policy: PlayerInsightPolicy | None = None) -> list[str]:
    """Return the list of invariant violations (empty means consistent)."""
    issues: list[str] = []
    games = profile.games

    # --- counts reconcile -----------------------------------------------------
    if games.wins + games.draws + games.losses != games.analyzed_games:
        issues.append(
            f"wins+draws+losses ({games.wins + games.draws + games.losses}) must equal "
            f"analyzed_games ({games.analyzed_games})"
        )
    color_games = sum(entry.games for entry in profile.by_color)
    if color_games != games.analyzed_games:
        issues.append(
            f"white+black games ({color_games}) must equal analyzed_games ({games.analyzed_games})"
        )
    # The import accounting is a profile-level fact: a profile below the minimum
    # deliberately has no sections, but the games it saw are still counted.
    if profile.imported_games:
        if profile.analyzed_games + profile.excluded_games != profile.imported_games:
            issues.append(
                f"analyzed ({profile.analyzed_games}) + excluded ({profile.excluded_games}) must equal "
                f"imported ({profile.imported_games})"
            )
        if profile.sufficient_data and games.analyzed_games != profile.analyzed_games:
            issues.append(
                f"games section carries {games.analyzed_games} analyzed games but the profile claims "
                f"{profile.analyzed_games}"
            )

    for name, value in (
        ("win_rate", games.win_rate),
        ("draw_rate", games.draw_rate),
        ("loss_rate", games.loss_rate),
        ("opening_deviations", profile.openings.deviation_rate),
        ("material_imbalance", profile.material.imbalance_share),
        ("conversion_rate", profile.conversion.conversion_rate),
        ("recovery_rate", profile.recovery.improvement_rate),
        ("recovery_save_rate", profile.recovery.save_rate),
        ("castling_rate", profile.king_safety.castling_rate),
    ):
        _check_rate(issues, name, value)

    # --- counts cannot exceed their universe ---------------------------------
    if profile.conversion.opportunities > games.analyzed_games:
        issues.append("conversion opportunities cannot exceed analyzed games")
    if profile.conversion.conversions > profile.conversion.opportunities:
        issues.append("conversions cannot exceed opportunities")
    if profile.recovery.situations > games.analyzed_games:
        issues.append("recovery situations cannot exceed analyzed games")
    if profile.recovery.improvements > profile.recovery.situations:
        issues.append("recoveries cannot exceed situations")
    if profile.king_safety.castled_games + profile.king_safety.uncastled_games > games.analyzed_games:
        issues.append("castled + uncastled games cannot exceed analyzed games")
    if profile.openings.distinct_openings > games.analyzed_games:
        issues.append("distinct openings cannot exceed analyzed games")

    # --- per-colour and per-opening counts -----------------------------------
    for entry in profile.by_color:
        if entry.wins + entry.draws + entry.losses != entry.games:
            issues.append(f"{entry.color}: wins+draws+losses must equal games")
        if entry.games > games.analyzed_games:
            issues.append(f"{entry.color}: games cannot exceed analyzed games")
    for entry in profile.openings.most_played:
        if entry.wins + entry.draws + entry.losses != entry.games:
            issues.append(f"opening {entry.key}: wins+draws+losses must equal games")
    for entry in profile.time_controls.entries:
        if entry.games > games.analyzed_games:
            issues.append(f"time control {entry.time_class}: games cannot exceed analyzed games")

    # --- no negative counts (material balance and ratings may be negative) ---
    non_negative: list[tuple[str, float | None]] = [
        ("wins", float(games.wins)),
        ("draws", float(games.draws)),
        ("losses", float(games.losses)),
        ("blunders_per_game", games.blunders_per_game),
        ("mistakes_per_game", games.mistakes_per_game),
        ("inaccuracies_per_game", games.inaccuracies_per_game),
        ("tactical_created_total", float(profile.tactical.created_total)),
        ("tactical_allowed_total", float(profile.tactical.allowed_total)),
        ("positional_error_candidates_per_game", profile.positional.error_candidates_per_game),
        ("king_safety_events_created", float(profile.king_safety.events_created)),
        ("king_safety_events_allowed", float(profile.king_safety.events_allowed)),
        ("promotions", float(profile.material.promotions)),
        ("imbalance_games", float(profile.material.imbalance_games)),
        ("castled_games", float(profile.king_safety.castled_games)),
        ("uncastled_games", float(profile.king_safety.uncastled_games)),
    ]
    for label, value in non_negative:
        if value is not None and value < 0:
            issues.append(f"{label} cannot be negative")
    for name, count in profile.tactical.created.items():
        if count < 0:
            issues.append(f"tactical count {name} cannot be negative")
    for entry in profile.insights:
        if entry.occurrences < 0:
            issues.append(f"insight {entry.id}: occurrences cannot be negative")

    # --- trends ---------------------------------------------------------------
    for trend in profile.trends.entries:
        if trend.recent_games + trend.baseline_games > games.analyzed_games:
            issues.append(f"trend window {trend.window}: recent + baseline cannot exceed analyzed games")
        if trend.recent_games > trend.window:
            issues.append(f"trend window {trend.window}: recent games cannot exceed the window")
        if trend.supported and (trend.recent_average_cpl is None or trend.baseline_average_cpl is None):
            issues.append(f"trend window {trend.window}: a supported trend needs both averages")

    # --- claims cannot outrun their sample -----------------------------------
    effective = policy or PlayerInsightPolicy(**(profile.policy or {}))
    for insight in profile.insights:
        if insight.games > games.analyzed_games:
            issues.append(f"insight {insight.id}: games cannot exceed analyzed games")
        if insight.claim_level is ClaimLevel.TENDENCY and insight.games < effective.min_games_for_profile:
            issues.append(f"insight {insight.id}: tendency claimed without enough games")
        if insight.claim_level in (ClaimLevel.TENDENCY, ClaimLevel.PATTERN) and not insight.evidence:
            issues.append(f"insight {insight.id}: a pattern/tendency claim needs at least one evidence ref")
        if insight.coverage is Coverage.INSUFFICIENT and insight.claim_level is ClaimLevel.TENDENCY:
            issues.append(f"insight {insight.id}: tendency claimed with insufficient coverage")

    return issues


def assert_profile_consistent(profile: PlayerProfile, *, policy: PlayerInsightPolicy | None = None) -> None:
    """Raise :class:`ProfileSanityError` when any invariant is violated."""
    issues = validate_profile(profile, policy=policy)
    if issues:
        raise ProfileSanityError("; ".join(issues))


__all__ = ["ProfileSanityError", "assert_profile_consistent", "validate_profile"]
