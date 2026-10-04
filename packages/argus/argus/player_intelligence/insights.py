"""Insight assembly.

The insight list is the product-facing surface of Phase 5, so the wording rules
of the phase objective are enforced here in code rather than in the UI:

* a statement always names its sample ("… in 8 of 31 analyzed games"),
* a tendency is only emitted when the policy allows it,
* descriptive facts (frequency, material character) are never dressed as
  strengths or weaknesses,
* a strength/weakness *candidate* is chosen by comparing measured numbers, and
  it says so.

The LLM layer that will later phrase these for a human consumes this structure;
it never gets to invent a claim that is not in the list.
"""

from __future__ import annotations

from argus.player_intelligence.models import (
    EvidenceRef,
    InsightCategory,
    PlayerGameInput,
    PlayerGameStatistics,
    PlayerInsight,
    PlayerPhaseStatistics,
    PlayerProfile,
)
from argus.player_intelligence.patterns import (
    detect_conversion_and_recovery,
    detect_error_category_patterns,
    detect_opening_patterns,
    detect_phase_patterns,
    detect_positional_patterns,
    detect_tactical_patterns,
)
from argus.player_intelligence.policy import ClaimLevel, PlayerInsightPolicy

#: A "best-move share" this high, with enough games, is measurable strength.
STRENGTH_BEST_MOVE_SHARE = 0.4
#: Castling in this share of games (with enough games) is measurable strength.
STRENGTH_CASTLING_RATE = 0.8


def _tendency_allowed(policy: PlayerInsightPolicy, games: int) -> bool:
    return games >= policy.min_games_for_tendency


def _game_refs(
    inputs: list[PlayerGameInput], *, color: str | None = None, limit: int = 8,
    label: str = "analyzed game",
) -> list[EvidenceRef]:
    """Game-level evidence for an aggregate claim.

    Aggregate insights (record, trends, averages) are supported by the games
    they summarize rather than by a single move, so their evidence is the game
    list itself — traceable, just at game granularity.
    """
    refs: list[EvidenceRef] = []
    for game in inputs:
        if color is not None and game.color != color:
            continue
        refs.append(EvidenceRef(game_id=game.game_id, ply=0, label=label))
        if len(refs) >= limit:
            break
    return refs


def statistical_insights(
    games: PlayerGameStatistics,
    policy: PlayerInsightPolicy,
    *,
    methodology_version: str,
    inputs: list[PlayerGameInput] | None = None,
) -> list[PlayerInsight]:
    """The headline record and move-quality aggregates, as statements."""
    insights: list[PlayerInsight] = []
    if not policy.can_profile(games.analyzed_games):
        return insights
    refs = _game_refs(inputs or [], label="game in the record")

    record_parts = []
    if games.win_rate is not None:
        record_parts.append(f"{games.wins}W/{games.draws}D/{games.losses}L")
    insights.append(
        PlayerInsight(
            id="record-overview",
            category=InsightCategory.STRENGTH if (games.win_rate or 0) >= 0.5 else InsightCategory.WEAKNESS_CANDIDATE,
            claim_level=(
                ClaimLevel.TENDENCY if _tendency_allowed(policy, games.analyzed_games)
                else ClaimLevel.OBSERVATION
            ),
            title="Record across analyzed games",
            statement=(
                f"Across {games.analyzed_games} analyzed games the record is {' '.join(record_parts)} "
                f"({(games.win_rate or 0) * 100:.0f}% wins)."
            ),
            metric="win_rate",
            value=games.win_rate,
            unit="share of games",
            games=games.analyzed_games,
            occurrences=games.wins,
            coverage=policy.coverage_for(games.analyzed_games),
            evidence=refs,
            methodology_version=methodology_version,
        )
    )

    if games.average_centipawn_loss is not None:
        insights.append(
            PlayerInsight(
                id="move-quality-overview",
                category=InsightCategory.STRENGTH
                if (games.average_centipawn_loss or 999) < 50
                else InsightCategory.WEAKNESS_CANDIDATE,
                claim_level=(
                    ClaimLevel.TENDENCY if _tendency_allowed(policy, games.analyzed_games)
                    else ClaimLevel.OBSERVATION
                ),
                title="Move quality across games",
                statement=(
                    f"Average centipawn loss is {games.average_centipawn_loss} per move "
                    f"(median {games.median_centipawn_loss}), with "
                    f"{games.blunders_per_game} blunders and {games.mistakes_per_game} mistakes per game "
                    f"over {games.analyzed_games} analyzed games."
                ),
                metric="average_centipawn_loss",
                value=games.average_centipawn_loss,
                unit="centipawns",
                games=games.analyzed_games,
                occurrences=games.analyzed_games,
                coverage=policy.coverage_for(games.analyzed_games),
                evidence=refs,
                methodology_version=methodology_version,
            )
        )
    return insights


