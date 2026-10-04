"""Whole-game replay exercises: WHAT_WENT_WRONG and RECONSTRUCTION.

These are the two training types Phase 8 declared but refused to emit, because a
single stored move analysis cannot honestly produce them. This module supplies the
missing input: the *sequence* of a game's stored move analyses.

What each type is, in terms of measurement rather than narrative:

``WHAT_WENT_WRONG``
    A position where the player's own move lost material or evaluation, with the
    game's **actual** continuation attached as context. The answer key is the
    engine's stored best move; the point of the exercise is the comparison between
    what was played, what was available, and what actually followed. Nothing about
    the continuation is generated: it is the real game, read from storage.

``RECONSTRUCTION``
    A stretch of the game the player handled well, which they must reproduce move
    by move. The graded moves are the **engine's stored best moves** at the
    player's own plies, and the opponent's replies between them are the engine's
    stored best moves too. So the "correct answer" at every step is a move a real
    search chose for that exact position, recorded in the game's own analysis.

Both refuse rather than approximate. A mistake with no stored score, a stretch
whose intermediate plies were never analysed, or a line whose moves are not legal
in sequence all produce no exercise, with the reason returned to the caller.
"""

from __future__ import annotations

import chess

from argus.training.difficulty import assess_difficulty
from argus.training.models import (
    TRAINING_METHODOLOGY_VERSION,
    Category,
    PositionType,
    TrainingPosition,
)

#: A move must lose at least this much (centipawns, the player's perspective) to
#: be worth a "what went wrong" exercise. Below it, the position is simply chess.
DEFAULT_MISTAKE_THRESHOLD_CP = 150

#: A reconstruction needs at least this many solver moves to be a reconstruction
#: rather than a single-move puzzle.
MIN_RECONSTRUCTION_MOVES = 2

#: A ply counts as "well played" for reconstruction when the stored loss is at
#: most this. Strict, because the exercise asks the player to reproduce the move.
RECONSTRUCTION_TOLERANCE_CP = 30


def _phase_category(phase: str | None) -> Category:
    """The category a replay exercise inherits from the phase it occurred in.

    This is deliberately *not* a guess about the mistake's nature. A stored move
    analysis records the game phase and the engine's numbers; it does not record
    whether the error was tactical or positional, and inventing that label is
    exactly what the training engine refuses to do. The phase is measured, so the
    phase is what the category says.
    """
    if phase == "opening":
        return Category.OPENING
    if phase == "endgame":
        return Category.ENDGAME
    return Category.CALCULATION


def _category_for(
    row: dict,
    normalized_fen: str,
    overrides: dict[str, Category] | None,
) -> Category:
    """The exercise's category: the position's own, else its phase.

    A category is a property of the *position*, not of the exercise format. When
    the same position is already stored as a puzzle, that puzzle's category was
    assigned from its stored evidence (the generator's rules), so the replay
    format reuses it instead of inventing a second label for the same board.
    Only when no such exercise exists does the measured phase decide.
    """
    if overrides:
        existing = overrides.get(normalized_fen)
        if existing is not None:
            return Category(existing)
    return _phase_category(row.get("phase"))


def _normalize_fen(fen: str) -> str:
    return " ".join(chess.Board(fen).fen().split(" ")[:4])


def _player_rows(rows: list[dict], player_color: str) -> list[dict]:
    return [row for row in rows if str(row.get("mover")) == player_color]


def _best_loss(row: dict) -> int | None:
    """The stored centipawn loss of the played move, or ``None`` when unknown."""
    loss = row.get("centipawn_loss")
    return None if loss is None else int(loss)


def _legal_line(start_fen: str, moves: list[str]) -> bool:
    """Whether a stored UCI sequence is playable from ``start_fen``, in order."""
    board = chess.Board(start_fen)
    for uci in moves:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return False
        if move not in board.legal_moves:
            return False
        board.push(move)
    return True


