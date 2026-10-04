"""Analysis routes: position analysis, MultiPV, game analysis, progress, cancel.

The engine is the sole source of every evaluation; these endpoints only expose
engine-derived, structured data. Natural-language interpretation is not
produced here (that belongs to the intelligence layer).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.orm import Session

from argus.analysis.engine.base import AnalyzedPosition
from argus.analysis.engine.stockfish import StockfishEngine
from argus.analysis.pipeline import build_analysis_config
from argus.chess_core.models import AnalysisStatus
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    analyzed_plies,
    get_critical_positions,
    get_game,
    get_latest_analysis_session,
    get_move_analyses,
    mark_analysis_status,
    resolve_analysis_version,
)
from argus_api.deps import get_db, rate_limited
from argus_api.observability import (
    AUDIT_ANALYSIS_CANCELLED,
    AUDIT_ANALYSIS_STARTED,
    audit,
)
from argus_api.services.authorization import authorize_game
from argus_api.schemas import (
    GameAnalysisStartRequest,
    PositionAnalysisRequest,
    PositionMultiPvRequest,
)
from argus_api.services.analysis_jobs import REGISTRY, AnalysisJobRunner

logger = get_logger(__name__)
router = APIRouter(prefix="/api/analysis", tags=["analysis"])


# --- position analysis --------------------------------------------------------


@router.post("/position", dependencies=[Depends(rate_limited("analysis"))])
async def analyze_position(body: PositionAnalysisRequest, request: Request) -> dict:
    """Send a position to Stockfish and return a structured analysis."""
    engine: StockfishEngine = request.app.state.engine
    analysis = await asyncio.to_thread(
        engine.analyze_position,
        body.fen,
        depth=body.depth,
        multipv=body.multipv,
        movetime_ms=body.movetime_ms,
    )
    return analysis.model_dump()


@router.post("/position/multipv", dependencies=[Depends(rate_limited("analysis"))])
async def analyze_position_multipv(body: PositionMultiPvRequest, request: Request) -> dict:
    """MultiPV analysis: the top ``multipv`` lines for one position."""
    engine: StockfishEngine = request.app.state.engine
    analysis: AnalyzedPosition = await asyncio.to_thread(
        engine.analyze_position_multipv,
        body.fen,
        multipv=body.multipv,
        depth=body.depth,
        movetime_ms=body.movetime_ms,
    )
    return analysis.model_dump()


# The Phase 1/2 synchronous ``POST /api/analysis/game`` and its legacy
# ``GET /api/analysis/{id}`` reader are retired. They persisted (and read) a
# second store — ``PositionAnalysis`` — that no other reader consulted, so an
# analysis could look successful while the report, training, scenarios and
# coaching (which all resolve the Phase 3 ``MoveAnalysis`` rows) saw nothing.
# The single analysis path is now the Phase 3 pipeline below; per-move reads use
# ``GET /api/analysis/games/{id}/moves`` and the full result
# ``GET /api/analysis/games/{id}``.


# --- Phase 3: asynchronous game analysis -------------------------------------


@router.post(
    "/games/{game_id}", status_code=202, dependencies=[Depends(rate_limited("analysis"))]
)
def start_game_analysis(
    game_id: str,
    request: Request,
    background: BackgroundTasks,
    body: GameAnalysisStartRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Queue (or resume) a background analysis run and return its configuration."""
    settings = request.app.state.settings
    authorize_game(db, game_id)  # 404 when missing or not the caller's
    options = body or GameAnalysisStartRequest()
    profile = options.profile or settings.analysis_profile
    config = build_analysis_config(
        profile,
        depth=options.depth if options.depth is not None else settings.engine_depth,
        multipv=options.multipv if options.multipv is not None else settings.analysis_multipv,
        movetime_ms=(
            options.movetime_ms
            if options.movetime_ms is not None
            else (settings.engine_movetime_ms or None)
        ),
    )
    # Mark the game ANALYZING synchronously so progress is observable the
    # instant this returns (the background task cannot win a race with the
    # client's first progress poll).
    mark_analysis_status(db, game_id, AnalysisStatus.ANALYZING.value, depth=config.depth)

    runner = AnalysisJobRunner(
        request.app.state.session_factory, request.app.state.engine, settings
    )
    background.add_task(runner.run, game_id, config=config, resume=options.resume)
    audit(AUDIT_ANALYSIS_STARTED, game_id=game_id, depth=config.depth, resume=options.resume)
    return {
        "game_id": game_id,
        "analysis_status": AnalysisStatus.ANALYZING.value,
        "config": config.describe(),
        "config_label": config.label,
    }


