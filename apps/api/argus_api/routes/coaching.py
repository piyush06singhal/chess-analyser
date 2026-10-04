"""Phase 11 coaching routes: context, debrief, feed, focus, plan, methodology.

Read-only and engine-free. Each endpoint returns a result whose empty sections
say *why* they are empty, so the frontend never has to invent a placeholder.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from argus.shared.logging import get_logger

from argus_api.deps import get_db
from argus_api.observability import AUDIT_COLLECTION_DELETED, audit
from argus_api.schemas import (
    CoachingCollectionCreateRequest,
    CoachingCollectionItemRequest,
    CoachingMatchPrepRequest,
)
from argus_api.services import coaching_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/coaching", tags=["coaching"])


@router.get("/method")
def get_method() -> dict:
    """The coaching methodology: priorities, feed rules, and every mode's behaviour."""
    return coaching_service.method()


@router.get("/context")
def get_context(
    request: Request,
    user_id: int | None = Query(default=None, ge=1),
    mode: str | None = Query(default=None, max_length=32),
    game_id: str | None = Query(default=None, max_length=64),
    fen: str | None = Query(default=None, max_length=120),
    phase: str | None = Query(default=None, max_length=24),
    training_position_id: int | None = Query(default=None, ge=1),
    opponent_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """The unified coach context for whatever the user currently has open.

    The situation and the mode are *resolved* from what is present, so the user
    does not have to announce where they are — and ``gaps`` lists what Caissa
    knows it does not have.
    """
    context = coaching_service.resolve_context(
        db,
        settings=getattr(request.app.state, "settings", None),
        user_id=user_id,
        mode=mode,
        game_id=game_id,
        fen=fen,
        phase=phase,
        training_position_id=training_position_id,
        opponent_id=opponent_id,
    )
    return coaching_service.context_payload(context)


@router.get("/games/{game_id}/debrief")
def get_debrief(
    game_id: str,
    user_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """The automatic review of a stored game, section by section."""
    return coaching_service.debrief(db, game_id, user_id=user_id)


@router.get("/feed")
def get_feed(
    user_id: int | None = Query(default=None, ge=1),
    game_id: str | None = Query(default=None, max_length=64),
    opponent_id: int | None = Query(default=None, ge=1),
    dismissed: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
) -> dict:
    """Today's Caissa: recent game, mistakes, patterns, training, preparation."""
    return coaching_service.feed(
        db,
        user_id=user_id,
        game_id=game_id,
        opponent_id=opponent_id,
        dismissed=dismissed,
    )


@router.get("/focus")
def get_focus(
    user_id: int = Query(ge=1),
    dismissed: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
) -> dict:
    """"What should I work on?" — evidence first, or an honest refusal."""
    return coaching_service.focus(db, user_id=user_id, dismissed=dismissed)


@router.get("/plan")
def get_plan(
    user_id: int = Query(ge=1),
    weeks: int = Query(default=1, ge=1, le=12),
    dismissed: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
) -> dict:
    """A short training plan, derived only from focus areas with measured evidence."""
    return coaching_service.plan(db, user_id=user_id, weeks=weeks, dismissed=dismissed)


# --- §24/§25 Show me why -------------------------------------------------------


@router.get("/evidence")
def get_evidence(
    claim: str = Query(min_length=1, max_length=500),
    game_id: str | None = Query(default=None, max_length=64),
    ply: int | None = Query(default=None, ge=0),
    player_id: int | None = Query(default=None, ge=1),
    insight_key: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
) -> dict:
    """Resolve a claim to the stored evidence behind it, or state the gap.

    Every reference is classified (engine fact / Caissa feature / interpretation /
    prediction) and every reference Caissa cannot follow is reported as a gap.
    """
    return coaching_service.show_me_why(
        db,
        claim=claim,
        game_id=game_id,
        ply=ply,
        player_id=player_id,
        insight_key=insight_key,
    )


# --- §29 study collections -----------------------------------------------------


@router.get("/collections")
def list_collections(
    player_id: int = Query(ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """One player's study collections, each with its item count."""
    return coaching_service.collections(db, player_id=player_id)


@router.post("/collections", status_code=201)
def create_collection(
    body: CoachingCollectionCreateRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Create an empty study collection."""
    return coaching_service.create_collection(
        db,
        player_id=int(body.player_id),
        name=body.name,
        kind=body.kind,
        description=body.description,
    )


@router.get("/collections/{collection_id}")
def get_collection(
    collection_id: int,
    player_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """One collection with its typed pointers; a foreign collection is a 404."""
    return coaching_service.get_collection(db, collection_id, player_id=player_id)


@router.delete("/collections/{collection_id}")
def delete_collection(
    collection_id: int,
    player_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a collection and its items."""
    result = coaching_service.delete_collection(db, collection_id, player_id=player_id)
    # Destructive: record who removed which collection.
    audit(AUDIT_COLLECTION_DELETED, collection_id=collection_id, player_id=player_id)
    return result


@router.post("/collections/{collection_id}/items", status_code=201)
def add_collection_item(
    collection_id: int,
    body: CoachingCollectionItemRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Add a typed pointer; a kind the collection forbids is refused."""
    return coaching_service.add_collection_item(
        db,
        collection_id,
        player_id=int(body.player_id),
        kind=body.kind,
        ref=body.ref,
        label=body.label,
        note=body.note,
        game_id=body.game_id,
        ply=body.ply,
        fen=body.fen,
    )


@router.delete("/collections/{collection_id}/items")
def remove_collection_item(
    collection_id: int,
    kind: str = Query(min_length=1, max_length=24),
    ref: str = Query(min_length=1, max_length=200),
    player_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """Remove one pointer from a collection."""
    return coaching_service.remove_collection_item(
        db, collection_id, player_id=player_id, kind=kind, ref=ref
    )


# --- §30 unified search --------------------------------------------------------


@router.get("/search")
def unified_search(
    q: str = Query(default="", max_length=200),
    player_id: int | None = Query(default=None, ge=1),
    kind: list[str] = Query(default=[]),
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Search games, players, training, scenarios, insights, openings, collections.

    An empty query and an unmatched query return different statuses, each with a
    reason, so the UI never shows a bare \"no results\".
    """
    return coaching_service.search(db, q, player_id=player_id, kinds=kind or None, limit=limit)


# --- §14–§17 match preparation -------------------------------------------------


@router.post("/match-preparation", status_code=201)
def create_match_preparation(
    body: CoachingMatchPrepRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Build (and by default store) a match preparation object and MATCH BRIEF."""
    return coaching_service.prepare(
        db,
        preparing_player_id=int(body.preparing_player_id),
        opponent_id=body.opponent_id,
        as_white=body.as_white,
        persist=body.persist,
    )


@router.get("/match-preparation")
def list_match_preparations(
    preparing_player_id: int = Query(ge=1),
    opponent_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """Stored preparation snapshots for one player, or for one opponent."""
    return coaching_service.preparations(
        db, preparing_player_id=preparing_player_id, opponent_id=opponent_id
    )


@router.get("/match-preparation/{preparation_id}")
def get_match_preparation(
    preparation_id: int,
    preparing_player_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """One stored preparation snapshot with its match brief."""
    return coaching_service.get_preparation(
        db, preparation_id, preparing_player_id=preparing_player_id
    )


# --- §26–§28 progress ----------------------------------------------------------


@router.get("/progress/compare")
def compare_progress(
    player_id: int = Query(ge=1),
    split: float = Query(default=0.5, gt=0.0, lt=1.0),
    db: Session = Depends(get_db),
) -> dict:
    """Compare earlier and recent analysed games, with the causality warning.

    Every measure reports its sample; below the minimum it is reported as
    insufficient rather than shown as a number.
    """
    return coaching_service.compare(db, player_id=player_id, split=split)