def color_insights(
    profile: PlayerProfile,
    policy: PlayerInsightPolicy,
    *,
    methodology_version: str,
    inputs: list[PlayerGameInput] | None = None,
) -> list[PlayerInsight]:
    """Colour split — only claimed when both colours have enough games."""
    insights: list[PlayerInsight] = []
    by_color = {entry.color: entry for entry in profile.by_color}
    white, black = by_color.get("white"), by_color.get("black")
    if not white or not black:
        return insights

    enough = (
        white.games >= policy.min_games_per_color_for_claim
        and black.games >= policy.min_games_per_color_for_claim
    )
    if not enough:
        insights.append(
            PlayerInsight(
                id="color-split-insufficient",
                category=InsightCategory.PHASE_PATTERN,
                claim_level=ClaimLevel.INSUFFICIENT,
                title="Colour split",
                statement=(
                    f"The colour split ({white.games} as White, {black.games} as Black) is below the "
                    f"{policy.min_games_per_color_for_claim}-game threshold per colour, so no "
                    "White-vs-Black comparison is made."
                ),
                metric="color_games",
                value=float(min(white.games, black.games)),
                unit="games",
                games=min(white.games, black.games),
                occurrences=min(white.games, black.games),
                coverage=policy.coverage_for(min(white.games, black.games)),
                evidence=_game_refs(inputs or [], label="game counted in the split"),
                methodology_version=methodology_version,
            )
        )
        return insights

    for entry in (white, black):
        insights.append(
            PlayerInsight(
                id=f"color-{entry.color}",
                category=InsightCategory.PHASE_PATTERN,
                claim_level=(
                    ClaimLevel.TENDENCY if _tendency_allowed(policy, entry.games)
                    else ClaimLevel.OBSERVATION
                ),
                title=f"Performance as {entry.color.capitalize()}",
                statement=(
                    f"As {entry.color.capitalize()}: {entry.games} games, "
                    f"{entry.wins}W/{entry.draws}D/{entry.losses}L"
                    + (f", average accuracy {entry.average_accuracy}%" if entry.average_accuracy is not None else "")
                    + (f", CPL {entry.average_centipawn_loss}." if entry.average_centipawn_loss is not None else ".")
                ),
                metric=f"{entry.color}_win_rate",
                value=entry.win_rate,
                unit="share of games",
                games=entry.games,
                occurrences=entry.wins,
                coverage=policy.coverage_for(entry.games),
                evidence=_game_refs(
                    inputs or [], color=entry.color, label=f"game as {entry.color}"
                ),
                methodology_version=methodology_version,
            )
        )
    return insights


