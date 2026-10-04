"""Recurring-pattern detection.

A pattern is only emitted when the *same measurable thing* recurs across
independent games above the policy thresholds:

* occurrences (how many times it happened),
* distinct games (how spread out it is),
* coverage (over what share of the analyzed games),
* consistency (over what share of that side's relevant events).

Each pattern carries :class:`~argus.player_intelligence.models.EvidenceRef`
pointers — game, ply and move — so the UI can open the exact position and the
claim can be checked. Nothing here produces prose the evidence does not
support: statements are composed from the measured numbers themselves.
"""

from __future__ import annotations

from collections import Counter

from argus.player_intelligence.models import (
    EvidenceRef,
    GameOutcome,
    InsightCategory,
    PlayerGameInput,
    PlayerInsight,
)
from argus.player_intelligence.policy import ClaimLevel, PlayerInsightPolicy

#: How many evidence refs travel with a pattern (the UI shows the first few and
#: links to the rest through the games themselves).
MAX_EVIDENCE = 8

#: Human labels for the error taxonomy used at player level.
CATEGORY_LABELS = {
    "tactical": "tactical",
    "positional": "positional",
    "king_safety": "king-safety",
    "material": "material",
    "conversion": "conversion",
    "opening": "opening",
    "endgame": "endgame",
    "calculation": "calculation",
}


def _insight_id(*parts: str) -> str:
    return "-".join(part.lower().replace(" ", "_") for part in parts if part)


def _evidence_from_errors(game: PlayerGameInput, category: str | None = None) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for error in game.errors:
        if category and category not in error.categories:
            continue
        refs.append(
            EvidenceRef(
                game_id=game.game_id,
                ply=error.ply,
                move_number=error.move_number,
                san=error.san,
                label=f"{error.classification or 'flagged'} · {', '.join(error.categories) or 'uncategorised'}",
            )
        )
    return refs


def detect_error_category_patterns(
    inputs: list[PlayerGameInput], policy: PlayerInsightPolicy, *, methodology_version: str
) -> list[PlayerInsight]:
    """Error categories that recur across games (spec §17, §18)."""
    total_errors = sum(len(game.errors) for game in inputs if game.errors)
    insights: list[PlayerInsight] = []
    games = len(inputs)

    for category in CATEGORY_LABELS:
        occurrences = 0
        affected_games: list[PlayerGameInput] = []
        evidence: list[EvidenceRef] = []
        for game in inputs:
            hits = [error for error in game.errors if category in error.categories]
            if not hits:
                continue
            occurrences += len(hits)
            affected_games.append(game)
            evidence.extend(_evidence_from_errors(game, category)[:2])

        if not occurrences:
            continue
        coverage = len(affected_games) / games if games else 0.0
        consistency = occurrences / total_errors if total_errors else 0.0
        supported = policy.pattern_is_supported(
            occurrences=occurrences,
            games=len(affected_games),
            coverage=coverage,
            consistency=consistency,
        )
        insights.append(
            PlayerInsight(
                id=_insight_id("pattern", category),
                category=InsightCategory.RECURRING_PATTERN
                if supported
                else InsightCategory.WEAKNESS_CANDIDATE,
                claim_level=ClaimLevel.PATTERN if supported else ClaimLevel.OBSERVATION,
                # The title must not claim more than the evidence: "recurring" is
                # only earned once the repetition thresholds are met, otherwise
                # this is an observation over the available games (spec §28, §39).
                title=(
                    f"Recurring {CATEGORY_LABELS[category]} errors"
                    if supported
                    else f"{CATEGORY_LABELS[category].capitalize()} errors observed"
                ),
                statement=(
                    f"{CATEGORY_LABELS[category].capitalize()} errors were flagged in "
                    f"{len(affected_games)} of {games} analyzed games "
                    f"({occurrences} occurrences, {consistency * 100:.0f}% of the player's flagged moves)."
                ),
                metric="error_category",
                value=float(occurrences),
                unit="occurrences",
                games=len(affected_games),
                occurrences=occurrences,
                coverage=policy.coverage_for(games),
                evidence=evidence[:MAX_EVIDENCE],
                methodology_version=methodology_version,
            )
        )
    return insights


