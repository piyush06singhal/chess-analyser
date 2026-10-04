"""Persistence repository: save and fetch games, analyses, players.

All operations are explicit about failures: when persistence is disabled or a
query fails, a :class:`RepositoryError` is raised and the API layer maps it
centrally. Fetches of missing entities raise :class:`NotFoundError`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from argus_api.observability import current_caller_id

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from argus.analysis.game_analyzer import GameAnalysis
from argus.chess_core.models import Game as ChessGame
from argus.chess_core.positions import generate_game_positions
from argus.shared.errors import ConflictError, NotFoundError, RepositoryError
from argus.shared.logging import get_logger

from argus_api.db.models import (
    AnalysisSession,
    CriticalPosition,
    EngineConfiguration,
    Game,
    GameMove,
    GamePosition,
    GameReportRecord,
    LiveGameEventRecord,
    LiveGameMoveRecord,
    LiveGameRecord,
    MatchPreparationRecord,
    MoveAnalysis,
    OpponentProfileRecord,
    Player,
    ScenarioRecordRow,
    PlayerGame,
    PlayerProfileRecord,
    StudyCollectionRecord,
    StudyItemRecord,
    TrainingAttempt,
    TrainingPosition as TrainingPositionRecord,
    TrainingSession,
    utcnow,
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
    source: str = "pgn_text",
    source_game_id: str | None = None,
    owner: str | None = None,
) -> Game:
    """Persist a parsed game (and its analysis when available) in one transaction.

    Player rows are upserted by name; ``PlayerGame`` links both sides for
    longitudinal profiling. The full position sequence is generated and stored
    so the engine phase can consume FENs directly. Returns the persisted ORM
    ``Game``.

    ``source_game_id`` is the upstream identity (e.g. the Chess.com game URL).
    It is what makes re-importing a player's games idempotent instead of
    duplicating the library.
    """
    try:
        # The import source doubles as the platform identity when the game came
        # from a platform read; pasted/uploaded PGNs have no platform (None).
        platform = source if source in ("chess_com", "lichess") else None
        white = _upsert_player(
            session,
            game.white_player.name,
            game.white_player.title,
            platform=platform,
            platform_username=game.white_player.name if platform else None,
        )
        black = _upsert_player(
            session,
            game.black_player.name,
            game.black_player.title,
            platform=platform,
            platform_username=game.black_player.name if platform else None,
        )

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
            source=source,
            source_game_id=source_game_id,
            owner=owner,
            analysis_status="analyzed" if analysis is not None else "ready",
            analysis_depth=depth or None,
            analysis_updated_at=utcnow() if analysis is not None else None,
        )
        session.add(orm_game)
        session.flush()

        # Canonical position sequence: initial position + one per move.
        game.id = orm_game.id
        for position in generate_game_positions(game):
            session.add(
                GamePosition(
                    game_id=orm_game.id,
                    ply=position.ply,
                    move_number=position.move_number,
                    side_to_move=position.side_to_move.value,
                    fen=position.fen,
                    san=position.san,
                    uci=position.uci,
                    previous_fen=position.previous_fen,
                    resulting_fen=position.resulting_fen,
                    is_check=position.is_check,
                    is_checkmate=position.is_checkmate,
                    is_stalemate=position.is_stalemate,
                    is_terminal=position.is_terminal,
                    terminal_reason=position.terminal_reason,
                )
            )

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


def get_game(session: Session, game_id: str) -> Game:
    """Fetch a game by id.

    Raises:
        NotFoundError: when the game does not exist.
    """
    game = session.get(Game, game_id)
    if game is None:
        raise NotFoundError(f"Game '{game_id}' not found")
    return game


def find_game_by_source_game_id(
    session: Session, source: str, source_game_id: str
) -> Game | None:
    """Find an already-imported platform game, or ``None``.

    This is the idempotency check behind re-importing a player's games: an
    existing game is reported as *skipped*, never inserted a second time.
    """
    try:
        return session.execute(
            select(Game).where(
                Game.source == source, Game.source_game_id == source_game_id
            )
        ).scalar_one_or_none()
    except Exception as exc:
        raise RepositoryError(f"Failed to look up imported game: {exc}") from exc


def find_games_by_source_game_ids(
    session: Session, source: str, source_game_ids: list[str]
) -> dict[str, Game]:
    """Look up many platform games in one query (avoids N+1 on bulk import)."""
    if not source_game_ids:
        return {}
    try:
        rows = session.execute(
            select(Game).where(
                Game.source == source, Game.source_game_id.in_(source_game_ids)
            )
        ).scalars()
        return {row.source_game_id: row for row in rows if row.source_game_id}
    except Exception as exc:
        raise RepositoryError(f"Failed to look up imported games: {exc}") from exc


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


def list_game_ids(session: Session, *, caller: str | None = None) -> list[str]:
    """The game ids a caller may read, newest first — the authorization allow-list.

    A dedicated id-only query rather than ``list_games``: the caller needs the whole
    set (not a page) and none of the columns, and this runs on the agent's request
    path. Reading 50 games and then asking for more would be both slower and wrong.

    Ownership semantics (Phase 15): a game with no owner is part of the shared
    library and visible to everyone; a game with an owner is visible only to that
    caller. When ``caller`` is ``None`` (an open, single-user deployment) the
    filter is not applied at all, so behaviour is unchanged there.
    """
    try:
        query = select(Game.id).order_by(Game.created_at.desc())
        if caller is not None:
            query = query.where(or_(Game.owner.is_(None), Game.owner == caller))
        return [str(value) for value in session.execute(query).scalars()]
    except Exception as exc:
        raise RepositoryError(f"Failed to list game ids: {exc}") from exc


def game_visible_to(session: Session, game_id: str, caller: str | None) -> bool:
    """Whether a caller may read one game, by the same rule as the allow-list."""
    if caller is None:
        return session.get(Game, game_id) is not None
    try:
        # ``first()`` (not ``scalar_one_or_none``) distinguishes "no such game"
        # from "a game whose owner is NULL": the latter is the shared library and
        # must stay visible, while the former does not exist.
        row = session.execute(select(Game.owner).where(Game.id == game_id)).first()
    except Exception as exc:
        raise RepositoryError(f"Failed to check game visibility: {exc}") from exc
    if row is None:
        return False
    owner = row[0]
    return owner is None or owner == caller


def set_game_owner(session: Session, game_id: str, owner: str | None) -> None:
    """Assign (or clear) a game's owner. Used when ownership is adopted."""
    try:
        session.execute(
            update(Game).where(Game.id == game_id).values(owner=owner)
        )
    except Exception as exc:
        raise RepositoryError(f"Failed to set game owner: {exc}") from exc


def player_id_for_game(session: Session, game_id: str) -> str | None:
    """The tracked player who played in this game, if either side is tracked.

    A game may have a white/black player id (the Caissa-tracked identities) or only
    names (an imported PGN with no matching player row). Returns ``None`` in the
    latter case rather than inventing a player.
    """
    try:
        game = session.execute(
            select(Game.white_player_id, Game.black_player_id).where(Game.id == game_id)
        ).first()
    except Exception as exc:
        raise RepositoryError(f"Failed to resolve the player for '{game_id}': {exc}") from exc
    if game is None:
        return None
    for candidate in game:
        if candidate is not None:
            return str(candidate)
    return None


def get_positions(session: Session, game_id: str) -> list[GamePosition]:
    """All generated positions of a game, ordered by ply (includes ply 0)."""
    try:
        return list(
            session.execute(
                select(GamePosition)
                .where(GamePosition.game_id == game_id)
                .order_by(GamePosition.ply)
            ).scalars()
        )
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch positions: {exc}") from exc


def mark_analysis_status(
    session: Session,
    game_id: str,
    status: str,
    *,
    depth: int | None = None,
    error: str | None = None,
) -> Game:
    """Update a game's analysis lifecycle state and commit.

    Raises:
        NotFoundError: when the game does not exist.
    """
    try:
        game = get_game(session, game_id)
        game.analysis_status = status
        game.analysis_updated_at = utcnow()
        if depth is not None:
            game.analysis_depth = depth
        game.analysis_error = error
        session.commit()
        return game
    except NotFoundError:
        raise
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to update analysis status: {exc}") from exc


def delete_game(session: Session, game_id: str) -> None:
    """Delete a game and everything that cascades from it.

    Raises:
        NotFoundError: when the game does not exist.
    """
    try:
        game = get_game(session, game_id)  # 404 when missing
        session.delete(game)
        session.commit()
        logger.info("Deleted game %s", game_id)
    except NotFoundError:
        raise
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to delete game: {exc}") from exc


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


# --- Phase 3: move analyses, critical positions, sessions ----------------------


