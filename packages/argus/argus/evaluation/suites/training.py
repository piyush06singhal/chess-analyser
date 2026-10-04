"""Training benchmarks (§16–§20).

Three questions, all engine-free:

* **Is a solution graded fairly?** A move that is practically equivalent to the
  engine's first choice must not be marked wrong (§17).
* **Is difficulty measured, not faked?** ``assess_difficulty`` takes no engine
  depth and returns factors, so difficulty is not a rename of search depth (§18).
* **Does the scheduler only master on a real streak?** One correct answer must
  not master an exercise (§19/§20).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

from argus.evaluation.results import SuiteResult, check
from argus.training.acceptance import AcceptanceOutcome, evaluate_attempt
from argus.training.difficulty import assess_difficulty
from argus.training.models import Category, TrainingPosition, TrainingState
from argus.training.scheduler import MultiplierSchedule, apply_attempt

_SOLUTION = "a1a8"
#: A different legal move in the same position, so a *non-exact* attempt is graded
#: on its evaluation rather than short-circuited by a UCI match.
_ALT = "h1h8"


def _grade(submitted_cp: int | None, *, solution_cp: int = 50, uci: str = _ALT):
    return evaluate_attempt(
        solution_eval_cp=solution_cp,
        solution_eval_mate=None,
        submitted_eval_cp=submitted_cp,
        submitted_eval_mate=None,
        submitted_uci=uci,
        solution_uci=_SOLUTION,
    )


def training_suite(context) -> SuiteResult:
    """Acceptance policy, difficulty methodology and retention."""
    checks = []

    # --- acceptance (§17) ----------------------------------------------------
    exact = _grade(50, uci=_SOLUTION)
    equivalent = _grade(35)  # 15cp worse — practically the same move
    near = _grade(50 - 100)
    wrong = _grade(50 - 400)
    checks.append(check("exact solution is correct", exact.outcome is AcceptanceOutcome.CORRECT))
    checks.append(
        check(
            "a practically equivalent move is not marked wrong",
            equivalent.outcome is AcceptanceOutcome.CORRECT,
            detail=f"delta=15cp -> {equivalent.outcome.value}",
            critical=True,
        )
    )
    checks.append(
        check(
            "a recorded equivalent is correct",
            evaluate_attempt(
                solution_eval_cp=50,
                solution_eval_mate=None,
                submitted_eval_cp=50,
                submitted_eval_mate=None,
                submitted_uci="h1h8",
                solution_uci=_SOLUTION,
                acceptable_moves={"h1h8": "a1a8"},
            ).outcome
            is AcceptanceOutcome.CORRECT,
        )
    )
    checks.append(
        check(
            "a playable-but-worse move is near-best, not wrong",
            near.outcome is AcceptanceOutcome.NEAR_BEST,
            detail=f"delta=100cp -> {near.outcome.value}",
        )
    )
    checks.append(
        check(
            "a clearly inferior move is incorrect",
            wrong.outcome is AcceptanceOutcome.INCORRECT,
            detail=f"delta=400cp -> {wrong.outcome.value}",
        )
    )
    # Without a stored evaluation the attempt is refused, not guessed.
    unknown = _grade(None)
    checks.append(
        check(
            "an ungraded move is refused, not passed",
            unknown.outcome is AcceptanceOutcome.INCORRECT and "no stored evaluation" in unknown.reason,
            detail=unknown.reason,
            critical=True,
        )
    )

    # --- difficulty (§18) ----------------------------------------------------
    signature = inspect.signature(assess_difficulty)
    checks.append(
        check(
            "difficulty does not take engine depth",
            "depth" not in signature.parameters,
            detail=", ".join(signature.parameters),
            critical=True,
        )
    )
    easy = assess_difficulty(
        fen="6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1",
        solution_uci="a1a8",
        solution_eval_cp=10000,
        alternative_best_loss_cp=0,
        solution_pv_length=1,
    )
    hard = assess_difficulty(
        fen="r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 6 6",
        solution_uci="f3e5",
        solution_eval_cp=30,
        alternative_best_loss_cp=5,
        solution_pv_length=6,
    )
    checks.append(
        check(
            "difficulty returns measured factors",
            bool(easy.factors) and bool(hard.factors),
            detail=f"easy={easy.difficulty.value}@{easy.score:.1f}, hard={hard.difficulty.value}@{hard.score:.1f}",
        )
    )
    checks.append(
        check(
            "difficulty is bounded 0..100",
            0.0 <= easy.score <= 100.0 and 0.0 <= hard.score <= 100.0,
        )
    )

    # --- retention (§20) -----------------------------------------------------
    schedule = MultiplierSchedule()
    position = TrainingPosition(
        side_to_move="white",
        fen="6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1",
        category=Category.TACTICAL,
        solution_uci="a1a8",
        solution_san="Ra8#",
        state=TrainingState.NEW,
    )
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    mastered_streak = 0
    states: list[str] = []
    for index in range(schedule.mastered_streak):
        decision = apply_attempt(position, AcceptanceOutcome.CORRECT, now=now, schedule=schedule)
        states.append(decision.new_state.value)
        if decision.new_state is TrainingState.MASTERED:
            mastered_streak = index + 1
            break
    checks.append(
        check(
            "mastery requires the full streak",
            mastered_streak >= schedule.mastered_streak,
            detail=f"mastered after {mastered_streak or '>'+str(schedule.mastered_streak)} correct; states={states}",
            critical=True,
        )
    )
    # One incorrect answer returns a mastered exercise to review.
    before = position.state
    dropped = apply_attempt(position, AcceptanceOutcome.INCORRECT, now=now, schedule=schedule)
    checks.append(
        check(
            "an incorrect answer drops it back out of mastered",
            before is TrainingState.MASTERED and dropped.new_state is not TrainingState.MASTERED,
            detail=f"{before.value} -> {dropped.new_state.value}",
            critical=True,
        )
    )

    return SuiteResult(
        suite="training",
        title="Training validation, acceptance, difficulty and retention",
        checks=checks,
    )


__all__ = ["training_suite"]
