"""Phase 8 training service: the seam between stored analyses and the engine.

This module owns the *API* side of the training engine:

* **Generation** turns a game's stored move analyses into exercises. It runs the
  deterministic ``argus.training`` generator and persists only what the
  eligibility gate accepts — a refusal is a result, reported with its reason.
* **Grading** compares a submitted move against the stored, engine-verified
  solution using :class:`~argus.training.acceptance.MoveAcceptancePolicy`. The
  engine is consulted only for a move the stored data cannot already grade
  (spec §44: analysis happens at creation, not on every read).
* **Reading** (library, review queue, progress, recommendations, sessions) never
  runs an engine: it is pure database work over already-verified exercises.

The service never invents data. When an exercise has no honest answer to give,
the endpoint says so.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import chess
from sqlalchemy.orm import Session

from argus.analysis.engine.base import ChessEngine
from argus.chess_core.models import Color
from argus.shared.errors import NotFoundError, ValidationError
from argus.shared.logging import get_logger
from argus.shared.time import as_utc as _as_utc
from argus.training import (
    CATEGORY_LABELS,
    HINT_POLICY_VERSION,
    TrainingPosition as PackagePosition,
    apply_attempt,
    build_hints,
    compute_progress,
    due_positions,
    plan_session,
    recommend,
)
from argus.training.models import REPLAY_POSITION_TYPES, Category
from argus.training.acceptance import default_policy, evaluate_attempt
from argus.training.recommendations import CategoryStats
from argus.training.sessions import SESSION_KINDS

from argus_api.db.models import TrainingAttempt, TrainingPosition, TrainingSession
from argus_api.db.repository import (
    create_training_session,
    get_game,
    get_move_analyses,
    get_player,
    get_training_position,
    get_training_session,
    list_training_attempts,
    list_training_positions,
    list_training_sessions,
    opponent_game_rows,
    save_training_attempt,
    save_training_position,
    training_categories_by_fen,
    training_dedupe_keys,
    update_training_position_state,
    update_training_session,
)

logger = get_logger(__name__)

#: The whole-game exercise types (methodology 8.2), used to label the generation
#: summary. Kept as plain strings because the API layer speaks strings.
REPLAY_TYPE_VALUES = tuple(position_type.value for position_type in REPLAY_POSITION_TYPES)



# ---------------------------------------------------------------------------
# Package <-> ORM conversion
# ---------------------------------------------------------------------------


def package_position(row: TrainingPosition) -> PackagePosition:
    """Rebuild the engine's plain model from a persisted exercise row."""
    return PackagePosition(
        id=row.id,
        player_id=row.player_id,
        source_game_id=row.source_game_id,
        source_ply=row.source_ply,
        source_position_id=row.source_position_id,
        side_to_move=row.side_to_move,
        fen=row.fen,
        source_fen_normalized=row.source_fen_normalized,
        position_type=row.position_type,
        category=row.category,
        difficulty=row.difficulty,
        difficulty_factors=dict(row.difficulty_factors or {}),
        data_source=row.data_source,
        source_reason=row.source_reason or "",
        tags=list(row.tags or []),
        solution_uci=row.solution_uci,
        solution_san=row.solution_san,
        acceptable_moves=dict(row.acceptable_moves or {}),
        candidate_moves=list(row.candidate_moves or []),
        principal_variation=list(row.principal_variation or []),
        continuation_line=list(getattr(row, "continuation_line", None) or []),
        replay_context=dict(getattr(row, "replay_context", None) or {}),
        solution_eval_cp=row.solution_eval_cp,
        solution_eval_mate=row.solution_eval_mate,
        played_move_uci=row.played_move_uci,
        played_move_san=row.played_move_san,
        played_eval_cp=row.played_eval_cp,
        played_loss_cp=row.played_loss_cp,
        engine=row.engine,
        engine_version=row.engine_version,
        depth=row.depth,
        analysis_version=row.analysis_version,
        methodology_version=row.methodology_version,
        state=row.state,
        attempts=row.attempts,
        correct_attempts=row.correct_attempts,
        streak=row.streak,
        review_interval_days=row.review_interval_days,
        next_review_at=_as_utc(row.next_review_at),
        last_attempted_at=_as_utc(row.last_attempted_at),
        created_at=_as_utc(row.created_at),
    )


def _position_payload(position: PackagePosition) -> dict[str, Any]:
    """A persistable dict — enums flattened to their stored string values."""
    payload = position.model_dump()
    payload["category"] = position.category.value
    payload["difficulty"] = position.difficulty.value
    payload["position_type"] = position.position_type.value
    payload["state"] = position.state.value
    return payload


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _origin(row: TrainingPosition) -> dict[str, Any]:
    """The traceable origin chain, and whether its game still exists."""
    return {
        "game_id": row.source_game_id,
        "ply": row.source_ply,
        "position_id": row.source_position_id,
        # A deleted game leaves the exercise and its history intact and only
        # marks the source unavailable (spec §43).
        "available": row.source_game_id is not None,
    }