@router.get("/games/{game_id}/progress")
def get_analysis_progress(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Progress of the latest analysis run (honest when none exists)."""
    authorize_game(db, game_id)
    game = get_game(db, game_id)
    run = get_latest_analysis_session(db, game_id)
    # Progress counts the plies stored for *this run's* generation. A resolved
    # "latest complete" read would report the previous generation's total while a
    # fresh run is still filling in, which would look like a stalled analysis.
    if run is not None:
        positions_analyzed = len(analyzed_plies(db, game_id, run.analysis_version))
    else:
        # No session row (e.g. rows written directly by an importer or an older
        # schema version): count the generation reads would actually resolve to,
        # so progress is never a misleading zero when stored rows exist.
        version, _ = resolve_analysis_version(db, game_id)
        positions_analyzed = len(analyzed_plies(db, game_id, version)) if version else 0
    if run is None:
        return {
            "game_id": game_id,
            "analysis_status": game.analysis_status,
            "has_session": False,
            "status": None,
            "current_position": 0,
            "total_positions": max(game.move_count, 0),
            "positions_analyzed": positions_analyzed,
            "error": game.analysis_error,
        }
    return {
        "game_id": game_id,
        "analysis_status": game.analysis_status,
        "has_session": True,
        "status": run.status,
        "current_position": run.current_position,
        "total_positions": run.total_positions,
        "positions_analyzed": positions_analyzed,
        "profile": run.profile,
        "depth": run.depth,
        "multipv": run.multipv,
        "movetime_ms": run.movetime_ms,
        "analysis_version": run.analysis_version,
        "engine": run.engine,
        "engine_version": run.engine_version,
        "duration_seconds": run.duration_seconds,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error": run.error,
    }


@router.get("/games/{game_id}/moves")
def get_game_move_analyses(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Per-move engine analysis (evaluations, CPL, classification, PV)."""
    authorize_game(db, game_id)  # 404 when missing or not the caller's
    version, complete = resolve_analysis_version(db, game_id)
    rows = get_move_analyses(db, game_id, analysis_version=version)
    return {
        "game_id": game_id,
        # The generation actually read from storage. Reporting the constant in this
        # module would claim the current methodology produced rows that an older
        # run wrote.
        "analysis_version": version,
        "analysis_complete": complete,
        "count": len(rows),
        "moves": [
            {
                "ply": row.ply,
                "move_number": row.move_number,
                "mover": row.mover,
                "played_move_uci": row.played_move_uci,
                "played_move_san": row.played_move_san,
                "best_move_uci": row.best_move_uci,
                "best_move_san": row.best_move_san,
                "evaluation_before_cp": row.evaluation_before_cp,
                "evaluation_before_mate": row.evaluation_before_mate,
                "evaluation_after_cp": row.evaluation_after_cp,
                "evaluation_after_mate": row.evaluation_after_mate,
                "evaluation_change_cp": row.evaluation_change_cp,
                "centipawn_loss": row.centipawn_loss,
                "classification": row.classification,
                "is_best_move": row.is_best_move,
                "phase": row.phase,
                "depth": row.depth,
                "principal_variation": row.principal_variation,
            }
            for row in rows
        ],
    }


@router.get("/games/{game_id}/critical-moments")
def get_game_critical_moments(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Candidate critical positions, most severe first (engine-derived facts)."""
    authorize_game(db, game_id)  # 404 when missing or not the caller's
    rows = get_critical_positions(db, game_id)
    return {
        "game_id": game_id,
        "count": len(rows),
        "critical_moments": [
            {
                "ply": row.ply,
                "move_number": row.move_number,
                "color": row.color,
                "san": row.san,
                "fen_before": row.fen_before,
                "evaluation_before_white": row.evaluation_before_white,
                "evaluation_after_white": row.evaluation_after_white,
                "swing_cp": row.swing_cp,
                "classification": row.classification,
                "reason": row.reason,
                "severity": row.severity,
                "severity_score": row.severity_score,
                "is_mate_related": row.is_mate_related,
                "detail": row.detail,
            }
            for row in rows
        ],
    }


@router.get("/games/{game_id}")
def get_game_analysis(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Full stored analysis of a game: session metadata, moves, and criticals."""
    authorize_game(db, game_id)
    game = get_game(db, game_id)
    run = get_latest_analysis_session(db, game_id)
    # Moves and criticals must come from the *same* generation, so the generation
    # is resolved once here and passed to both reads.
    version, complete = resolve_analysis_version(db, game_id)
    moves = get_move_analyses(db, game_id, analysis_version=version)
    criticals = get_critical_positions(db, game_id, analysis_version=version)

    counts: dict[str, int] = {}
    for row in moves:
        if row.classification:
            counts[row.classification] = counts.get(row.classification, 0) + 1

    return {
        "game_id": game_id,
        "analysis_status": game.analysis_status,
        "analysis_version": version,
        "analysis_complete": complete,
        "session": None
        if run is None
        else {
            "status": run.status,
            "profile": run.profile,
            "depth": run.depth,
            "multipv": run.multipv,
            "movetime_ms": run.movetime_ms,
            "engine": run.engine,
            "engine_version": run.engine_version,
            "engine_config": run.engine_config,
            "policy": run.policy,
            "total_positions": run.total_positions,
            "positions_analyzed": run.positions_analyzed,
            "duration_seconds": run.duration_seconds,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "error": run.error,
        },
        "moves_analyzed": len(moves),
        "classification_counts": counts,
        "critical_moments_count": len(criticals),
    }


@router.post("/games/{game_id}/cancel", status_code=202)
def cancel_game_analysis(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Request cancellation of the running analysis for a game.

    Returns whether a run was active. Cancellation is cooperative: the engine
    stops after the current position and the run is marked ``cancelled`` with
    its completed plies preserved.
    """
    authorize_game(db, game_id)
    active = REGISTRY.cancel(game_id)
    audit(AUDIT_ANALYSIS_CANCELLED, game_id=game_id, cancel_requested=active)
    return {"game_id": game_id, "cancel_requested": active}