def analyzed_plies(session: Session, game_id: str, analysis_version: str) -> set[int]:
    """Plies already analyzed at this analysis version (for resuming a run)."""
    try:
        rows = session.execute(
            select(MoveAnalysis.ply).where(
                MoveAnalysis.game_id == game_id,
                MoveAnalysis.analysis_version == analysis_version,
            )
        ).scalars()
        return set(rows)
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch analyzed plies: {exc}") from exc


def save_move_analysis(
    session: Session,
    game_id: str,
    move,  # AnalyzedMove (duck-typed to keep the repository layer light)
    *,
    analysis_version: str,
    engine: str,
    engine_version: str | None,
) -> None:
    """Insert or update the analysis of a single move (incremental persistence).

    Commits so a partial run is durable; the pipeline calls this per move.
    """
    try:
        existing = session.execute(
            select(MoveAnalysis).where(
                MoveAnalysis.game_id == game_id,
                MoveAnalysis.ply == move.ply,
                MoveAnalysis.analysis_version == analysis_version,
            )
        ).scalar_one_or_none()
        row = existing or MoveAnalysis(
            game_id=game_id, ply=move.ply, analysis_version=analysis_version
        )
        row.move_number = move.move_number
        row.mover = move.color.value
        row.played_move_uci = move.uci
        row.played_move_san = move.san
        row.fen_before = move.fen_before
        row.fen_after = move.fen_after
        row.best_move_uci = move.best_move_uci
        row.best_move_san = move.best_move_san
        row.evaluation_before_cp = move.evaluation_before_cp
        row.evaluation_before_mate = move.evaluation_before_mate
        row.evaluation_after_cp = move.evaluation_after_cp
        row.evaluation_after_mate = move.evaluation_after_mate
        row.evaluation_change_cp = move.evaluation_change_cp
        row.centipawn_loss = move.centipawn_loss
        row.played_eval_cp = getattr(move, "played_eval_cp", None)
        row.played_eval_mate = getattr(move, "played_eval_mate", None)
        row.played_eval_source = getattr(move, "played_eval_source", None)
        row.classification = move.classification.value if move.classification else None
        row.is_best_move = move.is_best_move
        row.phase = move.phase.value if move.phase else None
        row.depth = move.depth
        row.candidate_moves = [
            candidate.model_dump() if hasattr(candidate, "model_dump") else dict(candidate)
            for candidate in (getattr(move, "candidate_moves", None) or [])
        ]
        row.principal_variation = move.principal_variation
        row.engine = engine
        row.engine_version = engine_version
        if existing is None:
            session.add(row)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to save move analysis: {exc}") from exc


def latest_analysis_version(session: Session, game_id: str) -> str | None:
    """Analysis version of the most recently written per-move analysis of a game.

    Re-analyzing a game at a new ``analysis_version`` writes a second generation
    of rows next to the old one (the unique key includes the version). Reads must
    never mix generations: two rows for the same ply is not noise, it is a wrong
    answer, so this resolves which generation is current.
    """
    try:
        return session.execute(
            select(MoveAnalysis.analysis_version)
            .where(MoveAnalysis.game_id == game_id)
            .order_by(MoveAnalysis.created_at.desc(), MoveAnalysis.analysis_version.desc())
            .limit(1)
        ).scalar_one_or_none()
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch latest analysis version: {exc}") from exc


def analysis_generations(session: Session, game_id: str) -> list[tuple[str, int]]:
    """Every stored analysis generation of a game: ``(version, plies covered)``.

    Generations are returned newest first, so the first entry is the most
    recently written one. ``plies covered`` is the number of distinct plies the
    generation has rows for — which is how a run that was interrupted part-way is
    told apart from a complete one.
    """
    try:
        rows = session.execute(
            select(MoveAnalysis.analysis_version, func.count(MoveAnalysis.ply))
            .where(MoveAnalysis.game_id == game_id)
            .group_by(MoveAnalysis.analysis_version)
            .order_by(
                func.max(MoveAnalysis.created_at).desc(),
                MoveAnalysis.analysis_version.desc(),
            )
        ).all()
        return [(str(version), int(count)) for version, count in rows]
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch analysis generations: {exc}") from exc


def resolve_analysis_version(
    session: Session, game_id: str, *, ply_count: int | None = None
) -> tuple[str | None, bool]:
    """The analysis generation reads should use, and whether it is complete.

    The newest generation is not automatically the right one. A re-analysis that
    was cancelled or crashed part-way leaves a *newer but partial* generation
    next to the older complete one; reading the newer one would silently present
    half a game as analysed. So the newest generation that covers **every ply**
    wins, and when none is complete the newest is returned with
    ``complete=False`` so the caller can say so instead of implying a full run.
    """
    generations = analysis_generations(session, game_id)
    if not generations:
        return None, False
    if ply_count is None:
        ply_count = len(get_moves(session, game_id))
    for version, covered in generations:
        if ply_count > 0 and covered >= ply_count:
            return version, True
    return generations[0][0], False


def get_move_analyses(
    session: Session, game_id: str, *, analysis_version: str | None = None
) -> list[MoveAnalysis]:
    """Per-move analyses of a game for **one** analysis version, ordered by ply.

    ``analysis_version`` defaults to the newest *complete* generation, so a game
    that has been analysed more than once returns exactly one row per ply and an
    interrupted re-run cannot shadow the last good analysis. Pass a version
    explicitly to read a specific generation by name.
    """
    try:
        target = analysis_version
        if target is None:
            target, _complete = resolve_analysis_version(session, game_id)
        if target is None:
            return []
        return list(
            session.execute(
                select(MoveAnalysis)
                .where(
                    MoveAnalysis.game_id == game_id,
                    MoveAnalysis.analysis_version == target,
                )
                .order_by(MoveAnalysis.ply)
            ).scalars()
        )
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch move analyses: {exc}") from exc


def replace_critical_positions(
    session: Session,
    game_id: str,
    candidates: list,
    *,
    analysis_version: str,
) -> int:
    """Replace the critical positions of a game for one analysis version.

    Keeps the most severe candidate per ``(ply, reason)``: the table has a unique
    constraint on exactly that key, and a duplicate inside one batch would abort
    the whole insert (and with it the analysis run) instead of being harmless.
    """
    deduped: dict[tuple[int, str], object] = {}
    for candidate in candidates:
        key = (candidate.ply, candidate.reason.value)
        current = deduped.get(key)
        if current is None or candidate.severity_score > current.severity_score:
            deduped[key] = candidate
    candidates = sorted(deduped.values(), key=lambda c: (c.ply, c.reason.value))
    try:
        session.execute(
            sa_delete(CriticalPosition).where(
                CriticalPosition.game_id == game_id,
                CriticalPosition.analysis_version == analysis_version,
            )
        )
        for candidate in candidates:
            session.add(
                CriticalPosition(
                    game_id=game_id,
                    ply=candidate.ply,
                    move_number=candidate.move_number,
                    color=candidate.color.value,
                    san=candidate.san,
                    fen_before=candidate.fen_before,
                    evaluation_before_white=candidate.evaluation_before_white,
                    evaluation_after_white=candidate.evaluation_after_white,
                    swing_cp=candidate.swing_cp,
                    classification=candidate.classification.value
                    if candidate.classification
                    else None,
                    reason=candidate.reason.value,
                    severity=candidate.severity.value,
                    severity_score=candidate.severity_score,
                    is_mate_related=candidate.is_mate_related,
                    detail=candidate.detail,
                    analysis_version=analysis_version,
                )
            )
        session.commit()
        return len(candidates)
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to save critical positions: {exc}") from exc


def get_critical_positions(
    session: Session, game_id: str, *, analysis_version: str | None = None
) -> list[CriticalPosition]:
    """Critical positions of a game for one analysis version, most severe first.

    Defaults to the same generation :func:`get_move_analyses` resolves — the
    newest *complete* one — for the same reason: returning two generations at once
    would report every critical moment twice, and reading a generation other than
    the moves' would pair a move list with critical moments from a different run.
    """
    try:
        target = analysis_version
        if target is None:
            target, _complete = resolve_analysis_version(session, game_id)
        query = select(CriticalPosition).where(CriticalPosition.game_id == game_id)
        if target is not None:
            query = query.where(CriticalPosition.analysis_version == target)
        return list(
            session.execute(
                query.order_by(
                    CriticalPosition.severity_score.desc(), CriticalPosition.ply
                )
            ).scalars()
        )
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch critical positions: {exc}") from exc


def create_analysis_session(
    session: Session,
    game_id: str,
    *,
    engine: str,
    engine_version: str | None,
    depth: int | None,
    multipv: int,
    movetime_ms: int | None,
    profile: str | None,
    analysis_version: str,
    engine_config: dict,
    policy: dict,
    total_positions: int,
) -> AnalysisSession:
    """Create a running analysis session (records the full engine configuration)."""
    try:
        row = AnalysisSession(
            game_id=game_id,
            engine=engine,
            engine_version=engine_version,
            depth=depth,
            multipv=multipv,
            movetime_ms=movetime_ms,
            profile=profile,
            analysis_version=analysis_version,
            engine_config=engine_config,
            policy=policy,
            status="running",
            total_positions=total_positions,
            current_position=0,
            started_at=utcnow(),
        )
        session.add(row)
        session.commit()
        return row
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to create analysis session: {exc}") from exc


