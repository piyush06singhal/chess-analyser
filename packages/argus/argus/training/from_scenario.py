"""Training positions built from a counterfactual (Phase 10 → Phase 8 bridge).

A counterfactual analysis knows two things a training engine can use: a position,
and a move that the engine measured as better than the move that was played. That
is enough to build an honest exercise — the solution is the *engine's* move with
the engine's own score, and the position is the position the player actually
faced.

Three rules keep this from producing junk exercises:

* **A measured solution only.** No engine score means no exercise. A position
  whose search returned nothing is not silently turned into a "best move" puzzle.
* **Only a genuinely good answer becomes the solution.** The alternative must be
  the engine's first choice or within the near-best tolerance of it. A move the
  engine scores as inferior never becomes an answer key.
* **The same position never becomes two exercises.** The dedupe key is the
  existing normalized-FEN key, so a counterfactual that repeats an existing
  exercise returns that exercise instead of a duplicate.
"""

from __future__ import annotations

from typing import Any

import chess

from argus.training.acceptance import default_policy
from argus.training.difficulty import assess_difficulty
from argus.training.models import (
    TRAINING_METHODOLOGY_VERSION,
    Category,
    PositionType,
    TrainingPosition,
)

#: A solution further than this from the engine's best is not an answer key. The
#: value comes from the acceptance policy the grader already uses, so an exercise
#: can never be built around a move the grader would have marked wrong.
NEAR_BEST_TOLERANCE_CP = default_policy().equal_tolerance_cp


def normalize_fen(fen: str) -> str:
    """Drop the move counters so the same position dedupes regardless of history."""
    board = chess.Board(fen)
    return " ".join(board.fen().split(" ")[:4])


def classify_from_evidence(*, tactical_notes: list[str], solution_is_capture: bool) -> Category:
    """Assign a category only where the board actually shows the reason."""
    if tactical_notes or solution_is_capture:
        return Category.TACTICAL
    return Category.CALCULATION


