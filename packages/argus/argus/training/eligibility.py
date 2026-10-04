"""Training eligibility: not every mistake should become a puzzle (spec §4).

The eligibility service is the gate between stored analysis and the exercise
library. It is deliberately strict: a puzzle the player cannot learn from — or
worse, one whose "solution" is not actually better — costs trust, and trust is
the entire product. Every rejection carries its reason, so a generation report
can say exactly why a game yielded three exercises instead of fifteen.

The checks, in the order they are applied (cheapest first):

1. **Legality** — the position must parse as a legal chess position and the
   solution move must be legal in it. A corrupted row is a data bug, and
   exercises must never be built on corrupted data.
2. **Engine analysis available** — no stored evaluation, no puzzle. The
   solution *is* the engine's output; without it there is nothing to verify.
3. **Solution clarity** — the correct move must be sufficiently better than the
   alternatives. What "sufficiently" means is a policy (``min_advantage_cp``,
   ``min_gap_to_played``), not a vibe, and it is configurable and documented.
4. **Educational value** — the mistake must be big enough to be worth
   practising (``min_loss_cp``) but the position not so won that any move wins
   (``max_solution_advantage_cp`` — the trivially obvious case).
5. **Not trivially obvious** — positions with a tiny branching factor (forced
   or nearly-forced) are rejected: there is nothing to *find*.
6. **Duplicate** — the same normalized FEN for the same player is rejected;
   the caller decides whether to keep the earlier exercise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import chess

from argus.shared.logging import get_logger

logger = get_logger(__name__)


class RejectionReason(str, Enum):
    """Why a candidate was rejected. Stable values — they reach the UI."""

    NOT_LEGAL = "position_not_legal"
    SOLUTION_NOT_LEGAL = "solution_not_legal"
    NO_ENGINE_ANALYSIS = "no_engine_analysis"
    SOLUTION_NOT_CLEAR = "solution_not_clear"
    NOT_A_MISTAKE = "not_a_mistake"
    TRIVIALLY_OBVIOUS = "trivially_obvious"
    TOO_FORCED = "too_few_candidates"
    DUPLICATE = "duplicate_position"


@dataclass(frozen=True)
class EligibilityPolicy:
    """The documented thresholds of the gate (spec §8: configurable, documented).

    All values are in centipawns from the solver's perspective unless stated.
    """

    #: The solution must be at least this much better than the played move —
    #: below this, "the mistake" is within engine noise and the exercise would
    #: teach disputable lessons.
    min_gap_to_played_cp: int = 120
    #: The played move's loss must reach this to be worth practising at all.
    min_loss_cp: int = 100
    #: The best alternative (non-solution) must be at least this much worse than
    #: the solution, so "the correct move" is genuinely singular.
    min_advantage_cp: int = 60
    #: Above this advantage for the solver, almost anything wins: the position
    #: is trivially obvious and the exercise teaches nothing.
    max_solution_advantage_cp: int = 900
    #: A position with fewer legal moves than this is nearly forced — nothing to
    #: find. (A pure mate-in-1 exercise is handled by the generator, not here.)
    min_legal_moves: int = 3


@dataclass
class EligibilityReport:
    """The outcome of checking one candidate: accept, or reject with reasons."""

    accepted: bool
    reasons: list[str] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "accepted"


def default_eligibility_service() -> "TrainingEligibilityService":
    return TrainingEligibilityService()


class TrainingEligibilityService:
    """The gate between stored move analysis and the exercise library."""

    def __init__(self, policy: EligibilityPolicy | None = None) -> None:
        self.policy = policy or EligibilityPolicy()

    def check(
        self,
        *,
        fen: str,
        solution_uci: str,
        solution_eval_cp: int | None,
        played_loss_cp: int | None,
        alternative_best_loss_cp: int | None = None,
        solution_eval_mate: int | None = None,
        duplicate: bool = False,
    ) -> EligibilityReport:
        """Apply the policy to one candidate.

        ``alternative_best_loss_cp`` is the centipawn loss of the *second*-best
        move (i.e. how much worse the best alternative is than the solution);
        when the caller cannot compute it, pass ``None`` and the check is
        skipped rather than guessed.

        ``solution_eval_mate`` carries a forced-mate solution (positive = the
        solver delivers mate). A mate score *is* engine analysis, so a mate-only
        candidate is not rejected for missing evaluations — but the centipawn
        clarity checks are skipped, not guessed, for such rows.
        """
        policy = self.policy
        report = EligibilityReport(accepted=False, policy=self._policy_dict())

        # 1. Legality — of the position and of the proposed solution in it.
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            report.reasons.append(f"{RejectionReason.NOT_LEGAL.value}: {exc}")
            return report
        if board.is_game_over():
            # A finished position has no decision to make.
            report.reasons.append(f"{RejectionReason.NOT_LEGAL.value}: game is already over")
            return report
        try:
            move = chess.Move.from_uci(solution_uci)
        except ValueError as exc:
            report.reasons.append(f"{RejectionReason.SOLUTION_NOT_LEGAL.value}: {exc}")
            return report
        if move not in board.legal_moves:
            report.reasons.append(
                f"{RejectionReason.SOLUTION_NOT_LEGAL.value}: "
                f"{solution_uci} is not legal in this position"
            )
            return report
        legal_count = board.legal_moves.count()

        # 2. Engine analysis must exist (a mate score counts).
        if solution_eval_cp is None and solution_eval_mate is None and played_loss_cp is None:
            report.reasons.append(f"{RejectionReason.NO_ENGINE_ANALYSIS.value}: no stored evaluation")
            return report

        # 3. Solution clarity: the solution must clearly beat the played move.
        if played_loss_cp is not None and played_loss_cp < policy.min_gap_to_played_cp:
            report.reasons.append(
                f"{RejectionReason.NOT_A_MISTAKE.value}: the played move cost only "
                f"{played_loss_cp}cp (policy minimum {policy.min_gap_to_played_cp}cp)"
            )

        # 3b. And the alternatives must be clearly worse than the solution.
        if (
            alternative_best_loss_cp is not None
            and alternative_best_loss_cp < policy.min_advantage_cp
        ):
            report.reasons.append(
                f"{RejectionReason.SOLUTION_NOT_CLEAR.value}: the best alternative is only "
                f"{alternative_best_loss_cp}cp worse than the solution "
                f"(policy minimum {policy.min_advantage_cp}cp)"
            )

        # 4. Not trivially obvious: a totally winning position teaches nothing.
        #    (Mate solutions are graded by mate distance, not centipawns, so
        #    this check applies to centipawn solutions only.)
        if solution_eval_cp is not None and abs(solution_eval_cp) > policy.max_solution_advantage_cp:
            report.reasons.append(
                f"{RejectionReason.TRIVIALLY_OBVIOUS.value}: the evaluation "
                f"({solution_eval_cp}cp) is beyond the 'decided' threshold "
                f"({policy.max_solution_advantage_cp}cp)"
            )

        # 5. Something to find: nearly-forced positions are not puzzles.
        if legal_count < policy.min_legal_moves:
            report.reasons.append(
                f"{RejectionReason.TOO_FORCED.value}: only {legal_count} legal move(s) "
                f"(policy minimum {policy.min_legal_moves})"
            )

        # 6. Duplicates.
        if duplicate:
            report.reasons.append(f"{RejectionReason.DUPLICATE.value}: an exercise from this position already exists")

        report.accepted = not report.reasons
        return report

    def _policy_dict(self) -> dict[str, Any]:
        return {
            "min_gap_to_played_cp": self.policy.min_gap_to_played_cp,
            "min_loss_cp": self.policy.min_loss_cp,
            "min_advantage_cp": self.policy.min_advantage_cp,
            "max_solution_advantage_cp": self.policy.max_solution_advantage_cp,
            "min_legal_moves": self.policy.min_legal_moves,
        }


__all__ = [
    "EligibilityPolicy",
    "EligibilityReport",
    "RejectionReason",
    "TrainingEligibilityService",
    "default_eligibility_service",
]
