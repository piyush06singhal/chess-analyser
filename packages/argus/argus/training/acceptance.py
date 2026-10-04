"""Move acceptance: marking an attempt correct, near-best, or incorrect (spec §8).

The policy exists because chess is not binary. Stockfish's first line is the
*best* move, not the only good one: a move costing 10cp in a winning position is
a perfectly reasonable choice, and telling a player "wrong" for playing it is
both untrue and demotivating — the exact failure the spec calls out ("do not
mark a practically equivalent move wrong simply because it is not Stockfish's
first PV").

The bands (solver's perspective, centipawns):

======================  =========================================
``correct``             within ``equal_tolerance_cp`` of the solution,
                        or the move is recorded as acceptable, or the
                        position's alternatives were practically forced
``near_best``           worse than ``correct`` but inside ``near_best_cp``
``incorrect``           beyond ``near_best_cp``
======================  =========================================

Mate handling: when either the solution or the attempt is mate-in-N, raw
centipawns cannot be compared, so the policy compares mate distances and
treats "mate now" as the boundary of the correct band.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AcceptanceOutcome(str, Enum):
    CORRECT = "correct"
    NEAR_BEST = "near_best"
    INCORRECT = "incorrect"


@dataclass(frozen=True)
class MoveAcceptancePolicy:
    """The documented thresholds. All values are centipawns unless stated."""

    #: At most this much worse than the solution is still *correct*.
    equal_tolerance_cp: int = 30
    #: At most this much worse is *near-best* (playable, not best).
    near_best_cp: int = 150
    #: When the player is already being mated in N, any defence delaying mate
    #: past this many moves is still creditable — hopeless positions have no
    #: "best" worth grading.
    hopeless_mate_threshold: int = 2

    def to_dict(self) -> dict[str, int]:
        return {
            "equal_tolerance_cp": self.equal_tolerance_cp,
            "near_best_cp": self.near_best_cp,
            "hopeless_mate_threshold": self.hopeless_mate_threshold,
        }


def default_policy() -> MoveAcceptancePolicy:
    return MoveAcceptancePolicy()


@dataclass
class AcceptanceDecision:
    outcome: AcceptanceOutcome
    #: Evaluation of the submitted move (mover perspective), when known.
    submitted_eval_cp: int | None = None
    #: How much worse the submitted move is than the solution (>= 0).
    evaluation_delta_cp: int | None = None
    #: Human-readable justification, safe to render (spec §10's feedback).
    reason: str = ""


def evaluate_attempt(
    *,
    solution_eval_cp: int | None,
    solution_eval_mate: int | None,
    submitted_eval_cp: int | None,
    submitted_eval_mate: int | None,
    submitted_uci: str,
    solution_uci: str,
    acceptable_moves: dict[str, str] | None = None,
    policy: MoveAcceptancePolicy | None = None,
) -> AcceptanceDecision:
    """Grade one attempt against the stored solution.

    The comparison is always *mover perspective*: both evaluations must come
    from the side to move in the puzzle position. The generator stores them
    that way, and the API converts an attempt's evaluation before calling this.
    """
    policy = policy or default_policy()

    # The exact solution, or a recorded equivalent, is correct — no arithmetic.
    if submitted_uci == solution_uci or (acceptable_moves or {}).get(submitted_uci):
        delta = _delta_or_none(solution_eval_cp, submitted_eval_cp)
        return AcceptanceDecision(
            outcome=AcceptanceOutcome.CORRECT,
            submitted_eval_cp=submitted_eval_cp,
            evaluation_delta_cp=delta,
            reason="matches the engine's choice" if submitted_uci == solution_uci else "a recorded equivalent of the best move",
        )

    # Mate arithmetic first: centipawns cannot express "mated in 2".
    if solution_eval_mate is not None or submitted_eval_mate is not None:
        return _mate_decision(
            solution_eval_mate,
            submitted_eval_mate,
            submitted_uci,
            policy,
        )

    if submitted_eval_cp is None or solution_eval_cp is None:
        # Without an evaluation of the attempt there is nothing honest to say.
        return AcceptanceDecision(
            outcome=AcceptanceOutcome.INCORRECT,
            submitted_eval_cp=None,
            evaluation_delta_cp=None,
            reason="the submitted move has no stored evaluation, so it cannot be graded as playable",
        )

    delta = max(0, int(solution_eval_cp - submitted_eval_cp))
    if delta <= policy.equal_tolerance_cp:
        return AcceptanceDecision(
            AcceptanceOutcome.CORRECT,
            submitted_eval_cp=submitted_eval_cp,
            evaluation_delta_cp=delta,
            reason=f"within {delta}cp of the best move",
        )
    if delta <= policy.near_best_cp:
        return AcceptanceDecision(
            AcceptanceOutcome.NEAR_BEST,
            submitted_eval_cp=submitted_eval_cp,
            evaluation_delta_cp=delta,
            reason=f"playable, but {delta}cp worse than the best move",
        )
    return AcceptanceDecision(
        AcceptanceOutcome.INCORRECT,
        submitted_eval_cp=submitted_eval_cp,
        evaluation_delta_cp=delta,
        reason=f"{delta}cp worse than the best move",
    )


def _mate_decision(
    solution_mate: int | None,
    submitted_mate: int | None,
    submitted_uci: str,
    policy: MoveAcceptancePolicy,
) -> AcceptanceDecision:
    """Grade when the solution or the attempt is a mate score.

    Signs follow the mover perspective: a *positive* mate means the mover
    delivers it; a *negative* mate means the mover receives it.
    """
    # Delivering mate: exactly matching the fastest mate is correct.
    if solution_mate is not None and solution_mate > 0:
        if submitted_mate is not None and submitted_mate > 0:
            if submitted_mate <= solution_mate:
                return AcceptanceDecision(
                    AcceptanceOutcome.CORRECT,
                    submitted_eval_cp=None,
                    evaluation_delta_cp=0,
                    reason=f"mates in {submitted_mate}"
                    + ("" if submitted_mate == solution_mate else f" (engine's line: {solution_mate})"),
                )
            # Slower mate: still winning, but the engine's line was faster.
            return AcceptanceDecision(
                AcceptanceOutcome.NEAR_BEST,
                submitted_eval_cp=None,
                evaluation_delta_cp=None,
                reason=f"mates in {submitted_mate}; the engine's line mates in {solution_mate}",
            )
        return AcceptanceDecision(
            AcceptanceOutcome.INCORRECT,
            submitted_eval_cp=None,
            evaluation_delta_cp=None,
            reason=f"the position is a forced mate in {solution_mate}",
        )

    # Receiving mate: credit any defence that delays it meaningfully.
    if solution_mate is not None and solution_mate < 0:
        if submitted_mate is not None and submitted_mate < 0:
            delay = abs(submitted_mate) - abs(solution_mate)
            if abs(submitted_mate) > policy.hopeless_mate_threshold and delay > 0:
                return AcceptanceDecision(
                    AcceptanceOutcome.NEAR_BEST,
                    submitted_eval_cp=None,
                    evaluation_delta_cp=None,
                    reason=f"delays mate to {abs(submitted_mate)} moves (engine: {abs(solution_mate)})",
                )
            if submitted_mate == solution_mate:
                return AcceptanceDecision(
                    AcceptanceOutcome.CORRECT,
                    submitted_eval_cp=None,
                    evaluation_delta_cp=0,
                    reason="the only delaying move",
                )
        return AcceptanceDecision(
            AcceptanceOutcome.INCORRECT,
            submitted_eval_cp=None,
            evaluation_delta_cp=None,
            reason=f"the opponent mates in {abs(solution_mate)}",
        )

    # Mixed: solution is mate, attempt is not (or vice versa) — the attempt that
    # is not mate while the position is a forced mate is simply worse.
    return AcceptanceDecision(
        AcceptanceOutcome.INCORRECT,
        submitted_eval_cp=None,
        evaluation_delta_cp=None,
        reason="the engine's line is a forced mate and the submitted move is not",
    )


def _delta_or_none(solution_eval_cp: int | None, submitted_eval_cp: int | None) -> int | None:
    if solution_eval_cp is None or submitted_eval_cp is None:
        return None
    return max(0, int(solution_eval_cp - submitted_eval_cp))


__all__ = [
    "AcceptanceDecision",
    "AcceptanceOutcome",
    "MoveAcceptancePolicy",
    "default_policy",
    "evaluate_attempt",
]