def build_training_position(
    *,
    fen: str,
    player_id: int | None,
    solution_uci: str,
    solution_san: str | None,
    solution_eval_cp: int | None,
    solution_eval_mate: int | None = None,
    alternatives: list[dict[str, Any]] | None = None,
    played_move_uci: str | None = None,
    played_move_san: str | None = None,
    played_eval_cp: int | None = None,
    tactical_notes: list[str] | None = None,
    engine: str = "stockfish",
    engine_version: str | None = None,
    depth: int | None = None,
    analysis_version: str | None = None,
    source_game_id: str | None = None,
    source_ply: int | None = None,
    evaluation_change_cp: int | None = None,
) -> TrainingPosition:
    """Build an exercise whose answer the engine actually measured.

    Raises:
        ValueError: when the solution is not legal in the position, or when the
            engine returned no score for it — both cases where the exercise would
            have to invent its answer key.
    """
    board = chess.Board(fen)
    try:
        solution = chess.Move.from_uci(solution_uci)
    except ValueError as exc:
        raise ValueError(f"'{solution_uci}' is not a usable move: {exc}") from exc
    if solution not in board.legal_moves:
        raise ValueError(f"'{solution_uci}' is not legal in {board.fen()}")
    if solution_eval_cp is None and solution_eval_mate is None:
        raise ValueError(
            "The engine returned no score for this move, so it cannot be an answer key"
        )

    san = solution_san or board.san(solution)
    notes = [note for note in (tactical_notes or []) if note]
    # The best alternative the engine preferred less: how much a near-miss loses.
    losses = [
        int(entry["centipawn_loss"])
        for entry in (alternatives or [])
        if entry.get("centipawn_loss") is not None
        and str(entry.get("uci")) != solution.uci()
        and not entry.get("is_engine_best")
    ]
    second_best_loss = min(losses) if losses else None
    assessment = assess_difficulty(
        fen=board.fen(),
        solution_uci=solution.uci(),
        solution_eval_cp=solution_eval_cp,
        alternative_best_loss_cp=second_best_loss,
        solution_pv_length=0,
    )

    acceptable: dict[str, str] = {}
    for entry in alternatives or []:
        uci = str(entry.get("uci") or "")
        loss = entry.get("centipawn_loss")
        if not uci or uci == solution.uci() or loss is None:
            continue
        if int(loss) <= NEAR_BEST_TOLERANCE_CP:
            acceptable[uci] = str(entry.get("san") or uci)

    candidate_rows = [
        {
            "uci": entry.get("uci"),
            "san": entry.get("san"),
            "cp": entry.get("cp"),
            "mate": entry.get("mate"),
            "rank": entry.get("rank"),
            "eval_source": entry.get("eval_source"),
        }
        for entry in (alternatives or [])
    ]

    reason_parts = ["Counterfactual analysis"]
    if played_move_san or played_move_uci:
        reason_parts.append(f"instead of {played_move_san or played_move_uci}")
    if evaluation_change_cp is not None:
        reason_parts.append(f"worth {evaluation_change_cp / 100:+.2f} pawns")
    if solution_eval_cp is not None:
        reason_parts.append(f"engine score {solution_eval_cp / 100:+.2f}")
    reason = ": ".join([reason_parts[0], ", ".join(reason_parts[1:])]) if len(reason_parts) > 1 else reason_parts[0]

    return TrainingPosition(
        player_id=player_id,
        source_game_id=source_game_id,
        source_ply=source_ply,
        side_to_move="white" if board.turn == chess.WHITE else "black",
        fen=board.fen(),
        source_fen_normalized=normalize_fen(board.fen()),
        position_type=(
            PositionType.FIND_TACTICAL_MOVE if notes else PositionType.FIND_BEST_MOVE
        ),
        category=classify_from_evidence(
            tactical_notes=notes, solution_is_capture=board.is_capture(solution)
        ),
        difficulty=assessment.difficulty,
        difficulty_factors=dict(assessment.factors),
        data_source="counterfactual",
        source_reason=reason,
        tags=notes,
        solution_uci=solution.uci(),
        solution_san=san,
        acceptable_moves=acceptable,
        candidate_moves=candidate_rows,
        principal_variation=[solution.uci()],
        solution_eval_cp=solution_eval_cp,
        solution_eval_mate=solution_eval_mate,
        played_move_uci=played_move_uci,
        played_move_san=played_move_san,
        played_eval_cp=played_eval_cp,
        played_loss_cp=(
            None
            if solution_eval_cp is None or played_eval_cp is None
            else max(0, solution_eval_cp - played_eval_cp)
        ),
        engine=engine,
        engine_version=engine_version,
        depth=depth,
        analysis_version=analysis_version or TRAINING_METHODOLOGY_VERSION,
        methodology_version=TRAINING_METHODOLOGY_VERSION,
    )


def solution_is_defensible(
    *,
    solution_uci: str,
    best_move_uci: str | None,
    centipawn_loss: int | None,
) -> tuple[bool, str | None]:
    """Whether a measured move may become an exercise's answer key.

    Returns ``(ok, reason_when_not)``. A move that is the engine's first choice is
    always defensible; otherwise it must be inside the near-best tolerance.
    """
    if not solution_uci:
        return False, "No move was supplied."
    if best_move_uci and solution_uci == best_move_uci:
        return True, None
    if centipawn_loss is None:
        return False, (
            "The engine returned no centipawn loss for this move, so Caissa cannot "
            "confirm it as an answer key."
        )
    if int(centipawn_loss) <= NEAR_BEST_TOLERANCE_CP:
        return True, None
    return False, (
        f"The engine scores this move {centipawn_loss / 100:.2f} pawns below its first "
        "choice, so it is not a defensible answer key."
    )


__all__ = [
    "NEAR_BEST_TOLERANCE_CP",
    "build_training_position",
    "classify_from_evidence",
    "normalize_fen",
    "solution_is_defensible",
]
