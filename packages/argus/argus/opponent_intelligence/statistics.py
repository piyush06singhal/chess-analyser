"""Measured statistics and tendencies for an opponent.

Everything here is a count, a share, or a centipawn measurement over the
opponent's stored moves. The two entry points are:

* :func:`phase_statistics` — performance by game phase, from stored move
  analysis (phases, centipawn loss, significant-error rate).
* :func:`tendencies` — recurring, evidence-gated regularities (castling habit,
  how often they check or capture, where their significant errors concentrate,
  which time controls they actually play).

A tendency is emitted only when its named sample-size gate is met; otherwise it
is returned with ``ClaimLevel.INSUFFICIENT`` and the measured value, so the UI
can show the count without dressing it up as a finding.
"""

from __future__ import annotations

import chess

from argus.opponent_intelligence.common import claim_level, share
from argus.opponent_intelligence.models import (
    OpponentEvidence,
    OpponentGameInput,
    OpponentPhaseStat,
    OpponentPhaseStatistics,
    OpponentTendency,
)
from argus.opponent_intelligence.policy import ClaimLevel, OpponentInsightPolicy
from argus.player_intelligence.aggregate import classify_time_control

MAX_EVIDENCE = 5


def _scored(move) -> bool:  # noqa: ANN001 — OpponentMoveInput
    return move.centipawn_loss is not None


def phase_statistics(
    games: list[OpponentGameInput],
    *,
    color: str | None = None,
    policy: OpponentInsightPolicy,
) -> OpponentPhaseStatistics:
    """Performance by phase, measured from stored move analysis only."""
    relevant = [game for game in games if color is None or game.color == color]
    analyzed = [game for game in relevant if game.analyzed]

    per_phase: dict[str, dict[str, object]] = {}
    tactical_errors = 0
    positional_errors = 0

    for game in analyzed:
        for move in game.moves:
            if move.color != game.color:
                continue
            if color is not None and move.color != color:
                continue
            phase = move.phase or "unknown"
            row = per_phase.setdefault(
                phase, {"moves": 0, "games": set(), "errors": 0, "loss_sum": 0, "loss_n": 0, "within": 0}
            )
            row["moves"] = int(row["moves"]) + 1
            row["games"].add(game.game_id)  # type: ignore[union-attr]
            if _scored(move):
                row["loss_sum"] = int(row["loss_sum"]) + int(move.centipawn_loss or 0)
                row["loss_n"] = int(row["loss_n"]) + 1
                if int(move.centipawn_loss or 0) <= 30:
                    row["within"] = int(row["within"]) + 1
            if _scored(move) and int(move.centipawn_loss or 0) >= policy.significant_loss_cp:
                row["errors"] = int(row["errors"]) + 1
                kind = _error_kind(move)
                if kind == "tactical":
                    tactical_errors += 1
                elif kind == "positional":
                    positional_errors += 1

    stats: list[OpponentPhaseStat] = []
    for phase in sorted(per_phase):
        row = per_phase[phase]
        moves = int(row["moves"])
        games_count = len(row["games"])  # type: ignore[arg-type]
        errors = int(row["errors"])
        loss_n = int(row["loss_n"])
        level = (
            ClaimLevel.PATTERN
            if games_count >= policy.min_games_for_phase_comparison
            else ClaimLevel.OBSERVATION
        )
        stats.append(
            OpponentPhaseStat(
                phase=phase,
                moves=moves,
                games=games_count,
                significant_errors=errors,
                error_rate=round(errors / moves, 4) if moves else None,
                avg_centipawn_loss=round(int(row["loss_sum"]) / loss_n, 2) if loss_n else None,
                avg_accuracy_proxy=round(int(row["within"]) / loss_n, 4) if loss_n else None,
                claim_level=level,
            )
        )

    classified = tactical_errors + positional_errors
    if not analyzed:
        note = "No analysed games for this colour yet: there is no phase data to measure."
    else:
        note = (
            f"Measured over {len(analyzed)} analysed game(s). 'accuracy_proxy' is the share of "
            "scored moves within 30cp of the engine's best; it is a move-quality proxy, not a "
            "rating and not comparable to other sites' accuracy."
        )
    return OpponentPhaseStatistics(
        color=color,
        analyzed_games=len(analyzed),
        phases=stats,
        tactical_error_share=round(tactical_errors / classified, 4) if classified else None,
        positional_error_share=round(positional_errors / classified, 4) if classified else None,
        sample_note=note,
        policy=policy.to_dict(),
    )


