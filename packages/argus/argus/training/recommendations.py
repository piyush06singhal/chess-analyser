"""Evidence-backed training recommendations (spec §25).

The engine reads *measurements* — category accuracy with sample sizes, recency,
frequency of specific failure patterns — and outputs prioritized opportunities.
It never produces a single pseudo-precise "weakness score": priorities come from
named, individually-inspectable factors, and every recommendation carries its
evidence as strings a human can check against the data.

Inputs (all supplied by the caller from the player's own data):

``category_stats``
    per category: attempts, correct, near_best, incorrect counts and the last
    attempt timestamp — computed over stored attempts only.
``pattern_counts``
    counts of recurring failure tags (e.g. ``hanging_piece``) from stored
    exercise tags, used for the "recurring pattern" evidence lines.
``library``
    the player's exercise library (for diversity and untried checks).

Priority factors (weights sum to 1.0):

* ``accuracy`` — lower measured accuracy (with ≥ ``MIN_SAMPLE_FOR_ACCURACY``
  decided attempts) ranks higher; categories without enough data score neutral.
* ``sample`` — the measurement strengthens with sample size, saturating at
  ``FULL_STRENGTH_SAMPLE`` attempts.
* ``frequency`` — how often the category's exercises were attempted: a
  frequently-failed area deserves attention.
* ``recency`` — recent failures weigh more than old ones (half-life
  ``RECENCY_HALF_LIFE_DAYS``).
* ``untried`` — a category with exercises the player never attempted gets a
  boost so new evidence can accumulate.

A category at or above ``NOT_RECOMMENDED_ACCURACY`` measured accuracy is not
recommended — there is nothing to fix, and saying otherwise would be an
unsupported claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from argus.training.models import CATEGORY_LABELS

#: The named priority factors (weights sum to 1.0; documented constants).
PRIORITY_WEIGHTS = {
    "accuracy": 0.35,
    "sample": 0.20,
    "frequency": 0.15,
    "recency": 0.15,
    "untried": 0.15,
}

#: Below this many decided attempts a category has no measured accuracy.
MIN_SAMPLE_FOR_ACCURACY = 3

#: At or above this measured accuracy a category is not recommended.
NOT_RECOMMENDED_ACCURACY = 0.80

#: Attempts at which the sample-strength factor saturates.
FULL_STRENGTH_SAMPLE = 12

#: Evidence decays with this half-life, in days.
RECENCY_HALF_LIFE_DAYS = 45.0


@dataclass
class CategoryStats:
    """Measured performance in one category — computed from stored attempts."""

    category: str
    attempts: int = 0
    correct: int = 0
    near_best: int = 0
    incorrect: int = 0
    last_attempt_at: datetime | None = None

    @property
    def decided(self) -> int:
        return self.correct + self.incorrect

    @property
    def accuracy(self) -> float | None:
        """Correct / decided (near_best is not a success and not a failure)."""
        if self.decided == 0:
            return None
        return self.correct / self.decided

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "attempts": self.attempts,
            "correct": self.correct,
            "near_best": self.near_best,
            "incorrect": self.incorrect,
            "accuracy": round(self.accuracy, 3) if self.accuracy is not None else None,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
        }


@dataclass
class TrainingOpportunity:
    """One prioritized training opportunity, with its evidence."""

    category: str
    label: str
    priority: float
    #: Named, inspectable factor scores (0..1) — never shown as one opaque number.
    factors: dict[str, float] = field(default_factory=dict)
    #: Human-checkable evidence lines, each pointing at real data.
    evidence: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "label": self.label,
            "priority": round(self.priority, 3),
            "factors": {k: round(v, 3) for k, v in self.factors.items()},
            "evidence": list(self.evidence),
            "reason": self.reason,
        }


class RecommendationEngine:
    """Prioritizes training categories from measured, stored performance."""

    def __init__(
        self,
        *,
        min_sample_for_accuracy: int = MIN_SAMPLE_FOR_ACCURACY,
        not_recommended_accuracy: float = NOT_RECOMMENDED_ACCURACY,
    ) -> None:
        self.min_sample_for_accuracy = min_sample_for_accuracy
        self.not_recommended_accuracy = not_recommended_accuracy

    def recommend(
        self,
        category_stats: list[CategoryStats],
        *,
        library_categories: list[str] | None = None,
        attempted_categories: list[str] | None = None,
        pattern_counts: dict[str, int] | None = None,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[TrainingOpportunity]:
        """Rank categories needing work; evidence attached to every entry.

        ``library_categories`` — categories present in the exercise library.
        ``attempted_categories`` — categories the player has ever attempted.
        ``pattern_counts`` — recurring failure tag counts, for evidence lines.
        """
        now = now or datetime.now(timezone.utc)
        library = set(library_categories or [])
        attempted = set(attempted_categories or [])
        patterns = pattern_counts or {}

        opportunities: list[TrainingOpportunity] = []
        for stats in category_stats:
            category = stats.category
            if library and category not in library:
                continue  # cannot recommend what the library cannot deliver
            if stats.decided < self.min_sample_for_accuracy:
                # Not enough data to claim anything — skip, never guess.
                continue
            accuracy = stats.accuracy
            if accuracy is not None and accuracy >= self.not_recommended_accuracy:
                continue  # measured as fine: no recommendation

            accuracy_factor = 1.0 - accuracy if accuracy is not None else 0.5
            sample_factor = min(1.0, stats.decided / FULL_STRENGTH_SAMPLE)
            frequency_factor = min(1.0, stats.attempts / FULL_STRENGTH_SAMPLE)
            recency_factor = self._recency_factor(stats.last_attempt_at, now)
            untried = category not in attempted
            untried_factor = 1.0 if untried else 0.0

            priority = (
                PRIORITY_WEIGHTS["accuracy"] * accuracy_factor
                + PRIORITY_WEIGHTS["sample"] * sample_factor
                + PRIORITY_WEIGHTS["frequency"] * frequency_factor
                + PRIORITY_WEIGHTS["recency"] * recency_factor
                + PRIORITY_WEIGHTS["untried"] * untried_factor
            )

            evidence: list[str] = []
            decided = stats.decided
            pct = round(accuracy * 100)
            evidence.append(
                f"{stats.correct}/{decided} decided attempts correct ({pct}%) in "
                f"{CATEGORY_LABELS.get(category, category)}"
            )
            if stats.incorrect:
                evidence.append(f"{stats.incorrect} incorrect attempt(s) recorded")
            if stats.near_best:
                evidence.append(f"{stats.near_best} near-best attempt(s) recorded")
            if stats.last_attempt_at:
                days = (now - stats.last_attempt_at).total_seconds() / 86400
                evidence.append(f"last attempt {max(0, int(days))} day(s) ago")

            # Recurring-pattern evidence: only for tags that actually occur.
            tag_lines = [
                (tag, count)
                for tag, count in sorted(patterns.items(), key=lambda kv: -kv[1])
                if count >= 2
            ]
            for tag, count in tag_lines[:3]:
                evidence.append(
                    f"recurring pattern '{tag}' appears in {count} exercise(s)"
                )

            reason = self._reason(accuracy, stats, untried)

            opportunities.append(
                TrainingOpportunity(
                    category=category,
                    label=CATEGORY_LABELS.get(category, category),
                    priority=round(priority, 4),
                    factors={
                        "accuracy": round(accuracy_factor, 3),
                        "sample": round(sample_factor, 3),
                        "frequency": round(frequency_factor, 3),
                        "recency": round(recency_factor, 3),
                        "untried": round(untried_factor, 3),
                    },
                    evidence=evidence,
                    reason=reason,
                )
            )

        opportunities.sort(key=lambda o: -o.priority)
        if limit is not None and limit >= 0:
            opportunities = opportunities[:limit]
        return opportunities

    def _recency_factor(self, last_attempt_at: datetime | None, now: datetime) -> float:
        """0..1, 1 = failed very recently. No timestamp → neutral 0.5."""
        if last_attempt_at is None:
            return 0.5
        days = max(0.0, (now - last_attempt_at).total_seconds() / 86400)
        return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)

    def _reason(
        self,
        accuracy: float | None,
        stats: CategoryStats,
        untried: bool,
    ) -> str:
        if untried:
            return "never attempted: new evidence worth collecting"
        pct = round((accuracy or 0.0) * 100)
        return (
            f"measured accuracy {pct}% across {stats.decided} decided attempt(s) "
            f"is below the {round(self.not_recommended_accuracy * 100)}% threshold"
        )


def recommend(
    category_stats: list[CategoryStats],
    **kwargs: Any,
) -> list[TrainingOpportunity]:
    """Module-level convenience wrapper around :class:`RecommendationEngine`."""
    return RecommendationEngine().recommend(category_stats, **kwargs)


__all__ = [
    "CategoryStats",
    "FULL_STRENGTH_SAMPLE",
    "MIN_SAMPLE_FOR_ACCURACY",
    "NOT_RECOMMENDED_ACCURACY",
    "PRIORITY_WEIGHTS",
    "RECENCY_HALF_LIFE_DAYS",
    "RecommendationEngine",
    "TrainingOpportunity",
    "recommend",
]