def detect_phase_patterns(
    inputs: list[PlayerGameInput], policy: PlayerInsightPolicy, *, methodology_version: str
) -> list[PlayerInsight]:
    """Errors concentrated in one game phase (spec §10, §19)."""
    total_errors = sum(len(game.errors) for game in inputs if game.errors)
    games = len(inputs)
    insights: list[PlayerInsight] = []

    for phase in ("opening", "middlegame", "endgame"):
        occurrences = 0
        affected: list[PlayerGameInput] = []
        evidence: list[EvidenceRef] = []
        for game in inputs:
            hits = [error for error in game.errors if (error.phase or "") == phase]
            if not hits:
                continue
            occurrences += len(hits)
            affected.append(game)
            evidence.extend(_evidence_from_errors(game)[:2])

        if occurrences < policy.min_pattern_occurrences:
            continue
        coverage = len(affected) / games if games else 0.0
        consistency = occurrences / total_errors if total_errors else 0.0
        supported = policy.pattern_is_supported(
            occurrences=occurrences,
            games=len(affected),
            coverage=coverage,
            consistency=consistency,
        )
        if not supported:
            continue
        insights.append(
            PlayerInsight(
                id=_insight_id("phase_pattern", phase),
                category=InsightCategory.PHASE_PATTERN,
                claim_level=ClaimLevel.PATTERN,
                title=f"Flagged moves concentrated in the {phase}",
                statement=(
                    f"{occurrences} flagged moves occurred in the {phase} across "
                    f"{len(affected)} of {games} analyzed games."
                ),
                metric=f"{phase}_error_share",
                value=round(consistency, 4),
                unit="share of flagged moves",
                games=len(affected),
                occurrences=occurrences,
                coverage=policy.coverage_for(games),
                evidence=evidence[:MAX_EVIDENCE],
                methodology_version=methodology_version,
            )
        )
    return insights


def detect_positional_patterns(
    inputs: list[PlayerGameInput], policy: PlayerInsightPolicy, *, methodology_version: str
) -> list[PlayerInsight]:
    """Recurring positional error candidates (spec §12)."""
    total_candidates = sum(
        1 for game in inputs for event in game.positional_events if event.kind == "error_candidate"
    )
    games = len(inputs)
    insights: list[PlayerInsight] = []

    features = Counter(
        event.feature
        for game in inputs
        for event in game.positional_events
        if event.kind == "error_candidate"
    )
    for feature, occurrences in features.most_common():
        affected = [
            game for game in inputs
            if any(event.kind == "error_candidate" and event.feature == feature for event in game.positional_events)
        ]
        coverage = len(affected) / games if games else 0.0
        consistency = occurrences / total_candidates if total_candidates else 0.0
        if not policy.pattern_is_supported(
            occurrences=occurrences, games=len(affected), coverage=coverage, consistency=consistency
        ):
            continue
        evidence = [
            EvidenceRef(
                game_id=game.game_id,
                ply=event.ply,
                move_number=event.move_number,
                san=event.san,
                label=f"positional error candidate · {feature.replace('_', ' ')}",
            )
            for game in affected
            for event in game.positional_events
            if event.kind == "error_candidate" and event.feature == feature
        ]
        insights.append(
            PlayerInsight(
                id=_insight_id("positional_pattern", feature),
                category=InsightCategory.POSITIONAL_PATTERN,
                claim_level=ClaimLevel.PATTERN,
                title=f"Recurring positional theme: {feature.replace('_', ' ')}",
                statement=(
                    f"{feature.replace('_', ' ')} was flagged as an error candidate {occurrences} times "
                    f"across {len(affected)} of {games} analyzed games."
                ),
                metric=f"positional_{feature}",
                value=float(occurrences),
                unit="occurrences",
                games=len(affected),
                occurrences=occurrences,
                coverage=policy.coverage_for(games),
                evidence=_dedupe_evidence(evidence)[:MAX_EVIDENCE],
                methodology_version=methodology_version,
            )
        )
    return insights


def _dedupe_evidence(refs: list[EvidenceRef]) -> list[EvidenceRef]:
    """One entry per (game, ply, label); counts stay separate from evidence."""
    unique: list[EvidenceRef] = []
    seen: set[tuple[str, int, str | None]] = set()
    for ref in refs:
        key = (ref.game_id, ref.ply, ref.label)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


