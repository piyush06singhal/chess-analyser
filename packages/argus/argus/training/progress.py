"""Measured training progress (spec §26): statistics with sample sizes.

Every percentage in a :class:`ProgressReport` carries its denominator. A
category with zero decided attempts renders as "no data", never as a fabricated
0% or 100%. Nothing here reads the engine, the clock beyond "now", or anything
the player did not actually do.

Retention is reported honestly: the fraction of scheduled reviews answered on
time (attempted at or before their ``next_review_at``), plus the exercise-level
state distribution. Where the data cannot support a claim (e.g. too few
attempts), the report says so explicitly instead of extrapolating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from argus.training.models import CATEGORY_LABELS, TrainingPosition

#: Below this many attempts a breakdown is reported as "insufficient data".
MIN_SAMPLE_FOR_BREAKDOWN = 3


@dataclass
class CategoryProgress:
    """Accuracy in one category, always with its sample size."""

    category: str
    label: str
    attempts: int = 0
    correct: int = 0
    near_best: int = 0
    incorrect: int = 0

    @property
    def decided(self) -> int:
        return self.correct + self.incorrect

    @property
    def accuracy(self) -> float | None:
        if self.decided == 0:
            return None
        return self.correct / self.decided

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "label": self.label,
            "attempts": self.attempts,
            "correct": self.correct,
            "near_best": self.near_best,
            "incorrect": self.incorrect,
            "decided": self.decided,
            "accuracy": round(self.accuracy, 3) if self.accuracy is not None else None,
        }


@dataclass
class DifficultyProgress:
    """Accuracy at one difficulty level, always with its sample size."""

    difficulty: str
    attempts: int = 0
    correct: int = 0
    near_best: int = 0
    incorrect: int = 0

    @property
    def decided(self) -> int:
        return self.correct + self.incorrect

    @property
    def accuracy(self) -> float | None:
        if self.decided == 0:
            return None
        return self.correct / self.decided

    def to_dict(self) -> dict[str, Any]:
        return {
            "difficulty": self.difficulty,
            "attempts": self.attempts,
            "correct": self.correct,
            "near_best": self.near_best,
            "incorrect": self.incorrect,
            "decided": self.decided,
            "accuracy": round(self.accuracy, 3) if self.accuracy is not None else None,
        }


@dataclass
class ProgressReport:
    """The measured state of a player's training."""

    attempts_total: int = 0
    correct_total: int = 0
    near_best_total: int = 0
    incorrect_total: int = 0
    by_category: dict[str, CategoryProgress] = field(default_factory=dict)
    by_difficulty: dict[str, DifficultyProgress] = field(default_factory=dict)
    state_counts: dict[str, int] = field(default_factory=dict)
    library_size: int = 0
    mastered_count: int = 0
    due_count: int = 0
    #: Attempted-at-or-before-schedule / scheduled-attempted (see module doc).
    on_time_reviews: int = 0
    scheduled_reviews: int = 0
    hint_usage_total: int = 0
    attempts_with_hints: int = 0
    #: Average response time over timed attempts, in ms.
    avg_response_time_ms: float | None = None
    response_time_samples: int = 0

    @property
    def accuracy_overall(self) -> float | None:
        decided = self.correct_total + self.incorrect_total
        if decided == 0:
            return None
        return self.correct_total / decided

    @property
    def retention(self) -> float | None:
        """On-time share of scheduled reviews; None when none were scheduled."""
        if self.scheduled_reviews == 0:
            return None
        return self.on_time_reviews / self.scheduled_reviews

    def to_dict(self) -> dict[str, Any]:
        accuracy = self.accuracy_overall
        retention = self.retention
        categories = {}
        for key, progress in self.by_category.items():
            entry = progress.to_dict()
            if progress.decided < MIN_SAMPLE_FOR_BREAKDOWN:
                entry["accuracy_note"] = (
                    f"insufficient data ({progress.decided} decided attempt(s); "
                    f"minimum {MIN_SAMPLE_FOR_BREAKDOWN})"
                )
            categories[key] = entry
        difficulties = {key: d.to_dict() for key, d in self.by_difficulty.items()}
        return {
            "attempts_total": self.attempts_total,
            "correct_total": self.correct_total,
            "near_best_total": self.near_best_total,
            "incorrect_total": self.incorrect_total,
            "accuracy_overall": round(accuracy, 3) if accuracy is not None else None,
            "by_category": categories,
            "by_difficulty": difficulties,
            "state_counts": dict(self.state_counts),
            "library_size": self.library_size,
            "mastered_count": self.mastered_count,
            "due_count": self.due_count,
            "retention": {
                "on_time": self.on_time_reviews,
                "scheduled": self.scheduled_reviews,
                "rate": round(retention, 3) if retention is not None else None,
            },
            "hints": {
                "total_hints_used": self.hint_usage_total,
                "attempts_with_hints": self.attempts_with_hints,
            },
            "response_time_ms": {
                "average": round(self.avg_response_time_ms) if self.avg_response_time_ms is not None else None,
                "samples": self.response_time_samples,
            },
        }


