"""Game routes: import, upload, validate, list, detail, positions, status, delete."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from argus.analysis.game_analyzer import GameAnalyzer
from argus.analysis.reports import build_report
from argus.chess_core.models import AnalysisStatus
from argus.chess_core.pgn import parse_time_control
from argus.analysis.pipeline import build_analysis_config
from argus.importing import available_sources, planned_sources, resolve_importer
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    analyzed_plies,
    delete_game as delete_game_row,
    get_game,
    get_moves,
    get_positions,
    list_games,
    new_game_id,
    resolve_analysis_version,
    save_game_with_analysis,
)
from argus_api.deps import get_db, rate_limited
from argus_api.schemas import (
    GameImportRequest,
    GameImportResponse,
    GameStatusResponse,
    PgnValidateRequest,
    PgnValidationResponse,
)
from argus_api.services.analysis_jobs import AnalysisJobRunner
from argus_api.observability import (
    AUDIT_GAME_DELETED,
    AUDIT_GAME_IMPORTED,
    audit,
    current_caller_id,
)
from argus_api.security import is_open
from argus_api.services.authorization import authorize_game, authorized_for_game
from argus_api.services.uploads import sanitize_filename, validate_upload

logger = get_logger(__name__)
router = APIRouter(prefix="/api/games", tags=["games"])


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


def _game_summary(orm_game) -> dict:  # noqa: ANN001 — ORM row
    return {
        "id": orm_game.id,
        "white_player": orm_game.white_player_name,
        "black_player": orm_game.black_player_name,
        "white_rating": orm_game.white_rating,
        "black_rating": orm_game.black_rating,
        "result": orm_game.result,
        "date": orm_game.date,
        "event": orm_game.event,
        "eco_code": orm_game.eco_code,
        "opening_name": orm_game.opening_name,
        "move_count": orm_game.move_count,
        "analysis_status": orm_game.analysis_status,
        "source": orm_game.source,
        "source_game_id": orm_game.source_game_id,
        "created_at": orm_game.created_at.isoformat() if orm_game.created_at else None,
    }


# --- validation ---------------------------------------------------------------


@router.post("/validate", response_model=PgnValidationResponse)
def validate_game(body: PgnValidateRequest) -> PgnValidationResponse:
    """Validate a PGN payload without importing or analyzing it.

    Returns structured issues (category, game, move number) instead of raw
    exceptions so the frontend can present precise, user-facing messages.
    """
    importer = resolve_importer(body.source)
    report = importer.validate(body.pgn_text)
    return PgnValidationResponse(
        is_valid=report.is_valid,
        game_count=report.game_count,
        ply_count=report.ply_count,
        issues=report.issues,
        errors=report.error_messages,
        available_sources=available_sources(),
        planned_sources=planned_sources(),
    )


# --- import -------------------------------------------------------------------


async def _run_import(
    *,
    request: Request,
    db: Session,
    pgn_text: str,
    source: str,
    run_analysis: bool,
    persist: bool,
    depth: int | None,
    multipv: int | None,
) -> GameImportResponse:
    settings = request.app.state.settings
    importer = resolve_importer(source)
    result = importer.import_games(pgn_text)
    game = result.games[0]

    engine = request.app.state.engine
    engine_info = None
    resolved_depth = depth or settings.engine_depth
    resolved_multipv = multipv or settings.engine_multipv

    # Nothing is persisted, so the in-memory analyzer is the only honest path:
    # the response carries the analysis it produced and nothing is stored.
    if run_analysis and not persist:
        engine_info = engine.info()
        analysis = await asyncio.to_thread(
            GameAnalyzer(engine).analyze, game, depth=resolved_depth, multipv=resolved_multipv
        )
        report = build_report(
            analysis,
            game,
            engine="stockfish",
            engine_version=engine_info.get("version"),
            depth=resolved_depth,
        ).model_copy(update={"game_id": new_game_id()})
        logger.info(
            "Imported game (not persisted) [source=%s moves=%d analyzed=True]",
            source,
            game.move_count,
        )
        return GameImportResponse(
            game_id=report.game_id,
            moves=game.move_count,
            analyzed=True,
            analysis_status=AnalysisStatus.ANALYZED.value,
            source=source,
            warnings=result.warnings,
            analysis=analysis,
            report=report,
            engine=engine_info,
        )

    if persist:
        # Ownership: when authentication is on, the importer owns the game; in an
        # open deployment the game joins the shared library (owner stays NULL), so
        # single-user behaviour is unchanged.
        owner = None if is_open(settings) else current_caller_id()
        orm_game = save_game_with_analysis(
            db,
            game,
            None,
            pgn_text=pgn_text,
            depth=resolved_depth,
            multipv=resolved_multipv,
            engine_version=None,
            duration_seconds=None,
            source=source,
            owner=owner,
        )
        db.commit()
        game_id = orm_game.id
        audit(AUDIT_GAME_IMPORTED, game_id=game_id, source=source, plies=len(game.moves))
    else:
        game_id = new_game_id()

    if run_analysis and persist:
        # One analysis path. The Phase 3 pipeline is what writes the per-move rows,
        # the critical positions and the run record that every reader (report,
        # training, scenarios, coaching, opponents) resolves against — so a game
        # imported *with* analysis is immediately readable, rather than being
        # marked analyzed while only legacy position rows exist.
        config = build_analysis_config(
            settings.analysis_profile,
            depth=resolved_depth,
            multipv=resolved_multipv,
            movetime_ms=settings.engine_movetime_ms or None,
        )
        runner = AnalysisJobRunner(
            request.app.state.session_factory, engine, settings
        )
        await asyncio.to_thread(runner.run, game_id, config=config)
        db.expire_all()
        orm_game = get_game(db, game_id)
        engine_info = engine.info()

    status = orm_game.analysis_status if persist else AnalysisStatus.READY.value
    logger.info(
        "Imported game %s [source=%s moves=%d analyzed=%s status=%s]",
        game_id,
        source,
        game.move_count,
        status == AnalysisStatus.ANALYZED.value,
        status,
    )
    return GameImportResponse(
        game_id=game_id,
        moves=game.move_count,
        analyzed=status == AnalysisStatus.ANALYZED.value,
        analysis_status=status,
        source=source,
        warnings=result.warnings,
        analysis=None,
        report=None,
        engine=engine_info,
    )


@router.post(
    "/import", response_model=GameImportResponse, dependencies=[Depends(rate_limited("import"))]
)
async def import_game(
    body: GameImportRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> GameImportResponse:
    """Import a pasted PGN game: parse → validate → (optionally) analyze → persist."""
    return await _run_import(
        request=request,
        db=db,
        pgn_text=body.pgn_text,
        source=body.source,
        run_analysis=body.run_analysis,
        persist=body.persist,
        depth=body.depth,
        multipv=body.multipv,
    )


@router.post(
    "/import/file",
    response_model=GameImportResponse,
    dependencies=[Depends(rate_limited("upload"))],
)
async def import_game_file(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    run_analysis: bool = True,
    persist: bool = True,
    depth: int | None = None,
    multipv: int | None = None,
    db: Session = Depends(get_db),
) -> GameImportResponse:
    """Import a PGN file upload (multipart).

    The upload is validated for extension, MIME type and size before parsing;
    its content is treated as data only and is never executed or stored on a
    user-controlled path.
    """
    data = await file.read()
    try:
        pgn_text = validate_upload(
            filename=file.filename, content_type=file.content_type, data=data
        )
    finally:
        await file.close()

    logger.info("Received PGN upload %r (%d bytes)", sanitize_filename(file.filename), len(data))
    return await _run_import(
        request=request,
        db=db,
        pgn_text=pgn_text,
        source="pgn_file",
        run_analysis=run_analysis,
        persist=persist,
        depth=depth,
        multipv=multipv,
    )


# --- library & detail ---------------------------------------------------------


@router.get("")
def get_games(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)) -> dict:
    """List recently imported games (newest first) with their analysis status."""
    games = list_games(db, limit=min(limit, 200), offset=max(offset, 0))
    # Only games the caller may read. An owned game belonging to another caller
    # is filtered out here, so the library never leaks it.
    visible = [game for game in games if authorized_for_game(db, game.id)]
    return {"games": [_game_summary(game) for game in visible], "count": len(visible)}


@router.get("/{game_id}")
def get_game_detail(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Fetch one game with its metadata and moves (404 when missing)."""
    authorize_game(db, game_id)
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
        "analysis_status": game.analysis_status,
        "analysis_depth": game.analysis_depth,
        "analysis_error": game.analysis_error,
        "source": game.source,
        "source_game_id": game.source_game_id,
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