def update_analysis_session(
    session: Session,
    session_id: int,
    *,
    status: str | None = None,
    current_position: int | None = None,
    positions_analyzed: int | None = None,
    duration_seconds: float | None = None,
    error: str | None = None,
    engine_version: str | None = None,
) -> None:
    """Update progress/outcome of an analysis session and commit."""
    try:
        row = session.get(AnalysisSession, session_id)
        if row is None:
            return
        if status is not None:
            row.status = status
            if status in {"completed", "failed", "cancelled"}:
                row.completed_at = utcnow()
        if current_position is not None:
            row.current_position = current_position
        if positions_analyzed is not None:
            row.positions_analyzed = positions_analyzed
        if duration_seconds is not None:
            row.duration_seconds = duration_seconds
        if engine_version is not None:
            row.engine_version = engine_version
            if row.engine_config is not None:
                row.engine_config = {**row.engine_config, "engine_version": engine_version}
        if error is not None:
            row.error = error
        session.commit()
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to update analysis session: {exc}") from exc


def get_latest_analysis_session(session: Session, game_id: str) -> AnalysisSession | None:
    """Most recent analysis session of a game, or ``None``."""
    try:
        return session.execute(
            select(AnalysisSession)
            .where(AnalysisSession.game_id == game_id)
            .order_by(AnalysisSession.id.desc())
            .limit(1)
        ).scalar_one_or_none()
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch analysis session: {exc}") from exc


