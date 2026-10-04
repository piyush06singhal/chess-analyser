"""Insight prioritisation: which verified finding deserves the user's attention.

Phase 11 §6 asks for prioritisation, and is explicit about the trap: "do not
create arbitrary scores without documenting the methodology".

So this module derives every factor from a number that already exists in Caissa —
occurrences, games, a measured centipawn magnitude, a coverage band, a last-seen
date, a training accuracy — and it *refuses* to score a factor it cannot measure:
an unavailable factor is dropped from the weighted mean and its weight is
redistributed, never assumed to be a neutral middle value.

Two properties are load-bearing:

* **Priority is workflow priority, not a judgement about the player.** The
  categories are named for what the product should surface first, and the
  docstrings say so. `CRITICAL` means "this is likely to keep costing you games
  and you have not worked on it", never "you are a bad player".
* **Evidence gates promotion.** A weak claim cannot become CRITICAL by scoring
  high: the claim level and coverage bands cap it (see ``_cap_by_evidence``).

The methodology version below is a constant; changing any weight, band or cap
means changing it, so a stored priority can be traced to the rules that made it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

#: Bumped whenever a weight, band or cap below changes.
PRIORITY_METHODOLOGY_VERSION = "11.0"

#: Reference sample size for the confidence factor: the Phase 5 policy's
#: "games needed before a repeated pattern becomes usable".
REFERENCE_GAMES_FOR_CONFIDENCE = 20

#: A centipawn magnitude this large saturates the severity factor.
SEVERITY_SATURATION_CP = 300

#: Occurrences that saturate the recurrence factor.
RECURRENCE_SATURATION = 5

#: Weights. Documented, fixed, and normalised over the factors that are actually
#: available, so a missing measurement weakens the score's basis instead of
#: silently adding a made-up value.
FACTOR_WEIGHTS: dict[str, float] = {
    "severity": 0.25,
    "frequency": 0.20,
    "recurrence": 0.15,
    "recency": 0.15,
    "training_need": 0.15,
    "confidence": 0.10,
}

#: Recency: days since the finding was last seen → factor. A fresh finding is
#: more actionable than one from three months ago, and the bands are stated
#: rather than tuned invisibly.
RECENCY_BANDS: tuple[tuple[float, float], ...] = (
    (7, 1.0),
    (30, 0.7),
    (90, 0.4),
    (365, 0.2),
)

#: Severity labels the upstream engines already assign, mapped to the factor.
SEVERITY_LABELS: dict[str, float] = {"high": 1.0, "medium": 0.6, "low": 0.3}

#: Score bands. `CRITICAL` is "act on this now", `LOW` is "recorded, not urgent".
CRITICAL_MIN = 78.0
HIGH_MIN = 60.0
NORMAL_MIN = 40.0


class InsightPriority(str, Enum):
    """Workflow priority — where a finding sits in the coach's queue."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class InsightCandidate(BaseModel):
    """One finding offered for prioritisation, with its own evidence.

    Every optional field is optional because the upstream layer may not have
    measured it; nothing here is defaulted to a value that implies measurement.
    """

    key: str
    title: str
    statement: str
    source: str = Field(description="player_profile | game_report | training | opponent")
    category: str | None = None
    claim_level: str | None = None
    coverage: str | None = None
    games: int = 0
    occurrences: int = 0
    total_events: int | None = Field(
        default=None, description="Denominator for the frequency factor, when one exists"
    )
    value: float | None = None
    unit: str | None = None
    severity: str | None = None
    last_seen: datetime | None = None
    training_attempts: int | None = None
    training_accuracy: float | None = Field(
        default=None, description="Share of correct attempts on this pattern, when tried"
    )
    evidence: list[dict] = Field(default_factory=list)
    dismissible: bool = True


class ScoredInsight(BaseModel):
    """A finding with its priority and the factors that produced it."""

    key: str
    title: str
    statement: str
    source: str
    category: str | None = None
    priority: InsightPriority = InsightPriority.LOW
    score: float = 0.0
    factors: dict[str, float] = Field(
        default_factory=dict, description="Available factors only, each 0..1"
    )
    missing_factors: list[str] = Field(
        default_factory=list, description="Factors with no measurement behind them"
    )
    context_relevance: float = 1.0
    capped_by: str | None = Field(
        default=None, description="The evidence rule that limited the priority, if any"
    )
    sample_size: int = 0
    claim_level: str | None = None
    coverage: str | None = None
    evidence: list[dict] = Field(default_factory=list)
    dismissible: bool = True
    methodology_version: str = PRIORITY_METHODOLOGY_VERSION


