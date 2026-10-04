"""Chess DNA: measurable behaviour, not a personality quiz.

Each dimension exposes a *raw, interpretable* metric with its unit, its
definition and the sample it came from — deliberately no 0–100 score, because
an arbitrary scale would have to hide the methodology. The measurable
quantities come out of the Phase 4 report; the wording stays descriptive.

Two rules:

* a dimension with no measurable value is still listed, marked
  ``insufficient``, with the reason — it is never silently dropped or faked;
* no dimension makes a psychological claim. "Material imbalance tendency" is a
  playing characteristic, not "risk-taking personality".
"""

from __future__ import annotations

from argus.player_intelligence.aggregate import _per_game, _rate
from argus.player_intelligence.models import (
    ChessDna,
    ChessDnaDimension,
    EvidenceRef,
    PlayerGameInput,
    PlayerGameStatistics,
    PlayerKingSafetyStatistics,
    PlayerMaterialStatistics,
    PlayerOpeningStatistics,
    PlayerPhaseStatistics,
    PlayerPositionalStatistics,
    PlayerTacticalStatistics,
    PlayerConversionStatistics,
)
from argus.player_intelligence.policy import ClaimLevel, PlayerInsightPolicy

#: Every dimension is defined here so the UI and the docs cannot drift.
DIMENSION_DEFINITIONS: dict[str, str] = {
    "tactical_creation": "Tactical events the player executed per analyzed game "
    "(forks, pins, skewers, discovered attacks, mating threats) as detected by the Phase 4 tactics layer.",
    "tactical_oversight": "Tactical events the opponent executed against the player per analyzed game "
    "plus the player's own tactical error candidates.",
    "opening_diversity": "Distinct openings played divided by analyzed games. 1.0 means a different "
    "opening every game; lower means a compact repertoire.",
    "material_imbalance_tendency": "Share of analyzed games in which the material balance left equality. "
    "A playing characteristic, not a judgement.",
    "exchange_tendency": "Exchanges (capture-for-capture sequences) per analyzed game.",
    "king_safety_tendency": "Share of games in which the player castled, with the king-safety events "
    "recorded by the Phase 4 layer for context.",
    "conversion_tendency": "Share of games where a winning evaluation was reached and then converted.",
    "endgame_tendency": "Share of analyzed games that reached a detected endgame, with endgame vs. "
    "overall centipawn loss for context.",
    "positional_complexity": "Positional features detected per analyzed game (pawn structure, weak "
    "squares, piece activity, space).",
    "simplification_tendency": "Captures per analyzed game — how often pieces come off the board.",
    "aggression_indicator": "Tactical events the player created plus king-safety attacks they launched, "
    "per analyzed game.",
}


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """``1 game`` / ``2 games`` — a count in a note is always grammatical.

    Notes are product-facing prose, and "1 games" reads as a bug to a user even
    when the number is right, so the count and the noun are formatted together.
    """
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural or singular + 's'}"


def _dimension(
    key: str,
    value: float | None,
    unit: str,
    *,
    games: int,
    events: int,
    policy: PlayerInsightPolicy,
    evidence: list[EvidenceRef] | None = None,
    note: str | None = None,
) -> ChessDnaDimension:
    if value is None:
        claim = ClaimLevel.INSUFFICIENT
    elif games >= policy.min_games_for_tendency:
        claim = ClaimLevel.TENDENCY
    elif games >= policy.min_games_for_profile:
        claim = ClaimLevel.OBSERVATION
    else:
        claim = ClaimLevel.INSUFFICIENT
    return ChessDnaDimension(
        key=key,
        label=key.replace("_", " ").capitalize(),
        value=value,
        unit=unit,
        definition=DIMENSION_DEFINITIONS[key],
        games=games,
        events=events,
        coverage=policy.coverage_for(games),
        claim_level=claim,
        evidence=(evidence or [])[:8],
        note=note if value is not None else (note or "Not measurable from the available analysis."),
    )