def detect_tactical_patterns(
    inputs: list[PlayerGameInput], policy: PlayerInsightPolicy, *, methodology_version: str
) -> list[PlayerInsight]:
    """Tactical events the player created vs. allowed (spec §11)."""
    games = len(inputs)
    insights: list[PlayerInsight] = []

    for direction, category in (("created", InsightCategory.TACTICAL_PATTERN),
                                ("allowed", InsightCategory.WEAKNESS_CANDIDATE)):
        counter = Counter(
            event.event_type
            for game in inputs
            for event in game.tactical_events
            if event.direction == direction
        )
        total = sum(counter.values())
        for event_type, occurrences in counter.most_common(6):
            affected = [
                game for game in inputs
                if any(event.direction == direction and event.event_type == event_type
                       for event in game.tactical_events)
            ]
            coverage = len(affected) / games if games else 0.0
            consistency = occurrences / total if total else 0.0
            is_pattern = policy.pattern_is_supported(
                occurrences=occurrences, games=len(affected), coverage=coverage, consistency=consistency
            )
            # A tactic the player executes repeatedly is worth stating even before
            # it clears every pattern bar — but then it is an *observation* over
            # the games it happened in, never a recurring-pattern claim. Nothing
            # (in either direction) is ever claimed as a pattern from too few
            # games: the spread requirement is absolute.
            if not is_pattern and (
                direction != "created" or occurrences < policy.min_pattern_occurrences
            ):
                continue
            evidence = [
                EvidenceRef(
                    game_id=game.game_id,
                    ply=event.ply,
                    move_number=event.move_number,
                    san=event.san,
                    label=f"{event_type.replace('_', ' ')} ({direction})",
                )
                for game in affected
                for event in game.tactical_events
                if event.direction == direction and event.event_type == event_type
            ]
            verb = "executed" if direction == "created" else "allowed"
            insights.append(
                PlayerInsight(
                    id=_insight_id("tactical", direction, event_type),
                    category=category,
                    claim_level=ClaimLevel.PATTERN if is_pattern else ClaimLevel.OBSERVATION,
                    title=f"{event_type.replace('_', ' ').capitalize()} {'executed' if direction == 'created' else 'allowed'}",
                    statement=(
                        f"The player {verb} {event_type.replace('_', ' ')} {occurrences} times across "
                        f"{len(affected)} of {games} analyzed games."
                    ),
                    metric=f"tactical_{direction}_{event_type}",
                    value=float(occurrences),
                    unit="occurrences",
                    games=len(affected),
                    occurrences=occurrences,
                    coverage=policy.coverage_for(games),
                    evidence=evidence[:MAX_EVIDENCE],
                    methodology_version=methodology_version,
                )
            )
    return insights


def detect_opening_patterns(
    inputs: list[PlayerGameInput], policy: PlayerInsightPolicy, *, methodology_version: str
) -> list[PlayerInsight]:
    """Opening frequency and deviation behaviour — performance only with sample."""
    games = len(inputs)
    insights: list[PlayerInsight] = []
    grouped: dict[str, list[PlayerGameInput]] = {}
    for game in inputs:
        key = game.opening_name or game.eco_code or "Unknown / Unclassified"
        grouped.setdefault(key, []).append(game)

    for key, subset in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        count = len(subset)
        if count < policy.min_games_per_opening_for_claim:
            continue
        wins = sum(1 for game in subset if game.outcome is GameOutcome.WIN)
        draws = sum(1 for game in subset if game.outcome is GameOutcome.DRAW)
        losses = sum(1 for game in subset if game.outcome is GameOutcome.LOSS)
        claim = _claim_level_for_opening(policy, count)
        insights.append(
            PlayerInsight(
                id=_insight_id("opening", key),
                category=InsightCategory.OPENING_PATTERN,
                claim_level=claim,
                title=f"Opening: {key}",
                statement=(
                    f"{key} was played in {count} of {games} analyzed games "
                    f"({wins}W/{draws}D/{losses}L)."
                ),
                metric="opening_games",
                value=float(count),
                unit="games",
                games=count,
                occurrences=count,
                coverage=policy.coverage_for(games),
                evidence=[
                    EvidenceRef(game_id=game.game_id, ply=0, label="opening played")
                    for game in subset[:MAX_EVIDENCE]
                ],
                methodology_version=methodology_version,
            )
        )

    deviated = [game for game in inputs if game.opening_deviation is not None]
    if len(deviated) >= policy.min_games_per_opening_for_claim:
        rate = len(deviated) / games if games else 0.0
        insights.append(
            PlayerInsight(
                id="opening-deviation-rate",
                category=InsightCategory.OPENING_PATTERN,
                claim_level=ClaimLevel.PATTERN if policy.can_claim_tendency(games) else ClaimLevel.OBSERVATION,
                title="Leaving the opening book",
                statement=(
                    f"The player left the Caissa opening table in {len(deviated)} of {games} analyzed games "
                    f"({rate * 100:.0f}%). Leaving theory is not an error."
                ),
                metric="opening_deviation_rate",
                value=round(rate, 4),
                unit="share of games",
                games=len(deviated),
                occurrences=len(deviated),
                coverage=policy.coverage_for(games),
                evidence=[
                    EvidenceRef(
                        game_id=game.game_id,
                        ply=game.opening_deviation.ply if game.opening_deviation else 0,
                        move_number=game.opening_deviation.move_number if game.opening_deviation else None,
                        san=game.opening_deviation.played_san if game.opening_deviation else None,
                        label="left the book",
                    )
                    for game in deviated[:MAX_EVIDENCE]
                ],
                methodology_version=methodology_version,
            )
        )
    return insights


