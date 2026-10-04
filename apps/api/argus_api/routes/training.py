"""Training routes (Phase 8): generate, browse, solve, review, track, recommend.

The training engine is deterministic and evidence-based. These endpoints:

* **generate** exercises from a game's already-stored engine analysis (never a
  fresh Stockfish run at request time),
* **serve** exercises with their solution withheld until the player attempts
  the move or explicitly asks to reveal it,
* **grade** attempts against the stored solution and advance the spaced
  repetition schedule,
* **report** measured statistics and evidence-backed recommendations.

Authorization is server-side: a client-supplied player id is resolved to a real
tracked player, and an exercise or session owned by another player answers 404
rather than leaking their data.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from argus.shared.logging import get_logger
from argus.training import (
    CATEGORIES,
    CATEGORY_LABELS,
    DIFFICULTIES,
    HINT_POLICY_VERSION,
    POSITION_TYPES,
    TRAINING_METHODOLOGY_VERSION,
    default_eligibility_service,
    default_policy,
)
from argus.training.sessions import SESSION_KINDS

from argus_api.db.repository import (
    delete_training_positions_for_game,
    get_player,
    get_training_position,
    list_training_attempts,
)
from argus_api.deps import get_db
from argus_api.schemas import (
    TrainingAttemptRequest,
    TrainingContinueRequest,
    TrainingGenerateRequest,
    TrainingOpponentPrepRequest,
    TrainingSessionStartRequest,
)
from argus_api.services import training_service
from argus_api.services.authorization import authorize_game

logger = get_logger(__name__)
router = APIRouter(prefix="/api/training", tags=["training"])


def _player_key(player_id: str) -> int:
    """Validate that a client-supplied player id names a real tracked player."""
    try:
        return int(player_id)
    except (TypeError, ValueError):
        from argus.shared.errors import NotFoundError

        raise NotFoundError(f"Player '{player_id}' not found") from None


# --- catalogue -----------------------------------------------------------------


@router.get("/meta")
def get_training_meta() -> dict:
    """The training engine's vocabulary, thresholds and methodology versions.

    Exposed deliberately: a reader can see exactly which categories exist, which
    difficulty bands are measurable, the move-acceptance thresholds, and the
    versions that produced any stored exercise.
    """
    eligibility = default_eligibility_service().policy
    return {
        "methodology_version": TRAINING_METHODOLOGY_VERSION,
        "hint_policy_version": HINT_POLICY_VERSION,
        "categories": list(CATEGORIES),
        "category_labels": CATEGORY_LABELS,
        "difficulties": list(DIFFICULTIES),
        "position_types": list(POSITION_TYPES),
        "session_kinds": list(SESSION_KINDS),
        "acceptance_policy": default_policy().to_dict(),
        "eligibility_policy": asdict(eligibility),
        "data_sources": ["personalized", "general"],
        "privacy": (
            "Exercises derived from a player's games are PERSONALIZED and scoped to "
            "that player; GENERAL exercises are the only globally shareable ones."
        ),
        "note": (
            "Categories whose evidence is insufficient are never assigned; a "
            "trivially obvious, ambiguous or engine-missing position is refused."
        ),
    }


# --- generation ----------------------------------------------------------------


@router.post("/games/{game_id}/generate")
def generate_training(game_id: str, body: TrainingGenerateRequest, db: Session = Depends(get_db)) -> dict:
    """Generate (and store) the exercises a game's analysis justifies."""
    authorize_game(db, game_id)  # 404 when missing or not the caller's
    player_id = _player_key(body.player_id) if body.player_id else None
    if player_id is not None:
        get_player(db, body.player_id)  # 404 when the player is unknown
    if body.regenerate:
        removed = delete_training_positions_for_game(db, game_id)
        logger.info("Removed %d existing exercise(s) for game %s before regeneration", removed, game_id)
    return training_service.generate_for_game(
        db,
        game_id,
        player_id=player_id,
        data_source=body.data_source,
        include_replay=body.include_replay,
    )