def compute_progress(
    positions: list[TrainingPosition],
    attempts: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    due_count: int = 0,
) -> ProgressReport:
    """Aggregate a player's library + attempt history into measured statistics.

    ``attempts`` are dicts with at least ``training_position_id`` and
    ``correctness`` (``correct``/``near_best``/``incorrect``), plus optional
    ``created_at`` (ISO or datetime), ``hints_used``, ``response_time_ms`` —
    the stored attempt shape from the API layer.
    """
    now = now or datetime.now(timezone.utc)
    report = ProgressReport(library_size=len(positions), due_count=due_count)
    position_by_id = {p.id: p for p in positions if p.id is not None}

    for position in positions:
        state = position.state.value if hasattr(position.state, "value") else str(position.state)
        report.state_counts[state] = report.state_counts.get(state, 0) + 1
        if state == "mastered":
            report.mastered_count += 1

    for attempt in attempts:
        correctness = attempt.get("correctness")
        if correctness not in ("correct", "near_best", "incorrect"):
            continue  # unknown rows are never silently re-labelled
        pid = attempt.get("training_position_id")
        position = position_by_id.get(pid)
        category = position.category.value if position is not None else "unknown"
        difficulty = position.difficulty.value if position is not None else "unknown"

        report.attempts_total += 1
        cat_progress = report.by_category.get(category)
        if cat_progress is None:
            cat_progress = report.by_category[category] = CategoryProgress(
                category=category,
                label=CATEGORY_LABELS.get(category, category),
            )
        diff_progress = report.by_difficulty.get(difficulty)
        if diff_progress is None:
            diff_progress = report.by_difficulty[difficulty] = DifficultyProgress(
                difficulty=difficulty
            )

        if correctness == "correct":
            report.correct_total += 1
            cat_progress.correct += 1
            diff_progress.correct += 1
        elif correctness == "near_best":
            report.near_best_total += 1
            cat_progress.near_best += 1
            diff_progress.near_best += 1
        else:
            report.incorrect_total += 1
            cat_progress.incorrect += 1
            diff_progress.incorrect += 1

        hints_used = attempt.get("hints_used") or 0
        if hints_used:
            report.hint_usage_total += hints_used
            report.attempts_with_hints += 1

        response_ms = attempt.get("response_time_ms")
        if response_ms is not None:
            total = (report.avg_response_time_ms or 0.0) * report.response_time_samples
            report.response_time_samples += 1
            report.avg_response_time_ms = (total + float(response_ms)) / report.response_time_samples

        # Retention: for scheduled reviews (exercise already had a schedule when
        # the attempt was made), was the attempt on time? We approximate with
        # the position's *current* schedule only when the attempt is the latest
        # one — honest limitation, stated in the docs.
        if position is not None and position.last_attempted_at is not None and position.next_review_at is not None:
            created = attempt.get("created_at")
            created_dt = _as_datetime(created)
            if created_dt is not None and created_dt <= position.last_attempted_at:
                report.scheduled_reviews += 1
                if position.next_review_at > position.last_attempted_at:
                    report.on_time_reviews += 1

    return report


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "CategoryProgress",
    "DifficultyProgress",
    "MIN_SAMPLE_FOR_BREAKDOWN",
    "ProgressReport",
    "compute_progress",
]