def _claim_level_for_opening(policy: PlayerInsightPolicy, games: int) -> ClaimLevel:
    if games >= policy.min_games_for_tendency:
        return ClaimLevel.TENDENCY
    if games >= policy.min_games_per_opening_for_claim:
        return ClaimLevel.PATTERN
    return ClaimLevel.OBSERVATION


def detect_conversion_and_recovery(
    inputs: list[PlayerGameInput], policy: PlayerInsightPolicy, *, methodology_version: str
) -> list[PlayerInsight]:
    """Advantage conversion and recovery, phrased as measurements."""
    games = len(inputs)
    insights: list[PlayerInsight] = []

    opportunities = [
        game for game in inputs
        if game.trajectory.peak_band is not None and game.trajectory.peak_band >= policy.conversion_min_band
    ]
    if len(opportunities) >= policy.min_pattern_occurrences:
        conversions = [
            game for game in opportunities
            if game.outcome is GameOutcome.WIN
            or (game.trajectory.final_band is not None
                and game.trajectory.final_band >= policy.conversion_min_band)
        ]
        rate = len(conversions) / len(opportunities)
        insights.append(
            PlayerInsight(
                id="conversion-rate",
                category=InsightCategory.CONVERSION_PATTERN,
                claim_level=ClaimLevel.PATTERN,
                title="Converting a winning advantage",
                statement=(
                    f"A winning evaluation was reached in {len(opportunities)} of {games} analyzed games; "
                    f"{len(conversions)} of those were converted "
                    f"({rate * 100:.0f}% of opportunities)."
                ),
                metric="conversion_rate",
                value=round(rate, 4),
                unit="share of opportunities",
                games=len(opportunities),
                occurrences=len(conversions),
                coverage=policy.coverage_for(games),
                evidence=[
                    EvidenceRef(game_id=game.game_id, ply=0, label="winning advantage reached")
                    for game in opportunities[:MAX_EVIDENCE]
                ],
                methodology_version=methodology_version,
            )
        )

    losing = [
        game for game in inputs
        if game.trajectory.worst_band is not None and game.trajectory.worst_band <= policy.recovery_max_band
    ]
    if len(losing) >= policy.min_pattern_occurrences:
        improved = [
            game for game in losing
            if game.trajectory.final_band is not None and game.trajectory.final_band > game.trajectory.worst_band
        ]
        rate = len(improved) / len(losing)
        insights.append(
            PlayerInsight(
                id="recovery-rate",
                category=InsightCategory.RECOVERY_PATTERN,
                claim_level=ClaimLevel.PATTERN,
                title="Recovering from a losing position",
                statement=(
                    f"The evaluation reached a losing band in {len(losing)} of {games} analyzed games; "
                    f"it improved from that point in {len(improved)} of them ({rate * 100:.0f}%)."
                ),
                metric="recovery_rate",
                value=round(rate, 4),
                unit="share of losing positions",
                games=len(losing),
                occurrences=len(improved),
                coverage=policy.coverage_for(games),
                evidence=[
                    EvidenceRef(game_id=game.game_id, ply=0, label="losing evaluation reached")
                    for game in losing[:MAX_EVIDENCE]
                ],
                methodology_version=methodology_version,
            )
        )
    return insights


__all__ = [
    "CATEGORY_LABELS",
    "MAX_EVIDENCE",
    "detect_conversion_and_recovery",
    "detect_error_category_patterns",
    "detect_opening_patterns",
    "detect_phase_patterns",
    "detect_positional_patterns",
    "detect_tactical_patterns",
]