@router.get("/{game_id}/positions")
def get_game_positions(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Return the canonical position sequence of a game (ply 0 = initial)."""
    authorize_game(db, game_id)  # 404 when missing or not the caller's
    positions = get_positions(db, game_id)
    return {
        "game_id": game_id,
        "count": len(positions),
        "positions": [
            {
                "ply": pos.ply,
                "move_number": pos.move_number,
                "side_to_move": pos.side_to_move,
                "fen": pos.fen,
                "san": pos.san,
                "uci": pos.uci,
                "previous_fen": pos.previous_fen,
                "resulting_fen": pos.resulting_fen,
                "is_check": pos.is_check,
                "is_checkmate": pos.is_checkmate,
                "is_stalemate": pos.is_stalemate,
                "is_terminal": pos.is_terminal,
                "terminal_reason": pos.terminal_reason,
            }
            for pos in positions
        ],
    }


@router.get("/{game_id}/status", response_model=GameStatusResponse)
def get_game_status(game_id: str, db: Session = Depends(get_db)) -> GameStatusResponse:
    """Return the canonical analysis lifecycle state of a game."""
    authorize_game(db, game_id)
    game = get_game(db, game_id)
    # Count the plies of the generation reads resolve to (the stored MoveAnalysis
    # rows), so the status agrees with /moves and /progress. Counting the legacy
    # position rows instead reported zero for every game analysed by the pipeline.
    version, _ = resolve_analysis_version(db, game_id)
    positions_analyzed = len(analyzed_plies(db, game_id, version)) if version else 0
    return GameStatusResponse(
        game_id=game.id,
        analysis_status=game.analysis_status,
        analysis_depth=game.analysis_depth,
        positions_analyzed=positions_analyzed,
        analysis_error=game.analysis_error,
        updated_at=game.analysis_updated_at,
    )


@router.delete("/{game_id}", status_code=204)
def delete_game(game_id: str, db: Session = Depends(get_db)) -> None:
    """Delete a game and all data that cascades from it (moves, positions, analyses)."""
    authorize_game(db, game_id)
    delete_game_row(db, game_id)
    # Destructive and security-relevant: record who removed what.
    audit(AUDIT_GAME_DELETED, game_id=game_id)


# --- asynchronous analysis ----------------------------------------------------


@router.post(
    "/{game_id}/analyze",
    status_code=202,
    dependencies=[Depends(rate_limited("analysis"))],
)
def analyze_game_async(
    game_id: str,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Queue a background Stockfish analysis for a game.

    Returns immediately with the new lifecycle state (``analyzing``); the
    frontend polls ``GET /api/games/{id}/status``. The engine run itself is the
    real Stockfish pipeline — nothing is simulated.
    """
    settings = request.app.state.settings
    authorize_game(db, game_id)  # 404 when missing or not the caller's
    config = build_analysis_config(
        settings.analysis_profile,
        depth=settings.engine_depth,
        multipv=settings.analysis_multipv,
        movetime_ms=settings.engine_movetime_ms or None,
    )

    runner = AnalysisJobRunner(
        request.app.state.session_factory, request.app.state.engine, settings
    )
    background.add_task(runner.run, game_id, config=config)

    return {
        "game_id": game_id,
        "analysis_status": AnalysisStatus.ANALYZING.value,
        "depth": config.depth,
        "multipv": config.multipv,
        "movetime_ms": config.movetime_ms,
        "profile": config.profile.value,
    }
