"""Profile assembly, evidence-gated insights, and the preparation report.

This module is the *composition* layer: it calls the repertoire, response and
statistics aggregators, folds their outputs into an :class:`OpponentProfile`,
and then derives the handful of findings that survive the evidence gates into
an :class:`OpponentPreparationReport`.

The rules the insights obey:

* A finding is only emitted when the aggregation that produced it already
  carries a claim level. Nothing is re-derived here by inference.
* Every insight names its sample size and links its evidence back to specific
  games/plies.
* A ``preparation_hint`` states what to *prepare*, never what will happen.

If the opponent has no games, the report says so and lists that as a limitation
instead of producing a confident-looking empty document.
"""

from __future__ import annotations

from datetime import datetime, timezone

from argus.opponent_intelligence.models import (
    OPPONENT_METHODOLOGY_VERSION,
    OpponentGameInput,
    OpponentInsight,
    OpponentOpeningNode,
    OpponentPreparationReport,
    OpponentProfile,
    OpponentStatistics,
)
from argus.opponent_intelligence.policy import (
    ClaimLevel,
    Coverage,
    OpponentInsightPolicy,
    coverage_for,
)
from argus.opponent_intelligence.repertoire import build_repertoire
from argus.opponent_intelligence.responses import find_position_patterns
from argus.opponent_intelligence.statistics import phase_statistics, tendencies


def summarize(games: list[OpponentGameInput], policy: OpponentInsightPolicy) -> OpponentStatistics:
    """Measured counts over the opponent's games — no interpretation."""
    total = len(games)
    analyzed = sum(1 for game in games if game.analyzed)
    wins = sum(1 for game in games if game.outcome.value == "win")
    draws = sum(1 for game in games if game.outcome.value == "draw")
    losses = sum(1 for game in games if game.outcome.value == "loss")
    ratings = [game.opponent_rating for game in games if game.opponent_rating is not None]
    dates = [game.date for game in games if game.date]
    result_share: dict[str, float] = {}
    if total:
        result_share = {
            "win": round(wins / total, 4),
            "draw": round(draws / total, 4),
            "loss": round(losses / total, 4),
        }
    return OpponentStatistics(
        total_games=total,
        analyzed_games=analyzed,
        wins=wins,
        draws=draws,
        losses=losses,
        as_white=sum(1 for game in games if game.color == "white"),
        as_black=sum(1 for game in games if game.color == "black"),
        avg_opponent_rating=round(sum(ratings) / len(ratings), 1) if ratings else None,
        first_date=min(dates) if dates else None,
        last_date=max(dates) if dates else None,
        result_share=result_share,
    )


def build_profile(
    games: list[OpponentGameInput],
    identity,
    *,
    policy: OpponentInsightPolicy,
) -> OpponentProfile:
    """The opponent document: identity, history, repertoire, statistics."""
    statistics = summarize(games, policy)
    coverage = coverage_for(statistics.analyzed_games)
    repertoire = {
        color: build_repertoire(games, color=color, policy=policy, coverage=coverage)
        for color in ("white", "black")
    }
    phases = phase_statistics(games, policy=policy)
    measured = tendencies(games, policy=policy, phase_stats=phases)
    patterns = find_position_patterns(games, policy=policy)

    limitations: list[str] = []
    if statistics.total_games == 0:
        limitations.append("Caissa has no stored games for this opponent.")
    if statistics.analyzed_games < statistics.total_games:
        limitations.append(
            f"{statistics.total_games - statistics.analyzed_games} game(s) are imported but not "
            "analysed; move-level tendencies only use analysed games."
        )
    if coverage in (Coverage.INSUFFICIENT, Coverage.LIMITED):
        limitations.append(
            f"Only {statistics.analyzed_games} analysed game(s): findings are reported at their "
            "measured strength and are not generalised."
        )
    limitations.append(
        "This is a statistical summary of stored games, not a psychological profile or a prediction."
    )

    return OpponentProfile(
        identity=identity,
        generated_at=datetime.now(timezone.utc),
        statistics=statistics,
        coverage=coverage,
        repertoire=repertoire,
        phase_statistics=phases,
        tendencies=measured,
        position_patterns=patterns,
        policy=policy.to_dict(),
        limitations=limitations,
    )


