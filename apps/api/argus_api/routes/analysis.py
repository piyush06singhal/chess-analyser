"""Analysis routes: position analysis, game analysis, stored analyses."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from argus.analysis.engine.stockfish import StockfishEngine
from argus.analysis.game_analyzer import GameAnalyzer
from argus.analysis.reports import build_report
from argus.shared.logging import get_logger

from argus_api.db.repository import get_game, get_moves, get_position_analyses, save_analysis
from argus_api.deps import get_db
from argus_api.routes.games import _chess_game_from_orm  # shared ORM → domain converter
from argus_api.schemas import GameAnalysisRequest, GameAnalysisResponse, PositionAnalysisRequest

logger = get_logger(__name__)
router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.post("/position")
async def analyze_position(body: PositionAnalysisRequest, request: Request) -> dict:
    """Send a position to Stockfish and return a structured analysis."""
    engine: StockfishEngine = request.app.state.engine
    analysis = await asyncio.to_thread(
        engine.analyze_position, body.fen, depth=body.depth, multipv=body.multipv
    )
    return analysis.model_dump()


@router.post("/game", response_model=GameAnalysisResponse)
async def analyze_game(
    body: GameAnalysisRequest, request: Request, db: Session = Depends(get_db)
) -> GameAnalysisResponse:
    """Run the full deterministic analysis for a stored game and persist it."""
    settings = request.app.state.settings
    orm_game = get_game(db, body.game_id)
    moves = get_moves(db, body.game_id)
    game = _chess_game_from_orm(orm_game, moves)

    engine: StockfishEngine = request.app.state.engine
    depth = body.depth or settings.engine_depth
    multipv = body.multipv or settings.engine_multipv

    start = time.perf_counter()
    analysis = await asyncio.to_thread(
        GameAnalyzer(engine).analyze, game, depth=depth, multipv=multipv
    )
    duration = time.perf_counter() - start

    save_analysis(
        db,
        game.id,
        analysis,
        depth=depth,
        multipv=multipv,
        engine="stockfish",
        engine_version=engine.info().get("version"),
        duration_seconds=duration,
    )
    db.commit()

    report = build_report(
        analysis,
        game,
        engine="stockfish",
        engine_version=engine.info().get("version"),
        depth=depth,
    )
    logger.info("Analyzed game %s [moves=%d duration=%.1fs]", game.id, game.move_count, duration)
    return GameAnalysisResponse(
        game_id=game.id, analysis=analysis, report=report, engine=engine.info()
    )


@router.get("/{game_id}")
def get_stored_analysis(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Return previously stored analyses of a game (no engine run).

    Honest empty state: when no analysis is stored, the response says so
    instead of fabricating data.
    """
    get_game(db, game_id)  # 404 when missing
    rows = get_position_analyses(db, game_id)
    return {
        "game_id": game_id,
        "positions_analyzed": len(rows),
        "note": None
        if rows
        else "No stored analysis for this game yet; run POST /api/analysis/game first",
        "analyses": [
            {
                "ply": row.ply,
                "move_number": row.move_number,
                "fen": row.fen,
                "played_move": row.played_move,
                "best_move": row.best_move,
                "evaluation_before": row.evaluation_before,
                "evaluation_after": row.evaluation_after,
                "evaluation_change": row.evaluation_change,
                "depth": row.depth,
                "centipawn_loss": row.centipawn_loss,
                "classification": row.classification,
                "phase": row.phase,
                "is_sacrifice": row.is_sacrifice,
                "principal_variation": row.principal_variation,
                "engine": row.engine,
                "engine_version": row.engine_version,
            }
            for row in rows
        ],
    }