def build_what_went_wrong(
    *,
    game_id: str,
    rows: list[dict],
    player_color: str,
    player_id: int | None = None,
    result: str | None = None,
    threshold_cp: int = DEFAULT_MISTAKE_THRESHOLD_CP,
    max_exercises: int = 10,
    continuation_plies: int = 8,
    category_overrides: dict[str, Category] | None = None,
) -> tuple[list[TrainingPosition], list[dict]]:
    """Exercises where the player's own move went wrong, with the real aftermath.

    Returns ``(positions, refusals)``. A refusal records the ply and why it could
    not become an exercise, so the caller can report the gap instead of silently
    producing fewer exercises than expected.
    """
    positions: list[TrainingPosition] = []
    refusals: list[dict] = []
    ordered = sorted(rows, key=lambda row: int(row.get("ply") or 0))

    for index, row in enumerate(ordered):
        if len(positions) >= max_exercises:
            break
        if str(row.get("mover")) != player_color:
            continue
        loss = _best_loss(row)
        if loss is None:
            refusals.append({"ply": row.get("ply"), "reason": "no stored centipawn loss"})
            continue
        if loss < threshold_cp:
            continue
        fen = str(row.get("fen_before") or "")
        best_uci = row.get("best_move_uci")
        played_uci = row.get("played_move_uci")
        if not fen or not best_uci or not played_uci:
            refusals.append({"ply": row.get("ply"), "reason": "missing stored position or move"})
            continue
        if best_uci == played_uci:
            # The engine agreed with the player; whatever the loss, there is no
            # better move to teach.
            continue
        try:
            board = chess.Board(fen)
            solution = chess.Move.from_uci(str(best_uci))
            if solution not in board.legal_moves:
                raise ValueError("not legal")
        except ValueError:
            refusals.append({"ply": row.get("ply"), "reason": "stored best move is not legal here"})
            continue

        # The real continuation, straight from the game's stored plies. This is
        # the "what happened" half of the exercise; it is never regenerated.
        aftermath: list[dict] = []
        for following in ordered[index + 1 : index + 1 + continuation_plies]:
            aftermath.append(
                {
                    "ply": following.get("ply"),
                    "mover": following.get("mover"),
                    "uci": following.get("played_move_uci"),
                    "san": following.get("played_move_san"),
                    "eval_cp": following.get("evaluation_before_cp"),
                    "loss_cp": following.get("centipawn_loss"),
                    "classification": following.get("classification"),
                    "phase": following.get("phase"),
                }
            )
        final_row = ordered[-1] if ordered else {}
        try:
            assessment = assess_difficulty(
                fen=board.fen(),
                solution_uci=solution.uci(),
                solution_eval_cp=row.get("evaluation_before_cp"),
                # The player's own move is the alternative that was actually
                # chosen, so its measured gap is the exercise's difficulty.
                alternative_best_loss_cp=loss,
                solution_pv_length=0,
            )
        except (ValueError, AssertionError):  # pragma: no cover - defensive
            refusals.append({"ply": row.get("ply"), "reason": "difficulty could not be measured"})
            continue

        positions.append(
            TrainingPosition(
                player_id=player_id,
                source_game_id=game_id,
                source_ply=int(row.get("ply") or 0),
                side_to_move=player_color,
                fen=board.fen(),
                source_fen_normalized=_normalize_fen(board.fen()),
                position_type=PositionType.WHAT_WENT_WRONG,
                category=_category_for(
                    row, _normalize_fen(board.fen()), category_overrides
                ),
                difficulty=assessment.difficulty,
                difficulty_factors=dict(assessment.factors),
                data_source="personalized",
                source_reason=(
                    f"What went wrong: played {row.get('played_move_san') or played_uci} "
                    f"({row.get('classification') or 'not classified'}), losing "
                    f"{loss / 100:.2f} pawns against "
                    f"{row.get('best_move_san') or best_uci}"
                ),
                tags=[str(row.get("classification"))] if row.get("classification") else [],
                solution_uci=solution.uci(),
                solution_san=str(row.get("best_move_san") or board.san(solution)),
                acceptable_moves={},
                candidate_moves=[],
                principal_variation=[solution.uci()],
                continuation_line=[],
                solution_eval_cp=row.get("evaluation_before_cp"),
                solution_eval_mate=row.get("evaluation_before_mate"),
                played_move_uci=str(played_uci),
                played_move_san=row.get("played_move_san"),
                played_eval_cp=row.get("played_eval_cp"),
                played_loss_cp=loss,
                engine=str(row.get("engine") or "stockfish"),
                engine_version=row.get("engine_version"),
                depth=row.get("depth"),
                analysis_version=row.get("analysis_version"),
                methodology_version=TRAINING_METHODOLOGY_VERSION,
            )
        )
        # The aftermath travels with the position so the review screen can show
        # what actually followed; it is context, not a graded line.
        positions[-1].replay_context = {
            "kind": "what_went_wrong",
            "game_result": result,
            "played": {
                "uci": played_uci,
                "san": row.get("played_move_san"),
                "loss_cp": loss,
                "eval_cp": row.get("played_eval_cp"),
                "classification": row.get("classification"),
            },
            "better": {
                "uci": solution.uci(),
                "san": row.get("best_move_san"),
                "eval_cp": row.get("evaluation_before_cp"),
            },
            "actual_continuation": aftermath,
            "final_eval_cp": final_row.get("evaluation_before_cp"),
            "evaluation_trajectory": [
                {"ply": item.get("ply"), "eval_cp": item.get("evaluation_before_cp")}
                for item in ordered
            ],
            "methodology_version": TRAINING_METHODOLOGY_VERSION,
        }
    return positions, refusals


