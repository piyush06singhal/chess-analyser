"""Game routes: import, validate, list, detail."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from argus.analysis.game_analyzer import GameAnalyzer
from argus.analysis.reports import build_report
from argus.chess_core.pgn import parse_first_game, parse_time_control, validate_pgn
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    get_game,
    get_moves,
    list_games,
    new_game_id,
    save_analysis,
    save_game_with_analysis,
)
from argus_api.deps import get_db
from argus_api.schemas import GameImportRequest, GameImportResponse, PgnValidateRequest

logger = get_logger(__name__)
router = APIRouter(prefix="/api/games", tags=["games"])


@router.post("/validate")
def validate_game(body: PgnValidateRequest) -> dict:
    """Validate a PGN string without importing or analyzing it."""
    return validate_pgn(body.pgn_text).model_dump()


def _chess_game_from_orm(orm_game, moves):  # noqa: ANN001 — ORM rows; avoids a db import cycle
    """Rebuild the domain ``ChessGame`` from stored game + move rows."""
    from argus.chess_core.models import (
        Color,
        Game as ChessGame,
        GameMove,
        GameResult,
        OpeningInfo,
        PlayerInfo,
    )

    try:
        result = GameResult(orm_game.result)
    except ValueError:
        result = GameResult.UNKNOWN
    return ChessGame(
        id=orm_game.id,
        white_player=PlayerInfo(name=orm_game.white_player_name),
        black_player=PlayerInfo(name=orm_game.black_player_name),
        white_rating=orm_game.white_rating,
        black_rating=orm_game.black_rating,
        result=result,
        date=orm_game.date,
        event=orm_game.event,
        site=orm_game.site,
        time_control=parse_time_control(orm_game.time_control),
        opening=OpeningInfo(eco_code=orm_game.eco_code, name=orm_game.opening_name),
        moves=[
            GameMove(
                ply=move.ply,
                move_number=move.move_number,
                color=Color(move.color),
                san=move.san,
                uci=move.uci,
                fen_before=move.fen_before,
                fen_after=move.fen_after,
            )
            for move in moves
        ],
        initial_position=orm_game.initial_position,
        final_position=orm_game.final_position,
    )


# --- routes -------------------------------------------------------------------


@router.post("/import", response_model=GameImportResponse)
async def import_game(
    body: GameImportRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> GameImportResponse:
    """Import a PGN game: parse → validate → (optionally) analyze → persist.

    Analysis runs off the event loop in a worker thread; the engine remains
    the authoritative calculation source. Response fields are honest: when
    ``run_analysis`` is false or the engine is unavailable, ``analyzed`` is
    false and no analysis is included.
    """
    settings = request.app.state.settings
    game = parse_first_game(body.pgn_text)  # raises InvalidPgnError → 422

    analysis = None
    engine_info = None
    depth = body.depth or settings.engine_depth
    multipv = body.multipv or settings.engine_multipv
    duration = None

    if body.run_analysis:
        engine = request.app.state.engine
        engine_info = engine.info()
        start = time.perf_counter()
        analysis = await asyncio.to_thread(
            GameAnalyzer(engine).analyze, game, depth=depth, multipv=multipv
        )
        duration = time.perf_counter() - start

    if body.persist:
        orm_game = save_game_with_analysis(
            db,
            game,
            analysis,
            pgn_text=body.pgn_text,
            depth=depth,
            multipv=multipv,
            engine_version=(engine_info or {}).get("version"),
            duration_seconds=duration,
        )
        if analysis is not None:
            save_analysis(
                db,
                orm_game.id,
                analysis,
                depth=depth,
                multipv=multipv,
                engine="stockfish",
                engine_version=(engine_info or {}).get("version"),
                duration_seconds=duration,
            )
        db.commit()
        game_id = orm_game.id
    else:
        game_id = new_game_id()

    report = None
    if analysis is not None:
        report = build_report(
            analysis,
            game,
            engine="stockfish",
            engine_version=(engine_info or {}).get("version"),
            depth=depth,
        )
        report = report.model_copy(update={"game_id": game_id})
        logger.info(
            "Imported game %s [moves=%d analyzed=%s duration=%.1fs]",
            game_id,
            game.move_count,
            analysis is not None,
            duration or 0.0,
        )
    return GameImportResponse(
        game_id=game_id,
        moves=game.move_count,
        analyzed=analysis is not None,
        analysis=analysis,
        report=report,
        engine=engine_info,
    )


@router.get("")
def get_games(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)) -> dict:
    """List recently imported games (newest first)."""
    games = list_games(db, limit=min(limit, 200), offset=max(offset, 0))
    return {
        "games": [
            {
                "id": game.id,
                "white_player": game.white_player_name,
                "black_player": game.black_player_name,
                "white_rating": game.white_rating,
                "black_rating": game.black_rating,
                "result": game.result,
                "date": game.date,
                "event": game.event,
                "opening_name": game.opening_name,
                "move_count": game.move_count,
            }
            for game in games
        ],
        "count": len(games),
    }


@router.get("/{game_id}")
def get_game_detail(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Fetch one game with its moves (404 when missing)."""
    game = get_game(db, game_id)
    moves = get_moves(db, game_id)
    return {
        "id": game.id,
        "white_player": game.white_player_name,
        "black_player": game.black_player_name,
        "white_rating": game.white_rating,
        "black_rating": game.black_rating,
        "result": game.result,
        "date": game.date,
        "event": game.event,
        "site": game.site,
        "time_control": game.time_control,
        "eco_code": game.eco_code,
        "opening_name": game.opening_name,
        "initial_position": game.initial_position,
        "final_position": game.final_position,
        "moves": [
            {
                "ply": move.ply,
                "move_number": move.move_number,
                "color": move.color,
                "san": move.san,
                "uci": move.uci,
                "fen_before": move.fen_before,
                "fen_after": move.fen_after,
            }
            for move in moves
        ],
    }