def _error_kind(move) -> str | None:  # noqa: ANN001 — OpponentMoveInput
    """Tactical vs positional proxy from the engine's *better* move.

    A significant error whose engine-best move is a capture or a check is
    classified tactical; anything else positional. This is a property of the
    position, not of the player's mind, and is labelled as a proxy everywhere.
    """
    if not move.best_move_uci:
        return None
    try:
        board = chess.Board(move.fen_before)
        best = chess.Move.from_uci(move.best_move_uci)
    except ValueError:
        return None
    if best not in board.legal_moves:
        return None
    if board.is_capture(best) or board.gives_check(best):
        return "tactical"
    return "positional"


def tendencies(
    games: list[OpponentGameInput],
    *,
    policy: OpponentInsightPolicy,
    phase_stats: OpponentPhaseStatistics | None = None,
) -> list[OpponentTendency]:
    """Recurring, evidence-gated regularities across the opponent's games."""
    analyzed = [game for game in games if game.analyzed]
    result: list[OpponentTendency] = []

    result.append(_castling_tendency(analyzed, policy))
    result.append(_check_tendency(analyzed, policy))
    result.append(_capture_tendency(analyzed, policy))
    result.append(_opening_aggression_tendency(analyzed, policy))
    concentration = _error_concentration(analyzed, phase_stats, policy)
    if concentration is not None:
        result.append(concentration)
    result.append(_time_control_tendency(games, policy))

    return [tendency for tendency in result if tendency is not None]


def _castling_tendency(games: list[OpponentGameInput], policy: OpponentInsightPolicy) -> OpponentTendency:
    kingside = queenside = 0
    evidence: list[OpponentEvidence] = []
    for game in games:
        for move in game.moves:
            if move.color != game.color:
                continue
            if move.san in ("O-O", "0-0"):
                kingside += 1
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(_ev(game, move, "castled kingside"))
                break
            if move.san in ("O-O-O", "0-0-0"):
                queenside += 1
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(_ev(game, move, "castled queenside"))
                break
    total = kingside + queenside
    best_share = share(max(kingside, queenside), total) if total else None
    side = "kingside" if kingside >= queenside else "queenside"
    return OpponentTendency(
        key="castling_side",
        label=f"Castles {side}",
        measurement="share of castling games in which the opponent castled kingside vs queenside",
        value=f"{kingside} kingside / {queenside} queenside",
        sample_size=total,
        share=best_share,
        claim_level=claim_level(
            occurrences=total,
            share_value=best_share,
            games=total,
            policy=policy,
        ),
        evidence=evidence,
        note="Counted from stored SAN castling moves; games with no castling are excluded.",
    )


def _check_tendency(games: list[OpponentGameInput], policy: OpponentInsightPolicy) -> OpponentTendency:
    checks = 0
    total = 0
    evidence: list[OpponentEvidence] = []
    for game in games:
        for move in game.moves:
            if move.color != game.color:
                continue
            total += 1
            if _gives_check(move):
                checks += 1
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(_ev(game, move, "gave check"))
    value = share(checks, total)
    return OpponentTendency(
        key="check_frequency",
        label="Checks given",
        measurement="share of the opponent's moves that gave check",
        value=f"{checks} of {total} moves",
        sample_size=total,
        share=value,
        claim_level=claim_level(
            occurrences=checks,
            share_value=None,
            games=len(games),
            policy=policy,
        ),
        evidence=evidence,
        note="A raw rate, not a preference: positions with checks available are more common in sharp games.",
    )


def _capture_tendency(games: list[OpponentGameInput], policy: OpponentInsightPolicy) -> OpponentTendency:
    captures = 0
    total = 0
    evidence: list[OpponentEvidence] = []
    for game in games:
        for move in game.moves:
            if move.color != game.color:
                continue
            total += 1
            if _is_capture(move):
                captures += 1
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(_ev(game, move, "captured"))
    value = share(captures, total)
    return OpponentTendency(
        key="capture_frequency",
        label="Captures made",
        measurement="share of the opponent's moves that captured a piece",
        value=f"{captures} of {total} moves",
        sample_size=total,
        share=value,
        claim_level=claim_level(
            occurrences=captures,
            share_value=None,
            games=len(games),
            policy=policy,
        ),
        evidence=evidence,
        note="Measured from stored FENs before each move; a baseline rate, not aggression.",
    )


