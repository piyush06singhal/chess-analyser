"""Persistence repository: save and fetch games, analyses, players.

All operations are explicit about failures: when persistence is disabled or a
query fails, a :class:`RepositoryError` is raised and the API layer maps it
centrally. Fetches of missing entities raise :class:`NotFoundError`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.analysis.game_analyzer import GameAnalysis
from argus.chess_core.models import Game as ChessGame
from argus.shared.errors import NotFoundError, RepositoryError
from argus.shared.logging import get_logger

from argus_api.db.models import (
    AnalysisSession,
    Game,
    GameMove,
    Player,
    PlayerGame,
    PositionAnalysis,
)

logger = get_logger(__name__)


def new_game_id() -> str:
    return str(uuid.uuid4())


def save_game_with_analysis(
    session: Session,
    game: ChessGame,
    analysis: GameAnalysis | None,
    *,
    pgn_text: str | None = None,
    depth: int = 0,
    multipv: int = 1,
    engine: str = "stockfish",
    engine_version: str | None = None,
    duration_seconds: float | None = None,
) -> Game:
    """Persist a parsed game (and its analysis when available) in one transaction.

    Player rows are upserted by name; ``PlayerGame`` links both sides for
    longitudinal profiling. Returns the persisted ORM ``Game``.
    """
    try:
        white = _upsert_player(session, game.white_player.name, game.white_player.title)
        black = _upsert_player(session, game.black_player.name, game.black_player.title)

        orm_game = Game(
            id=new_game_id(),
            white_player_id=white.id,
            black_player_id=black.id,
            white_player_name=game.white_player.name,
            black_player_name=game.black_player.name,
            white_rating=game.white_rating,
            black_rating=game.black_rating,
            result=game.result.value,
            date=game.date,
            event=game.event,
            site=game.site,
            time_control=game.time_control.raw,
            eco_code=game.opening.eco_code,
            opening_name=game.opening.name,
            initial_position=game.initial_position,
            final_position=game.final_position,
            move_count=game.move_count,
            pgn_text=pgn_text,
        )
        session.add(orm_game)
        session.flush()

        for move in game.moves:
            session.add(
                GameMove(
                    game_id=orm_game.id,
                    ply=move.ply,
                    move_number=move.move_number,
                    color=move.color.value,
                    san=move.san,
                    uci=move.uci,
                    fen_before=move.fen_before,
                    fen_after=move.fen_after,
                )
            )
        session.add(
            PlayerGame(player_id=white.id, game_id=orm_game.id, color="white",
                       rating=game.white_rating)
        )
        session.add(
            PlayerGame(player_id=black.id, game_id=orm_game.id, color="black",
                       rating=game.black_rating)
        )
        session.flush()
        logger.info(
            "Saved game %s [moves=%d analyzed=%d]",
            orm_game.id,
            orm_game.move_count,
            len(analysis.moves) if analysis else 0,
        )
        return orm_game
    except Exception as exc:
        raise RepositoryError(f"Failed to save game: {exc}") from exc


def save_analysis(
    session: Session,
    game_id: str,
    analysis: GameAnalysis,
    *,
    depth: int,
    multipv: int,
    engine: str,
    engine_version: str | None,
    duration_seconds: float | None = None,
) -> int:
    """Persist the position analyses of one analysis run.

    Returns the number of analyzed moves stored. Re-running at the same depth
    replaces the previous run (analyses at different depths coexist).
    """
    try:
        session.execute(
            sa_delete(PositionAnalysis).where(
                PositionAnalysis.game_id == game_id, PositionAnalysis.depth == depth
            )
        )
        session.execute(
            sa_delete(AnalysisSession).where(
                AnalysisSession.game_id == game_id, AnalysisSession.depth == depth
            )
        )
        for evaluated in analysis.moves:
            session.add(
                PositionAnalysis(
                    game_id=game_id,
                    ply=evaluated.ply,
                    move_number=evaluated.move_number,
                    color=evaluated.color.value,
                    fen=evaluated.fen_before,
                    played_move=evaluated.uci,
                    best_move=evaluated.best_move_uci,
                    evaluation_before=evaluated.evaluation_before_cp,
                    evaluation_after=evaluated.evaluation_after_cp,
                    evaluation_change=evaluated.evaluation_change_cp,
                    depth=evaluated.depth,
                    multipv=multipv,
                    centipawn_loss=evaluated.centipawn_loss,
                    classification=evaluated.classification.value
                    if evaluated.classification
                    else None,
                    phase=evaluated.phase.value,
                    is_sacrifice=evaluated.is_sacrifice,
                    is_best_move=evaluated.is_best_move,
                    principal_variation=evaluated.principal_variation,
                    raw_features=evaluated.features_before.model_dump()
                    if evaluated.features_before is not None
                    else None,
                    engine=engine,
                    engine_version=engine_version,
                )
            )
        session.add(
            AnalysisSession(
                game_id=game_id,
                engine=engine,
                engine_version=engine_version,
                depth=depth,
                multipv=multipv,
                status="completed",
                positions_analyzed=len(analysis.moves) * 2,
                duration_seconds=duration_seconds,
            )
        )
        session.flush()
        logger.info("Saved %d position analyses for game %s", len(analysis.moves), game_id)
        return len(analysis.moves)
    except Exception as exc:
        raise RepositoryError(f"Failed to save analysis: {exc}") from exc


def get_game(session: Session, game_id: str) -> Game:
    """Fetch a game by id.

    Raises:
        NotFoundError: when the game does not exist.
    """
    game = session.get(Game, game_id)
    if game is None:
        raise NotFoundError(f"Game '{game_id}' not found")
    return game


def list_games(session: Session, *, limit: int = 50, offset: int = 0) -> list[Game]:
    """List recent games (newest first)."""
    try:
        return list(
            session.execute(
                select(Game).order_by(Game.created_at.desc()).limit(limit).offset(offset)
            ).scalars()
        )
    except Exception as exc:
        raise RepositoryError(f"Failed to list games: {exc}") from exc


def get_moves(session: Session, game_id: str) -> list[GameMove]:
    """All moves of a game, ordered by ply."""
    try:
        return list(
            session.execute(
                select(GameMove).where(GameMove.game_id == game_id).order_by(GameMove.ply)
            ).scalars()
        )
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch moves: {exc}") from exc


def get_position_analyses(session: Session, game_id: str) -> list[PositionAnalysis]:
    """All stored position analyses of a game, ordered by ply."""
    try:
        return list(
            session.execute(
                select(PositionAnalysis)
                .where(PositionAnalysis.game_id == game_id)
                .order_by(PositionAnalysis.ply)
            ).scalars()
        )
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch position analyses: {exc}") from exc


def _upsert_player(session: Session, name: str, title: str | None) -> Player:
    existing = session.execute(select(Player).where(Player.name == name)).scalar_one_or_none()
    if existing is not None:
        return existing
    player = Player(name=name, title=title)
    session.add(player)
    session.flush()
    return player
