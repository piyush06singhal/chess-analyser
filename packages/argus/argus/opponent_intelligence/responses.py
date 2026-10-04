"""Position-specific responses: how the opponent answered a given position.

Two questions are answered here, both from stored games only:

* **"How has this opponent answered this exact position?"** — every game in
  which the opponent faced the query position is found (exact FEN match, then a
  looser piece-placement match), and their move distribution is returned with
  its sample size.
* **"Which positions does this opponent reach again and again, and what do they
  do there?"** — positions reached across at least the structure gate's number
  of games are listed as recurring position patterns.

Nothing is predicted. A response list with one occurrence is shown as one
occurrence, and a position the opponent has never faced returns ``match: none``
rather than a guess.
"""

from __future__ import annotations

from argus.opponent_intelligence.common import (
    claim_level,
    normalize_fen,
    piece_placement,
    share,
)
from argus.opponent_intelligence.models import (
    OpponentEvidence,
    OpponentGameInput,
    OpponentPositionPattern,
    OpponentPositionResponse,
    OpponentResponseOption,
)
from argus.opponent_intelligence.policy import OpponentInsightPolicy
from argus.player_intelligence.models import GameOutcome

MAX_EVIDENCE_PER_OPTION = 5


def _outcome_counts(option: OpponentResponseOption, outcome: GameOutcome) -> None:
    if outcome == GameOutcome.WIN:
        option.wins += 1
    elif outcome == GameOutcome.DRAW:
        option.draws += 1
    elif outcome == GameOutcome.LOSS:
        option.losses += 1


def _collect(
    games: list[OpponentGameInput],
    *,
    key,
) -> tuple[dict[str, OpponentResponseOption], int, list[OpponentEvidence]]:
    """Aggregate the opponent's answers in every game whose position matches."""
    options: dict[str, OpponentResponseOption] = {}
    occurrences = 0
    evidence: list[OpponentEvidence] = []
    for game in games:
        if not game.analyzed:
            continue
        for move in sorted(game.moves, key=lambda m: m.ply):
            if move.color != game.color:
                continue
            if not key(move.fen_before):
                continue
            occurrences += 1
            option = options.get(move.uci)
            if option is None:
                option = OpponentResponseOption(uci=move.uci, san=move.san, occurrences=0, share=0.0)
                options[move.uci] = option
            option.occurrences += 1
            _outcome_counts(option, game.outcome)
            if len(option.evidence) < MAX_EVIDENCE_PER_OPTION:
                option.evidence.append(
                    OpponentEvidence(
                        game_id=game.game_id,
                        ply=move.ply,
                        move_number=move.move_number,
                        san=move.san,
                        label=f"answered with {move.san}",
                        detail=game.opening_name,
                    )
                )
            if len(evidence) < MAX_EVIDENCE_PER_OPTION:
                evidence.append(
                    OpponentEvidence(
                        game_id=game.game_id,
                        ply=move.ply,
                        move_number=move.move_number,
                        san=move.san,
                        label=f"played {move.san} here",
                    )
                )
    return options, occurrences, evidence


def find_position_responses(
    games: list[OpponentGameInput],
    fen: str,
    *,
    policy: OpponentInsightPolicy,
) -> OpponentPositionResponse:
    """The opponent's answers to one position, exact match preferred."""
    target = normalize_fen(fen)
    target_pieces = piece_placement(fen)
    if not target:
        return OpponentPositionResponse(query_fen=fen, match="none", sample_note="No position given.")

    options, occurrences, evidence = _collect(
        games, key=lambda before: normalize_fen(before) == target
    )
    match = "exact"
    if not options:
        options, occurrences, evidence = _collect(
            games, key=lambda before: piece_placement(before) == target_pieces
        )
        match = "normalized_pieces" if options else "none"

    total = sum(option.occurrences for option in options.values())
    ordered = list(options.values())
    for option in ordered:
        option.share = share(option.occurrences, total)
    ordered.sort(key=lambda option: (option.occurrences, option.san), reverse=True)

    best_share = ordered[0].share if ordered else None
    level = claim_level(
        occurrences=total,
        share_value=best_share,
        games=total,
        policy=policy,
        gate="min_occurrences_for_tendency",
    )
    if total == 0:
        note = "The opponent has no stored game in which they faced this position."
    elif total < policy.min_occurrences_for_tendency:
        note = f"Only {total} stored occurrence(s): reported as an observation, not a tendency."
    else:
        note = f"{total} stored occurrence(s) of this position."
    return OpponentPositionResponse(
        query_fen=fen,
        match=match,
        occurrences=occurrences,
        responses=ordered,
        claim_level=level,
        sample_note=note,
        evidence=evidence[:MAX_EVIDENCE_PER_OPTION],
    )


def find_position_patterns(
    games: list[OpponentGameInput],
    *,
    policy: OpponentInsightPolicy,
    limit: int = 20,
) -> list[OpponentPositionPattern]:
    """Positions the opponent reaches repeatedly, with their answers there."""
    analyzed = [game for game in games if game.analyzed]

    # position (normalized) → how many distinct games reached it
    reach: dict[str, set[str]] = {}
    samples: dict[str, tuple[str, str]] = {}  # fen → (side_to_move, representative prefixed line)

    for game in analyzed:
        prefix: list[tuple[int, str]] = []
        for move in sorted(game.moves, key=lambda m: m.ply):
            prefix.append((move.ply, move.san))
            if move.color == game.color:
                key = normalize_fen(move.fen_before)
                if key:
                    reach.setdefault(key, set()).add(game.game_id)
                    if key not in samples:
                        side = "white" if move.ply % 2 == 1 else "black"
                        samples[key] = (side, _format_prefix(prefix))

    ranked = sorted(reach.items(), key=lambda item: len(item[1]), reverse=True)
    patterns: list[OpponentPositionPattern] = []
    for key, game_ids in ranked:
        if len(game_ids) < policy.min_positions_for_structure_insight:
            continue
        side, label = samples.get(key, ("unknown", ""))
        options, occurrences, evidence = _collect(
            games, key=lambda before, position_key=key: normalize_fen(before) == position_key
        )
        total = sum(option.occurrences for option in options.values())
        ordered = list(options.values())
        for option in ordered:
            option.share = share(option.occurrences, total)
        ordered.sort(key=lambda option: (option.occurrences, option.san), reverse=True)
        wins = sum(option.wins for option in ordered)
        draws = sum(option.draws for option in ordered)
        losses = sum(option.losses for option in ordered)
        best_share = ordered[0].share if ordered else None
        level = claim_level(
            occurrences=occurrences,
            share_value=best_share,
            games=len(game_ids),
            policy=policy,
            gate="min_positions_for_structure_insight",
        )
        patterns.append(
            OpponentPositionPattern(
                key=key,
                label=label or f"Reached after {occurrences} moves",
                fen_signature=key,
                occurrences=occurrences,
                side_to_move=side,
                responses=ordered,
                wins=wins,
                draws=draws,
                losses=losses,
                claim_level=level,
                evidence=evidence[:MAX_EVIDENCE_PER_OPTION],
            )
        )
        if len(patterns) >= limit:
            break
    return patterns


def _format_prefix(prefix: list[tuple[int, str]]) -> str:
    """Render a move prefix as ``1.e4 e5 2.Nf3`` from real plies."""
    parts: list[str] = []
    for ply, san in prefix[-8:]:
        if ply % 2 == 1:
            parts.append(f"{(ply + 1) // 2}.")
        parts.append(san)
    return " ".join(parts).strip()


__all__ = ["find_position_patterns", "find_position_responses"]
