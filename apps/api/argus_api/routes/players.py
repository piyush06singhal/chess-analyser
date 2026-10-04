"""Player Intelligence routes (Phase 5).

Read-mostly by design: the profile is a derived, versioned snapshot, so the
routes return what is stored and rebuild only when the inputs changed (or when
``/rebuild`` is called explicitly).

Every response carries its coverage and sample sizes; there is no endpoint that
returns a player-level claim without the evidence that supports it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from argus.player_intelligence import PlayerIntelligenceService, PlayerProfile
from argus.shared.logging import get_logger

from argus_api.db.repository import list_players
from argus_api.deps import get_db
from argus_api.services.player_profile_service import (
    build_inputs,
    get_player_profile,
    policy_from_settings,
    rebuild_player_profile,
)
from argus_api.db.repository import get_player, player_game_rows

logger = get_logger(__name__)
router = APIRouter(prefix="/api/players", tags=["players"])


def _settings(request: Request):  # noqa: ANN202 — Settings, avoids importing it here
    return getattr(request.app.state, "settings", None)


@router.get("")
def list_all_players(request: Request, db: Session = Depends(get_db)) -> dict:
    """Every player with imported vs. analyzed game counts.

    This is the honest list: a player with games imported but not analyzed is
    shown with ``analyzed_games: 0`` rather than being omitted.
    """
    players = list_players(db)
    return {"players": players, "count": len(players)}


@router.get("/{player_id}")
def get_player_detail(player_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """The stored (or rebuilt) profile snapshot for one player."""
    settings = _settings(request)
    policy = policy_from_settings(settings)
    max_games = getattr(settings, "player_profile_max_games", None)
    payload = get_player_profile(db, player_id, policy=policy, max_games=max_games)
    return payload


@router.post("/{player_id}/rebuild")
def rebuild_profile(player_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """Force a full recomputation of the profile (debugging / methodology bump)."""
    settings = _settings(request)
    policy = policy_from_settings(settings)
    max_games = getattr(settings, "player_profile_max_games", None)
    payload = rebuild_player_profile(db, player_id, policy=policy, max_games=max_games)
    logger.info(
        "Rebuilt player profile [player=%s analyzed=%s]", player_id, payload.get("analyzed_games")
    )
    return payload


@router.get("/{player_id}/insights")
def get_insights(player_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """Insights only — each with claim level, sample size and evidence refs."""
    settings = _settings(request)
    policy = policy_from_settings(settings)
    payload = get_player_profile(
        db, player_id, policy=policy, max_games=getattr(settings, "player_profile_max_games", None)
    )
    service = PlayerIntelligenceService(PlayerProfile.model_validate(payload))
    return service.get_player_insights()


@router.get("/{player_id}/evidence")
def get_evidence(
    player_id: str,
    request: Request,
    insight_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Evidence for one insight (or all): game, ply and move behind every claim."""
    settings = _settings(request)
    policy = policy_from_settings(settings)
    payload = get_player_profile(
        db, player_id, policy=policy, max_games=getattr(settings, "player_profile_max_games", None)
    )
    service = PlayerIntelligenceService(PlayerProfile.model_validate(payload))
    return service.get_player_evidence(insight_id)


@router.get("/{player_id}/features")
def get_features(player_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """The ML-ready feature set (user-specific; not training-eligible).

    Exposed so a later phase can build on a stable contract — Phase 5 itself
    trains nothing.
    """
    settings = _settings(request)
    policy = policy_from_settings(settings)
    player = get_player(db, player_id)
    rows = player_game_rows(db, player.id)
    inputs, _excluded = build_inputs(rows, policy=policy)
    payload = get_player_profile(
        db, player_id, policy=policy, max_games=getattr(settings, "player_profile_max_games", None)
    )
    service = PlayerIntelligenceService(PlayerProfile.model_validate(payload), inputs)
    return service.get_player_features().model_dump(mode="json")
