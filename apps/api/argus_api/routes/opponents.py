"""Opponent Intelligence routes (Phase 9).

Read-mostly by design, like Player Intelligence: the opponent profile is a
derived, versioned snapshot, so reads return what is stored and rebuild only
when the inputs changed (or when ``/rebuild`` is called explicitly).

The surface answers the spec's questions one at a time:

* ``opponent-profile`` — identity, history, repertoire, statistics (cached)
* ``opponent-games`` — the game history behind every claim
* ``repertoire`` / ``repertoire/recent`` — what they play as a colour, all-time
  and lately
* ``position-response`` — how they answered one specific position
* ``responses`` — the recurring positions they reach, with their answers
* ``tendencies`` — measured, evidence-gated regularities
* ``phase-statistics`` — performance by phase
* ``preparation-report`` — the composed preparation document

Authorization is server-side: the player must be a real tracked player (404
otherwise). Reports are analytics over stored games — never psychology, never a
prediction — and every response carries its sample size and claim levels.
"""

from __future__ import annotations

import chess
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from argus.opponent_intelligence import OPPONENT_METHODOLOGY_VERSION, DEFAULT_POLICY
from argus.shared.errors import ValidationError
from argus.shared.logging import get_logger

from argus_api.db.repository import get_player
from argus_api.deps import get_db
from argus_api.services import opponent_service

logger = get_logger(__name__)
router = APIRouter(prefix="/api/players", tags=["opponents"])


def _settings(request: Request):  # noqa: ANN202 — Settings, avoids importing it here
    return getattr(request.app.state, "settings", None)


def _validated_fen(fen: str) -> str:
    try:
        chess.Board(fen)
    except ValueError as exc:
        raise ValidationError(f"Invalid FEN: {exc}", details={"fen": fen}) from exc
    return fen


@router.get("/opponent-meta")
def get_opponent_meta() -> dict:
    """The opponent engine's vocabulary, gates and methodology version."""
    return {
        "methodology_version": OPPONENT_METHODOLOGY_VERSION,
        "policy_defaults": DEFAULT_POLICY.to_dict(),
        "claim_levels": ["insufficient", "observation", "pattern", "tendency"],
        "coverage_bands": ["insufficient", "limited", "moderate", "robust"],
        "colors": ["white", "black"],
        "privacy": (
            "Opponent intelligence is derived from a player's own stored games and is scoped "
            "to a real tracked player. It is an analytics summary, not a psychological profile "
            "and not a prediction, and every statement carries its sample size."
        ),
    }


@router.get("/{player_id}/opponent-profile")
def get_opponent_profile(player_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """The cached opponent profile: identity, history, repertoire, statistics."""
    get_player(db, player_id)
    settings = _settings(request)
    return opponent_service.get_opponent_profile(db, int(player_id), settings=settings)


@router.post("/{player_id}/opponent-profile/rebuild")
def rebuild_opponent_profile(player_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """Force a full recomputation of the opponent profile (debugging / bump)."""
    get_player(db, player_id)
    payload = opponent_service.rebuild_player_opponent_profile(
        db, int(player_id), settings=_settings(request)
    )
    logger.info("Rebuilt opponent profile [player=%s analyzed=%s]", player_id, payload.get("analyzed_games"))
    return payload


@router.get("/{player_id}/opponent-games")
def get_opponent_games(
    player_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    color: str | None = Query(None, pattern="^(white|black)$"),
) -> dict:
    """The opponent's game history, newest first, with measured statistics."""
    get_player(db, player_id)
    return opponent_service.get_opponent_games(db, int(player_id), limit=limit, offset=offset, color=color)


@router.get("/{player_id}/repertoire")
def get_repertoire(
    player_id: str,
    db: Session = Depends(get_db),
    color: str = Query("white", pattern="^(white|black)$"),
    recent: bool = Query(False),
) -> dict:
    """What the opponent plays as a colour, evidence-gated by sample size."""
    get_player(db, player_id)
    return opponent_service.get_repertoire(db, int(player_id), color=color, recent=recent)


@router.get("/{player_id}/repertoire/recent")
def get_recent_repertoire(
    player_id: str,
    db: Session = Depends(get_db),
    color: str = Query("white", pattern="^(white|black)$"),
) -> dict:
    """The opponent's repertoire over their most recent games only."""
    get_player(db, player_id)
    return opponent_service.get_repertoire(db, int(player_id), color=color, recent=True)


@router.get("/{player_id}/position-response")
def get_position_response(
    player_id: str,
    db: Session = Depends(get_db),
    fen: str = Query(...),
) -> dict:
    """How the opponent answered one specific position (exact, then loose match)."""
    get_player(db, player_id)
    return opponent_service.get_position_responses(db, int(player_id), fen=_validated_fen(fen))


@router.get("/{player_id}/responses")
def get_responses(player_id: str, db: Session = Depends(get_db)) -> dict:
    """Recurring positions the opponent reaches, with their answers there."""
    player = get_player(db, player_id)
    payload = opponent_service.get_opponent_profile(db, int(player_id))
    return {
        "player_id": int(player_id),
        "identity": opponent_service.identity_for(player).model_dump(mode="json"),
        "methodology_version": OPPONENT_METHODOLOGY_VERSION,
        "position_patterns": payload.get("position_patterns", []),
        "note": (
            "A recurring position is one the opponent reached across at least the structure "
            "gate's number of games; each carries its measured answer distribution."
        ),
    }


@router.get("/{player_id}/tendencies")
def get_tendencies(player_id: str, db: Session = Depends(get_db)) -> dict:
    """The opponent's measured, evidence-gated tendencies."""
    get_player(db, player_id)
    return opponent_service.get_tendencies(db, int(player_id))


@router.get("/{player_id}/phase-statistics")
def get_phase_statistics(
    player_id: str,
    db: Session = Depends(get_db),
    color: str | None = Query(None, pattern="^(white|black)$"),
) -> dict:
    """The opponent's measured performance by game phase."""
    get_player(db, player_id)
    return opponent_service.get_phase_statistics(db, int(player_id), color=color)


@router.get("/{player_id}/preparation-report")
def get_preparation_report(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
    color: str | None = Query(None, pattern="^(white|black)$"),
) -> dict:
    """The composed preparation document: repertoire, tendencies, insights."""
    get_player(db, player_id)
    return opponent_service.get_preparation_report(
        db, int(player_id), color=color, settings=_settings(request)
    )
