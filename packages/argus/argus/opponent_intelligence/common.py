"""Small shared helpers for opponent intelligence.

Nothing here runs an engine or touches a database: these are pure functions
over already-stored FEN strings and already-computed counts.
"""

from __future__ import annotations

from argus.opponent_intelligence.policy import ClaimLevel, OpponentInsightPolicy


def normalize_fen(fen: str) -> str:
    """Board + side to move + castling + en passant — the position identity.

    Halfmove and fullmove counters are dropped so transpositions that arrive by
    different routes compare equal for *retrieval* (the stored FEN keeps its
    move number for display).
    """
    return " ".join((fen or "").split()[:4])


def piece_placement(fen: str) -> str:
    """Just the piece placement field — the loosest useful position match."""
    parts = (fen or "").split()
    return parts[0] if parts else ""


def share(count: int, total: int) -> float:
    """A share in [0, 1]; zero when there is nothing to divide by."""
    if total <= 0:
        return 0.0
    return round(count / total, 4)


def claim_level(
    *,
    occurrences: int,
    share_value: float | None,
    games: int,
    policy: OpponentInsightPolicy,
    gate: str = "min_occurrences_for_tendency",
) -> ClaimLevel:
    """Promote an observation to a pattern/tendency only when its gates are met.

    ``gate`` selects which named sample-size gate applies. The function never
    rounds up: below the gate it returns ``INSUFFICIENT`` rather than a softer
    truth.
    """
    minimum = int(getattr(policy, gate))
    if occurrences < minimum:
        return ClaimLevel.INSUFFICIENT
    if share_value is None:
        return ClaimLevel.OBSERVATION
    if share_value >= policy.tendency_share_for_tendency and occurrences >= minimum:
        return ClaimLevel.TENDENCY
    if share_value >= policy.repertoire_share_for_pattern:
        return ClaimLevel.PATTERN
    return ClaimLevel.OBSERVATION


__all__ = ["claim_level", "normalize_fen", "piece_placement", "share"]