def _recency_factor(last_seen: datetime | None, now: datetime) -> float | None:
    if last_seen is None:
        return None
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - last_seen).total_seconds() / 86_400.0)
    for limit, value in RECENCY_BANDS:
        if days <= limit:
            return value
    return 0.1


def _severity_factor(candidate: InsightCandidate) -> float | None:
    """Severity from a measured magnitude, else an assigned label, else nothing."""
    if candidate.value is not None and (candidate.unit or "").lower() in ("cp", "centipawns"):
        return min(1.0, abs(candidate.value) / SEVERITY_SATURATION_CP)
    if candidate.severity:
        return SEVERITY_LABELS.get(candidate.severity.lower())
    return None


def _frequency_factor(candidate: InsightCandidate) -> float | None:
    """How much of the opportunity the finding occupies, when a denominator exists."""
    if candidate.total_events and candidate.total_events > 0:
        return min(1.0, candidate.occurrences / candidate.total_events)
    if candidate.games and candidate.games > 0 and candidate.occurrences:
        return min(1.0, candidate.occurrences / candidate.games)
    return None


def _recurrence_factor(candidate: InsightCandidate) -> float | None:
    if candidate.occurrences <= 0:
        return None
    return min(1.0, candidate.occurrences / RECURRENCE_SATURATION)


def _training_need_factor(candidate: InsightCandidate) -> float | None:
    """How much this still needs work — only known once it has been trained.

    Never trained → ``None`` (the factor is dropped), because "not yet trained"
    is not evidence that the player cannot do it.
    """
    if not candidate.training_attempts:
        return None
    if candidate.training_accuracy is None:
        return None
    return max(0.0, min(1.0, 1.0 - candidate.training_accuracy))


def _confidence_factor(candidate: InsightCandidate) -> float | None:
    if candidate.games <= 0:
        return None
    return min(1.0, candidate.games / REFERENCE_GAMES_FOR_CONFIDENCE)


def _context_relevance(candidate: InsightCandidate, context_categories: set[str]) -> float:
    """A mild, documented nudge towards what the user is working on right now."""
    if not context_categories or not candidate.category:
        return 1.0
    return 1.0 if candidate.category in context_categories else 0.9


def _cap_by_evidence(
    candidate: InsightCandidate, priority: InsightPriority
) -> tuple[InsightPriority, str | None]:
    """Evidence caps. Promotion requires more than a high score."""
    coverage = (candidate.coverage or "").lower()
    claim = (candidate.claim_level or "").lower()
    if coverage == "insufficient":
        return InsightPriority.LOW, "data coverage is insufficient for this finding"
    if coverage == "limited" and priority in (InsightPriority.CRITICAL, InsightPriority.HIGH):
        return InsightPriority.NORMAL, "coverage is limited, so this stays normal priority"
    if claim == "insufficient":
        return InsightPriority.LOW, "the claim level is insufficient"
    if claim == "observation" and priority is InsightPriority.CRITICAL:
        return InsightPriority.HIGH, "an observation cannot be critical"
    return priority, None


def _band(score: float) -> InsightPriority:
    if score >= CRITICAL_MIN:
        return InsightPriority.CRITICAL
    if score >= HIGH_MIN:
        return InsightPriority.HIGH
    if score >= NORMAL_MIN:
        return InsightPriority.NORMAL
    return InsightPriority.LOW