def record_engine_configuration(
    session: Session,
    *,
    config_hash: str,
    engine: str,
    engine_version: str | None,
    analysis_version: str,
    profile: str | None,
    depth: int | None,
    movetime_ms: int | None,
    multipv: int,
    threads: int | None,
    hash_mb: int | None,
    policy: dict,
) -> None:
    """Record an engine configuration once (idempotent by config hash)."""
    try:
        existing = session.execute(
            select(EngineConfiguration).where(
                EngineConfiguration.config_hash == config_hash
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        session.add(
            EngineConfiguration(
                config_hash=config_hash,
                engine=engine,
                engine_version=engine_version,
                analysis_version=analysis_version,
                profile=profile,
                depth=depth,
                movetime_ms=movetime_ms,
                multipv=multipv,
                threads=threads,
                hash_mb=hash_mb,
                policy=policy,
            )
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        # Recording a configuration is idempotent by design. Two analyses can run
        # concurrently (two games, same settings) and both pass the existence
        # check before either commits — the loser hits the unique constraint.
        # That means the row exists, which is exactly the desired end state, so a
        # duplicate is success, not a failure that aborts the whole analysis.
        if "uq_engine_config_hash" in str(exc) or "duplicate key" in str(exc).lower():
            return
        raise RepositoryError(f"Failed to record engine configuration: {exc}") from exc


# --- Phase 4: game intelligence reports ---------------------------------------


def save_game_report(
    session: Session,
    game_id: str,
    payload: dict,
    *,
    report_version: str,
    analysis_version: str,
    engine: str | None,
    engine_version: str | None,
    depth: int | None,
    moves_considered: int,
    evaluated_moves: int,
    generated_at,
) -> GameReportRecord:  # noqa: ANN001 — datetime, keeps the import list short
    """Store a generated intelligence report (replacing an identical version).

    The report keeps its own version key so a report generated from an older
    engine analysis is never silently served for a newer one.
    """
    try:
        session.execute(
            sa_delete(GameReportRecord).where(
                GameReportRecord.game_id == game_id,
                GameReportRecord.report_version == report_version,
                GameReportRecord.analysis_version == analysis_version,
            )
        )
        row = GameReportRecord(
            game_id=game_id,
            report_version=report_version,
            analysis_version=analysis_version,
            engine=engine,
            engine_version=engine_version,
            depth=depth,
            moves_considered=moves_considered,
            evaluated_moves=evaluated_moves,
            payload=payload,
            generated_at=generated_at,
        )
        session.add(row)
        session.commit()
        logger.info(
            "Stored game report for %s [report_version=%s analysis_version=%s]",
            game_id,
            report_version,
            analysis_version,
        )
        return row
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to store game report: {exc}") from exc


def get_game_report(session: Session, game_id: str) -> GameReportRecord | None:
    """Most recently stored intelligence report of a game, or ``None``."""
    try:
        return session.execute(
            select(GameReportRecord)
            .where(GameReportRecord.game_id == game_id)
            .order_by(GameReportRecord.id.desc())
            .limit(1)
        ).scalar_one_or_none()
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch game report: {exc}") from exc


# --- player identity (Phase 5) ------------------------------------------------


def normalize_identity_key(name: str) -> str:
    """Normalize a player name into a matching key.

    Case- and whitespace-insensitive, so ``Piyush1206`` and ``piyush1206 `` are
    the same player. Platform linking refines this key later; today it is what
    stops the same person from becoming two profiles because of a typo in
    capitalization.
    """
    return " ".join(name.split()).casefold()


def _upsert_player(
    session: Session,
    name: str,
    title: str | None,
    *,
    platform: str | None = None,
    platform_username: str | None = None,
) -> Player:
    """Find or create the player row for a name, identity-aware.

    Matching order: exact (platform, platform_username) when known, then the
    normalized ``identity_key``, then the legacy exact name. A matched row is
    enriched (never overwritten with weaker data) with identity fields it was
    missing.
    """
    key = normalize_identity_key(name)
    existing: Player | None = None

    if platform and platform_username:
        existing = session.execute(
            select(Player).where(
                Player.platform == platform,
                Player.platform_username == platform_username,
            )
        ).scalars().first()
    if existing is None:
        existing = session.execute(
            select(Player).where(Player.identity_key == key)
        ).scalars().first()
    if existing is None:
        existing = session.execute(select(Player).where(Player.name == name)).scalars().first()

    if existing is not None:
        changed = False
        if existing.identity_key != key:
            existing.identity_key = key
            changed = True
        if platform and not existing.platform:
            existing.platform = platform
            changed = True
        if platform_username and not existing.platform_username:
            existing.platform_username = platform_username
            changed = True
        if title and not existing.title:
            existing.title = title
            changed = True
        if changed:
            existing.updated_at = utcnow()
        return existing

    player = Player(
        name=name,
        title=title,
        identity_key=key,
        platform=platform,
        platform_username=platform_username,
        updated_at=utcnow(),
    )
    session.add(player)
    session.flush()
    return player


def backfill_player_identities(session: Session) -> int:
    """Give every legacy player row the identity key it should have had.

    Idempotent and additive: it only fills a missing ``identity_key``. It is a
    separate, explicit step (called at startup) rather than DDL, because
    normalization rules belong in Python, not in an ``ALTER TABLE``.
    """
    try:
        rows = session.execute(select(Player).where(Player.identity_key.is_(None))).scalars().all()
        for row in rows:
            row.identity_key = normalize_identity_key(row.name)
        if rows:
            session.commit()
            logger.info("Backfilled identity keys for %d player row(s)", len(rows))
        return len(rows)
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to backfill player identities: {exc}") from exc


def merge_duplicate_players(session: Session) -> list[dict]:
    """Merge player rows that share an identity key into the oldest one.

    Games, player-game links and stored profiles are re-pointed before the
    duplicates are deleted, so no game and no profile is ever lost by a merge.
    Returns a report of what was merged (empty when there was nothing to do).
    """
    try:
        rows = session.execute(select(Player).order_by(Player.id)).scalars().all()
        groups: dict[str, list[Player]] = {}
        for row in rows:
            key = row.identity_key or normalize_identity_key(row.name)
            groups.setdefault(key, []).append(row)

        merged: list[dict] = []
        for key, members in groups.items():
            if len(members) < 2:
                continue
            keeper, duplicates = members[0], members[1:]
            duplicate_ids = [dup.id for dup in duplicates]
            for game in session.execute(
                select(Game).where(
                    Game.white_player_id.in_(duplicate_ids)
                    | Game.black_player_id.in_(duplicate_ids)
                )
            ).scalars().all():
                if game.white_player_id in duplicate_ids:
                    game.white_player_id = keeper.id
                if game.black_player_id in duplicate_ids:
                    game.black_player_id = keeper.id
            for link in session.execute(
                select(PlayerGame).where(PlayerGame.player_id.in_(duplicate_ids))
            ).scalars().all():
                existing = session.execute(
                    select(PlayerGame).where(
                        PlayerGame.player_id == keeper.id,
                        PlayerGame.game_id == link.game_id,
                        PlayerGame.color == link.color,
                    )
                ).scalars().first()
                if existing is not None:
                    session.delete(link)
                else:
                    link.player_id = keeper.id
            for profile in session.execute(
                select(PlayerProfileRecord).where(
                    PlayerProfileRecord.player_id.in_(duplicate_ids)
                )
            ).scalars().all():
                clash = session.execute(
                    select(PlayerProfileRecord).where(
                        PlayerProfileRecord.player_id == keeper.id,
                        PlayerProfileRecord.profile_version == profile.profile_version,
                    )
                ).scalars().first()
                if clash is not None:
                    session.delete(profile)
                else:
                    profile.player_id = keeper.id
            for duplicate in duplicates:
                session.delete(duplicate)
            keeper.identity_key = key
            keeper.updated_at = utcnow()
            merged.append({"identity_key": key, "kept": keeper.id, "removed": duplicate_ids})

        if merged:
            session.commit()
            logger.info("Merged %d duplicate player group(s)", len(merged))
        return merged
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to merge duplicate players: {exc}") from exc


def prune_orphan_players(session: Session) -> list[str]:
    """Delete players left behind by deleted games.

    A player with no game and no link to one is stale identity — exactly what
    would silently corrupt a profile (a name with zero games, or a duplicate
    row that another row's games should have pointed at). Only rows with a
    truly empty history are removed.

    Derived snapshots are deleted in the same statement batch rather than left to
    an ORM cascade: a profile of a player who has no games left is itself stale,
    and deleting it must not depend on loading it first.
    """
    try:
        rows = session.execute(select(Player)).scalars().all()
        removed: list[str] = []
        for row in rows:
            has_game_as_side = session.execute(
                select(Game.id).where(
                    (Game.white_player_id == row.id) | (Game.black_player_id == row.id)
                ).limit(1)
            ).first()
            has_link = session.execute(
                select(PlayerGame.id).where(PlayerGame.player_id == row.id).limit(1)
            ).first()
            if has_game_as_side or has_link:
                continue
            removed.append(f"{row.id}:{row.name}")
            session.execute(
                sa_delete(PlayerProfileRecord).where(PlayerProfileRecord.player_id == row.id)
            )
            session.delete(row)
        if removed:
            session.commit()
            logger.info("Pruned %d orphan player row(s)", len(removed))
        return removed
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to prune orphan players: {exc}") from exc


def list_players(session: Session, *, caller: str | None = None) -> list[dict]:
    """Every player with their game counts (analyzed vs. imported).

    Counts come from the games themselves, so a player with no analyzed game
    still appears with an honest zero rather than being hidden.

    Ownership scoping (Phase 15): the counts only include games the caller may
    read, so a player's win/draw/loss totals cannot reveal the shape of another
    caller's private games. ``caller`` defaults to the request context; ``None``
    (an open deployment) counts everything.
    """
    if caller is None:
        caller = current_caller_id()
    try:
        players = session.execute(select(Player).order_by(Player.name)).scalars().all()
        games_query = select(
            Game.id, Game.white_player_id, Game.black_player_id, Game.result, Game.analysis_status
        )
        if caller is not None:
            games_query = games_query.where(or_(Game.owner.is_(None), Game.owner == caller))
        games = session.execute(games_query).all()

        per_player: dict[int, dict] = {}
        for game_id, white_id, black_id, result, analysis_status in games:
            for player_id, color in ((white_id, "white"), (black_id, "black")):
                if player_id is None:
                    continue
                entry = per_player.setdefault(
                    player_id,
                    {"games": 0, "analyzed": 0, "wins": 0, "draws": 0, "losses": 0, "game_ids": []},
                )
                entry["games"] += 1
                entry["game_ids"].append(game_id)
                if analysis_status == "analyzed":
                    entry["analyzed"] += 1
                if result == "1/2-1/2":
                    entry["draws"] += 1
                elif (color == "white" and result == "1-0") or (color == "black" and result == "0-1"):
                    entry["wins"] += 1
                elif result in ("1-0", "0-1"):
                    entry["losses"] += 1

        summaries: list[dict] = []
        for player in players:
            stats = per_player.get(
                player.id,
                {"games": 0, "analyzed": 0, "wins": 0, "draws": 0, "losses": 0, "game_ids": []},
            )
            summaries.append(
                {
                    "id": str(player.id),
                    "name": player.name,
                    "title": player.title,
                    "platform": player.platform,
                    "platform_username": player.platform_username,
                    "games": stats["games"],
                    "analyzed_games": stats["analyzed"],
                    "wins": stats["wins"],
                    "draws": stats["draws"],
                    "losses": stats["losses"],
                }
            )
        return summaries
    except Exception as exc:
        raise RepositoryError(f"Failed to list players: {exc}") from exc


def get_player(session: Session, player_id: str) -> Player:
    """One player row by id (404 semantics for callers)."""
    try:
        player = session.get(Player, int(player_id))
    except (TypeError, ValueError) as exc:
        raise NotFoundError(f"Player '{player_id}' not found") from exc
    if player is None:
        raise NotFoundError(f"Player '{player_id}' not found")
    return player


def player_game_rows(
    session: Session, player_id: int, *, caller: str | None = None
) -> list[dict]:
    """Every game of one player with the metadata a profile needs.

    Returns rows (not ORM objects) with the player's colour, opponent, ratings,
    result, time control, opening and the stored report payload — everything the
    Phase 5 mapper turns into ``PlayerGameInput``.

    Ownership scoping (Phase 15): only games the caller may read are included, so
    a player profile cannot aggregate analysis from another caller's private
    game. ``caller`` is resolved from the request context when not given; ``None``
    (an open deployment) applies no filter.
    """
    if caller is None:
        caller = current_caller_id()
    try:
        query = select(Game).where(
            (Game.white_player_id == player_id) | (Game.black_player_id == player_id)
        )
        if caller is not None:
            query = query.where(or_(Game.owner.is_(None), Game.owner == caller))
        games = session.execute(query.order_by(Game.created_at)).scalars().all()
        rows: list[dict] = []
        for game in games:
            color = "white" if game.white_player_id == player_id else "black"
            opponent = game.black_player_name if color == "white" else game.white_player_name
            report = get_game_report(session, game.id)
            moves = session.execute(
                select(GameMove.san, GameMove.ply, GameMove.color)
                .where(GameMove.game_id == game.id)
                .order_by(GameMove.ply)
            ).all()
            rows.append(
                {
                    "game_id": game.id,
                    "color": color,
                    "opponent": opponent,
                    "player_rating": game.white_rating if color == "white" else game.black_rating,
                    "opponent_rating": game.black_rating if color == "white" else game.white_rating,
                    "result": game.result,
                    "date": game.date,
                    "time_control": game.time_control,
                    "eco_code": game.eco_code,
                    "opening_name": game.opening_name,
                    "move_count": game.move_count,
                    "source": game.source,
                    "analysis_status": game.analysis_status,
                    "analysis_depth": game.analysis_depth,
                    "analysis_updated_at": game.analysis_updated_at.isoformat()
                    if game.analysis_updated_at
                    else None,
                    "report_version": report.report_version if report else None,
                    "analysis_version": report.analysis_version if report else None,
                    "engine": report.engine if report else None,
                    "engine_version": report.engine_version if report else None,
                    "report_payload": report.payload if report else None,
                    "moves": [{"san": san, "ply": ply, "color": move_color} for san, ply, move_color in moves],
                }
            )
        return rows
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch player games: {exc}") from exc


def player_input_signature(rows: list[dict]) -> str:
    """Fingerprint of the inputs a profile was built from.

    Any new analysis, a changed report version or a changed analysis version
    changes the signature — which is how a stored snapshot is detected as
    stale without re-deriving the whole profile on every request.
    """
    analyzed = [row for row in rows if row.get("report_payload")]
    latest = max((row.get("analysis_updated_at") or "" for row in analyzed), default="")
    versions = sorted({f"{row.get('report_version')}/{row.get('analysis_version')}" for row in analyzed})
    return f"analyzed:{len(analyzed)}|total:{len(rows)}|latest:{latest}|versions:{','.join(versions)}"


def save_player_profile(
    session: Session,
    *,
    player_id: int,
    profile_version: str,
    methodology_version: str,
    feature_version: str,
    source_signature: str,
    imported_games: int,
    analyzed_games: int,
    coverage: str,
    payload: dict,
    generated_at,
) -> PlayerProfileRecord:  # noqa: ANN001 — datetime, keeps the import list short
    """Store (or replace) the profile snapshot for a player + profile version."""
    generated_at = generated_at or utcnow()
    try:
        existing = session.execute(
            select(PlayerProfileRecord).where(
                PlayerProfileRecord.player_id == player_id,
                PlayerProfileRecord.profile_version == profile_version,
            )
        ).scalars().first()
        if existing is None:
            existing = PlayerProfileRecord(
                player_id=player_id,
                profile_version=profile_version,
                methodology_version=methodology_version,
                feature_version=feature_version,
                source_signature=source_signature,
                imported_games=imported_games,
                analyzed_games=analyzed_games,
                coverage=coverage,
                payload=payload,
                generated_at=generated_at,
                updated_at=utcnow(),
            )
            session.add(existing)
        else:
            existing.methodology_version = methodology_version
            existing.feature_version = feature_version
            existing.source_signature = source_signature
            existing.imported_games = imported_games
            existing.analyzed_games = analyzed_games
            existing.coverage = coverage
            existing.payload = payload
            existing.generated_at = generated_at
            existing.updated_at = utcnow()
        session.commit()
        return existing
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to store player profile: {exc}") from exc


def get_player_profile_record(
    session: Session, player_id: int, *, profile_version: str | None = None
) -> PlayerProfileRecord | None:
    """Most recent stored profile snapshot for a player (optionally by version)."""
    try:
        query = select(PlayerProfileRecord).where(PlayerProfileRecord.player_id == player_id)
        if profile_version:
            query = query.where(PlayerProfileRecord.profile_version == profile_version)
        return session.execute(
            query.order_by(PlayerProfileRecord.updated_at.desc()).limit(1)
        ).scalar_one_or_none()
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch player profile: {exc}") from exc


def delete_player_profiles(session: Session, player_id: int) -> int:
    """Drop every stored snapshot of a player (used before a clean rebuild)."""
    try:
        result = session.execute(
            sa_delete(PlayerProfileRecord).where(PlayerProfileRecord.player_id == player_id)
        )
        session.commit()
        return int(result.rowcount or 0)
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to delete player profiles: {exc}") from exc


# --- Phase 8: training engine -------------------------------------------------
#
# The training tables are written once (generation) and read forever (dashboards,
# sessions, reviews). Nothing here runs an engine: exercises arrive with their
# solution already verified, so every read below is a pure database operation
# (spec §44).


def training_dedupe_keys(
    session: Session, player_id: int | None, *, position_type: str | None = None
) -> set[str]:
    """Normalized FENs already used as exercises for this player (dedupe gate).

    ``player_id=None`` matches GENERAL (global) exercises, which dedupe among
    themselves exactly like personalized ones do. A NULL-safe comparison is
    required: ``== None`` is never true in SQL, which would silently disable the
    whole gate and let every position duplicate.

    ``position_type`` scopes the gate to one exercise *format*. The single-move
    generator leaves it unset (a position is used once, whichever single-move
    format claimed it); the whole-game formats pass their own type, because a
    WHAT_WENT_WRONG review of a mistake and the puzzle for that mistake are
    different exercises carrying different evidence.
    """
    try:
        condition = (
            TrainingPositionRecord.player_id.is_(None)
            if player_id is None
            else TrainingPositionRecord.player_id == player_id
        )
        query = select(TrainingPositionRecord.source_fen_normalized).where(condition)
        if position_type is not None:
            query = query.where(TrainingPositionRecord.position_type == position_type)
        rows = session.execute(query).scalars()
        return {str(value) for value in rows if value}
    except Exception as exc:
        raise RepositoryError(f"Failed to load training dedupe keys: {exc}") from exc


def training_categories_by_fen(session: Session, player_id: int | None) -> dict[str, str]:
    """Existing exercises for this player, keyed by normalized FEN → category.

    Used to keep one position's category consistent across exercise formats: the
    category belongs to the position, and the format that stored it first
    assigned it from that position's evidence.
    """
    try:
        condition = (
            TrainingPositionRecord.player_id.is_(None)
            if player_id is None
            else TrainingPositionRecord.player_id == player_id
        )
        rows = session.execute(
            select(
                TrainingPositionRecord.source_fen_normalized,
                TrainingPositionRecord.category,
            ).where(condition)
        ).all()
        return {str(fen): str(category) for fen, category in rows if fen}
    except Exception as exc:
        raise RepositoryError(f"Failed to load training categories: {exc}") from exc


def save_training_position(
    session: Session, payload: dict, *, position_type: str | None = None
) -> TrainingPositionRecord:
    """Insert one training position, idempotent on its normalized FEN.

    ``payload`` is the package model dumped to a plain dict by the service layer
    (keeps the repository free of the training package and avoids the
    ``TrainingPosition`` name collision between the ORM and pydantic models).
    Returns the existing row when the FEN is already stored — scoped to
    ``position_type`` when one is given, matching the store's uniqueness.
    """
    normalized = payload.get("source_fen_normalized") or ""
    player_id = payload.get("player_id")
    try:
        condition = (
            TrainingPositionRecord.player_id.is_(None)
            if player_id is None
            else TrainingPositionRecord.player_id == player_id
        )
        query = select(TrainingPositionRecord).where(
            condition,
            TrainingPositionRecord.source_fen_normalized == normalized,
        )
        if position_type is not None:
            query = query.where(TrainingPositionRecord.position_type == position_type)
        existing = session.execute(query).scalars().first()
        if existing is not None:
            return existing
        row = TrainingPositionRecord(**payload)
        session.add(row)
        session.commit()
        return row
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to save training position: {exc}") from exc


def get_training_position(session: Session, position_id: int) -> TrainingPositionRecord:
    """One training position by id.

    Raises:
        NotFoundError: when the exercise does not exist.
    """
    row = session.get(TrainingPositionRecord, position_id)
    if row is None:
        raise NotFoundError(f"Training position {position_id} not found")
    return row


def list_training_positions(
    session: Session,
    *,
    player_id: int | None = None,
    include_general: bool = True,
    category: str | None = None,
    state: str | None = None,
    source_game_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[TrainingPositionRecord]:
    """The training library for a player (their exercises plus shared GENERAL ones).

    Ordering is newest first so a freshly generated batch is visible immediately.
    """
    try:
        conditions = []
        if player_id is not None:
            condition = TrainingPositionRecord.player_id == player_id
            if include_general:
                condition = condition | TrainingPositionRecord.player_id.is_(None)
            conditions.append(condition)
        if category:
            conditions.append(TrainingPositionRecord.category == category)
        if state:
            conditions.append(TrainingPositionRecord.state == state)
        if source_game_id:
            conditions.append(TrainingPositionRecord.source_game_id == source_game_id)
        query = select(TrainingPositionRecord)
        if conditions:
            query = query.where(*conditions)
        query = query.order_by(TrainingPositionRecord.created_at.desc(), TrainingPositionRecord.id.desc())
        if offset:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        return list(session.execute(query).scalars())
    except Exception as exc:
        raise RepositoryError(f"Failed to list training positions: {exc}") from exc


def update_training_position_state(
    session: Session,
    position_id: int,
    *,
    state: str,
    attempts: int,
    correct_attempts: int,
    streak: int,
    review_interval_days: float,
    next_review_at,
    last_attempted_at,
) -> TrainingPositionRecord:  # noqa: ANN001 — datetimes, keeps the import list short
    """Persist the scheduler's decision for one exercise."""
    try:
        row = get_training_position(session, position_id)
        row.state = state
        row.attempts = attempts
        row.correct_attempts = correct_attempts
        row.streak = streak
        row.review_interval_days = review_interval_days
        row.next_review_at = next_review_at
        row.last_attempted_at = last_attempted_at
        row.updated_at = utcnow()
        session.commit()
        return row
    except NotFoundError:
        raise
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to update training position state: {exc}") from exc


def save_training_attempt(
    session: Session,
    *,
    player_id: int,
    training_position_id: int,
    session_id: int | None,
    submitted_uci: str,
    submitted_san: str | None,
    correctness: str,
    submitted_eval_cp: int | None,
    evaluation_delta_cp: int | None,
    hints_used: int,
    response_time_ms: int | None,
) -> TrainingAttempt:
    """Store one attempt — permanently (spec §24: never only final scores)."""
    try:
        row = TrainingAttempt(
            player_id=player_id,
            training_position_id=training_position_id,
            session_id=session_id,
            submitted_uci=submitted_uci,
            submitted_san=submitted_san,
            correctness=correctness,
            submitted_eval_cp=submitted_eval_cp,
            evaluation_delta_cp=evaluation_delta_cp,
            hints_used=hints_used,
            response_time_ms=response_time_ms,
        )
        session.add(row)
        session.commit()
        return row
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to save training attempt: {exc}") from exc


def list_training_attempts(
    session: Session,
    *,
    player_id: int | None = None,
    training_position_id: int | None = None,
    session_id: int | None = None,
    limit: int | None = None,
) -> list[TrainingAttempt]:
    """Attempt history, newest first."""
    try:
        conditions = []
        if player_id is not None:
            conditions.append(TrainingAttempt.player_id == player_id)
        if training_position_id is not None:
            conditions.append(TrainingAttempt.training_position_id == training_position_id)
        if session_id is not None:
            conditions.append(TrainingAttempt.session_id == session_id)
        query = select(TrainingAttempt)
        if conditions:
            query = query.where(*conditions)
        query = query.order_by(TrainingAttempt.created_at.desc(), TrainingAttempt.id.desc())
        if limit is not None:
            query = query.limit(limit)
        return list(session.execute(query).scalars())
    except Exception as exc:
        raise RepositoryError(f"Failed to list training attempts: {exc}") from exc


def create_training_session(
    session: Session,
    *,
    player_id: int,
    kind: str,
    target_category: str | None,
    planned_position_ids: list[int],
) -> TrainingSession:
    """Create a resumable training session (spec §19)."""
    try:
        row = TrainingSession(
            player_id=player_id,
            kind=kind,
            target_category=target_category,
            planned_position_ids=list(planned_position_ids),
            completed_position_ids=[],
            status="active",
        )
        session.add(row)
        session.commit()
        return row
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to create training session: {exc}") from exc


def get_training_session(session: Session, session_id: int) -> TrainingSession:
    """One training session by id.

    Raises:
        NotFoundError: when the session does not exist.
    """
    row = session.get(TrainingSession, session_id)
    if row is None:
        raise NotFoundError(f"Training session {session_id} not found")
    return row


def update_training_session(
    session: Session,
    session_id: int,
    *,
    completed_position_ids: list[int] | None = None,
    correct_count: int | None = None,
    near_best_count: int | None = None,
    incorrect_count: int | None = None,
    hints_used: int | None = None,
    status: str | None = None,
    completed_at=None,  # noqa: ANN001 — datetime, keeps the import list short
) -> TrainingSession:
    """Update a session's progress/outcome and commit."""
    try:
        row = get_training_session(session, session_id)
        if completed_position_ids is not None:
            row.completed_position_ids = list(completed_position_ids)
        if correct_count is not None:
            row.correct_count = correct_count
        if near_best_count is not None:
            row.near_best_count = near_best_count
        if incorrect_count is not None:
            row.incorrect_count = incorrect_count
        if hints_used is not None:
            row.hints_used = hints_used
        if status is not None:
            row.status = status
            if status in {"completed", "cancelled"}:
                row.completed_at = completed_at or utcnow()
        session.commit()
        return row
    except NotFoundError:
        raise
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to update training session: {exc}") from exc


def list_training_sessions(
    session: Session,
    *,
    player_id: int,
    status: str | None = None,
    limit: int | None = None,
) -> list[TrainingSession]:
    """A player's sessions, newest first."""
    try:
        query = select(TrainingSession).where(TrainingSession.player_id == player_id)
        if status:
            query = query.where(TrainingSession.status == status)
        query = query.order_by(TrainingSession.started_at.desc(), TrainingSession.id.desc())
        if limit is not None:
            query = query.limit(limit)
        return list(session.execute(query).scalars())
    except Exception as exc:
        raise RepositoryError(f"Failed to list training sessions: {exc}") from exc


def delete_training_positions_for_game(session: Session, game_id: str) -> int:
    """Remove the exercises derived from one game (used when regenerating).

    Attempts cascade with their position (``ON DELETE CASCADE``), which is the
    intended behaviour for *regeneration* — the exercise is being replaced, not
    abandoned. Deleting a whole game instead keeps the exercises and only marks
    their source unavailable (``ON DELETE SET NULL``), so history survives.
    """
    try:
        result = session.execute(
            sa_delete(TrainingPositionRecord).where(
                TrainingPositionRecord.source_game_id == game_id
            )
        )
        session.commit()
        return int(result.rowcount or 0)
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to delete training positions: {exc}") from exc


# =============================================================================
# Phase 9: opponent intelligence
# =============================================================================


def opponent_game_rows(
    session: Session, player_id: int, *, caller: str | None = None
) -> list[dict]:
    """Every game of one player with the stored analysis an opponent report needs.

    This is the Phase 9 read primitive. It reuses the existing Game / PlayerGame
    / MoveAnalysis tables — there is no second player-identity system and no
    duplicated game data. Each row carries the subject's colour, the opponent's
    name and rating, the result, and the stored per-move analysis (all colours,
    so a position's move-prefix can be rendered).

    A game with no stored analysis is returned too, with ``moves: []`` and
    ``analysis_status`` telling the caller it cannot contribute move evidence.

    Ownership scoping (Phase 15): only games the caller may read are included, so
    an opponent report cannot aggregate analysis from another caller's private
    game. ``caller`` defaults to the request context; ``None`` applies no filter.
    """
    if caller is None:
        caller = current_caller_id()
    try:
        query = select(Game).where(
            (Game.white_player_id == player_id) | (Game.black_player_id == player_id)
        )
        if caller is not None:
            query = query.where(or_(Game.owner.is_(None), Game.owner == caller))
        games = session.execute(query.order_by(Game.created_at)).scalars().all()
        rows: list[dict] = []
        for game in games:
            color = "white" if game.white_player_id == player_id else "black"
            opponent_name = game.black_player_name if color == "white" else game.white_player_name
            opponent_rating = game.black_rating if color == "white" else game.white_rating
            other_rating = game.white_rating if color == "white" else game.black_rating
            analyses = get_move_analyses(session, game.id)
            moves: list[dict] = []
            for row in analyses:
                moves.append(
                    {
                        "ply": row.ply,
                        "move_number": row.move_number,
                        "color": row.mover,
                        "san": row.played_move_san,
                        "uci": row.played_move_uci,
                        "fen_before": row.fen_before,
                        "phase": row.phase,
                        "classification": row.classification,
                        "centipawn_loss": row.centipawn_loss,
                        "is_best_move": bool(row.is_best_move),
                        "best_move_uci": row.best_move_uci,
                        "best_move_san": row.best_move_san,
                        "evaluation_before_cp": row.evaluation_before_cp,
                        "evaluation_before_mate": row.evaluation_before_mate,
                        "candidate_moves": list(row.candidate_moves or []),
                        "principal_variation": list(row.principal_variation or []),
                    }
                )
            first = analyses[0] if analyses else None
            rows.append(
                {
                    "game_id": game.id,
                    "color": color,
                    "opponent_name": opponent_name,
                    "opponent_rating": opponent_rating,
                    "other_rating": other_rating,
                    "result": game.result,
                    "date": game.date,
                    "event": game.event,
                    "time_control": game.time_control,
                    "eco_code": game.eco_code,
                    "opening_name": game.opening_name,
                    "move_count": game.move_count,
                    "source": game.source,
                    "analysis_status": game.analysis_status,
                    "analysis_version": first.analysis_version if first else None,
                    "engine": first.engine if first else None,
                    "engine_version": first.engine_version if first else None,
                    "depth": first.depth if first else None,
                    "moves": moves,
                }
            )
        return rows
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(f"Failed to fetch opponent game rows: {exc}") from exc


def opponent_input_signature(rows: list[dict]) -> str:
    """Fingerprint of the inputs an opponent profile was built from.

    Any new analysed game or a changed analysis version changes the signature,
    which is how a cached snapshot is detected as stale without re-deriving the
    repertoire on every read.
    """
    analyzed = [row for row in rows if row.get("moves")]
    versions = sorted(
        {
            f"{row.get('analysis_version')}:{len(row.get('moves') or [])}"
            for row in analyzed
        }
    )
    latest = max((row.get("date") or "" for row in analyzed), default="")
    return f"analyzed:{len(analyzed)}|total:{len(rows)}|latest:{latest}|versions:{','.join(versions)}"


def save_opponent_profile(
    session: Session,
    *,
    player_id: int,
    profile_version: str,
    methodology_version: str,
    source_signature: str,
    imported_games: int,
    analyzed_games: int,
    coverage: str,
    payload: dict,
    generated_at,
) -> OpponentProfileRecord:  # noqa: ANN001 — datetime
    """Store (or replace) the opponent snapshot for a player + profile version."""
    generated_at = generated_at or utcnow()
    try:
        existing = (
            session.execute(
                select(OpponentProfileRecord).where(
                    OpponentProfileRecord.player_id == player_id,
                    OpponentProfileRecord.profile_version == profile_version,
                )
            )
            .scalars()
            .first()
        )
        if existing is None:
            existing = OpponentProfileRecord(
                player_id=player_id,
                profile_version=profile_version,
                methodology_version=methodology_version,
                source_signature=source_signature,
                imported_games=imported_games,
                analyzed_games=analyzed_games,
                coverage=coverage,
                payload=payload,
                generated_at=generated_at,
                updated_at=utcnow(),
            )
            session.add(existing)
        else:
            existing.methodology_version = methodology_version
            existing.source_signature = source_signature
            existing.imported_games = imported_games
            existing.analyzed_games = analyzed_games
            existing.coverage = coverage
            existing.payload = payload
            existing.generated_at = generated_at
            existing.updated_at = utcnow()
        session.commit()
        session.refresh(existing)
        return existing
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to save opponent profile: {exc}") from exc


def get_opponent_profile_record(
    session: Session, player_id: int, *, profile_version: str | None = None
) -> OpponentProfileRecord | None:
    """The stored opponent snapshot for a player, newest version first."""
    try:
        query = select(OpponentProfileRecord).where(
            OpponentProfileRecord.player_id == player_id
        )
        if profile_version is not None:
            query = query.where(OpponentProfileRecord.profile_version == profile_version)
        query = query.order_by(OpponentProfileRecord.generated_at.desc())
        return session.execute(query).scalars().first()
    except Exception as exc:
        raise RepositoryError(f"Failed to read opponent profile: {exc}") from exc


def delete_opponent_profiles(session: Session, player_id: int) -> int:
    """Remove every stored opponent snapshot for a player (used by rebuild)."""
    try:
        result = session.execute(
            sa_delete(OpponentProfileRecord).where(OpponentProfileRecord.player_id == player_id)
        )
        session.commit()
        return int(result.rowcount or 0)
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to delete opponent profiles: {exc}") from exc


# --- Phase 10: decision intelligence (scenarios) -------------------------------


def save_scenario(session: Session, payload: dict) -> ScenarioRecordRow:
    """Persist one counterfactual scenario. Records are always inserted.

    A scenario is never updated: re-asking the same question writes a new row so
    the history of what was asked stays intact. Nothing in this path can modify a
    game — the row only *references* one.
    """
    try:
        row = ScenarioRecordRow(
            scenario_type=str(payload.get("scenario_type")),
            methodology_version=str(payload.get("methodology_version") or ""),
            source_fen=str(payload.get("source_fen") or ""),
            resulting_fen=payload.get("resulting_fen"),
            game_id=payload.get("game_id"),
            ply=payload.get("ply"),
            owner_player_id=payload.get("owner_player_id"),
            engine_config=payload.get("engine_config") or {},
            branch=payload.get("branch") or {},
            evidence=payload.get("evidence") or [],
            created_at=utcnow(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to save scenario: {exc}") from exc


def get_scenario(session: Session, scenario_id: int) -> ScenarioRecordRow:
    """One stored scenario, or ``NotFoundError``.

    Raises:
        NotFoundError: when no scenario has that id.
    """
    try:
        row = session.get(ScenarioRecordRow, scenario_id)
    except Exception as exc:
        raise RepositoryError(f"Failed to read scenario: {exc}") from exc
    if row is None:
        raise NotFoundError(
            f"Scenario {scenario_id} was not found",
            details={"scenario_id": scenario_id},
        )
    return row


def list_scenarios(
    session: Session,
    *,
    game_id: str | None = None,
    owner_player_id: int | None = None,
    scenario_type: str | None = None,
    limit: int = 50,
) -> list[ScenarioRecordRow]:
    """Stored scenarios, newest first, filtered by whatever the caller scopes to."""
    try:
        query = select(ScenarioRecordRow)
        if game_id is not None:
            query = query.where(ScenarioRecordRow.game_id == game_id)
        if owner_player_id is not None:
            query = query.where(ScenarioRecordRow.owner_player_id == owner_player_id)
        if scenario_type is not None:
            query = query.where(ScenarioRecordRow.scenario_type == scenario_type)
        query = query.order_by(ScenarioRecordRow.created_at.desc(), ScenarioRecordRow.id.desc()).limit(
            max(1, limit)
        )
        return list(session.execute(query).scalars())
    except Exception as exc:
        raise RepositoryError(f"Failed to list scenarios: {exc}") from exc


# =============================================================================
# Phase 11: study collections and match preparation
# =============================================================================


def create_study_collection(
    session: Session,
    *,
    player_id: int,
    name: str,
    kind: str = "mixed",
    description: str = "",
    methodology_version: str = "11.0",
) -> StudyCollectionRecord:
    """Create an empty collection; the name is unique per player."""
    try:
        row = StudyCollectionRecord(
            player_id=player_id,
            name=name.strip(),
            kind=kind,
            description=description,
            methodology_version=methodology_version,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        # A duplicate name is a conflict the caller can act on, not an internal
        # failure — surface it as one instead of a 500 carrying SQL text.
        if "uq_study_collection_name" in str(exc) or "unique" in str(exc).lower():
            raise ConflictError(
                f"You already have a collection named '{name.strip()}'"
            ) from exc
        raise RepositoryError(f"Failed to create study collection: {exc}") from exc


def get_study_collection(session: Session, collection_id: int) -> StudyCollectionRecord:
    """One collection, or ``NotFoundError``."""
    try:
        row = session.get(StudyCollectionRecord, collection_id)
    except Exception as exc:
        raise RepositoryError(f"Failed to read study collection: {exc}") from exc
    if row is None:
        raise NotFoundError(
            f"Study collection {collection_id} was not found",
            details={"collection_id": collection_id},
        )
    return row


def find_study_collection_by_name(
    session: Session, *, player_id: int, name: str
) -> StudyCollectionRecord | None:
    """A player's collection with this name, or ``None``. Name is unique per player."""
    try:
        return session.execute(
            select(StudyCollectionRecord).where(
                StudyCollectionRecord.player_id == player_id,
                StudyCollectionRecord.name == name.strip(),
            )
        ).scalar_one_or_none()
    except Exception as exc:
        raise RepositoryError(f"Failed to find study collection: {exc}") from exc


def list_study_collections(
    session: Session, *, player_id: int | None = None, limit: int = 100
) -> list[StudyCollectionRecord]:
    """Collections, newest first, optionally scoped to one owner."""
    try:
        query = select(StudyCollectionRecord)
        if player_id is not None:
            query = query.where(StudyCollectionRecord.player_id == player_id)
        query = query.order_by(
            StudyCollectionRecord.updated_at.desc(), StudyCollectionRecord.id.desc()
        ).limit(max(1, limit))
        return list(session.execute(query).scalars())
    except Exception as exc:
        raise RepositoryError(f"Failed to list study collections: {exc}") from exc


def delete_study_collection(session: Session, collection_id: int) -> None:
    """Delete a collection and its items (cascade)."""
    try:
        row = session.get(StudyCollectionRecord, collection_id)
        if row is None:
            raise NotFoundError(
                f"Study collection {collection_id} was not found",
                details={"collection_id": collection_id},
            )
        session.delete(row)
        session.commit()
    except NotFoundError:
        raise
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to delete study collection: {exc}") from exc


def add_study_item(
    session: Session,
    *,
    collection: StudyCollectionRecord,
    item_kind: str,
    item_ref: str,
    label: str = "",
    note: str = "",
    game_id: str | None = None,
    ply: int | None = None,
    fen: str | None = None,
) -> StudyItemRecord:
    """Add one pointer to a collection; duplicates are refused by the constraint."""
    try:
        row = StudyItemRecord(
            collection_id=collection.id,
            item_kind=item_kind,
            item_ref=item_ref,
            label=label,
            note=note,
            game_id=game_id,
            ply=ply,
            fen=fen,
            created_at=utcnow(),
        )
        session.add(row)
        collection.updated_at = utcnow()
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to add study item: {exc}") from exc


def remove_study_item(
    session: Session, *, collection: StudyCollectionRecord, item_kind: str, item_ref: str
) -> None:
    """Remove one pointer; an absent item raises ``NotFoundError``."""
    try:
        row = session.execute(
            select(StudyItemRecord).where(
                StudyItemRecord.collection_id == collection.id,
                StudyItemRecord.item_kind == item_kind,
                StudyItemRecord.item_ref == item_ref,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                f"Item {item_kind}:{item_ref} is not in this collection",
                details={"collection_id": collection.id},
            )
        session.delete(row)
        collection.updated_at = utcnow()
        session.commit()
    except NotFoundError:
        raise
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to remove study item: {exc}") from exc


def save_match_preparation(
    session: Session,
    *,
    preparing_player_id: int,
    opponent_id: int,
    opponent_name: str,
    as_white: bool | None,
    coverage: str,
    opponent_games: int,
    analysed_games: int,
    sections: list,
    scenarios: list,
    opponent_profile_version: str | None = None,
    methodology_version: str = "11.0",
) -> MatchPreparationRecord:
    """Store a match preparation snapshot (append-only, like a scenario)."""
    try:
        row = MatchPreparationRecord(
            preparing_player_id=preparing_player_id,
            opponent_id=opponent_id,
            opponent_name=opponent_name,
            as_white=as_white,
            coverage=coverage,
            opponent_games=opponent_games,
            analysed_games=analysed_games,
            sections=sections,
            scenarios=scenarios,
            opponent_profile_version=opponent_profile_version,
            methodology_version=methodology_version,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise RepositoryError(f"Failed to save match preparation: {exc}") from exc


def get_match_preparation(session: Session, preparation_id: int) -> MatchPreparationRecord:
    """One preparation snapshot, or ``NotFoundError``."""
    try:
        row = session.get(MatchPreparationRecord, preparation_id)
    except Exception as exc:
        raise RepositoryError(f"Failed to read match preparation: {exc}") from exc
    if row is None:
        raise NotFoundError(
            f"Match preparation {preparation_id} was not found",
            details={"preparation_id": preparation_id},
        )
    return row


def list_match_preparations(
    session: Session,
    *,
    preparing_player_id: int | None = None,
    opponent_id: int | None = None,
    limit: int = 50,
) -> list[MatchPreparationRecord]:
    """Preparation snapshots, newest first."""
    try:
        query = select(MatchPreparationRecord)
        if preparing_player_id is not None:
            query = query.where(MatchPreparationRecord.preparing_player_id == preparing_player_id)
        if opponent_id is not None:
            query = query.where(MatchPreparationRecord.opponent_id == opponent_id)
        query = query.order_by(
            MatchPreparationRecord.created_at.desc(), MatchPreparationRecord.id.desc()
        ).limit(max(1, limit))
        return list(session.execute(query).scalars())
    except Exception as exc:
        raise RepositoryError(f"Failed to list match preparations: {exc}") from exc


# =============================================================================
# Phase 12: live chess
# =============================================================================
#
# The repository treats a live game like any other stored aggregate: it writes
# whole, validated results, never partial ones. The chess reasoning happens in
# `argus.live`; these functions only move that result to and from the database,
# and the single write path (:func:`save_live_game`) commits the state, the new
# moves and the new events together so a crash cannot leave a game whose stored
# version disagrees with its stored events.


def create_live_game(
    session: Session,
    *,
    game_id: str,
    owner_player_id: int,
    white_player_id: int | None,
    black_player_id: int | None,
    mode: str,
    analysis_mode: str,
    coach_level: str,
    visibility: str,
    rated: bool,
    variant: str,
    initial_fen: str,
    clock_config: dict,
    clock: dict,
    invite_token: str,
    invite_expires_at: datetime | None,
    seats: dict | None = None,
    engine: dict | None = None,
    training_mode: str = "",
    status: str = "waiting",
    methodology_version: str = "12.0",
) -> LiveGameRecord:
    """Insert a new live game with a freshly built state. Never updates."""
    try:
        row = LiveGameRecord(
            id=game_id,
            status=status,
            mode=mode,
            analysis_mode=analysis_mode,
            coach_level=coach_level,
            visibility=visibility,
            rated=rated,
            variant=variant,
            owner_player_id=owner_player_id,
            white_player_id=white_player_id,
            black_player_id=black_player_id,
            invite_token=invite_token,
            invite_expires_at=invite_expires_at,
            initial_fen=initial_fen,
            current_fen=initial_fen,
            side_to_move="white",
            move_number=1,
            clock_config=clock_config,
            clock=clock,
            seats=seats or {},
            engine=engine or {},
            training_mode=training_mode,
            version=0,
            sequence=0,
            result="*",
            methodology_version=methodology_version,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:  # noqa: BLE001 — mapped centrally
        session.rollback()
        raise RepositoryError(f"Failed to create live game: {exc}") from exc


def get_live_game(session: Session, live_game_id: str) -> LiveGameRecord:
    """One live game, or ``NotFoundError``."""
    try:
        row = session.get(LiveGameRecord, live_game_id)
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Failed to read live game: {exc}") from exc
    if row is None:
        raise NotFoundError(
            f"Live game {live_game_id} was not found", details={"live_game_id": live_game_id}
        )
    return row


def find_live_game_by_invite(session: Session, token: str) -> LiveGameRecord | None:
    """Look up a live game by invite token (``None`` if the token matches none)."""
    try:
        query = select(LiveGameRecord).where(LiveGameRecord.invite_token == token)
        return session.execute(query).scalars().first()
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Failed to look up the invite: {exc}") from exc


def list_live_games(
    session: Session,
    *,
    player_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[LiveGameRecord]:
    """Live games newest-first, optionally scoped to a player's own games."""
    try:
        query = select(LiveGameRecord)
        if player_id is not None:
            query = query.where(
                (LiveGameRecord.white_player_id == player_id)
                | (LiveGameRecord.black_player_id == player_id)
                | (LiveGameRecord.owner_player_id == player_id)
            )
        if status is not None:
            query = query.where(LiveGameRecord.status == status)
        query = (
            query.order_by(LiveGameRecord.created_at.desc(), LiveGameRecord.id.desc())
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        return list(session.execute(query).scalars())
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Failed to list live games: {exc}") from exc


def save_live_game(
    session: Session,
    record: LiveGameRecord,
    *,
    state: dict,
    events: list[dict] | None = None,
    moves: list[dict] | None = None,
) -> LiveGameRecord:
    """Persist one validated transition: state, new moves and new events.

    Everything is written under a single commit. If any insert fails, the whole
    transition rolls back — the stored version can never advance without its
    events, which is what a reconnecting client relies on.
    """
    events = events or []
    moves = moves or []
    try:
        record.status = state["status"]
        record.mode = state["mode"]
        record.analysis_mode = state["analysis_mode"]
        record.coach_level = state["coach_level"]
        record.visibility = state["visibility"]
        record.rated = bool(state["rated"])
        record.current_fen = state["current_fen"]
        record.side_to_move = state["side_to_move"]
        record.move_number = int(state["move_number"])
        record.clock_config = state["clock_config"]
        record.clock = state["clock"]
        record.seats = state.get("seats") or {}
        record.engine = state.get("engine") or {}
        record.training_mode = state.get("training_mode") or ""
        record.version = int(state["version"])
        record.sequence = int(state["sequence"])
        record.result = state["result"]
        record.result_reason = state.get("result_reason")
        record.draw_offer = state.get("draw_offer")
        record.updated_at = utcnow()
        started_at = state.get("started_at")
        if started_at and record.started_at is None:
            record.started_at = datetime.fromisoformat(started_at)
        ended_at = state.get("ended_at")
        if ended_at and record.ended_at is None:
            record.ended_at = datetime.fromisoformat(ended_at)

        for move in moves:
            session.add(
                LiveGameMoveRecord(
                    live_game_id=record.id,
                    ply=int(move["ply"]),
                    move_number=int(move["move_number"]),
                    side=move["side"],
                    san=move["san"],
                    uci=move["uci"],
                    fen_before=move["fen_before"],
                    fen_after=move["fen_after"],
                    clock_white_ms=int(move["clock_white_ms"]),
                    clock_black_ms=int(move["clock_black_ms"]),
                    sequence_number=int(move["sequence_number"]),
                    played_at=datetime.fromisoformat(move["played_at"]),
                )
            )
        for event in events:
            session.add(
                LiveGameEventRecord(
                    live_game_id=record.id,
                    event_id=event["event_id"],
                    event_type=event["event_type"],
                    sequence_number=int(event["sequence_number"]),
                    game_version=int(event["game_version"]),
                    payload=event["payload"],
                    created_at=datetime.fromisoformat(event["timestamp"]),
                )
            )
        session.commit()
        session.refresh(record)
        return record
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise RepositoryError(f"Failed to save live game: {exc}") from exc


def live_game_moves(session: Session, live_game_id: str) -> list[LiveGameMoveRecord]:
    """Every stored move of a live game, in ply order."""
    try:
        query = (
            select(LiveGameMoveRecord)
            .where(LiveGameMoveRecord.live_game_id == live_game_id)
            .order_by(LiveGameMoveRecord.ply)
        )
        return list(session.execute(query).scalars())
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Failed to read live game moves: {exc}") from exc


def live_game_events(
    session: Session,
    live_game_id: str,
    *,
    after_sequence: int | None = None,
    limit: int = 500,
) -> list[LiveGameEventRecord]:
    """Stored events in sequence order, optionally only those after a sequence."""
    try:
        query = select(LiveGameEventRecord).where(
            LiveGameEventRecord.live_game_id == live_game_id
        )
        if after_sequence is not None:
            query = query.where(LiveGameEventRecord.sequence_number > after_sequence)
        query = query.order_by(LiveGameEventRecord.sequence_number).limit(max(1, limit))
        return list(session.execute(query).scalars())
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Failed to read live game events: {exc}") from exc


def link_live_game_to_library(
    session: Session, record: LiveGameRecord, library_game_id: str
) -> LiveGameRecord:
    """Record the library ``games.id`` a finished live game became."""
    try:
        record.library_game_id = library_game_id
        record.updated_at = utcnow()
        session.commit()
        session.refresh(record)
        return record
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise RepositoryError(f"Failed to link live game to its library game: {exc}") from exc