def serialize_position(row: TrainingPosition, *, reveal: bool = False) -> dict[str, Any]:
    """One exercise for the API.

    The solution is withheld until ``reveal`` — the interactive board must never
    receive the answer before the player has attempted the move (spec §9).
    """
    data: dict[str, Any] = {
        "id": row.id,
        "player_id": row.player_id,
        "data_source": row.data_source,
        "is_personalized": row.data_source == "personalized",
        "is_opponent_preparation": row.data_source == "opponent_preparation",
        "source_reason": row.source_reason,
        "origin": _origin(row),
        "category": row.category,
        "category_label": CATEGORY_LABELS.get(row.category, row.category),
        "difficulty": row.difficulty,
        "difficulty_factors": dict(row.difficulty_factors or {}),
        "position_type": row.position_type,
        "side_to_move": row.side_to_move,
        "fen": row.fen,
        "tags": list(row.tags or []),
        "state": row.state,
        "attempts": row.attempts,
        "correct_attempts": row.correct_attempts,
        "streak": row.streak,
        "review_interval_days": row.review_interval_days,
        "next_review_at": row.next_review_at.isoformat() if row.next_review_at else None,
        "last_attempted_at": row.last_attempted_at.isoformat() if row.last_attempted_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "methodology_version": row.methodology_version,
        "engine": row.engine,
        "engine_version": row.engine_version,
        "depth": row.depth,
        "analysis_version": row.analysis_version,
    }
    if reveal:
        data["solution"] = {
            "uci": row.solution_uci,
            "san": row.solution_san,
            "eval_cp": row.solution_eval_cp,
            "eval_mate": row.solution_eval_mate,
        }
        data["acceptable_moves"] = dict(row.acceptable_moves or {})
        data["principal_variation"] = list(row.principal_variation or [])
        data["continuation_line"] = list(getattr(row, "continuation_line", None) or [])
        # Whole-game provenance for WHAT_WENT_WRONG / RECONSTRUCTION, revealed
        # with the solution: the real continuation belongs to the debrief, not to
        # the moment before the player has answered.
        data["replay_context"] = dict(getattr(row, "replay_context", None) or {})
        data["played_move"] = {
            "uci": row.played_move_uci,
            "san": row.played_move_san,
            "eval_cp": row.played_eval_cp,
            "loss_cp": row.played_loss_cp,
        }
        data["candidate_moves"] = list(row.candidate_moves or [])
    return data