def score_insight(
    candidate: InsightCandidate,
    *,
    now: datetime | None = None,
    context_categories: set[str] | None = None,
) -> ScoredInsight:
    """Score one finding: weighted mean over measured factors, then evidence caps."""
    now = now or datetime.now(timezone.utc)
    measured = {
        "severity": _severity_factor(candidate),
        "frequency": _frequency_factor(candidate),
        "recurrence": _recurrence_factor(candidate),
        "recency": _recency_factor(candidate.last_seen, now),
        "training_need": _training_need_factor(candidate),
        "confidence": _confidence_factor(candidate),
    }
    available = {name: value for name, value in measured.items() if value is not None}
    missing = sorted(name for name, value in measured.items() if value is None)
    if not available:
        # Nothing measurable: the finding is recorded and nothing is claimed.
        return ScoredInsight(
            key=candidate.key,
            title=candidate.title,
            statement=candidate.statement,
            source=candidate.source,
            category=candidate.category,
            priority=InsightPriority.LOW,
            score=0.0,
            factors={},
            missing_factors=missing,
            capped_by="no measurable factor was available",
            sample_size=candidate.games,
            claim_level=candidate.claim_level,
            coverage=candidate.coverage,
            evidence=candidate.evidence,
            dismissible=candidate.dismissible,
        )

    weight_total = sum(FACTOR_WEIGHTS[name] for name in available)
    weighted = sum(FACTOR_WEIGHTS[name] * value for name, value in available.items())
    base = weighted / weight_total
    relevance = _context_relevance(candidate, context_categories or set())
    score = round(100.0 * base * relevance, 2)
    priority = _band(score)
    priority, capped_by = _cap_by_evidence(candidate, priority)
    return ScoredInsight(
        key=candidate.key,
        title=candidate.title,
        statement=candidate.statement,
        source=candidate.source,
        category=candidate.category,
        priority=priority,
        score=score,
        factors={name: round(value, 4) for name, value in available.items()},
        missing_factors=missing,
        context_relevance=relevance,
        capped_by=capped_by,
        sample_size=candidate.games,
        claim_level=candidate.claim_level,
        coverage=candidate.coverage,
        evidence=candidate.evidence,
        dismissible=candidate.dismissible,
    )


def prioritize(
    candidates: list[InsightCandidate],
    *,
    now: datetime | None = None,
    dismissed: set[str] | None = None,
    context_categories: set[str] | None = None,
    limit: int | None = None,
) -> list[ScoredInsight]:
    """Rank findings for the coach's queue.

    Dismissed keys are dropped, ordering is deterministic (score, then key), and
    the result is capped. Determinism matters: a feed that reshuffles itself
    every refresh would be nagging rather than coaching.
    """
    dismissed = dismissed or set()
    scored = [
        score_insight(candidate, now=now, context_categories=context_categories)
        for candidate in candidates
        if candidate.key not in dismissed
    ]
    scored.sort(key=lambda item: (-item.score, item.key))
    if limit is not None:
        scored = scored[:limit]
    return scored


def priority_counts(insights: list[ScoredInsight]) -> dict[str, int]:
    """How many findings sit in each band — the honest summary of a feed."""
    counts = {band.value: 0 for band in InsightPriority}
    for insight in insights:
        counts[insight.priority.value] += 1
    return counts


def explain_method() -> dict:
    """The methodology, published so a priority can be argued with."""
    return {
        "methodology_version": PRIORITY_METHODOLOGY_VERSION,
        "factor_weights": dict(FACTOR_WEIGHTS),
        "recency_bands_days_to_factor": [list(band) for band in RECENCY_BANDS],
        "severity_saturation_cp": SEVERITY_SATURATION_CP,
        "severity_labels": dict(SEVERITY_LABELS),
        "recurrence_saturation": RECURRENCE_SATURATION,
        "reference_games_for_confidence": REFERENCE_GAMES_FOR_CONFIDENCE,
        "bands": {"critical": CRITICAL_MIN, "high": HIGH_MIN, "normal": NORMAL_MIN},
        "rules": [
            "A factor is only used when it is measured; missing factors are dropped and "
            "their weight is redistributed, never filled with a pretend value.",
            "Priority describes workflow urgency, not the player's ability.",
            "Insufficient data coverage caps a finding at low priority.",
            "Limited coverage caps a finding at normal priority.",
            "An observation can never be critical; a tendency can.",
            "Identical inputs always produce the same ordering.",
        ],
    }


__all__ = [
    "CRITICAL_MIN",
    "FACTOR_WEIGHTS",
    "HIGH_MIN",
    "NORMAL_MIN",
    "PRIORITY_METHODOLOGY_VERSION",
    "InsightCandidate",
    "InsightPriority",
    "ScoredInsight",
    "explain_method",
    "prioritize",
    "priority_counts",
    "score_insight",
]
