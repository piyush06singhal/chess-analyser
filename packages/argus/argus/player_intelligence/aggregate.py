"""Deterministic aggregation of many games into a player profile.

Every function here is a pure function of its inputs: no database, no engine,
no randomness, no wall-clock. That is what makes a profile reproducible and
what lets the tests assert exact numbers from real report fixtures.

Two rules are enforced throughout:

1. **A rate without a denominator is never emitted.** Every average/percentage
   returns ``None`` instead of dividing by zero, and every block carries the
   sample it came from.
2. **A statistic is not a claim.** The numbers are reported for whatever the
   sample supports; whether they may be *spoken about as a tendency* is decided
   by :class:`~argus.player_intelligence.policy.PlayerInsightPolicy` and marked
   with a :class:`~argus.player_intelligence.policy.ClaimLevel`.
"""

from __future__ import annotations

from statistics import median
from typing import Iterable, Sequence

from argus.player_intelligence.models import (
    GameOutcome,
    PlayerColorStatistics,
    PlayerConversionStatistics,
    PlayerGameInput,
    PlayerGameStatistics,
    PlayerKingSafetyStatistics,
    PlayerMaterialStatistics,
    PlayerOpeningEntry,
    PlayerOpeningStatistics,
    PlayerOpponentContext,
    PlayerPhaseEntry,
    PlayerPhaseStatistics,
    PlayerPositionalStatistics,
    PlayerRecoveryStatistics,
    PlayerTacticalStatistics,
    PlayerTimeControlEntry,
    PlayerTimeControlStatistics,
    PlayerTrendEntry,
    PlayerTrendStatistics,
    SampleNote,
    TimeClass,
)
from argus.player_intelligence.policy import ClaimLevel, PlayerInsightPolicy

#: Estimated total game length used only to classify time controls:
#: initial seconds plus 40 moves' worth of increment. Standard, documented
#: boundaries (bullet < 3 min, blitz < 10, rapid < 30, classical ≥ 30).
_ESTIMATED_MOVES = 40
_BULLET_MAX = 179
_BLITZ_MAX = 599
_RAPID_MAX = 1799

#: Castling after this move number counts as "late" for the king-safety
#: section. A documented starting heuristic, not a rule of chess.
LATE_CASTLING_MOVE = 20

#: Words that end an opening's family name ("Sicilian Defense" → "Sicilian",
#: "Queen's Gambit Declined" → "Queen's Gambit"). "Gambit" is deliberately NOT
#: a terminator: it is part of the family name for King's/Queen's Gambit.
_FAMILY_TERMINATORS = (
    "defense", "defence", "opening", "game", "variation", "attack", "system",
    "declined", "accepted", "counter",
)


# --- small numeric helpers ----------------------------------------------------