def build_reconstruction(
    *,
    game_id: str,
    rows: list[dict],
    player_color: str,
    player_id: int | None = None,
    max_exercises: int = 5,
    min_moves: int = MIN_RECONSTRUCTION_MOVES,
    max_moves: int = 6,
    tolerance_cp: int = RECONSTRUCTION_TOLERANCE_CP,
    category_overrides: dict[str, Category] | None = None,
) -> tuple[list[TrainingPosition], list[dict]]:
    """Exercises where the player must reproduce the engine's own play.

    A candidate start is a ply where the player's move was good (within
    ``tolerance_cp``). The exercise then continues while *every* subsequent ply
    has a stored best move and the player's own moves keep meeting the tolerance,
    so a reconstruction never asks the player to reproduce a move the engine
    disliked.
    """
    positions: list[TrainingPosition] = []
    refusals: list[dict] = []
    ordered = sorted(rows, key=lambda row: int(row.get("ply") or 0))

    for index, row in enumerate(ordered):
        if len(positions) >= max_exercises:
            break
        if str(row.get("mover")) != player_color:
            continue
        loss = _best_loss(row)
        if loss is None or loss > tolerance_cp:
            continue
        start_fen = str(row.get("fen_before") or "")
        if not start_fen:
            continue

        # Walk forward, alternating: the opponent's ply then the player's ply.
        # Every step must have a stored best move, and the player's steps must be
        # moves the engine did not object to.
        line: list[str] = []
        solver_moves = 0
        cursor = index
        while solver_moves < max_moves:
            next_index = cursor + 1
            if next_index >= len(ordered):
                break
            following = ordered[next_index]
            best = following.get("best_move_uci")
            if not best:
                refusals.append(
                    {
                        "ply": following.get("ply"),
                        "reason": "stored ply has no best move, so the line cannot continue",
                    }
                )
                break
            line.append(str(best))
            if str(following.get("mover")) == player_color:
                solver_moves += 1
                following_loss = _best_loss(following)
                if following_loss is None or following_loss > tolerance_cp:
                    # The player did not handle this one; the reconstruction ends
                    # before it rather than grading a move the engine disliked.
                    line.pop()
                    solver_moves -= 1
                    break
            cursor = next_index

        if solver_moves < min_moves:
            continue
        solution = row.get("best_move_uci")
        if not solution:
            refusals.append(
                {"ply": row.get("ply"), "reason": "start ply has no stored best move"}
            )
            continue
        if not _legal_line(start_fen, [str(solution), *line]):
            refusals.append(
                {"ply": row.get("ply"), "reason": "stored line is not legal in sequence"}
            )
            continue
        try:
            board = chess.Board(start_fen)
            assessment = assess_difficulty(
                fen=board.fen(),
                solution_uci=str(solution),
                solution_eval_cp=row.get("evaluation_before_cp"),
                alternative_best_loss_cp=tolerance_cp,
                solution_pv_length=len(line),
            )
        except (ValueError, AssertionError):  # pragma: no cover - defensive
            refusals.append({"ply": row.get("ply"), "reason": "difficulty could not be measured"})
            continue

        positions.append(
            TrainingPosition(
                player_id=player_id,
                source_game_id=game_id,
                source_ply=int(row.get("ply") or 0),
                side_to_move=player_color,
                fen=board.fen(),
                source_fen_normalized=_normalize_fen(board.fen()),
                position_type=PositionType.RECONSTRUCTION,
                category=_category_for(
                    row, _normalize_fen(board.fen()), category_overrides
                ),
                difficulty=assessment.difficulty,
                difficulty_factors=dict(assessment.factors),
                data_source="personalized",
                source_reason=(
                    f"Reconstruction: reproduce the engine's own play from "
                    f"{row.get('played_move_san') or solution} for {solver_moves} move(s), "
                    "taken from this game's stored analysis"
                ),
                tags=["reconstruction"],
                solution_uci=str(chess.Move.from_uci(str(solution)).uci()),
                solution_san=str(row.get("best_move_san") or board.san(chess.Move.from_uci(str(solution)))),
                acceptable_moves={},
                candidate_moves=[],
                principal_variation=[str(solution)],
                continuation_line=line,
                solution_eval_cp=row.get("evaluation_before_cp"),
                solution_eval_mate=row.get("evaluation_before_mate"),
                played_move_uci=row.get("played_move_uci"),
                played_move_san=row.get("played_move_san"),
                played_eval_cp=row.get("played_eval_cp"),
                played_loss_cp=loss,
                engine=str(row.get("engine") or "stockfish"),
                engine_version=row.get("engine_version"),
                depth=row.get("depth"),
                analysis_version=row.get("analysis_version"),
                methodology_version=TRAINING_METHODOLOGY_VERSION,
            )
        )
        positions[-1].replay_context = {
            "kind": "reconstruction",
            "solver_moves": solver_moves,
            "line_plies": len(line),
            "source": "stored best moves from this game's analysis",
            "methodology_version": TRAINING_METHODOLOGY_VERSION,
        }
    return positions, refusals


__all__ = [
    "DEFAULT_MISTAKE_THRESHOLD_CP",
    "MIN_RECONSTRUCTION_MOVES",
    "RECONSTRUCTION_TOLERANCE_CP",
    "_category_for",
    "build_reconstruction",
    "build_what_went_wrong",
]