def strength_candidates(
    profile: PlayerProfile,
    policy: PlayerInsightPolicy,
    *,
    methodology_version: str,
    inputs: list[PlayerGameInput] | None = None,
) -> list[PlayerInsight]:
    """Measurable strengths — each one a comparison of measured numbers."""
    insights: list[PlayerInsight] = []
    games = profile.games.analyzed_games
    if not policy.can_profile(games):
        return insights
    refs = _game_refs(inputs or [], label="game with scored moves")

    if profile.games.average_accuracy is not None and games >= policy.min_games_for_profile:
        insights.append(
            PlayerInsight(
                id="accuracy-strength",
                category=InsightCategory.STRENGTH,
                claim_level=(
                    ClaimLevel.TENDENCY if _tendency_allowed(policy, games) else ClaimLevel.OBSERVATION
                ),
                title="Accuracy",
                statement=(
                    f"Average accuracy is {profile.games.average_accuracy}% over "
                    f"{profile.games.accuracy_sample} scored games."
                ),
                metric="average_accuracy",
                value=profile.games.average_accuracy,
                unit="percent",
                games=games,
                occurrences=profile.games.accuracy_sample,
                coverage=policy.coverage_for(games),
                evidence=refs,
                methodology_version=methodology_version,
            )
        )

    king = profile.king_safety
    if (
        king.castling_rate is not None
        and king.castling_rate >= STRENGTH_CASTLING_RATE
        and (king.castled_games + king.uncastled_games) >= policy.min_games_per_color_for_claim
    ):
        insights.append(
            PlayerInsight(
                id="castling-strength",
                category=InsightCategory.STRENGTH,
                claim_level=ClaimLevel.PATTERN,
                title="Regular castling",
                statement=(
                    f"The player castled in {king.castled_games} of "
                    f"{king.castled_games + king.uncastled_games} games where castling was observable."
                ),
                metric="castling_rate",
                value=king.castling_rate,
                unit="share of games",
                games=king.castled_games + king.uncastled_games,
                occurrences=king.castled_games,
                coverage=policy.coverage_for(games),
                # King-safety events when the engine recorded any, otherwise the
                # games the castling count came from.
                evidence=king.evidence[:4] or refs,
                methodology_version=methodology_version,
            )
        )

    if (
        profile.conversion.conversion_rate is not None
        and profile.conversion.conversion_rate >= 0.75
        and profile.conversion.opportunities >= policy.min_pattern_occurrences
    ):
        insights.append(
            PlayerInsight(
                id="conversion-strength",
                category=InsightCategory.STRENGTH,
                claim_level=ClaimLevel.PATTERN,
                title="Converts winning positions",
                statement=(
                    f"{profile.conversion.conversions} of {profile.conversion.opportunities} winning "
                    "advantages were converted."
                ),
                metric="conversion_rate",
                value=profile.conversion.conversion_rate,
                unit="share of opportunities",
                games=profile.conversion.opportunities,
                occurrences=profile.conversion.conversions,
                coverage=policy.coverage_for(games),
                evidence=profile.conversion.evidence[:4],
                methodology_version=methodology_version,
            )
        )
    return insights


def trend_insights(
    inputs: list[PlayerGameInput], policy: PlayerInsightPolicy, *, methodology_version: str
) -> list[PlayerInsight]:
    """Recent-vs-historical differences, stated as measurements only."""
    from argus.player_intelligence.aggregate import _chronological
    from argus.player_intelligence.aggregate import trends as compute_trends

    computed = compute_trends(inputs, policy)
    ordered = _chronological(inputs)
    insights: list[PlayerInsight] = []
    for entry in computed.entries:
        if not entry.supported or entry.direction == "steady":
            continue
        direction_word = "lower" if entry.direction == "lower_cpl" else "higher"
        insights.append(
            PlayerInsight(
                id=f"trend-{entry.window}",
                category=InsightCategory.IMPROVEMENT_TREND,
                claim_level=ClaimLevel.PATTERN,
                title=f"Last {entry.window} games vs. earlier games",
                statement=(
                    f"The most recent {entry.recent_games} games show {direction_word} average centipawn "
                    f"loss ({entry.recent_average_cpl}) than the earlier {entry.baseline_games} games "
                    f"({entry.baseline_average_cpl}) — a difference of "
                    f"{abs((entry.relative_change or 0) * 100):.0f}%. This is a measurement of the sample, "
                    "not a judgement about improvement."
                ),
                metric=f"cpl_change_last_{entry.window}",
                value=entry.relative_change,
                unit="relative change",
                games=entry.recent_games + entry.baseline_games,
                occurrences=entry.recent_games,
                coverage=policy.coverage_for(entry.recent_games + entry.baseline_games),
                # The evidence for a window comparison is the recent window it
                # measured, not a single move.
                evidence=_game_refs(
                    ordered[-entry.window:], label=f"game in the last {entry.window}"
                ),
                methodology_version=methodology_version,
            )
        )
    return insights