def _rate(numerator: int, denominator: int) -> float | None:
    """A share in [0, 1], or ``None`` when there is nothing to divide by."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(float(median(values)), 2)


def _per_game(total: int, games: int) -> float | None:
    if games <= 0:
        return None
    return round(total / games, 2)


def _numbers(values: Iterable[float | None]) -> list[float]:
    return [value for value in values if value is not None]


def classify_time_control(initial_seconds: int | None, increment_seconds: int | None) -> TimeClass:
    """Classify a PGN time control into bullet/blitz/rapid/classical.

    Uses the standard estimated game length (initial + 40 × increment). An
    unparseable control is ``unknown`` — never guessed into a category.
    """
    if initial_seconds is None:
        return TimeClass.UNKNOWN
    total = initial_seconds + _ESTIMATED_MOVES * (increment_seconds or 0)
    if total <= _BULLET_MAX:
        return TimeClass.BULLET
    if total <= _BLITZ_MAX:
        return TimeClass.BLITZ
    if total <= _RAPID_MAX:
        return TimeClass.RAPID
    return TimeClass.CLASSICAL


def opening_family(eco_code: str | None, opening_name: str | None) -> str | None:
    """A descriptive family label ("Sicilian", "Queen's Gambit"), or ``None``.

    Purely descriptive grouping for frequency questions; it never asserts a
    repertoire or a preference on its own.
    """
    if opening_name:
        tokens = opening_name.replace(",", " ").split()
        kept: list[str] = []
        for token in tokens:
            if token.lower().strip("'s") in _FAMILY_TERMINATORS:
                break
            kept.append(token)
            if len(kept) == 3:
                break
        if kept:
            return " ".join(kept)
    if eco_code:
        return f"ECO {eco_code[0]}"
    return None


def _claim_level(policy: PlayerInsightPolicy, games: int, events: int | None = None) -> ClaimLevel:
    """The strongest statement the sample supports for a section."""
    if games < policy.min_games_for_profile:
        return ClaimLevel.INSUFFICIENT
    if events is not None and events < policy.min_pattern_occurrences:
        return ClaimLevel.OBSERVATION
    if games >= policy.min_games_for_tendency:
        return ClaimLevel.TENDENCY
    return ClaimLevel.OBSERVATION


def _sample(
    policy: PlayerInsightPolicy,
    *,
    games: int,
    events: int = 0,
    note: str | None = None,
) -> SampleNote:
    return SampleNote(
        games=games,
        events=events,
        claim_level=_claim_level(policy, games, events if events else None),
        coverage=policy.coverage_for(games),
        note=note,
    )


def _chronological(inputs: Sequence[PlayerGameInput]) -> list[PlayerGameInput]:
    """Oldest first. Games without a date keep their incoming (stable) order."""
    dated = [game for game in inputs if game.date]
    undated = [game for game in inputs if not game.date]
    return sorted(dated, key=lambda game: game.date or "") + undated


# --- section builders ---------------------------------------------------------


def analyze_games(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerGameStatistics:
    """Win/draw/loss record and the headline move-quality aggregates."""
    games = len(inputs)
    wins = sum(1 for game in inputs if game.outcome is GameOutcome.WIN)
    draws = sum(1 for game in inputs if game.outcome is GameOutcome.DRAW)
    losses = sum(1 for game in inputs if game.outcome is GameOutcome.LOSS)

    accuracies = _numbers(game.accuracy for game in inputs)
    cpls = _numbers(game.average_centipawn_loss for game in inputs)
    dates = sorted(game.date for game in inputs if game.date)

    return PlayerGameStatistics(
        analyzed_games=games,
        wins=wins,
        draws=draws,
        losses=losses,
        win_rate=_rate(wins, games),
        draw_rate=_rate(draws, games),
        loss_rate=_rate(losses, games),
        average_accuracy=_mean(accuracies),
        median_accuracy=_median(accuracies),
        average_centipawn_loss=_mean(cpls),
        median_centipawn_loss=_median(cpls),
        blunders_per_game=_per_game(sum(game.blunders for game in inputs), games),
        mistakes_per_game=_per_game(sum(game.mistakes for game in inputs), games),
        inaccuracies_per_game=_per_game(sum(game.inaccuracies for game in inputs), games),
        average_game_length=_mean([float(game.move_count) for game in inputs if game.move_count]),
        accuracy_sample=len(accuracies),
        time_span=(dates[0] if dates else None, dates[-1] if dates else None),
        sample=_sample(policy, games=games, events=sum(game.scored_moves for game in inputs)),
    )


def by_color(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> list[PlayerColorStatistics]:
    """White and Black separated — with their own sample sizes.

    A difference between the two is never called meaningful here: each block
    merely states whether *that colour* has enough games to be claimed about.
    """
    results: list[PlayerColorStatistics] = []
    for color in ("white", "black"):
        subset = [game for game in inputs if game.color == color]
        games = len(subset)
        wins = sum(1 for game in subset if game.outcome is GameOutcome.WIN)
        draws = sum(1 for game in subset if game.outcome is GameOutcome.DRAW)
        losses = sum(1 for game in subset if game.outcome is GameOutcome.LOSS)
        claim_level = (
            _claim_level(policy, games)
            if games >= policy.min_games_per_color_for_claim
            else (ClaimLevel.INSUFFICIENT if games < policy.min_games_for_profile else ClaimLevel.OBSERVATION)
        )
        results.append(
            PlayerColorStatistics(
                color=color,
                games=games,
                wins=wins,
                draws=draws,
                losses=losses,
                win_rate=_rate(wins, games),
                average_accuracy=_mean(_numbers(game.accuracy for game in subset)),
                average_centipawn_loss=_mean(_numbers(game.average_centipawn_loss for game in subset)),
                blunders=sum(game.blunders for game in subset),
                mistakes=sum(game.mistakes for game in subset),
                inaccuracies=sum(game.inaccuracies for game in subset),
                sample=SampleNote(
                    games=games,
                    events=sum(game.scored_moves for game in subset),
                    claim_level=claim_level,
                    coverage=policy.coverage_for(games),
                    note=(
                        None
                        if games >= policy.min_games_per_color_for_claim
                        else f"Fewer than {policy.min_games_per_color_for_claim} games as {color} — "
                        "this split is descriptive only."
                    ),
                ),
            )
        )
    return results


def openings(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerOpeningStatistics:
    """Frequency (what is played) kept apart from performance (how it scores)."""
    games = len(inputs)
    grouped: dict[str, list[PlayerGameInput]] = {}
    families: dict[str, int] = {}
    deviations = 0

    for game in inputs:
        key = game.opening_name or game.eco_code or "Unknown / Unclassified"
        grouped.setdefault(key, []).append(game)
        family = game.opening_family or opening_family(game.eco_code, game.opening_name)
        if family:
            families[family] = families.get(family, 0) + 1
        if game.opening_deviation is not None:
            deviations += 1

    entries: list[PlayerOpeningEntry] = []
    for key, subset in grouped.items():
        count = len(subset)
        wins = sum(1 for game in subset if game.outcome is GameOutcome.WIN)
        draws = sum(1 for game in subset if game.outcome is GameOutcome.DRAW)
        losses = sum(1 for game in subset if game.outcome is GameOutcome.LOSS)
        claim = (
            _claim_level(policy, count)
            if count >= policy.min_games_per_opening_for_claim
            else ClaimLevel.OBSERVATION
        )
        entries.append(
            PlayerOpeningEntry(
                key=key,
                eco_code=next((game.eco_code for game in subset if game.eco_code), None),
                name=next((game.opening_name for game in subset if game.opening_name), None),
                family=next(
                    (game.opening_family or opening_family(game.eco_code, game.opening_name) for game in subset),
                    None,
                ),
                games=count,
                wins=wins,
                draws=draws,
                losses=losses,
                win_rate=_rate(wins, count),
                average_accuracy=_mean(_numbers(game.accuracy for game in subset)),
                average_centipawn_loss=_mean(_numbers(game.average_centipawn_loss for game in subset)),
                deviation_games=sum(1 for game in subset if game.opening_deviation is not None),
                recent_uses=min(3, count),
                sample=SampleNote(
                    games=count,
                    events=count,
                    claim_level=claim,
                    coverage=policy.coverage_for(count),
                    note=(
                        None
                        if count >= policy.min_games_per_opening_for_claim
                        else f"Fewer than {policy.min_games_per_opening_for_claim} games in this opening — "
                        "frequency is a fact, performance is not claimable."
                    ),
                ),
            )
        )

    def repertoire(color: str) -> list[PlayerOpeningEntry]:
        keys = {game.opening_name or game.eco_code or "Unknown / Unclassified"
                for game in inputs if game.color == color}
        return sorted(
            (entry for entry in entries if entry.key in keys),
            key=lambda entry: (-entry.games, entry.key),
        )

    return PlayerOpeningStatistics(
        white_repertoire=repertoire("white"),
        black_repertoire=repertoire("black"),
        most_played=sorted(entries, key=lambda entry: (-entry.games, entry.key)),
        families=dict(sorted(families.items(), key=lambda item: (-item[1], item[0]))),
        distinct_openings=len(grouped),
        deviation_rate=_rate(deviations, games),
        sample=_sample(policy, games=games, events=len(entries)),
    )


def phases(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerPhaseStatistics:
    """Per-phase CPL/accuracy, each phase with its own move count."""
    per_phase: dict[str, dict[str, float | int | None]] = {}
    for game in inputs:
        for row in game.phase_performance:
            bucket = per_phase.setdefault(
                row.phase,
                {"evaluated_moves": 0, "problem_moves": 0, "cpls": [], "accuracies": [], "shares": [], "games": 0},
            )
            bucket["evaluated_moves"] = int(bucket["evaluated_moves"]) + row.evaluated_moves
            bucket["problem_moves"] = int(bucket["problem_moves"]) + row.problem_moves
            if row.average_centipawn_loss is not None:
                bucket["cpls"].append(row.average_centipawn_loss)  # type: ignore[union-attr]
            if row.accuracy is not None:
                bucket["accuracies"].append(row.accuracy)  # type: ignore[union-attr]
            if row.share_of_loss is not None:
                bucket["shares"].append(row.share_of_loss)  # type: ignore[union-attr]
            bucket["games"] = int(bucket["games"]) + 1

    entries: list[PlayerPhaseEntry] = []
    for phase in ("opening", "middlegame", "endgame"):
        data = per_phase.get(phase)
        if not data:
            continue
        evaluated = int(data["evaluated_moves"])
        claim = (
            _claim_level(policy, len(inputs))
            if evaluated >= policy.min_phase_moves_for_claim
            else ClaimLevel.OBSERVATION
        )
        entries.append(
            PlayerPhaseEntry(
                phase=phase,
                evaluated_moves=evaluated,
                average_centipawn_loss=_mean(data["cpls"]),  # type: ignore[arg-type]
                problem_moves=int(data["problem_moves"]),
                accuracy=_mean(data["accuracies"]),  # type: ignore[arg-type]
                share_of_loss=_mean(data["shares"]),  # type: ignore[arg-type]
                sample=SampleNote(
                    games=int(data["games"]),
                    events=evaluated,
                    claim_level=claim,
                    coverage=policy.coverage_for(len(inputs)),
                    note=(
                        None
                        if evaluated >= policy.min_phase_moves_for_claim
                        else f"Fewer than {policy.min_phase_moves_for_claim} evaluated moves in this phase."
                    ),
                ),
            )
        )

    claimable = [entry for entry in entries if entry.sample.claim_level is not ClaimLevel.INSUFFICIENT]
    weakest = max(claimable, key=lambda entry: entry.average_centipawn_loss or 0.0, default=None)
    strongest = min(claimable, key=lambda entry: entry.average_centipawn_loss or 9e9, default=None)
    return PlayerPhaseStatistics(
        phases=entries,
        weakest_phase=weakest.phase if weakest else None,
        strongest_phase=strongest.phase if strongest else None,
        sample=_sample(policy, games=len(inputs), events=sum(entry.evaluated_moves for entry in entries)),
    )


def tactical(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerTacticalStatistics:
    """Tactical events the player created vs. those they allowed.

    Kept apart on purpose: creating chances and defending against them are
    different skills, so they never collapse into one score.
    """
    created: dict[str, int] = {}
    allowed: dict[str, int] = {}
    for game in inputs:
        for event in game.tactical_events:
            target = created if event.direction == "created" else allowed
            target[event.event_type] = target.get(event.event_type, 0) + 1

    games = len(inputs)
    created_total = sum(created.values())
    allowed_total = sum(allowed.values())
    missed = sum(
        1
        for game in inputs
        for error in game.errors
        if "tactical" in error.categories and error.certainty == "confirmed"
    )
    return PlayerTacticalStatistics(
        created=dict(sorted(created.items(), key=lambda item: (-item[1], item[0]))),
        allowed=dict(sorted(allowed.items(), key=lambda item: (-item[1], item[0]))),
        created_total=created_total,
        allowed_total=allowed_total,
        created_per_game=_per_game(created_total, games),
        allowed_per_game=_per_game(allowed_total, games),
        missed_opportunities=missed,
        sample=_sample(policy, games=games, events=created_total + allowed_total),
    )


def positional(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerPositionalStatistics:
    """Structural features (facts) separated from supported error candidates."""
    features: dict[str, int] = {}
    errors: dict[str, int] = {}
    evidence: list[tuple[str, str, int, int | None, str | None]] = []
    for game in inputs:
        for event in game.positional_events:
            if event.kind == "error_candidate":
                errors[event.feature] = errors.get(event.feature, 0) + 1
                evidence.append(
                    (game.game_id, event.feature, event.ply, event.move_number, event.san)
                )
            else:
                features[event.feature] = features.get(event.feature, 0) + 1

    games = len(inputs)
    return PlayerPositionalStatistics(
        features=dict(sorted(features.items(), key=lambda item: (-item[1], item[0]))),
        error_candidates=dict(sorted(errors.items(), key=lambda item: (-item[1], item[0]))),
        features_per_game=_per_game(sum(features.values()), games),
        error_candidates_per_game=_per_game(sum(errors.values()), games),
        evidence=_evidence_list(
            [
                _evidence(
                    game_id,
                    ply,
                    move_number,
                    san,
                    label=f"positional error candidate · {feature}",
                )
                for game_id, feature, ply, move_number, san in evidence
            ]
        ),
        sample=_sample(policy, games=games, events=sum(errors.values())),
    )


def _evidence_list(refs: Sequence):
    """Evidence, de-duplicated by (game, ply, label) and capped.

    One move can change several structures at once (a capture can isolate a pawn
    *and* open a file), so the same ply legitimately carries several evidence
    rows — but never the same row twice. The cap keeps the payload readable; the
    counts above stay complete regardless.
    """
    unique: list = []
    seen: set[tuple[str, int, str | None]] = set()
    for ref in refs:
        key = (ref.game_id, ref.ply, ref.label)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
        if len(unique) >= 25:
            break
    return unique


def _evidence(game_id: str, ply: int, move_number: int | None, san: str | None, *, label: str | None = None):
    from argus.player_intelligence.models import EvidenceRef

    return EvidenceRef(game_id=game_id, ply=ply, move_number=move_number, san=san, label=label)


def king_safety(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerKingSafetyStatistics:
    """Castling behaviour plus king-safety events created/allowed."""
    games = len(inputs)
    castled = sum(1 for game in inputs if game.castled is True)
    uncastled = sum(1 for game in inputs if game.castled is False)
    late = sum(
        1
        for game in inputs
        if game.castled is True and (game.castling_ply or 0) > LATE_CASTLING_MOVE
    )
    created = 0
    allowed = 0
    types: dict[str, int] = {}
    evidence: list = []
    for game in inputs:
        for event in game.king_safety_events:
            if event.direction == "created":
                created += 1
            else:
                allowed += 1
            types[event.event_type] = types.get(event.event_type, 0) + 1
            evidence.append(
                _evidence(game.game_id, event.ply, event.move_number, event.san,
                          label=f"king safety · {event.event_type}")
            )

    by_color: dict[str, dict[str, float | int | None]] = {}
    for color in ("white", "black"):
        subset = [game for game in inputs if game.color == color]
        count = len(subset)
        color_castled = sum(1 for game in subset if game.castled is True)
        color_events = sum(len(game.king_safety_events) for game in subset)
        by_color[color] = {
            "games": count,
            "castled_games": color_castled,
            "castling_rate": _rate(color_castled, count),
            "king_safety_events": color_events,
            "events_per_game": _per_game(color_events, count),
        }

    return PlayerKingSafetyStatistics(
        castled_games=castled,
        uncastled_games=uncastled,
        castling_rate=_rate(castled, castled + uncastled),
        late_castling_games=late,
        events_created=created,
        events_allowed=allowed,
        top_event_types=dict(sorted(types.items(), key=lambda item: (-item[1], item[0]))),
        by_color=by_color,
        evidence=_evidence_list(evidence),
        sample=_sample(policy, games=games, events=created + allowed),
    )


def material_stats(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerMaterialStatistics:
    """Material behaviour as a *characteristic*, never as good or bad."""
    games = len(inputs)
    if games == 0:
        return PlayerMaterialStatistics(sample=_sample(policy, games=0))

    captures = [game.material.total_captures for game in inputs]
    exchanges = [game.material.exchanges for game in inputs]
    balances = [game.material.final_balance for game in inputs]
    imbalance = sum(1 for game in inputs if game.material.imbalance_reached)
    return PlayerMaterialStatistics(
        average_captures=_mean([float(value) for value in captures]),
        average_exchanges=_mean([float(value) for value in exchanges]),
        promotions=sum(game.material.promotions for game in inputs),
        imbalance_games=imbalance,
        imbalance_share=_rate(imbalance, games),
        average_final_balance=_mean([float(value) for value in balances]),
        sample=_sample(policy, games=games, events=sum(captures)),
    )


def conversion(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerConversionStatistics:
    """Advantage conversion, using the documented Caissa band thresholds.

    An *opportunity* is a game where the player's peak advantage band reached
    ``conversion_min_band`` (winning). It is converted when the game was won or
    the advantage was still winning at the final evaluated ply. Reaching a
    winning evaluation is not treated as a trivial win.
    """
    opportunities: list[PlayerGameInput] = []
    conversions: list[PlayerGameInput] = []
    for game in inputs:
        peak = game.trajectory.peak_band
        if peak is None or peak < policy.conversion_min_band:
            continue
        opportunities.append(game)
        final = game.trajectory.final_band
        if game.outcome is GameOutcome.WIN or (final is not None and final >= policy.conversion_min_band):
            conversions.append(game)

    games = len(inputs)
    lost = len(opportunities) - len(conversions)
    claim = (
        _claim_level(policy, games)
        if len(opportunities) >= policy.min_pattern_occurrences
        else ClaimLevel.OBSERVATION
    )
    return PlayerConversionStatistics(
        opportunities=len(opportunities),
        conversions=len(conversions),
        conversion_rate=_rate(len(conversions), len(opportunities)),
        advantage_lost=lost,
        maintenance_rate=_rate(len(conversions), len(opportunities)),
        evidence=[
            _evidence(game.game_id, 0, None, None, label="advantage reached winning")
            for game in opportunities[:25]
        ],
        sample=SampleNote(
            games=games,
            events=len(opportunities),
            claim_level=claim,
            coverage=policy.coverage_for(games),
            note=(
                None
                if len(opportunities) >= policy.min_pattern_occurrences
                else f"Fewer than {policy.min_pattern_occurrences} conversion opportunities observed."
            ),
        ),
    )


def recovery(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerRecoveryStatistics:
    """Games where the player was losing: did the evaluation come back?"""
    situations: list[PlayerGameInput] = []
    improved: list[PlayerGameInput] = []
    saved: list[PlayerGameInput] = []
    for game in inputs:
        worst = game.trajectory.worst_band
        if worst is None or worst > policy.recovery_max_band:
            continue
        situations.append(game)
        final = game.trajectory.final_band
        if final is not None and final > worst:
            improved.append(game)
        if game.outcome in (GameOutcome.WIN, GameOutcome.DRAW):
            saved.append(game)

    games = len(inputs)
    claim = (
        _claim_level(policy, games)
        if len(situations) >= policy.min_pattern_occurrences
        else ClaimLevel.OBSERVATION
    )
    return PlayerRecoveryStatistics(
        situations=len(situations),
        improvements=len(improved),
        improvement_rate=_rate(len(improved), len(situations)),
        saved_games=len(saved),
        save_rate=_rate(len(saved), len(situations)),
        evidence=[
            _evidence(game.game_id, 0, None, None, label="reached a losing evaluation")
            for game in situations[:25]
        ],
        sample=SampleNote(
            games=games,
            events=len(situations),
            claim_level=claim,
            coverage=policy.coverage_for(games),
            note=(
                None
                if len(situations) >= policy.min_pattern_occurrences
                else f"Fewer than {policy.min_pattern_occurrences} games reached a losing evaluation."
            ),
        ),
    )


def time_controls(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerTimeControlStatistics:
    """Performance per time class; classes without enough games stay descriptive."""
    entries: list[PlayerTimeControlEntry] = []
    grouped: dict[TimeClass, list[PlayerGameInput]] = {}
    for game in inputs:
        grouped.setdefault(game.time_class, []).append(game)

    for time_class in (TimeClass.BULLET, TimeClass.BLITZ, TimeClass.RAPID, TimeClass.CLASSICAL, TimeClass.UNKNOWN):
        subset = grouped.get(time_class)
        if not subset:
            continue
        games = len(subset)
        wins = sum(1 for game in subset if game.outcome is GameOutcome.WIN)
        draws = sum(1 for game in subset if game.outcome is GameOutcome.DRAW)
        losses = sum(1 for game in subset if game.outcome is GameOutcome.LOSS)
        opportunities = [
            game for game in subset
            if game.trajectory.peak_band is not None and game.trajectory.peak_band >= policy.conversion_min_band
        ]
        converted = [
            game for game in opportunities
            if game.outcome is GameOutcome.WIN
            or (game.trajectory.final_band is not None and game.trajectory.final_band >= policy.conversion_min_band)
        ]
        entries.append(
            PlayerTimeControlEntry(
                time_class=time_class,
                games=games,
                wins=wins,
                draws=draws,
                losses=losses,
                win_rate=_rate(wins, games),
                average_accuracy=_mean(_numbers(game.accuracy for game in subset)),
                average_centipawn_loss=_mean(_numbers(game.average_centipawn_loss for game in subset)),
                blunders_per_game=_per_game(sum(game.blunders for game in subset), games),
                average_game_length=_mean([float(game.move_count) for game in subset if game.move_count]),
                conversion_rate=_rate(len(converted), len(opportunities)),
                sample=SampleNote(
                    games=games,
                    events=sum(game.scored_moves for game in subset),
                    claim_level=(
                        _claim_level(policy, games)
                        if games >= policy.min_games_per_time_control_for_claim
                        else ClaimLevel.OBSERVATION
                    ),
                    coverage=policy.coverage_for(games),
                    note=(
                        None
                        if games >= policy.min_games_per_time_control_for_claim
                        else f"Fewer than {policy.min_games_per_time_control_for_claim} games in this "
                        "time control — descriptive only."
                    ),
                ),
            )
        )

    return PlayerTimeControlStatistics(
        entries=entries,
        sample=_sample(policy, games=len(inputs), events=len(entries)),
    )


def opponents(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerOpponentContext:
    """Opponent strength kept as context (buckets), not averaged away."""
    rated = [game for game in inputs if game.rating_difference is not None]
    buckets: dict[str, dict[str, float | int | None]] = {}
    for game in rated:
        bucket = game.rating_bucket()
        entry = buckets.setdefault(
            bucket, {"games": 0, "wins": 0, "draws": 0, "losses": 0, "accuracies": [], "cpls": []}
        )
        entry["games"] = int(entry["games"]) + 1
        if game.outcome is GameOutcome.WIN:
            entry["wins"] = int(entry["wins"]) + 1
        elif game.outcome is GameOutcome.DRAW:
            entry["draws"] = int(entry["draws"]) + 1
        elif game.outcome is GameOutcome.LOSS:
            entry["losses"] = int(entry["losses"]) + 1
        if game.accuracy is not None:
            entry["accuracies"].append(game.accuracy)  # type: ignore[union-attr]
        if game.average_centipawn_loss is not None:
            entry["cpls"].append(game.average_centipawn_loss)  # type: ignore[union-attr]

    simplified: dict[str, dict[str, float | int | None]] = {}
    for bucket, entry in buckets.items():
        games = int(entry["games"])
        simplified[bucket] = {
            "games": games,
            "wins": int(entry["wins"]),
            "draws": int(entry["draws"]),
            "losses": int(entry["losses"]),
            "win_rate": _rate(int(entry["wins"]), games),
            "average_accuracy": _mean(entry["accuracies"]),  # type: ignore[arg-type]
            "average_centipawn_loss": _mean(entry["cpls"]),  # type: ignore[arg-type]
        }

    return PlayerOpponentContext(
        games_with_rating=len(rated),
        average_player_rating=_mean(_numbers(float(game.player_rating) for game in rated if game.player_rating)),
        average_opponent_rating=_mean(
            _numbers(float(game.opponent_rating) for game in rated if game.opponent_rating)
        ),
        average_rating_difference=_mean(_numbers(float(game.rating_difference) for game in rated
                                                 if game.rating_difference is not None)),
        by_bucket=simplified,
        sample=SampleNote(
            games=len(inputs),
            events=len(rated),
            claim_level=(
                _claim_level(policy, len(inputs))
                if len(rated) >= policy.min_games_with_opponent_rating
                else ClaimLevel.OBSERVATION
            ),
            coverage=policy.coverage_for(len(inputs)),
            note=(
                None
                if len(rated) >= policy.min_games_with_opponent_rating
                else "Too few games with a known opponent rating to compare strength contexts."
            ),
        ),
    )


def trends(inputs: Sequence[PlayerGameInput], policy: PlayerInsightPolicy) -> PlayerTrendStatistics:
    """Recent window vs. historical baseline, per configured window.

    Reports only the measured difference. It never says "you improved" —
    statistical significance is explicitly out of scope for this phase.
    """
    ordered = _chronological(inputs)
    entries: list[PlayerTrendEntry] = []
    for window in policy.trend_windows:
        if len(ordered) < games_required_for_window(policy, window):
            entries.append(
                PlayerTrendEntry(
                    window=window,
                    note=f"Needs at least {games_required_for_window(policy, window)} analyzed games "
                    f"for a last-{window} comparison.",
                )
            )
            continue
        recent = ordered[-window:]
        baseline = ordered[: -window]
        recent_cpls = _numbers(game.average_centipawn_loss for game in recent)
        baseline_cpls = _numbers(game.average_centipawn_loss for game in baseline)
        recent_cpl = _mean(recent_cpls)
        baseline_cpl = _mean(baseline_cpls)
        change = None
        direction = "steady"
        if recent_cpl is not None and baseline_cpl not in (None, 0):
            change = round((recent_cpl - baseline_cpl) / baseline_cpl, 4)
            if change <= -policy.trend_min_relative_change:
                direction = "lower_cpl"
            elif change >= policy.trend_min_relative_change:
                direction = "higher_cpl"
        supported = len(recent) >= policy.min_recent_games_for_trend and len(baseline) > 0
        entries.append(
            PlayerTrendEntry(
                window=window,
                recent_games=len(recent),
                baseline_games=len(baseline),
                recent_average_cpl=recent_cpl,
                baseline_average_cpl=baseline_cpl,
                relative_change=change,
                direction=direction,
                supported=supported and change is not None,
                recent_wins=sum(1 for game in recent if game.outcome is GameOutcome.WIN),
                recent_draws=sum(1 for game in recent if game.outcome is GameOutcome.DRAW),
                recent_losses=sum(1 for game in recent if game.outcome is GameOutcome.LOSS),
                note=None if supported else f"Fewer than {policy.min_recent_games_for_trend} recent games.",
            )
        )

    return PlayerTrendStatistics(
        entries=entries,
        note=(
            "Recent windows are compared against all earlier analyzed games. A difference is a "
            "measurement, not a conclusion about improvement."
        ),
    )


def games_required_for_window(policy: PlayerInsightPolicy, window: int) -> int:
    """Games needed for a window comparison: the window plus its baseline."""
    return max(policy.min_games_for_trend, window + policy.min_recent_games_for_trend)


__all__ = [
    "LATE_CASTLING_MOVE",
    "analyze_games",
    "by_color",
    "classify_time_control",
    "conversion",
    "king_safety",
    "material_stats",
    "openings",
    "opening_family",
    "games_required_for_window",
    "opponents",
    "phases",
    "positional",
    "recovery",
    "tactical",
    "time_controls",
    "trends",
]