def _opening_aggression_tendency(
    games: list[OpponentGameInput], policy: OpponentInsightPolicy
) -> OpponentTendency:
    sharp = 0
    total = 0
    evidence: list[OpponentEvidence] = []
    for game in games:
        for move in game.moves:
            if move.color != game.color or move.ply > 12:
                continue
            total += 1
            if _is_capture(move) or _gives_check(move):
                sharp += 1
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(_ev(game, move, "sharp opening move"))
    value = share(sharp, total)
    return OpponentTendency(
        key="opening_aggression",
        label="Sharp opening moves",
        measurement="share of the opponent's first six moves that were captures or checks",
        value=f"{sharp} of {total} opening moves",
        sample_size=total,
        share=value,
        claim_level=claim_level(
            occurrences=sharp,
            share_value=None,
            games=len(games),
            policy=policy,
        ),
        evidence=evidence,
        note="Opening window is the opponent's first six moves (ply ≤ 12).",
    )


def _error_concentration(
    games: list[OpponentGameInput],
    phase_stats: OpponentPhaseStatistics | None,
    policy: OpponentInsightPolicy,
) -> OpponentTendency | None:
    if phase_stats is None or not phase_stats.phases:
        return None
    ranked = sorted(
        (stat for stat in phase_stats.phases if stat.phase != "unknown" and stat.significant_errors > 0),
        key=lambda stat: (stat.significant_errors, stat.moves),
        reverse=True,
    )
    if not ranked:
        return None
    top = ranked[0]
    total_errors = sum(stat.significant_errors for stat in phase_stats.phases)
    return OpponentTendency(
        key="error_concentration",
        label=f"Errors concentrate in the {top.phase}",
        measurement="phase with the most significant errors (≥100cp), across analysed games",
        value=f"{top.significant_errors} of {total_errors} significant errors",
        sample_size=top.games,
        share=share(top.significant_errors, total_errors),
        claim_level=(
            ClaimLevel.PATTERN
            if top.games >= policy.min_games_for_phase_comparison
            else ClaimLevel.OBSERVATION
        ),
        evidence=[],
        note="Based on the phase labels stored by analysis; phases with no significant errors are omitted.",
    )


def _time_control_tendency(games: list[OpponentGameInput], policy: OpponentInsightPolicy) -> OpponentTendency:
    counts: dict[str, int] = {}
    for game in games:
        label = _time_class(game.time_control)
        counts[label] = counts.get(label, 0) + 1
    total = sum(counts.values())
    best = max(counts.items(), key=lambda item: item[1]) if counts else ("unknown", 0)
    return OpponentTendency(
        key="time_control",
        label="Most played time control",
        measurement="game count by time-control class",
        value=f"{best[0]} in {best[1]} of {total} games",
        sample_size=total,
        share=share(best[1], total) if total else None,
        claim_level=claim_level(
            occurrences=best[1],
            share_value=None,
            games=total,
            policy=policy,
        ),
        evidence=[],
        note="Classes are estimated from the stored time control; unparseable controls are 'unknown'.",
    )


# ---------------------------------------------------------------------------
# small move inspectors (pure, from stored FEN/SAN)
# ---------------------------------------------------------------------------


def _gives_check(move) -> bool:  # noqa: ANN001
    try:
        board = chess.Board(move.fen_before)
        candidate = chess.Move.from_uci(move.uci)
    except ValueError:
        return move.san.endswith("+") or move.san.endswith("#")
    if candidate not in board.legal_moves:
        return move.san.endswith("+") or move.san.endswith("#")
    return board.gives_check(candidate)


def _is_capture(move) -> bool:  # noqa: ANN001
    if "x" in move.san:
        return True
    try:
        board = chess.Board(move.fen_before)
        candidate = chess.Move.from_uci(move.uci)
    except ValueError:
        return False
    if candidate not in board.legal_moves:
        return False
    return board.is_capture(candidate)


def _time_class(time_control: str | None) -> str:
    """Classify a stored time control via Phase 5's canonical classifier."""
    if not time_control:
        return "unknown"
    text = time_control.strip()
    base, _, inc = text.partition("+")
    try:
        seconds = int(float(base))
        increment = int(float(inc or 0)) if inc else None
    except ValueError:
        return "unknown"
    return classify_time_control(seconds, increment).value


def _ev(game: OpponentGameInput, move, label: str) -> OpponentEvidence:  # noqa: ANN001
    return OpponentEvidence(
        game_id=game.game_id,
        ply=move.ply,
        move_number=move.move_number,
        san=move.san,
        label=label,
        detail=game.opening_name,
    )


__all__ = ["phase_statistics", "tendencies"]