def build_insights(profile: OpponentProfile, policy: OpponentInsightPolicy) -> list[OpponentInsight]:
    """Findings that survived the gates, ready to render with sample sizes."""
    insights: list[OpponentInsight] = []

    # -- repertoire: the opponent's most frequent choice, by colour ----------
    for color, opening in profile.repertoire.items():
        root_nodes = _root_choices(opening.nodes)
        if not root_nodes:
            continue
        top = root_nodes[0]
        level = top.claim_level
        insights.append(
            OpponentInsight(
                key=f"repertoire_{color}",
                title=f"Repertoire as {color}",
                statement=(
                    f"As {color}, the opponent played {top.san} in {top.occurrences} of "
                    f"{opening.analyzed_games} analysed game(s) "
                    f"({top.share:.0%} of games reaching that position)."
                ),
                category="opening_repertoire",
                claim_level=level,
                sample_size=opening.analyzed_games,
                evidence=top.evidence,
                preparation_hint=(
                    f"Prepare a line against {top.san} before playing this opponent as "
                    f"{'Black' if color == 'white' else 'White'}."
                ),
            )
        )
        if level in (ClaimLevel.PATTERN, ClaimLevel.TENDENCY) and len(root_nodes) > 1:
            alt = root_nodes[1]
            insights.append(
                OpponentInsight(
                    key=f"repertoire_{color}_alternative",
                    title=f"Second choice as {color}",
                    statement=(
                        f"Their next most common {color} first choice is {alt.san} "
                        f"({alt.occurrences} game(s), {alt.share:.0%})."
                    ),
                    category="opening_repertoire",
                    claim_level=alt.claim_level,
                    sample_size=opening.analyzed_games,
                    evidence=alt.evidence,
                    preparation_hint=f"Have a second line ready for {alt.san}.",
                )
            )

    # -- phase concentration -------------------------------------------------
    phases = profile.phase_statistics
    if phases and phases.phases:
        scored = [stat for stat in phases.phases if stat.phase != "unknown" and stat.moves > 0]
        worst = max(
            (stat for stat in scored if stat.avg_centipawn_loss is not None),
            key=lambda stat: stat.avg_centipawn_loss or 0,
            default=None,
        )
        if worst is not None and worst.claim_level == ClaimLevel.PATTERN:
            insights.append(
                OpponentInsight(
                    key="phase_weakness",
                    title=f"Worst phase: {worst.phase}",
                    statement=(
                        f"In the {worst.phase}, the opponent averaged {worst.avg_centipawn_loss}cp "
                        f"lost per scored move across {worst.games} game(s)."
                    ),
                    category="phase_statistics",
                    claim_level=worst.claim_level,
                    sample_size=worst.games,
                    evidence=[],
                    preparation_hint=(
                        f"Steer the game toward the {worst.phase} if you can do so soundly."
                    ),
                )
            )

    # -- measured tendencies -------------------------------------------------
    for tendency in profile.tendencies:
        insights.append(
            OpponentInsight(
                key=f"tendency_{tendency.key}",
                title=tendency.label,
                statement=f"{tendency.measurement}: {tendency.value} (n={tendency.sample_size}).",
                category="tendency",
                claim_level=tendency.claim_level,
                sample_size=tendency.sample_size,
                evidence=tendency.evidence,
                preparation_hint=tendency.note,
            )
        )

    # -- recurring positions -------------------------------------------------
    for pattern in profile.position_patterns[:3]:
        insights.append(
            OpponentInsight(
                key=f"position_{pattern.key[:12]}",
                title="Recurring position",
                statement=(
                    f"They reached this position {pattern.occurrences} time(s) across "
                    f"{pattern.occurrences} move(s); their most common answer was "
                    + (pattern.responses[0].san if pattern.responses else "not recorded")
                    + "."
                ),
                category="position_pattern",
                claim_level=pattern.claim_level,
                sample_size=pattern.occurrences,
                evidence=pattern.evidence,
                preparation_hint="Study the natural plan from this structure.",
            )
        )

    # Highest-confidence findings first; ties keep insertion order.
    order = {
        ClaimLevel.TENDENCY: 0,
        ClaimLevel.PATTERN: 1,
        ClaimLevel.OBSERVATION: 2,
        ClaimLevel.INSUFFICIENT: 3,
    }
    insights.sort(key=lambda insight: order.get(insight.claim_level, 4))
    return insights


def build_preparation_report(
    games: list[OpponentGameInput],
    identity,
    *,
    policy: OpponentInsightPolicy,
    color_to_prepare: str | None = None,
) -> OpponentPreparationReport:
    """The full preparation document for one opponent (and optional colour)."""
    profile = build_profile(games, identity, policy=policy)
    insights = build_insights(profile, policy)

    repertoire = None
    if color_to_prepare:
        repertoire = profile.repertoire.get(color_to_prepare.lower())

    evidence_count = sum(len(insight.evidence) for insight in insights)

    return OpponentPreparationReport(
        identity=identity,
        generated_at=profile.generated_at,
        color_to_prepare=color_to_prepare,
        statistics=profile.statistics,
        coverage=profile.coverage,
        repertoire=repertoire,
        phase_statistics=profile.phase_statistics,
        tendencies=profile.tendencies,
        insights=insights,
        expected_lines=list((repertoire.nodes if repertoire else [])[:5]),
        policy=profile.policy,
        evidence_count=evidence_count,
        limitations=profile.limitations,
    )


def _root_choices(nodes: list[OpponentOpeningNode]) -> list[OpponentOpeningNode]:
    """The opponent's earliest-move nodes — their entry into the game."""
    if not nodes:
        return []
    earliest_ply = min(node.ply for node in nodes)
    roots = [node for node in nodes if node.ply == earliest_ply]
    return sorted(roots, key=lambda node: (node.occurrences, node.share), reverse=True)


__all__ = [
    "OPPONENT_METHODOLOGY_VERSION",
    "build_insights",
    "build_preparation_report",
    "build_profile",
    "summarize",
]