def serialize_attempt(row: TrainingAttempt) -> dict[str, Any]:
    return {
        "id": row.id,
        "training_position_id": row.training_position_id,
        "session_id": row.session_id,
        "submitted_uci": row.submitted_uci,
        "submitted_san": row.submitted_san,
        "correctness": row.correctness,
        "submitted_eval_cp": row.submitted_eval_cp,
        "evaluation_delta_cp": row.evaluation_delta_cp,
        "hints_used": row.hints_used,
        "response_time_ms": row.response_time_ms,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def serialize_session(row: TrainingSession, *, reveal: bool = False) -> dict[str, Any]:
    completed = list(row.completed_position_ids or [])
    planned = list(row.planned_position_ids or [])
    remaining = [pid for pid in planned if pid not in set(completed)]
    return {
        "id": row.id,
        "player_id": row.player_id,
        "kind": row.kind,
        "target_category": row.target_category,
        "status": row.status,
        "planned": len(planned),
        "completed": len(completed),
        "remaining": len(remaining),
        "remaining_position_ids": remaining,
        "completed_position_ids": completed,
        "planned_position_ids": planned,
        "counts": {
            "correct": row.correct_count,
            "near_best": row.near_best_count,
            "incorrect": row.incorrect_count,
        },
        "hints_used": row.hints_used,
        "score": row.score,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def move_analysis_rows(db: Session, game_id: str, *, color: Color | None = None) -> list[dict]:
    """Stored move analyses as the generator's row shape (optionally one side)."""
    rows: list[dict] = []
    for row in get_move_analyses(db, game_id):
        if color is not None and row.mover != color.value:
            continue
        rows.append(
            {
                "ply": row.ply,
                "move_number": row.move_number,
                "mover": row.mover,
                "fen_before": row.fen_before,
                "fen_after": row.fen_after,
                "best_move_uci": row.best_move_uci,
                "best_move_san": row.best_move_san,
                "played_move_uci": row.played_move_uci,
                "played_move_san": row.played_move_san,
                "evaluation_before_cp": row.evaluation_before_cp,
                "evaluation_before_mate": row.evaluation_before_mate,
                "centipawn_loss": row.centipawn_loss,
                "played_eval_cp": getattr(row, "played_eval_cp", None),
                # MultiPV candidates, so the generator can record which alternative
                # moves are practically equivalent to the solution.
                "candidate_moves": list(getattr(row, "candidate_moves", None) or []),
                "classification": row.classification,
                "phase": row.phase,
                "principal_variation": list(row.principal_variation or []),
                "depth": row.depth,
                "engine": row.engine,
                "engine_version": row.engine_version,
                "analysis_version": row.analysis_version,
            }
        )
    return rows


def _player_color_for_game(db: Session, game_id: str, player_id: int) -> Color:
    """Which side a player held in this game.

    Raises:
        ValidationError: when the player is not one of the game's two sides —
        training must not be generated for a player who did not play.
    """
    game = get_game(db, game_id)
    if game.white_player_id == player_id:
        return Color.WHITE
    if game.black_player_id == player_id:
        return Color.BLACK
    raise ValidationError(
        f"Player {player_id} is not a participant in game '{game_id}'",
        details={"game_id": game_id, "player_id": player_id},
    )


def _generate_replay(
    db: Session,
    *,
    game_id: str,
    player_id: int | None,
    player_color: Color,
    result: str | None,
    replay_reasons: list[dict],
) -> list[dict]:
    """Persist the whole-game exercises a game's stored analysis justifies.

    Both builders are evidence-gated: they refuse (and say why) rather than
    approximating. A game that was handled well produces a reconstruction and no
    "what went wrong"; a game with no stored best moves produces neither.

    Dedupe is scoped to each format (see the ORM's unique constraint): the single
    -move formats share a position between themselves, while a review of a
    mistake is a different exercise from the puzzle for that mistake. Sessions
    apply a final pass so one sitting never serves the same position twice.
    """
    from argus.training.replay import build_reconstruction, build_what_went_wrong

    rows = move_analysis_rows(db, game_id)  # both sides: a replay needs the game
    if not rows:
        replay_reasons.append({"reason": "no stored move analysis for this game"})
        return []

    # A position's category comes from the position, not the format: when the
    # same board is already stored as a puzzle, its category (assigned from the
    # stored evidence by the generator) is reused rather than re-derived.
    overrides = {
        fen: Category(category)
        for fen, category in training_categories_by_fen(db, player_id).items()
    }

    created: list[dict] = []
    what_wrong, refuse_wrong = build_what_went_wrong(
        game_id=game_id,
        rows=rows,
        player_color=player_color.value,
        player_id=player_id,
        result=result,
        category_overrides=overrides,
    )
    replay_reasons.extend(refuse_wrong)
    reconstruction, refuse_recon = build_reconstruction(
        game_id=game_id,
        rows=rows,
        player_color=player_color.value,
        player_id=player_id,
        category_overrides=overrides,
    )
    replay_reasons.extend(refuse_recon)

    existing: dict[str, set[str]] = {
        position_type: training_dedupe_keys(db, player_id, position_type=position_type)
        for position_type in REPLAY_TYPE_VALUES
    }

    for position in [*what_wrong, *reconstruction]:
        kind = position.position_type.value
        if position.source_fen_normalized in existing.get(kind, set()):
            continue
        row = save_training_position(
            db, _position_payload(position), position_type=kind
        )
        existing.setdefault(kind, set()).add(position.source_fen_normalized)
        created.append(serialize_position(row, reveal=True))
        logger.info(
            "Replay exercise %s from game %s [ply=%s]",
            kind,
            game_id,
            position.source_ply,
        )
    return created


def generate_for_game(
    db: Session,
    game_id: str,
    *,
    player_id: int | None = None,
    data_source: str = "personalized",
    include_replay: bool = True,
) -> dict[str, Any]:
    """Generate (and persist) the exercises a game's analysis justifies.

    Only the moves of the requested player's side are turned into exercises: an
    opponent's blunder is not this player's training material. ``include_replay``
    controls the whole-game formats, which read the full game rather than one
    side of it.
    """
    from argus.training.generator import TrainingPositionGenerator

    game = get_game(db, game_id)  # 404 when missing
    color = _player_color_for_game(db, game_id, player_id) if player_id is not None else None
    rows = move_analysis_rows(db, game_id, color=color)
    if not rows:
        return {
            "game_id": game_id,
            "player_id": player_id,
            "accepted": 0,
            "rejected": 0,
            "seen": 0,
            "reasons": {},
            "generated": [],
            "created": [],
            "note": "no stored move analysis for this game and side",
        }

    existing = training_dedupe_keys(db, player_id)
    generator = TrainingPositionGenerator()
    report = generator.generate_from_game(
        rows,
        game_id=game_id,
        player_id=player_id,
        data_source=data_source,
        existing_normalized_fens=existing,
        # Whole-game arc evidence: the result plus which side the player held
        # lets a move at a failed conversion / held recovery be labelled so —
        # never from the single move alone.
        game_result=game.result,
        player_color=color,
    )

    created: list[dict] = []
    for candidate in report.generated:
        position = candidate.position
        if position is None:
            continue
        row = save_training_position(db, _position_payload(position))
        created.append(serialize_position(row, reveal=True))
        if position.source_fen_normalized:
            existing.add(position.source_fen_normalized)

    # Whole-game formats (methodology 8.2). These read the game, not one move:
    # WHAT_WENT_WRONG attaches the real continuation, RECONSTRUCTION asks for the
    # stored best moves to be reproduced. Both need the full move list, so the
    # unfiltered rows are fetched here rather than the single-side set above.
    replay_reasons: list[dict] = []
    replay_created = 0
    if include_replay and color is not None:
        replay_rows = _generate_replay(
            db,
            game_id=game_id,
            player_id=player_id,
            player_color=color,
            result=game.result,
            replay_reasons=replay_reasons,
        )
        replay_created = len(replay_rows)
        created.extend(replay_rows)

    reasons = report.reason_counts()
    for refusal in replay_reasons:
        key = f"replay: {refusal['reason']}"
        reasons[key] = reasons.get(key, 0) + 1

    summary = {
        "game_id": game_id,
        "player_id": player_id,
        # Single-move acceptances plus the replay exercises saved this run.
        "accepted": report.accepted_count + replay_created,
        "rejected": report.rejected_count,
        "seen": report.total_seen,
        "reasons": reasons,
        "skipped": list(report.skipped),
        "generated": created,
        "created": [entry["id"] for entry in created],
        "replay": {
            "emitted": [
                entry["position_type"]
                for entry in created
                if entry["position_type"] in REPLAY_TYPE_VALUES
            ],
            "refusals": replay_reasons,
        },
    }
    logger.info(
        "Training generation for game %s [player=%s accepted=%d rejected=%d]",
        game_id,
        player_id,
        report.accepted_count,
        report.rejected_count,
    )
    return summary


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def library(
    db: Session,
    player_id: int | None,
    *,
    include_general: bool = True,
    category: str | None = None,
    state: str | None = None,
    source_game_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    rows = list_training_positions(
        db,
        player_id=player_id,
        include_general=include_general,
        category=category,
        state=state,
        source_game_id=source_game_id,
        limit=limit,
        offset=offset,
    )
    return [serialize_position(row) for row in rows]


def review_queue(
    db: Session, player_id: int | None, *, now: datetime | None = None, limit: int | None = None
) -> dict:
    """Exercises whose review is due (plus optional fresh ones), deterministic order."""
    now = now or datetime.now(timezone.utc)
    rows = list_training_positions(db, player_id=player_id, include_general=True)
    package = [package_position(row) for row in rows]
    queue = due_positions(package, now=now, include_new=False, limit=limit)
    fresh = due_positions(package, now=now, include_new=True, limit=None)
    return {
        "player_id": player_id,
        "now": now.isoformat(),
        "due": [serialize_position(package_by_id(rows, p.id), reveal=False) for p in queue if p.id is not None],
        "count": len(queue),
        "total_queued": len(fresh),
        "note": (
            "Only exercises with a schedule that has come due are listed; "
            "attempting an exercise is what schedules the next review."
        ),
    }


def package_by_id(rows: list[TrainingPosition], position_id: int | None) -> TrainingPosition:
    for row in rows:
        if row.id == position_id:
            return row
    raise NotFoundError(f"Training position {position_id} not found")


def progress(db: Session, player_id: int | None) -> dict[str, Any]:
    """Measured statistics with sample sizes — never a bare percentage (spec §16)."""
    now = datetime.now(timezone.utc)
    rows = list_training_positions(db, player_id=player_id, include_general=True)
    package = [package_position(row) for row in rows]
    attempt_rows = list_training_attempts(db, player_id=player_id) if player_id else []
    attempts = [
        {
            "training_position_id": row.training_position_id,
            "correctness": row.correctness,
            # Passed as an aware datetime (not an ISO string): the engine compares it
            # against aware schedule timestamps, and a naive string would raise.
            "created_at": _as_utc(row.created_at),
            "hints_used": row.hints_used,
            "response_time_ms": row.response_time_ms,
        }
        for row in attempt_rows
    ]
    due_count = len(due_positions(package, now=now, include_new=False))
    report = compute_progress(package, attempts, now=now, due_count=due_count)
    return report.to_dict()


def recommendations(db: Session, player_id: int) -> dict[str, Any]:
    """Evidence-backed priorities from measured performance (spec §13)."""
    now = datetime.now(timezone.utc)
    rows = list_training_positions(db, player_id=player_id, include_general=True)
    attempt_rows = list_training_attempts(db, player_id=player_id)

    position_by_id = {row.id: row for row in rows}
    stats: dict[str, CategoryStats] = {}
    tags_by_position: dict[int, list[str]] = {row.id: list(row.tags or []) for row in rows}
    failing_patterns: dict[str, int] = defaultdict(int)

    for attempt in attempt_rows:
        row = position_by_id.get(attempt.training_position_id)
        if row is None:
            continue
        entry = stats.get(row.category)
        if entry is None:
            entry = stats[row.category] = CategoryStats(category=row.category)
        entry.attempts += 1
        if attempt.correctness == "correct":
            entry.correct += 1
        elif attempt.correctness == "near_best":
            entry.near_best += 1
        else:
            entry.incorrect += 1
            # Recurring-pattern evidence: tags of exercises the player got wrong.
            for tag in tags_by_position.get(row.id, []):
                failing_patterns[tag] += 1
        created = _as_utc(attempt.created_at)
        if created is not None:
            if entry.last_attempt_at is None or created > entry.last_attempt_at:
                entry.last_attempt_at = created

    library_categories = sorted({row.category for row in rows})
    attempted_categories = sorted(stats.keys())

    opportunities = recommend(
        list(stats.values()),
        library_categories=library_categories,
        attempted_categories=attempted_categories,
        pattern_counts=dict(failing_patterns),
        now=now,
    )
    return {
        "player_id": player_id,
        "opportunities": [opportunity.to_dict() for opportunity in opportunities],
        "library_categories": library_categories,
        "attempted_categories": attempted_categories,
        "evidence_policy": (
            "A category is recommended only when its measured accuracy is below "
            "the documented threshold across a minimum sample of decided attempts. "
            "With too little data, Caissa says so instead of guessing."
        ),
        "categories_without_evidence": [
            category
            for category in library_categories
            if stats.get(category) is None or stats[category].decided < 3
        ],
    }


def explain_position(db: Session, position_id: int, *, player_id: int | None = None) -> dict[str, Any]:
    """A deterministic explanation of why the solution is right (no LLM needed).

    Every sentence is assembled from stored evidence and board facts: the saved
    solution, its evaluation, the tactical tags the generator proved, the move
    the player actually made, and (when the player has history here) how often
    they have missed exercises carrying the same tags. Nothing is generated from
    a template that could describe a different position.
    """
    row = _authorized_position_for_read(db, position_id, player_id)
    board = chess.Board(row.fen)
    move = chess.Move.from_uci(row.solution_uci)
    gave_check = board.gives_check(move)
    is_capture = board.is_capture(move)
    board.push(move)
    is_mate = board.is_checkmate()

    board_facts: list[str] = []
    if is_capture:
        board_facts.append("it captures a piece")
    if is_mate:
        board_facts.append("it delivers checkmate")
    elif gave_check:
        board_facts.append("it gives check")

    tags = list(row.tags or [])
    delta = None
    if row.solution_eval_cp is not None and row.played_eval_cp is not None:
        delta = max(0, row.solution_eval_cp - row.played_eval_cp)
    elif row.played_loss_cp is not None:
        delta = row.played_loss_cp

    parts: list[str] = [f"The engine's best move is {row.solution_san} ({row.solution_uci})"]
    if board_facts:
        parts.append(", and ".join(board_facts))
    if row.solution_eval_mate:
        parts.append(f"mating in {abs(row.solution_eval_mate)}")
    elif row.solution_eval_cp is not None:
        parts.append(f"evaluated {row.solution_eval_cp / 100:+.2f} from your side")
    reason = "; ".join(part for part in parts if part)

    if row.played_move_san:
        reason += (
            f". In the game you played {row.played_move_san}"
            + (f", which cost {delta} centipawns" if delta is not None else "")
        )
    if tags:
        reason += f". The stored evidence for this position: {', '.join(tags)}"

    recurring = None
    if player_id is not None and tags:
        attempts = list_training_attempts(db, player_id=player_id)
        positions = {p.id: p for p in list_training_positions(db, player_id=player_id, include_general=True)}
        misses = 0
        for attempt in attempts:
            if attempt.correctness == "correct":
                continue
            source = positions.get(attempt.training_position_id)
            if source is not None and set(source.tags or []) & set(tags):
                misses += 1
        if misses:
            recurring = {
                "tag_overlap": tags,
                "incorrect_attempts": misses,
                "statement": (
                    f"Exercises with the same evidence have been missed {misses} time(s) "
                    f"in your recorded attempts."
                ),
            }

    return {
        "position_id": row.id,
        "solution": {
            "uci": row.solution_uci,
            "san": row.solution_san,
            "eval_cp": row.solution_eval_cp,
            "eval_mate": row.solution_eval_mate,
        },
        "category": row.category,
        "category_label": CATEGORY_LABELS.get(row.category, row.category),
        "position_type": row.position_type,
        "difficulty": row.difficulty,
        "tags": tags,
        "board_facts": board_facts,
        "played_move": {
            "san": row.played_move_san,
            "uci": row.played_move_uci,
            "loss_cp": row.played_loss_cp,
        },
        "evaluation_delta_cp": delta,
        "reason": reason,
        "recurring_pattern": recurring,
        "principal_variation": list(row.principal_variation or []),
        "source": {
            "game_id": row.source_game_id,
            "ply": row.source_ply,
            "engine": row.engine,
            "depth": row.depth,
            "analysis_version": row.analysis_version,
        },
        "methodology_version": row.methodology_version,
        "note": (
            "Every statement above is derived from the stored analysis or the board; "
            "Caissa does not add chess claims it cannot prove from this position."
        ),
    }


def _authorized_position_for_read(
    db: Session, position_id: int, player_id: int | None
) -> TrainingPosition:
    """Read-only ownership check for endpoints that expose an exercise."""
    row = get_training_position(db, position_id)
    if player_id is not None and row.player_id is not None and row.player_id != player_id:
        raise NotFoundError(f"Training position {position_id} not found")
    return row


def hints_for_position(db: Session, position_id: int, *, hint_index: int = 0) -> dict[str, Any]:
    """Progressive hints derived only from the stored exercise evidence (spec §11)."""
    row = get_training_position(db, position_id)
    position = package_position(row)
    hints = build_hints(
        fen=position.fen,
        solution_uci=position.solution_uci,
        solution_san=position.solution_san,
        solution_eval_cp=position.solution_eval_cp,
        solution_eval_mate=position.solution_eval_mate,
        played_move_san=position.played_move_san,
        played_loss_cp=position.played_loss_cp,
        tags=position.tags,
        category=position.category.value,
        position_type=position.position_type.value,
        side_to_move=position.side_to_move,
    )
    index = max(0, min(hint_index, len(hints)))
    return {
        "position_id": position_id,
        "policy_version": HINT_POLICY_VERSION,
        "hints": hints,
        "revealed": hints[:index],
        "hint_index": index,
        "remaining": len(hints) - index,
        "note": "Hints are generated from this exercise's stored evidence; the solution is not among them.",
    }


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def _submitted_score(engine: ChessEngine | None, fen: str, uci: str) -> tuple[int | None, int | None]:
    """The submitted move's evaluation, mover perspective, plus mate distance.

    Uses the engine's move comparison (a like-for-like best-vs-played search).
    A move that checkmates is reported as a positive mate distance; every other
    move keeps ``None`` for mate so centipawn arithmetic governs.
    """
    if engine is None:
        return None, None
    board = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    board.push(move)
    if board.is_checkmate():
        return None, 1
    comparison = engine.compare_moves(fen, [uci])[0]
    return comparison.played_cp, None


def _authorized_position(db: Session, position_id: int, player_id: int) -> TrainingPosition:
    """Fetch an exercise, refusing one owned by another player.

    An exercise owned by someone else answers 404 — never the exercise — because
    the ownership check is not optional (spec §36).
    """
    row = get_training_position(db, position_id)
    if row.player_id is not None and row.player_id != player_id:
        raise NotFoundError(f"Training position {position_id} not found")
    return row


def _decide(row: TrainingPosition, submitted_uci: str, engine: ChessEngine | None):
    """Validate the move and grade it — no persistence, no scheduling.

    Shared by the stored-attempt path and the read-only evaluation used by the
    agent, so the two can never disagree on the same submission.
    """
    board = chess.Board(row.fen)
    text = (submitted_uci or "").strip()
    try:
        move = chess.Move.from_uci(text)
    except ValueError as exc:
        raise ValidationError(f"Invalid UCI move '{submitted_uci}': {exc}") from exc
    if move not in board.legal_moves:
        raise ValidationError(
            f"Illegal move '{text}' in the exercise position",
            details={"fen": row.fen, "submitted_uci": text},
        )

    solution_uci = row.solution_uci
    acceptable = dict(row.acceptable_moves or {})

    # Fast path: the stored data can grade this without any engine call.
    if text == solution_uci or acceptable.get(text):
        submitted_cp, submitted_mate = row.solution_eval_cp, None
    elif row.played_move_uci and text == row.played_move_uci and row.played_eval_cp is not None:
        submitted_cp, submitted_mate = row.played_eval_cp, None
    else:
        submitted_cp, submitted_mate = _submitted_score(engine, row.fen, text)

    decision = evaluate_attempt(
        solution_eval_cp=row.solution_eval_cp,
        solution_eval_mate=row.solution_eval_mate,
        submitted_eval_cp=submitted_cp,
        submitted_eval_mate=submitted_mate,
        submitted_uci=text,
        solution_uci=solution_uci,
        acceptable_moves=acceptable,
        policy=default_policy(),
    )
    return decision, board.san(move)


def evaluate_only(
    db: Session, position_id: int, submitted_uci: str, *, player_id: int, engine: ChessEngine | None = None
) -> dict[str, Any]:
    """Grade a move against a stored exercise **without** storing an attempt.

    Used by the agent's ``evaluate_training_attempt`` tool: the agent may reason
    about a move, but it must not write to the player's training history.
    """
    row = _authorized_position(db, position_id, player_id)
    decision, submitted_san = _decide(row, submitted_uci, engine)
    return {
        "position_id": row.id,
        "submitted_uci": (submitted_uci or "").strip(),
        "submitted_san": submitted_san,
        "outcome": decision.outcome.value,
        "reason": decision.reason,
        "evaluation_delta_cp": decision.evaluation_delta_cp,
        "persisted": False,
        "note": "This is an evaluation only; the attempt was not stored.",
    }


def grade_attempt(
    db: Session,
    position_id: int,
    submitted_uci: str,
    *,
    player_id: int,
    engine: ChessEngine | None = None,
    session_id: int | None = None,
    hints_used: int = 0,
    response_time_ms: int | None = None,
    reveal: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Grade one attempt, store it forever, and advance the SRS schedule."""
    row = _authorized_position(db, position_id, player_id)
    decision, submitted_san = _decide(row, submitted_uci, engine)
    text = (submitted_uci or "").strip()

    # Persist the attempt first: it is the record that must never be lost.
    attempt = save_training_attempt(
        db,
        player_id=player_id,
        training_position_id=row.id,
        session_id=session_id,
        submitted_uci=text,
        submitted_san=submitted_san,
        correctness=decision.outcome.value,
        submitted_eval_cp=decision.submitted_eval_cp,
        evaluation_delta_cp=decision.evaluation_delta_cp,
        hints_used=max(0, int(hints_used or 0)),
        response_time_ms=response_time_ms,
    )

    # Advance the spaced-repetition state machine and store the decision.
    position = package_position(row)
    # Mutates `position` in place; the returned decision is not needed here.
    apply_attempt(position, decision.outcome, now=now)
    row = update_training_position_state(
        db,
        row.id,
        state=position.state.value,
        attempts=position.attempts,
        correct_attempts=position.correct_attempts,
        streak=position.streak,
        review_interval_days=position.review_interval_days,
        next_review_at=position.next_review_at,
        last_attempted_at=position.last_attempted_at,
    )

    # If part of a session, fold the attempt into its counters (resumable).
    if session_id is not None:
        _record_session_attempt(db, session_id, row.id, decision.outcome.value, hints_used)

    # The acceptable-move set is read from the stored row here too: it is the same
    # data `_decide` graded against, so the revealed alternatives match the verdict.
    acceptable = dict(row.acceptable_moves or {})
    response: dict[str, Any] = {
        "position_id": row.id,
        "player_id": player_id,
        "outcome": decision.outcome.value,
        "correct": decision.outcome.value == "correct",
        "reason": decision.reason,
        "submitted_uci": text,
        "submitted_san": attempt.submitted_san,
        "submitted_eval_cp": decision.submitted_eval_cp,
        "evaluation_delta_cp": decision.evaluation_delta_cp,
        "state": row.state,
        "streak": row.streak,
        "attempts": row.attempts,
        "correct_attempts": row.correct_attempts,
        "review_interval_days": row.review_interval_days,
        "next_review_at": row.next_review_at.isoformat() if row.next_review_at else None,
        "attempt_id": attempt.id,
        "reveal": reveal,
        "solution": {
            "uci": row.solution_uci,
            "san": row.solution_san,
            "eval_cp": row.solution_eval_cp,
            "eval_mate": row.solution_eval_mate,
        },
        "played_move": {
            "uci": row.played_move_uci,
            "san": row.played_move_san,
            "loss_cp": row.played_loss_cp,
        },
    }
    if reveal:
        response["principal_variation"] = list(row.principal_variation or [])
        response["acceptable_moves"] = acceptable
    else:
        response["pv_available"] = bool(row.principal_variation)
    return response


def _record_session_attempt(
    db: Session, session_id: int, position_id: int, correctness: str, hints_used: int | None
) -> None:
    """Fold one graded attempt into its session's counters and completion list."""
    session = get_training_session(db, session_id)
    completed = list(session.completed_position_ids or [])
    if position_id not in completed:
        completed.append(position_id)
    counts = {
        "correct": session.correct_count,
        "near_best": session.near_best_count,
        "incorrect": session.incorrect_count,
    }
    if correctness in counts:
        counts[correctness] += 1
    planned = list(session.planned_position_ids or [])
    status = "completed" if planned and all(pid in completed for pid in planned) else "active"
    update_training_session(
        db,
        session_id,
        completed_position_ids=completed,
        correct_count=counts["correct"],
        near_best_count=counts["near_best"],
        incorrect_count=counts["incorrect"],
        hints_used=session.hints_used + max(0, int(hints_used or 0)),
        status=status,
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _one_per_position(
    positions: list[PackagePosition], due_ids: set[int | None]
) -> list[PackagePosition]:
    """Narrow a library to one exercise per position, richest evidence first.

    When one position exists in two formats the review format wins — it asks the
    same question and additionally shows what really followed — but only if it is
    due; otherwise the exercise that is due is kept, so nothing scheduled is
    silently dropped.
    """
    grouped: dict[str, list[PackagePosition]] = {}
    for position in positions:
        grouped.setdefault(position.normalized_fen(), []).append(position)
    kept: list[PackagePosition] = []
    for group in grouped.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        due_in_group = [position for position in group if position.id in due_ids]
        candidates = due_in_group or group
        preferred = next(
            (
                position
                for position in candidates
                if position.position_type.value in REPLAY_TYPE_VALUES
            ),
            candidates[0],
        )
        kept.append(preferred)
    return kept


def start_session(
    db: Session,
    player_id: int,
    *,
    kind: str = "quick",
    target_category: str | None = None,
    game_id: str | None = None,
    custom_position_ids: list[int] | None = None,
    max_positions: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Plan and create a resumable training session from the player's library."""
    now = now or datetime.now(timezone.utc)
    if kind not in SESSION_KINDS:
        raise ValidationError(
            f"Unknown session kind '{kind}' (known: {', '.join(SESSION_KINDS)})"
        )
    rows = list_training_positions(db, player_id=player_id, include_general=True)
    package = [package_position(row) for row in rows]
    due = due_positions(package, now=now, include_new=True)
    due_ids = [p.id for p in due if p.id is not None]
    # Whole-game reviews and puzzles can share a position (they are stored
    # separately because they carry different evidence). A single sitting must
    # never serve the same position twice, so the library is narrowed to one
    # exercise per position before any session kind selects from it.
    package = _one_per_position(package, set(due_ids))

    weakness_order: list[str] = []
    if kind == "weakness":
        try:
            weakness_order = [
                item["category"]
                for item in recommendations(db, player_id)["opportunities"]
            ]
        except Exception:  # noqa: BLE001 — recommendations are advisory, not required
            weakness_order = []

    plan = plan_session(
        kind,
        package,
        now=now,
        due_position_ids=due_ids,
        weakness_category_order=weakness_order,
        game_id=game_id,
        custom_position_ids=custom_position_ids,
        max_positions=max_positions,
    )

    session = create_training_session(
        db,
        player_id=player_id,
        kind=kind,
        target_category=target_category or (plan_kind_category(kind)),
        planned_position_ids=plan.planned_position_ids,
    )
    data = serialize_session(session)
    data["notes"] = list(plan.notes)
    data["describe"] = plan.describe()
    return data


def plan_kind_category(kind: str) -> str | None:
    """The category a category-session targets (``endgame`` → ``endgame``)."""
    return kind if kind in {"endgame", "tactical"} else None


def get_session(db: Session, session_id: int, *, player_id: int) -> dict[str, Any]:
    """One session with the current exercise to solve next (never the solution)."""
    session = get_training_session(db, session_id)
    if session.player_id != player_id:
        raise NotFoundError(f"Training session {session_id} not found")
    data = serialize_session(session)
    remaining = data["remaining_position_ids"]
    if remaining and session.status == "active":
        next_row = get_training_position(db, remaining[0])
        data["current"] = serialize_position(next_row)
    else:
        data["current"] = None
    return data


def list_sessions(db: Session, player_id: int, *, status: str | None = None, limit: int | None = None) -> list[dict]:
    return [
        serialize_session(row)
        for row in list_training_sessions(db, player_id=player_id, status=status, limit=limit)
    ]


def cancel_session(db: Session, session_id: int, *, player_id: int) -> dict[str, Any]:
    """Cancel a session, deleting nothing: the attempts already made remain."""
    session = get_training_session(db, session_id)
    if session.player_id != player_id:
        raise NotFoundError(f"Training session {session_id} not found")
    updated = update_training_session(db, session_id, status="cancelled")
    return serialize_session(updated)


# ---------------------------------------------------------------------------
# CONTINUE_LINE: multi-ply grading against the stored engine line
# ---------------------------------------------------------------------------


def grade_continuation(
    db: Session,
    position_id: int,
    submitted_moves: list[str],
    *,
    player_id: int,
) -> dict[str, Any]:
    """Grade the continuation of a CONTINUE_LINE exercise, move by move.

    The exercise's stored ``continuation_line`` is the engine's principal
    variation **after** the solution: opponent reply, solver move, opponent
    reply, solver move, … (UCI). The solver submits only the moves they were to
    find; each is compared to the stored engine move at that point, and the
    position is advanced by the stored opponent replies in between.

    This is a read-only evaluation: it stores nothing, because a continuation is
    a practice extension of the same exercise rather than a new attempt type. A
    submission that is not legal in the advancing position is reported as an
    error rather than silently skipped.
    """
    row = _authorized_position(db, position_id, player_id)
    line = list(getattr(row, "continuation_line", None) or [])
    # RECONSTRUCTION is graded by exactly this mechanism: a stored line of real
    # moves that the solver must reproduce. It is the same comparison against
    # stored engine output, so it uses the same grader rather than a second,
    # divergent one.
    if row.position_type not in ("continue_line", "reconstruction") or not line:
        raise ValidationError(
            f"Training position {position_id} is not a line-continuation exercise"
        )

    board = chess.Board(row.fen)
    try:
        board.push(chess.Move.from_uci(row.solution_uci))
    except ValueError as exc:  # pragma: no cover — stored solutions are validated at creation
        raise ValidationError(f"Stored solution is not playable: {exc}") from exc

    submitted = [str(move).strip() for move in (submitted_moves or []) if str(move).strip()]
    expected_moves: list[str] = []
    results: list[dict[str, Any]] = []
    correct = 0
    submitted_index = 0

    for index, uci in enumerate(line):
        try:
            engine_move = chess.Move.from_uci(uci)
            if engine_move not in board.legal_moves:
                break
        except ValueError:
            break
        if index % 2 == 1:
            # A solver turn: compare the submission (when provided) to the line.
            expected = uci
            expected_moves.append(expected)
            given = submitted[submitted_index] if submitted_index < len(submitted) else None
            submitted_index += 1
            if given is None:
                results.append(
                    {
                        "ply": index + 1,
                        "expected_uci": expected,
                        "submitted_uci": None,
                        "correct": False,
                        "played_engine_move": False,
                    }
                )
                board.push(engine_move)
                continue
            try:
                candidate = chess.Move.from_uci(given)
            except ValueError:
                candidate = None
            legal = candidate is not None and candidate in board.legal_moves
            is_correct = legal and given == expected
            if is_correct:
                correct += 1
            results.append(
                {
                    "ply": index + 1,
                    "expected_uci": expected,
                    "expected_san": board.san(engine_move),
                    "submitted_uci": given,
                    "submitted_san": board.san(candidate) if legal and candidate else None,
                    "correct": is_correct,
                    "played_engine_move": bool(legal and candidate == engine_move),
                    "legal": bool(legal),
                }
            )
            # Advance the board by the submitted move when legal, so the next
            # comparison is made in the position the solver actually produced.
            board.push(candidate if legal and candidate else engine_move)
        else:
            # The opponent's forced reply comes from the stored line.
            board.push(engine_move)

    total = len(expected_moves)
    outcome = "correct" if total and correct == total else "incorrect"
    return {
        "position_id": row.id,
        "type": row.position_type,
        "outcome": outcome,
        "correct_moves": correct,
        "expected_moves": total,
        "moves": results,
        "engine_line": line,
        "solution_uci": row.solution_uci,
        "solution_san": row.solution_san,
        "persisted": False,
        "note": (
            "Each expected move is the engine's own stored principal variation, not a "
            "constructed line. This evaluation stores nothing."
        ),
    }


# ---------------------------------------------------------------------------
# Opponent preparation: exercises built from an opponent's stored games
# ---------------------------------------------------------------------------


def generate_opponent_preparation(
    db: Session,
    opponent_player_id: int,
    *,
    preparing_player_id: int,
    min_occurrences: int = 2,
    max_exercises: int = 20,
) -> dict[str, Any]:
    """Generate preparation exercises against one opponent, for one player.

    The exercises are the positions that follow a move the opponent plays
    *characteristically* (seen in at least ``min_occurrences`` of their games);
    each solution is the engine's stored best reply at the very next ply of the
    same game. Nothing is predicted and nothing is synthesised: the selection is
    a count over stored games, and the solution is stored analysis.

    The exercises are owned by ``preparing_player_id`` (private to them) but
    trace back to the opponent's game, so the link "why do I have this?" survives.
    """
    from argus.training.generator import TrainingPositionGenerator
    from argus.training.opponent_prep import select_preparation_rows
    from argus.training.models import OPPONENT_PREPARATION_SOURCE

    get_player(db, str(opponent_player_id))  # 404 when the opponent is unknown
    get_player(db, str(preparing_player_id))  # 404 when the preparing player is unknown

    opponent_rows = opponent_game_rows(db, opponent_player_id)
    selected = select_preparation_rows(
        opponent_rows, min_occurrences=min_occurrences, max_exercises=max_exercises
    )
    if not selected:
        return {
            "opponent_player_id": opponent_player_id,
            "player_id": preparing_player_id,
            "accepted": 0,
            "rejected": 0,
            "seen": 0,
            "reasons": {},
            "generated": [],
            "created": [],
            "data_source": OPPONENT_PREPARATION_SOURCE,
            "note": (
                "No move is yet characteristic enough of this opponent (each needs at least "
                f"{min_occurrences} of their games), so Caissa prepares nothing rather than "
                "inventing a line."
            ),
        }

    existing = training_dedupe_keys(db, preparing_player_id)
    generator = TrainingPositionGenerator()
    created: list[dict] = []
    accepted = rejected = 0
    reasons: dict[str, int] = {}

    for preparation in selected:
        normalized = " ".join(
            str(preparation.row.get("fen_before") or "").split()[:4]
        )
        duplicate = bool(normalized) and normalized in existing
        candidate = generator.generate_from_move_analysis(
            preparation.row,
            game_id=preparation.game_id,
            player_id=preparing_player_id,
            data_source=OPPONENT_PREPARATION_SOURCE,
            duplicate=duplicate,
            reason_prefix=preparation.reason(),
        )
        if candidate.accepted and candidate.position is not None:
            row = save_training_position(db, _position_payload(candidate.position))
            created.append(serialize_position(row, reveal=True))
            if normalized:
                existing.add(normalized)
            accepted += 1
        else:
            rejected += 1
            for reason in candidate.report.reasons:
                key = reason.split(":", 1)[0]
                reasons[key] = reasons.get(key, 0) + 1

    return {
        "opponent_player_id": opponent_player_id,
        "player_id": preparing_player_id,
        "accepted": accepted,
        "rejected": rejected,
        "seen": len(selected),
        "reasons": reasons,
        "generated": created,
        "created": [entry["id"] for entry in created],
        "data_source": OPPONENT_PREPARATION_SOURCE,
        "note": (
            "Each exercise is the position after a move this opponent actually plays, with the "
            "engine's stored best reply as its solution."
        ),
    }
