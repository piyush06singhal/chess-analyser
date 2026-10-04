"""Spaced repetition: a deterministic scheduler over an explicit state machine.

The lifecycle (spec §18) is the one defined in ``argus.training.models``:

    new → learning → review → mastered, with needs_review and failed as the
    attention states a correct answer can lift an exercise out of.

Every rule is a named constant in :class:`MultiplierSchedule`, and
:func:`apply_attempt` is a pure function of (position, outcome, schedule, now)
— same input, same result, always. There is no randomness and no clock
reading inside the decision itself.

The grading philosophy: only the engine's best move (or a recorded equivalent)
demonstrates mastery. A near-best move is *playable*, and the feedback says so,
but it does not advance the ladder and it resets the streak — the exercise stays
scheduled, the player has not demonstrated they can find the best move.

Promotion rules (all documented, all deterministic):

* ``new`` + correct → ``learning`` (streak 1, initial interval).
* ``learning`` + correct with streak ≥ ``learning_promotion_streak`` →
  ``review`` (interval enters the multiplication ladder).
* ``review`` + correct with streak ≥ ``mastered_streak`` AND interval ≥
  ``mastered_min_interval_days`` → ``mastered``. This is why an exercise can
  *never* be mastered after one success: reaching the state requires a
  consecutive streak across multiple scheduled reviews.
* ``mastered`` + correct → stays mastered, interval keeps growing to the cap.
* incorrect → the attention states: ``new`` becomes ``failed``; anything else
  becomes ``needs_review`` — with the relearn interval, so the exercise comes
  back quickly.
* Only transitions present in ``STATE_TRANSITIONS`` are ever taken.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argus.training.acceptance import AcceptanceOutcome
from argus.training.models import TrainingPosition, TrainingState, can_transition

#: The first scheduled interval, in days. Also the relearn interval after a
#: failure. Exported because the docs and the UI quote it.
INITIAL_INTERVAL_DAYS = 1.0


@dataclass(frozen=True)
class MultiplierSchedule:
    """The documented constants of the scheduler. Changing any of them is a
    methodology change (bump ``TRAINING_METHODOLOGY_VERSION``)."""

    #: Interval after the first correct answer (new → learning).
    initial_interval_days: float = INITIAL_INTERVAL_DAYS
    #: Interval multiplier on every successful review.
    review_multiplier: float = 2.0
    #: Interval after an incorrect answer (the attention states).
    relearn_interval_days: float = INITIAL_INTERVAL_DAYS
    #: Consecutive correct answers needed to leave ``learning``.
    learning_promotion_streak: int = 2
    #: Consecutive correct answers needed to reach ``mastered`` from ``review``.
    mastered_streak: int = 4
    #: The interval must also have grown to this before mastering.
    mastered_min_interval_days: float = 8.0
    #: Upper bound of the review ladder, in days.
    max_interval_days: float = 180.0


DEFAULT_SCHEDULE = MultiplierSchedule()


@dataclass
class SchedulerDecision:
    """What one attempt changed — the audit trail of the state machine."""

    previous_state: TrainingState
    new_state: TrainingState
    previous_streak: int
    new_streak: int
    previous_interval_days: float
    new_interval_days: float
    next_review_at: datetime | None
    reason: str


def apply_attempt(
    position: TrainingPosition,
    outcome: AcceptanceOutcome | str,
    *,
    now: datetime | None = None,
    schedule: MultiplierSchedule | None = None,
) -> SchedulerDecision:
    """Apply one graded attempt to a training position, in place.

    Mutates ``position`` (state, streak, interval, counters, next_review_at)
    and returns a :class:`SchedulerDecision` describing exactly what changed.
    Raises ``ValueError`` if the state machine would be violated — that would
    be a bug, not a runtime condition.
    """
    schedule = schedule or DEFAULT_SCHEDULE
    now = now or datetime.now(timezone.utc)
    outcome = AcceptanceOutcome(outcome)

    previous_state = TrainingState(position.state)
    previous_streak = int(position.streak or 0)
    previous_interval = float(position.review_interval_days or 0.0)

    # Counters: every attempt is stored forever (spec §24); near_best counts as
    # a decided attempt that is not a correct one.
    position.attempts += 1
    if outcome == AcceptanceOutcome.CORRECT:
        position.correct_attempts += 1

    if outcome == AcceptanceOutcome.CORRECT:
        new_state, new_streak, new_interval, reason = _on_correct(
            previous_state, previous_streak, previous_interval, schedule
        )
    elif outcome == AcceptanceOutcome.NEAR_BEST:
        # Playable, but not the engine's move: no ladder progress, streak reset.
        new_state = _near_best_state(previous_state)
        new_streak = 0
        new_interval = previous_interval if previous_interval > 0 else schedule.initial_interval_days
        reason = "near-best keeps the exercise scheduled but does not advance it"
    else:  # INCORRECT
        # NEW fails outright (never even started learning); FAILED stays FAILED
        # (re-attempting while failed without a correct answer changes nothing);
        # everything else drops to NEEDS_REVIEW.
        if previous_state == TrainingState.NEW:
            new_state = TrainingState.FAILED
        elif previous_state == TrainingState.FAILED:
            new_state = TrainingState.FAILED
        else:
            new_state = TrainingState.NEEDS_REVIEW
        new_streak = 0
        new_interval = schedule.relearn_interval_days
        reason = "incorrect answer returns the exercise on the relearn interval"

    _assert_transition(previous_state, new_state)

    position.state = new_state
    position.streak = new_streak
    position.review_interval_days = new_interval
    position.last_attempted_at = now
    position.next_review_at = now + timedelta(days=new_interval) if new_interval > 0 else None

    return SchedulerDecision(
        previous_state=previous_state,
        new_state=new_state,
        previous_streak=previous_streak,
        new_streak=new_streak,
        previous_interval_days=previous_interval,
        new_interval_days=new_interval,
        next_review_at=position.next_review_at,
        reason=reason,
    )


def _on_correct(
    state: TrainingState,
    streak: int,
    interval: float,
    schedule: MultiplierSchedule,
) -> tuple[TrainingState, int, float, str]:
    new_streak = streak + 1

    if state == TrainingState.NEW:
        return (
            TrainingState.LEARNING,
            new_streak,
            schedule.initial_interval_days,
            "first correct answer starts the learning ladder",
        )

    if state == TrainingState.LEARNING:
        if new_streak >= schedule.learning_promotion_streak:
            return (
                TrainingState.REVIEW,
                new_streak,
                schedule.initial_interval_days * schedule.review_multiplier,
                f"streak {new_streak} promotes the exercise to review",
            )
        return (
            TrainingState.LEARNING,
            new_streak,
            schedule.initial_interval_days,
            "still learning: correct answers build the streak",
        )

    if state in (TrainingState.REVIEW, TrainingState.MASTERED):
        new_interval = min(
            max(interval, schedule.initial_interval_days) * schedule.review_multiplier,
            schedule.max_interval_days,
        )
        if state == TrainingState.REVIEW and (
            new_streak >= schedule.mastered_streak
            and new_interval >= schedule.mastered_min_interval_days
        ):
            return (
                TrainingState.MASTERED,
                new_streak,
                new_interval,
                f"streak {new_streak} at a {new_interval:g}-day interval: mastered",
            )
        label = "review" if state == TrainingState.REVIEW else "mastered"
        return (
            state,
            new_streak,
            new_interval,
            f"correct at {label}: interval grows to {new_interval:g} days",
        )

    if state == TrainingState.FAILED:
        # A correct answer restarts the ladder from learning (never straight to
        # review — the previous failure has not been un-learned yet).
        return (
            TrainingState.LEARNING,
            new_streak,
            schedule.initial_interval_days,
            "correct answer restarts the ladder from learning",
        )

    # NEEDS_REVIEW: a correct answer lifts the exercise back into review, at
    # half its previous interval (never below the initial one).
    recovered_interval = max(
        schedule.initial_interval_days,
        (interval if interval > 0 else schedule.initial_interval_days) / 2.0,
    )
    return (
        TrainingState.REVIEW,
        new_streak,
        recovered_interval,
        "correct answer recovers the exercise from needs_review",
    )


def _near_best_state(state: TrainingState) -> TrainingState:
    """Where a near-best answer leaves each state (all allowed transitions)."""
    if state == TrainingState.NEW:
        return TrainingState.LEARNING
    if state == TrainingState.NEEDS_REVIEW:
        return TrainingState.NEEDS_REVIEW
    return state  # learning/review/mastered stay put


def _assert_transition(previous: TrainingState, new: TrainingState) -> None:
    if not can_transition(previous, new):
        raise ValueError(
            f"state machine violation: {previous.value} → {new.value} is not allowed"
        )


def due_positions(
    positions: list[TrainingPosition],
    *,
    now: datetime,
    include_new: bool = False,
    limit: int | None = None,
) -> list[TrainingPosition]:
    """The review queue: scheduled exercises whose time has come.

    Deterministic order: most overdue first, ties broken by id for stability.
    ``new`` exercises carry no schedule (``next_review_at is None``) and are
    included only when ``include_new`` is set, after every scheduled item.
    """
    scheduled: list[TrainingPosition] = []
    fresh: list[TrainingPosition] = []
    for position in positions:
        if position.next_review_at is not None and position.next_review_at <= now:
            scheduled.append(position)
        elif include_new and position.next_review_at is None and position.state == TrainingState.NEW:
            fresh.append(position)

    scheduled.sort(key=lambda p: (p.next_review_at, p.id if p.id is not None else 0))
    fresh.sort(key=lambda p: (p.id if p.id is not None else 0))

    queue = scheduled + fresh
    if limit is not None and limit >= 0:
        queue = queue[:limit]
    return queue


__all__ = [
    "DEFAULT_SCHEDULE",
    "INITIAL_INTERVAL_DAYS",
    "MultiplierSchedule",
    "SchedulerDecision",
    "TrainingState",
    "apply_attempt",
    "due_positions",
]