def phase_insights(
    phases: PlayerPhaseStatistics,
    policy: PlayerInsightPolicy,
    *,
    methodology_version: str,
    inputs: list[PlayerGameInput] | None = None,
) -> list[PlayerInsight]:
    """Weakest/strongest phase — only when the phase has enough moves."""
    if phases.weakest_phase is None:
        return []
    weakest = next((entry for entry in phases.phases if entry.phase == phases.weakest_phase), None)
    if weakest is None or weakest.average_centipawn_loss is None:
        return []
    return [
        PlayerInsight(
            id=f"phase-{weakest.phase}",
            category=InsightCategory.PHASE_PATTERN,
            claim_level=weakest.sample.claim_level,
            title=f"Highest centipawn loss in the {weakest.phase}",
            statement=(
                f"The {weakest.phase} shows the highest average centipawn loss "
                f"({weakest.average_centipawn_loss}) across {weakest.evaluated_moves} evaluated moves."
            ),
            metric=f"{weakest.phase}_cpl",
            value=weakest.average_centipawn_loss,
            unit="centipawns",
            games=weakest.sample.games,
            occurrences=weakest.evaluated_moves,
            coverage=weakest.sample.coverage,
            evidence=_game_refs(
                inputs or [], label=f"game with evaluated {weakest.phase} moves"
            ),
            methodology_version=methodology_version,
        )
    ]


def build_insights(
    profile: PlayerProfile,
    inputs: list[PlayerGameInput],
    policy: PlayerInsightPolicy,
    *,
    methodology_version: str,
) -> list[PlayerInsight]:
    """All insights for a profile, in a stable, meaningful order."""
    insights: list[PlayerInsight] = []
    insights.extend(
        statistical_insights(
            profile.games, policy, methodology_version=methodology_version, inputs=inputs
        )
    )
    insights.extend(
        color_insights(profile, policy, methodology_version=methodology_version, inputs=inputs)
    )
    insights.extend(
        phase_insights(profile.phases, policy, methodology_version=methodology_version, inputs=inputs)
    )
    insights.extend(
        strength_candidates(profile, policy, methodology_version=methodology_version, inputs=inputs)
    )
    insights.extend(
        detect_error_category_patterns(inputs, policy, methodology_version=methodology_version)
    )
    insights.extend(detect_phase_patterns(inputs, policy, methodology_version=methodology_version))
    insights.extend(detect_tactical_patterns(inputs, policy, methodology_version=methodology_version))
    insights.extend(detect_positional_patterns(inputs, policy, methodology_version=methodology_version))
    insights.extend(detect_opening_patterns(inputs, policy, methodology_version=methodology_version))
    insights.extend(
        detect_conversion_and_recovery(inputs, policy, methodology_version=methodology_version)
    )
    insights.extend(trend_insights(inputs, policy, methodology_version=methodology_version))

    # Stable ordering: strongest claims first, then by category, then id.
    order = {ClaimLevel.TENDENCY: 0, ClaimLevel.PATTERN: 1, ClaimLevel.OBSERVATION: 2,
             ClaimLevel.INSUFFICIENT: 3}
    insights.sort(key=lambda entry: (order[entry.claim_level], entry.category.value, entry.id))
    return insights


__all__ = [
    "STRENGTH_BEST_MOVE_SHARE",
    "STRENGTH_CASTLING_RATE",
    "build_insights",
    "color_insights",
    "phase_insights",
    "statistical_insights",
    "strength_candidates",
    "trend_insights",
]
