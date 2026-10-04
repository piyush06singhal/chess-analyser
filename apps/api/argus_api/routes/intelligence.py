"""Game-intelligence routes (Phase 4).

These endpoints expose the structured intelligence layer. They read the engine
analysis the Phase 3 pipeline already stored and **never run Stockfish**: if a
game has no stored analysis, the endpoint answers ``analysis_required`` (409)
rather than producing sections it cannot support.

The same accessors are the tool surface a future AI agent will call, so the
routes are deliberately thin: one accessor, one response.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from argus.intelligence import (
    REPORT_VERSION,
)
from argus.shared.errors import AnalysisRequiredError, NotFoundError
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    get_game,
    get_game_report,
    get_move_analyses,
)
from argus_api.deps import get_db
from argus_api.services.authorization import authorize_game
from argus_api.services.report_service import (
    build_intelligence,
    generate_and_store_report,
    report_is_current,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


def _served_stored(game_id: str, stored) -> dict:
    """The response envelope for a report served from its stored snapshot."""
    return {
        "game_id": game_id,
        "report_version": stored.report_version,
        "analysis_version": stored.analysis_version,
        "generated_at": stored.generated_at.isoformat() if stored.generated_at else None,
        "source": "stored",
        "report": stored.payload,
    }

#: Assembly lives in the shared report service (Phase 5 materialises reports for
#: analyzed games too); the local alias keeps every accessor below unchanged.
_build_intelligence = build_intelligence


# --- the structured report -----------------------------------------------------


@router.post("/games/{game_id}/report")
def generate_game_report(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Generate (and persist) the structured intelligence report for a game.

    Deterministic and engine-free: it consumes the stored analysis and produces
    the same report for the same data.
    """
    authorize_game(db, game_id)  # 404 before anything is generated
    return generate_and_store_report(db, game_id)


@router.get("/games/{game_id}/report")
def get_stored_game_report(game_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """Return the current report for a game (or 404).

    A stored snapshot is served only while it was produced by this version of
    the intelligence layer; a snapshot written by an older ``report_version`` is
    rebuilt from the stored analysis so a superseded answer is never served.
    ``?refresh=true`` forces a rebuild regardless of version.
    """
    authorize_game(db, game_id)  # 404 when missing or not the caller's
    refresh = str(request.query_params.get("refresh", "")).lower() in {"1", "true", "yes"}
    stored = get_game_report(db, game_id)
    if not refresh and report_is_current(stored):
        return _served_stored(game_id, stored)

    # Missing, explicitly refreshed, or written by an older intelligence
    # version: rebuild from the stored analysis so a stale answer is never
    # served once the detector semantics have moved.
    try:
        payload = generate_and_store_report(db, game_id)
    except AnalysisRequiredError:
        # Cannot rebuild (its analysis is gone). Serve the snapshot we have
        # rather than 409, so a stale-but-real report still reads.
        if stored is not None and stored.payload:
            return _served_stored(game_id, stored)
        raise
    return {
        "game_id": game_id,
        "report_version": payload.get("report_version"),
        "analysis_version": (payload.get("provenance") or {}).get("analysis_version"),
        "generated_at": payload.get("generated_at"),
        "source": "generated",
        "report": payload,
    }


@router.get("/games/{game_id}/report/status")
def get_report_status(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Whether a report exists, without generating one."""
    authorize_game(db, game_id)
    game = get_game(db, game_id)
    stored = get_game_report(db, game_id)
    analyses = get_move_analyses(db, game_id)
    return {
        "game_id": game_id,
        "has_report": stored is not None,
        "report_version": stored.report_version if stored else REPORT_VERSION,
        "analysis_version": stored.analysis_version if stored else None,
        "generated_at": stored.generated_at.isoformat() if stored and stored.generated_at else None,
        "has_analysis": bool(analyses),
        "stored_move_analyses": len(analyses),
        "analysis_status": game.analysis_status,
    }


# --- AI-ready tool endpoints ---------------------------------------------------


@router.get("/games/{game_id}/summary")
def get_game_summary(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Factual summary statements for the game."""
    return _build_intelligence(db, game_id).get_game_summary().model_dump(mode="json")


@router.get("/games/{game_id}/trajectory")
def get_game_trajectory(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Evaluation trajectory, advantage bands and analytical states."""
    return _build_intelligence(db, game_id).get_game_trajectory().model_dump(mode="json")


@router.get("/games/{game_id}/critical-moments")
def get_critical_moments(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Critical positions plus the merged, clickable timeline."""
    return _build_intelligence(db, game_id).get_critical_moments().model_dump(mode="json")


@router.get("/games/{game_id}/move-analysis")
def get_move_analysis(
    game_id: str, ply: int | None = None, db: Session = Depends(get_db)
) -> dict:
    """Stored per-move engine analysis (all plies, or one with ``?ply=``)."""
    intelligence = _build_intelligence(db, game_id)
    result = intelligence.get_move_analysis(ply)
    if isinstance(result, list):
        return {
            "game_id": game_id,
            "count": len(result),
            "moves": [fact.model_dump(mode="json") for fact in result],
        }
    if result is None:
        raise NotFoundError(f"Ply {ply} has no stored analysis for game '{game_id}'")
    return result.model_dump(mode="json")


@router.get("/games/{game_id}/tactical-events")
def get_tactical_events(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Tactical events: confirmed board facts and candidates, clearly separated."""
    return _build_intelligence(db, game_id).get_tactical_events().model_dump(mode="json")


@router.get("/games/{game_id}/positional-events")
def get_positional_events(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Positional features and error candidates."""
    return _build_intelligence(db, game_id).get_positional_events().model_dump(mode="json")


@router.get("/games/{game_id}/phase-analysis")
def get_phase_analysis(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Detected phases, transitions and phase-specific performance."""
    return _build_intelligence(db, game_id).get_phase_analysis().model_dump(mode="json")


@router.get("/games/{game_id}/material-timeline")
def get_material_timeline(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Material snapshots and events measured from the board."""
    return _build_intelligence(db, game_id).get_material_timeline().model_dump(mode="json")


@router.get("/games/{game_id}/accuracy")
def get_accuracy(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Caissa accuracy with its methodology and disclaimer attached."""
    return _build_intelligence(db, game_id).get_accuracy().model_dump(mode="json")


@router.get("/games/{game_id}/forecast")
def get_result_forecast(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Engine-derived outcome forecast, with the curve that produced it.

    A deterministic reading of the stored evaluations — not a trained model.
    """
    return _build_intelligence(db, game_id).get_result_forecast().model_dump(mode="json")


@router.get("/games/{game_id}/player-statistics")
def get_player_statistics(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Single-game statistics per side (never a player profile)."""
    return _build_intelligence(db, game_id).get_player_game_statistics()


@router.get("/games/{game_id}/tools")
def list_tools(game_id: str, db: Session = Depends(get_db)) -> dict:
    """The accessor surface available for the future AI agent."""
    intelligence = _build_intelligence(db, game_id)
    return {
        "game_id": game_id,
        "tools": sorted(intelligence.available_tools().keys()),
        "note": (
            "The intelligence layer produces structured, evidenced data. The explanation "
            "layer (LLM) is a later phase and is not implemented here."
        ),
    }