@router.get("/games/{game_id}")
def list_game_training(game_id: str, player_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    """Exercises sourced from one game (the game → training link)."""
    authorize_game(db, game_id)
    resolved = _player_key(player_id) if player_id else None
    if resolved is not None:
        get_player(db, player_id)
    positions = training_service.library(
        db, resolved, include_general=True, source_game_id=game_id, limit=200
    )
    return {
        "game_id": game_id,
        "count": len(positions),
        "positions": positions,
        "note": "Each exercise traces back to this game and a specific ply.",
    }


# --- library -------------------------------------------------------------------


@router.get("/positions")
def list_positions(
    player_id: str | None = None,
    include_general: bool = True,
    category: str | None = None,
    state: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """The training library for a player (their exercises plus shared GENERAL ones)."""
    resolved = _player_key(player_id) if player_id else None
    if resolved is not None:
        get_player(db, player_id)
    positions = training_service.library(
        db,
        resolved,
        include_general=include_general,
        category=category,
        state=state,
        limit=limit,
        offset=offset,
    )
    return {"count": len(positions), "positions": positions}


@router.get("/positions/{position_id}")
def get_position(position_id: int, player_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    """One exercise — with its solution withheld (spec §9)."""
    row = get_training_position(db, position_id)
    if player_id:
        resolved = _player_key(player_id)
        get_player(db, player_id)
        if row.player_id is not None and row.player_id != resolved:
            from argus.shared.errors import NotFoundError

            raise NotFoundError(f"Training position {position_id} not found")
    return training_service.serialize_position(row, reveal=False)


@router.get("/positions/{position_id}/explanation")
def get_position_explanation(
    position_id: int, player_id: str | None = None, db: Session = Depends(get_db)
) -> dict:
    """A deterministic explanation of why the solution is right (no LLM required).

    Assembled from stored evidence and board facts, so it works even with no LLM
    provider configured and never invents a chess claim.
    """
    resolved = _player_key(player_id) if player_id else None
    if resolved is not None:
        get_player(db, player_id)
    return training_service.explain_position(db, position_id, player_id=resolved)


@router.get("/positions/{position_id}/hints")
def get_position_hints(
    position_id: int,
    hint_index: int = Query(default=0, ge=0, le=20),
    db: Session = Depends(get_db),
) -> dict:
    """Progressive hints, derived only from this exercise's stored evidence."""
    return training_service.hints_for_position(db, position_id, hint_index=hint_index)


@router.get("/positions/{position_id}/solution")
def reveal_position_solution(position_id: int, db: Session = Depends(get_db)) -> dict:
    """Reveal the engine-verified solution and its line (explicit user action)."""
    row = get_training_position(db, position_id)
    return training_service.serialize_position(row, reveal=True)


@router.post("/positions/{position_id}/attempt")
def submit_attempt(
    position_id: int,
    body: TrainingAttemptRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Grade one attempt, store it forever, and advance the review schedule."""
    player_id = _player_key(body.player_id)
    get_player(db, body.player_id)  # 404 when the player is unknown
    return training_service.grade_attempt(
        db,
        position_id,
        body.submitted_uci,
        player_id=player_id,
        engine=request.app.state.engine,
        session_id=body.session_id,
        hints_used=body.hints_used,
        response_time_ms=body.response_time_ms,
        reveal=body.reveal,
    )


@router.post("/opponents/{opponent_player_id}/prepare")
def prepare_for_opponent(
    opponent_player_id: int,
    body: TrainingOpponentPrepRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Generate preparation exercises against an opponent, for the given player.

    Exercises are owned by ``body.player_id`` and trace back to the opponent's
    game; the opponent must be a real tracked player (404 otherwise).
    """
    preparing_player_id = _player_key(body.player_id)
    get_player(db, body.player_id)  # 404 when the preparing player is unknown
    return training_service.generate_opponent_preparation(
        db,
        opponent_player_id,
        preparing_player_id=preparing_player_id,
        min_occurrences=body.min_occurrences,
        max_exercises=body.max_exercises,
    )


@router.post("/positions/{position_id}/continue")
def continue_line(
    position_id: int,
    body: TrainingContinueRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Grade the continuation of a CONTINUE_LINE exercise against the stored line."""
    player_id = _player_key(body.player_id)
    get_player(db, body.player_id)  # 404 when the player is unknown
    return training_service.grade_continuation(
        db, position_id, list(body.moves), player_id=player_id
    )


@router.get("/positions/{position_id}/attempts")
def list_position_attempts(
    position_id: int,
    player_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """The full attempt history at one exercise (never only the last result)."""
    resolved = _player_key(player_id)
    get_player(db, player_id)
    get_training_position(db, position_id)
    rows = list_training_attempts(db, player_id=resolved, training_position_id=position_id, limit=200)
    return {"position_id": position_id, "count": len(rows), "attempts": [training_service.serialize_attempt(r) for r in rows]}


@router.get("/attempts")
def list_attempts(
    player_id: str,
    training_position_id: int | None = None,
    session_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """A player's attempt history, newest first."""
    resolved = _player_key(player_id)
    get_player(db, player_id)
    rows = list_training_attempts(
        db, player_id=resolved, training_position_id=training_position_id, session_id=session_id, limit=limit
    )
    return {"count": len(rows), "attempts": [training_service.serialize_attempt(r) for r in rows]}


# --- review, progress, recommendations -----------------------------------------


@router.get("/review-queue")
def get_review_queue(
    player_id: str | None = None, limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)
) -> dict:
    """Exercises whose spaced-repetition review has come due."""
    resolved = _player_key(player_id) if player_id else None
    if resolved is not None:
        get_player(db, player_id)
    return training_service.review_queue(db, resolved, limit=limit)


@router.get("/progress")
def get_progress(player_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    """Measured progress with sample sizes attached to every figure."""
    resolved = _player_key(player_id) if player_id else None
    if resolved is not None:
        get_player(db, player_id)
    return training_service.progress(db, resolved)


@router.get("/recommendations")
def get_recommendations(player_id: str, db: Session = Depends(get_db)) -> dict:
    """Evidence-backed training priorities for one player."""
    resolved = _player_key(player_id)
    get_player(db, player_id)
    return training_service.recommendations(db, resolved)


# --- sessions ------------------------------------------------------------------


@router.post("/sessions")
def start_session(body: TrainingSessionStartRequest, db: Session = Depends(get_db)) -> dict:
    """Plan and create a resumable training session."""
    resolved = _player_key(body.player_id)
    get_player(db, body.player_id)
    return training_service.start_session(
        db,
        resolved,
        kind=body.kind,
        target_category=body.target_category,
        game_id=body.game_id,
        custom_position_ids=body.custom_position_ids,
        max_positions=body.max_positions,
    )


@router.get("/sessions")
def list_sessions(
    player_id: str,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """A player's sessions, newest first."""
    resolved = _player_key(player_id)
    get_player(db, player_id)
    return {"sessions": training_service.list_sessions(db, resolved, status=status, limit=limit)}


@router.get("/sessions/{session_id}")
def get_session(session_id: int, player_id: str, db: Session = Depends(get_db)) -> dict:
    """One session with its next unsolved exercise (solution withheld)."""
    resolved = _player_key(player_id)
    get_player(db, player_id)
    return training_service.get_session(db, session_id, player_id=resolved)


@router.post("/sessions/{session_id}/cancel")
def cancel_session(session_id: int, player_id: str, db: Session = Depends(get_db)) -> dict:
    """Cancel a session without destroying any attempt already made."""
    resolved = _player_key(player_id)
    get_player(db, player_id)
    return training_service.cancel_session(db, session_id, player_id=resolved)
