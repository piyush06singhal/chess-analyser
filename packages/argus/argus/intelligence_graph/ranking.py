"""Evidence ranking (§45): a documented ordering, not an opaque score.

When many related nodes exist, the agent cannot be given all of them (and should
not be). Something must decide which evidence reaches the answer. That "something"
must be inspectable, or the ranking itself becomes an unfalsifiable claim.

So ranking here is an explicit weighted sum over seven named axes, and every
ranked item carries its own per-axis breakdown. The weights are published in
:data:`WEIGHTS` and :func:`methodology` returns them with their justification, so
a reviewer can disagree with the ranking of a specific item by reading the
numbers that produced it.

The axes (§45):

``relevance``
    How directly the node answers the question, supplied by the caller who knows
    what was asked. Caissa does not guess this.
``recency``
    Newer evidence ranks higher, decaying linearly over a one-year horizon.
``source_reliability``
    Engine facts outrank Caissa interpretations, which outrank predictions. This
    is the same ordering the platform uses everywhere.
``sample_size``
    Larger samples rank higher, saturating so a 5000-game sample does not drown a
    precise 20-game one.
``exactness``
    A position that is an EXACT match outranks a structurally similar one. This
    is the same non-interchangeability the similarity module enforces, expressed
    as a rank.
``contextual_similarity``
    Overlap with the current context (same opening, same phase, same opponent),
    supplied by the caller.
``user_ownership``
    The user's own games rank above an opponent's, because evidence about the
    user is what the user is usually asking about.

Nothing here invents a value: every axis not supplied defaults to a neutral 0.5
or to the honest floor, and the item is labelled as such.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from argus.intelligence_graph.similarity import SimilarityLevel

#: Published weights. They sum to 1.0; a reader can recompute every score.
WEIGHTS: dict[str, float] = {
    "relevance": 0.30,
    "recency": 0.10,
    "source_reliability": 0.15,
    "sample_size": 0.15,
    "exactness": 0.15,
    "contextual_similarity": 0.10,
    "user_ownership": 0.05,
}

_RECENCY_HORIZON_DAYS = 365.0
_SAMPLE_SATURATION = 30.0


class SourceReliability(str, Enum):
    """How much a source is trusted — the platform's standing ordering."""

    ENGINE = "engine"
    ARGUS_MEASURED = "argus_measured"
    INTERPRETATION = "interpretation"
    PREDICTION = "prediction"

    @property
    def value01(self) -> float:
        return _RELIABILITY[self]


_RELIABILITY: dict[SourceReliability, float] = {
    SourceReliability.ENGINE: 1.0,
    SourceReliability.ARGUS_MEASURED: 0.8,
    SourceReliability.INTERPRETATION: 0.6,
    SourceReliability.PREDICTION: 0.4,
}

_EXACTNESS: dict[SimilarityLevel, float] = {
    SimilarityLevel.EXACT: 1.0,
    SimilarityLevel.EQUIVALENT: 0.8,
    SimilarityLevel.STRUCTURALLY_SIMILAR: 0.6,
    SimilarityLevel.OPENING_SIMILAR: 0.4,
    SimilarityLevel.TACTICALLY_SIMILAR: 0.3,
}


class EvidenceCandidate(BaseModel):
    """One item to be ranked, with the facts each axis needs."""

    node: str
    label: str = ""
    #: Caller-supplied relevance in [0, 1]. Undefined means neutral (0.5).
    relevance: float | None = None
    occurred_at: datetime | None = None
    reliability: SourceReliability = SourceReliability.ARGUS_MEASURED
    sample_size: int | None = None
    exactness: SimilarityLevel | None = None
    contextual_similarity: float | None = None
    owned_by_user: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class RankedEvidence(BaseModel):
    """A candidate with its score and the per-axis breakdown that produced it."""

    node: str
    label: str
    score: float
    breakdown: dict[str, float]
    payload: dict[str, Any] = Field(default_factory=dict)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _recency_score(occurred_at: datetime | None, now: datetime) -> float:
    if occurred_at is None:
        return 0.5  # neutral: undated evidence is neither favoured nor buried
    when = occurred_at if occurred_at.tzinfo else occurred_at.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - when).total_seconds() / 86400.0)
    return _clamp(1.0 - days / _RECENCY_HORIZON_DAYS)


def _sample_score(sample_size: int | None) -> float:
    if sample_size is None or sample_size <= 0:
        return 0.0  # no sample is not a small sample; it is no evidence
    # Square-root saturation: 30 observations reach 1.0, and a 5000-game sample
    # does not drown a precise 20-game one.
    return _clamp((sample_size / _SAMPLE_SATURATION) ** 0.5)


def score_candidate(candidate: EvidenceCandidate, *, now: datetime | None = None) -> RankedEvidence:
    """Score one candidate along every documented axis."""
    moment = now or datetime.now(timezone.utc)
    breakdown = {
        "relevance": _clamp(candidate.relevance if candidate.relevance is not None else 0.5),
        "recency": _recency_score(candidate.occurred_at, moment),
        "source_reliability": candidate.reliability.value01,
        "sample_size": _sample_score(candidate.sample_size),
        # Undated/unknown exactness is neutral (0.5); a known level scores its map.
        "exactness": (
            _EXACTNESS[candidate.exactness] if candidate.exactness is not None else 0.5
        ),
        "contextual_similarity": _clamp(
            candidate.contextual_similarity
            if candidate.contextual_similarity is not None
            else 0.0
        ),
        "user_ownership": 1.0 if candidate.owned_by_user else 0.0,
    }
    score = sum(WEIGHTS[axis] * value for axis, value in breakdown.items())
    return RankedEvidence(
        node=candidate.node,
        label=candidate.label,
        score=round(score, 4),
        breakdown={axis: round(value, 4) for axis, value in breakdown.items()},
        payload=dict(candidate.payload),
    )


def rank_evidence(
    candidates: list[EvidenceCandidate],
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[RankedEvidence]:
    """Rank candidates strongest first; ties broken by node key for determinism."""
    moment = now or datetime.now(timezone.utc)
    ranked = [score_candidate(candidate, now=moment) for candidate in candidates]
    ranked.sort(key=lambda item: (-item.score, item.node))
    if limit is not None:
        return ranked[: max(0, int(limit))]
    return ranked


def methodology() -> dict[str, Any]:
    """Publish the ranking rules so a result can be argued with."""
    return {
        "weights": dict(WEIGHTS),
        "axes": {
            "relevance": "How directly the item answers the asked question (caller-supplied).",
            "recency": f"Linear decay over {int(_RECENCY_HORIZON_DAYS)} days; undated = 0.5.",
            "source_reliability": {
                "engine": 1.0,
                "argus_measured": 0.8,
                "interpretation": 0.6,
                "prediction": 0.4,
            },
            "sample_size": f"Sqrt saturation at {int(_SAMPLE_SATURATION)}; no sample = 0.",
            "exactness": {level.value: value for level, value in _EXACTNESS.items()},
            "contextual_similarity": "Overlap with the current context (caller-supplied).",
            "user_ownership": "The user's own objects rank above another's.",
        },
        "rule": (
            "The score is a weighted sum with published weights; every ranked item "
            "carries its per-axis breakdown. No axis is inferred from nothing."
        ),
    }


__all__ = [
    "WEIGHTS",
    "EvidenceCandidate",
    "RankedEvidence",
    "SourceReliability",
    "methodology",
    "rank_evidence",
    "score_candidate",
]