def build_chess_dna(
    inputs: list[PlayerGameInput],
    policy: PlayerInsightPolicy,
    *,
    games: PlayerGameStatistics,
    tactical: PlayerTacticalStatistics,
    positional: PlayerPositionalStatistics,
    king_safety: PlayerKingSafetyStatistics,
    material: PlayerMaterialStatistics,
    openings: PlayerOpeningStatistics,
    phases: PlayerPhaseStatistics,
    conversion: PlayerConversionStatistics,
) -> ChessDna:
    """Assemble the DNA dimensions from the already-computed sections."""
    analyzed = games.analyzed_games
    notes: list[str] = []
    if analyzed < policy.min_games_for_profile:
        notes.append(
            f"Chess DNA needs at least {policy.min_games_for_profile} analyzed games to say anything; "
            f"{analyzed} available."
        )
        return ChessDna(dimensions=[], derived_from_games=analyzed, notes=notes)

    endgame_entries = [entry for entry in phases.phases if entry.phase == "endgame"]
    endgame_moves = sum(entry.evaluated_moves for entry in endgame_entries)
    endgame_cpl = endgame_entries[0].average_centipawn_loss if endgame_entries else None
    endgame_note = None
    if endgame_cpl is not None and games.average_centipawn_loss is not None:
        endgame_note = (
            f"Endgame CPL {endgame_cpl} vs. overall {games.average_centipawn_loss} "
            f"across {endgame_moves} evaluated endgame moves."
        )

    evidence_tactical = [
        EvidenceRef(game_id=game.game_id, ply=event.ply, move_number=event.move_number, san=event.san,
                    label=f"tactical {event.direction}: {event.event_type}")
        for game in inputs
        for event in game.tactical_events
    ][:8]

    dimensions = [
        _dimension(
            "tactical_creation",
            _per_game(tactical.created_total, analyzed),
            "events per game",
            games=analyzed,
            events=tactical.created_total,
            policy=policy,
            evidence=evidence_tactical,
        ),
        _dimension(
            "tactical_oversight",
            _per_game(tactical.allowed_total + tactical.missed_opportunities, analyzed),
            "events per game",
            games=analyzed,
            events=tactical.allowed_total + tactical.missed_opportunities,
            policy=policy,
            evidence=[
                EvidenceRef(game_id=error.game_id, ply=error.ply, move_number=error.move_number,
                            san=error.san, label="tactical error candidate")
                for game in inputs for error in game.errors if "tactical" in error.categories
            ][:8],
        ),
        _dimension(
            "opening_diversity",
            _rate(openings.distinct_openings, analyzed),
            "distinct openings per game",
            games=analyzed,
            events=openings.distinct_openings,
            policy=policy,
            note=(
                f"{_plural(openings.distinct_openings, 'distinct opening')} in "
                f"{_plural(analyzed, 'game')}; most played: {openings.most_played[0].key} "
                f"({_plural(openings.most_played[0].games, 'game')})."
                if openings.most_played
                else None
            ),
        ),
        _dimension(
            "material_imbalance_tendency",
            material.imbalance_share,
            "share of games",
            games=analyzed,
            events=material.imbalance_games,
            policy=policy,
            note=f"Material balance left equality in {material.imbalance_games} of {analyzed} games.",
        ),
        _dimension(
            "exchange_tendency",
            material.average_exchanges,
            "exchanges per game",
            games=analyzed,
            events=int(sum(game.material.exchanges for game in inputs)),
            policy=policy,
        ),
        _dimension(
            "king_safety_tendency",
            king_safety.castling_rate,
            "share of games castled",
            games=analyzed,
            events=king_safety.castled_games + king_safety.uncastled_games,
            policy=policy,
            evidence=king_safety.evidence,
            note=(
                f"{king_safety.events_allowed} king-safety events were recorded against the player "
                f"and {king_safety.events_created} created by them."
            ),
        ),
        _dimension(
            "conversion_tendency",
            conversion.conversion_rate,
            "share of opportunities converted",
            games=analyzed,
            events=conversion.opportunities,
            policy=policy,
            evidence=conversion.evidence,
            note=(
                f"{conversion.conversions} of {conversion.opportunities} winning advantages were converted."
                if conversion.opportunities
                else "No game reached the documented winning threshold."
            ),
        ),
        _dimension(
            "endgame_tendency",
            _rate(sum(1 for game in inputs if any(
                row.phase == "endgame" and row.evaluated_moves > 0 for row in game.phase_performance
            )), analyzed),
            "share of games reaching an endgame",
            games=analyzed,
            events=endgame_moves,
            policy=policy,
            note=endgame_note,
        ),
        _dimension(
            "positional_complexity",
            positional.features_per_game,
            "features per game",
            games=analyzed,
            events=int(sum(positional.features.values())),
            policy=policy,
        ),
        _dimension(
            "simplification_tendency",
            material.average_captures,
            "captures per game",
            games=analyzed,
            events=int(sum(game.material.total_captures for game in inputs)),
            policy=policy,
        ),
        _dimension(
            "aggression_indicator",
            _per_game(tactical.created_total + king_safety.events_created, analyzed),
            "attacking events per game",
            games=analyzed,
            events=tactical.created_total + king_safety.events_created,
            policy=policy,
            note=(
                f"{tactical.created_total} tactical events created and {king_safety.events_created} "
                "king-safety attacks launched across the analyzed games."
            ),
        ),
    ]

    notes.append(
        "Every dimension is a measured count or share over the analyzed games — no composite score "
        "is applied, and none of these describes personality."
    )
    return ChessDna(dimensions=dimensions, derived_from_games=analyzed, notes=notes)


__all__ = ["DIMENSION_DEFINITIONS", "build_chess_dna"]
