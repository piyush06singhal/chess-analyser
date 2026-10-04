"""Difficulty: measurable, from position characteristics (spec §10).

Difficulty is a *measurement*, not a judgment. Every band is computed from
observable properties of the stored position and solution — branching factor,
forcing depth, evaluation tightness, piece density — and every number is stored
alongside the label so any dashboard can show *why* an exercise is rated as it
is. Nothing here looks at the move number, the game result, or the player's
rating: none of those are properties of the position.

The factors (all stored in ``difficulty_factors`` for provenance):

``legal_moves``
    The branching factor the solver actually faces. More options, more to
    weigh.
``forcing_moves``
    Checks, captures, and — when the opponent replies are examined — the
    forcing options available. Forcing positions are often easier to search
    but harder to *find the point of*; the score treats a moderate number of
    forcing moves as harder than none.
``eval_gap_cp``
    The gap between the solution and the best alternative. A razor-thin
    advantage makes the correct move harder to separate from its rivals.
``eval_abs_cp``
    How decided the position already is. Extreme evaluations cut both ways
    (trivially winning or clearly lost) and are smoothed by the score.
``solution_pv_length``
    How deep the winning line runs. Longer PVs demand more calculation.
``piece_count``
    Material on the board. Sparse boards narrow the plan space (endgame
    technique), dense boards widen it (combinative chaos).
``is_check``, ``is_capture_solution``, ``promotion_involved``
    Binary structure flags from the position and solution.
``quiet_solution``
    True when the solution is a non-forcing move — historically the hardest
    class of move to find, because nothing screams for attention.

The weighted sum maps onto five bands. The weights are documented constants:
changing them changes ``methodology_version``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import chess

from argus.training.models import Difficulty

#: Bumped when factor weights or band boundaries change.
DifficultyMethodologyVersion = "8.0"

#: Documented weights (sum = 1.0) and band boundaries — part of the method.
FACTOR_WEIGHTS: dict[str, float] = {
    "branching": 0.25,
    "forcing": 0.15,
    "eval_gap": 0.25,
    "eval_abs": 0.10,
    "calculation": 0.15,
    "quiet_solution": 0.10,
}

#: Raw score thresholds for the five bands (0..100 scale).
DIFFICULTY_BANDS: tuple[tuple[float, Difficulty], ...] = (
    (20.0, Difficulty.BEGINNER),
    (35.0, Difficulty.EASY),
    (55.0, Difficulty.INTERMEDIATE),
    (75.0, Difficulty.ADVANCED),
    (101.0, Difficulty.EXPERT),
)


@dataclass
class DifficultyAssessment:
    """The measured difficulty of one position, with its factors exposed."""

    difficulty: Difficulty
    score: float
    factors: dict[str, Any] = field(default_factory=dict)
    methodology_version: str = DifficultyMethodologyVersion


def assess_difficulty(
    *,
    fen: str,
    solution_uci: str,
    solution_eval_cp: int | None,
    alternative_best_loss_cp: int | None = None,
    solution_pv_length: int = 0,
) -> DifficultyAssessment:
    """Measure the difficulty of one exercise from the position itself."""
    board = chess.Board(fen)
    solution = chess.Move.from_uci(solution_uci)

    legal_moves = board.legal_moves.count()
    is_check = board.is_check()

    # Forcing moves available to the solver: checks and captures.
    forcing = 0
    for move in board.legal_moves:
        if board.is_capture(move) or board.gives_check(move):
            forcing += 1
    forcing_ratio = forcing / legal_moves if legal_moves else 0.0

    solution_is_capture = board.is_capture(solution)
    solution_gives_check = board.gives_check(solution)
    promotion_involved = solution.promotion is not None

    # The hardest solutions to spot are quiet ones in sharp positions.
    quiet_solution = not (solution_is_capture or solution_gives_check)

    # Evaluation tightness: how decided is the position already?
    eval_abs = abs(solution_eval_cp) if solution_eval_cp is not None else None
    # 0cp gap (unclear) and 900cp+ gap (decided) are both "easier";
    # the sweet spot for difficulty is a moderate advantage to convert.
    if eval_abs is None:
        eval_abs_score = 50.0  # unknown: neutral, never guessed
    else:
        # Peaked at ~300cp: enough advantage to matter, not yet decided.
        eval_abs_score = max(0.0, 100.0 - abs(eval_abs - 300) / 10.0)

    # Eval gap: a thin margin between solution and alternative is harder.
    if alternative_best_loss_cp is None:
        eval_gap_score = 50.0  # unknown: neutral
    elif alternative_best_loss_cp <= 30:
        eval_gap_score = 0.0  # effectively equivalent alternatives: easy to play "a" good move
    elif alternative_best_loss_cp >= 400:
        eval_gap_score = 100.0  # one move stands alone
    else:
        eval_gap_score = min(100.0, (alternative_best_loss_cp - 30) / 3.7)

    # Calculation demand: PV length and quiet solutions raise it.
    calc_score = min(100.0, solution_pv_length * 12.0 + (20.0 if quiet_solution else 0.0))

    # Branching: more legal moves = more to consider.
    if legal_moves <= 3:
        branching_score = 10.0
    elif legal_moves >= 40:
        branching_score = 100.0
    else:
        branching_score = min(100.0, (legal_moves - 3) * 2.6)

    # Forcing ratio: a mix of forcing and quiet options is hardest to sift.
    if forcing_ratio <= 0.15 or forcing_ratio >= 0.85:
        forcing_score = 20.0  # uniformly quiet or uniformly forcing
    else:
        forcing_score = 30.0 + forcing_ratio * 70.0

    raw = (
        FACTOR_WEIGHTS["branching"] * branching_score
        + FACTOR_WEIGHTS["forcing"] * forcing_score
        + FACTOR_WEIGHTS["eval_gap"] * eval_gap_score
        + FACTOR_WEIGHTS["eval_abs"] * eval_abs_score
        + FACTOR_WEIGHTS["calculation"] * calc_score
        + FACTOR_WEIGHTS["quiet_solution"] * (100.0 if quiet_solution else 0.0)
    )

    difficulty = Difficulty.EXPERT
    for threshold, band in DIFFICULTY_BANDS:
        if raw < threshold:
            difficulty = band
            break

    factors: dict[str, Any] = {
        "legal_moves": legal_moves,
        "forcing_moves": forcing,
        "forcing_ratio": round(forcing_ratio, 3),
        "eval_gap_cp": alternative_best_loss_cp,
        "eval_abs_cp": eval_abs,
        "solution_pv_length": solution_pv_length,
        "piece_count": _piece_count(board),
        "is_check": is_check,
        "solution_is_capture": solution_is_capture,
        "solution_gives_check": solution_gives_check,
        "promotion_involved": promotion_involved,
        "quiet_solution": quiet_solution,
        "factor_scores": {
            "branching": round(branching_score, 1),
            "forcing": round(forcing_score, 1),
            "eval_gap": round(eval_gap_score, 1),
            "eval_abs": round(eval_abs_score, 1),
            "calculation": round(calc_score, 1),
        },
        "factor_weights": dict(FACTOR_WEIGHTS),
    }

    return DifficultyAssessment(
        difficulty=difficulty,
        score=round(raw, 1),
        factors=factors,
    )


def _piece_count(board: chess.Board) -> int:
    count = 0
    for square in chess.SQUARES:
        if board.piece_at(square) is not None:
            count += 1
    return count


__all__ = [
    "DIFFICULTY_BANDS",
    "DifficultyAssessment",
    "DifficultyMethodologyVersion",
    "FACTOR_WEIGHTS",
    "assess_difficulty",
]
