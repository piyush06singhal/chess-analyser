"""External game-source routes (Chess.com, Lichess).

The connect flow the frontend drives, identical for every platform source:

1. ``GET /api/sources/{source}/{username}`` — does the player exist, and which
   months can we read games from?
2. ``GET /api/sources/{source}/{username}/games?...`` — the games in one month,
   each flagged with whether Caissa already has it.
3. ``POST /api/sources/{source}/import`` — import the games the user picked.

Properties this design keeps, on purpose:

* **Nothing is imported implicitly.** Browsing a player's games never writes to
  the library; only the explicit import call does.
* **Import is idempotent.** Every platform game carries its upstream game URL as
  its ``source_game_id``, so a game that is already imported is reported as
  *skipped*, never duplicated — re-running the same fetch is harmless.
* **Analysis is separate and optional.** Importing stores the game in the normal
  ``ready`` state; the real Stockfish run is queued through the same
  ``AnalysisJobRunner`` seam every other analysis uses (or triggered later from
  the game page). Nothing is faked and no request blocks on the engine.
* **Platform differences are stated, not hidden.** Chess.com publishes an
  archive index, so its months are real months the player has games in; Lichess
  publishes none, so its months are calendar months since the account was
  created. Each lookup says which it is.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from argus.analysis.pipeline import build_analysis_config
from argus.chess_core.models import AnalysisStatus
from argus.importing import (
    ChessComClient,
    LichessClient,
    available_sources,
    months_for_profile,
    planned_sources,
    resolve_importer,
)
from argus.importing.base import ImportSource
from argus.shared.errors import ArgusError, RepositoryError, UnsupportedSourceError, ValidationError
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    find_games_by_source_game_ids,
    save_game_with_analysis,
)
from argus_api.deps import get_db, rate_limited
from argus_api.observability import current_caller_id
from argus_api.security import is_open
from argus_api.schemas import ChessComImportRequest, LichessImportRequest
from argus_api.services.analysis_jobs import AnalysisJobRunner

logger = get_logger(__name__)
router = APIRouter(prefix="/api/sources", tags=["sources"])

MAX_GAMES_PER_MONTH = 200


def _client(request: Request) -> ChessComClient:
    settings = request.app.state.settings
    return ChessComClient(
        base_url=settings.chesscom_base_url,
        timeout=settings.chesscom_timeout_seconds,
        user_agent=settings.chesscom_user_agent,
    )


def _lichess_client(request: Request) -> LichessClient:
    settings = request.app.state.settings
    return LichessClient(
        base_url=settings.lichess_base_url,
        timeout=settings.lichess_timeout_seconds,
        user_agent=settings.chesscom_user_agent,  # one identifying agent for all sources
    )


def _imported_index(db: Session, source: ImportSource, game_ids: list[str]) -> dict[str, str]:
    """Map ``source_game_id -> local game id`` for already-imported games."""
    if db is None or not game_ids:
        return {}
    try:
        found = find_games_by_source_game_ids(db, source.value, game_ids)
    except RepositoryError as exc:  # storage down must not break browsing
        logger.warning("Could not check imported games: %s", exc.message)
        return {}
    return {key: row.id for key, row in found.items()}


def _import_games(
    request: Request,
    background: BackgroundTasks,
    db: Session,
    *,
    source: ImportSource,
    games_by_url: dict[str, object],
    requested_urls: list[str],
    already: dict[str, str],
    analyze: bool,
    depth: int | None,
    multipv: int | None,
    label: str,
    username: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Import the selected games through the shared, source-agnostic path.

    Every game goes through the same importer and persistence call as a pasted
    PGN, so a platform game is an ordinary Caissa game the moment it lands.
    """
    settings = request.app.state.settings
    importer = resolve_importer(source)

    imported: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for url in requested_urls:
        if url in already:
            skipped.append({"url": url, "game_id": already[url], "reason": "already_imported"})
            continue
        source_game = games_by_url.get(url)
        if source_game is None:
            failed.append(
                {
                    "url": url,
                    "code": "not_in_month",
                    "error": "That game is not in the requested month.",
                }
            )
            continue
        pgn = getattr(source_game, "pgn", None)
        if not isinstance(pgn, str) or not pgn.strip():
            failed.append(
                {"url": url, "code": "empty_pgn", "error": "That game has no PGN to import."}
            )
            continue
        try:
            result = importer.import_games(pgn)
            domain_game = result.games[0]
            # Ownership: the importing caller owns the game when auth is on; in an
            # open deployment it joins the shared library (owner stays NULL).
            owner = None if is_open(settings) else current_caller_id()
            orm_game = save_game_with_analysis(
                db,
                domain_game,
                None,  # analysis is queued separately, never faked
                pgn_text=pgn,
                source=source.value,
                source_game_id=url,
                owner=owner,
            )
            db.commit()
        except ArgusError as exc:
            db.rollback()
            failed.append({"url": url, "code": exc.code, "error": exc.message})
            continue
        except Exception as exc:  # noqa: BLE001 — one bad game must not kill the batch
            db.rollback()
            logger.warning("%s import failed for %s: %s", label, url, exc)
            failed.append(
                {"url": url, "code": "import_failed", "error": "Could not import that game."}
            )
            continue

        imported.append(
            {
                "url": url,
                "game_id": orm_game.id,
                "moves": domain_game.move_count,
                "analysis_status": orm_game.analysis_status,
                "queued_for_analysis": False,
            }
        )

    if analyze and imported:
        config = build_analysis_config(
            settings.analysis_profile,
            depth=depth or settings.engine_depth,
            multipv=multipv or settings.analysis_multipv,
            movetime_ms=settings.engine_movetime_ms or None,
        )
        runner = AnalysisJobRunner(
            request.app.state.session_factory, request.app.state.engine, settings
        )
        for entry in imported:
            background.add_task(runner.run, entry["game_id"], config=config)
            entry["queued_for_analysis"] = True
            entry["analysis_status"] = AnalysisStatus.ANALYZING.value

    logger.info(
        "%s import [player=%s imported=%d skipped=%d failed=%d analyze=%s]",
        label,
        username,
        len(imported),
        len(skipped),
        len(failed),
        analyze,
    )
    return imported, skipped, failed


@router.get("")
def list_sources() -> dict:
    """Which game sources Caissa can read, and what is still planned."""
    return {
        "implemented": available_sources(),
        "planned": planned_sources(),
        "sources": [
            {
                "id": ImportSource.CHESS_COM.value,
                "label": "Chess.com",
                "kind": "platform",
                "available": True,
                "description": (
                    "Import a player's public games by username, month by month. Read-only, "
                    "key-less, and only the games you select are stored."
                ),
                "supports_username_lookup": True,
            },
            {
                "id": ImportSource.LICHESS.value,
                "label": "Lichess",
                "kind": "platform",
                "available": True,
                "description": (
                    "Import a player's finished games by username, month by month. Read-only, "
                    "key-less, and only the games you select are stored."
                ),
                "supports_username_lookup": True,
            },
            {
                "id": ImportSource.PGN_TEXT.value,
                "label": "Pasted PGN",
                "kind": "pgn",
                "available": True,
                "description": "Paste PGN text directly.",
                "supports_username_lookup": False,
            },
            {
                "id": ImportSource.PGN_FILE.value,
                "label": "PGN file",
                "kind": "pgn",
                "available": True,
                "description": "Upload a .pgn file.",
                "supports_username_lookup": False,
            },
        ],
    }


# --- Chess.com --------------------------------------------------------------


@router.get("/chesscom/{username}")
def get_chesscom_player(
    username: str,
    request: Request,
    months: int | None = Query(default=None, ge=1, le=12),
) -> dict:
    """Look up a Chess.com player and the months Caissa can read games from."""
    settings = request.app.state.settings
    window = months or settings.chesscom_archive_months
    with _client(request) as client:
        profile = client.get_profile(username)
        archives = client.get_recent_months(profile.username, months=window)

    return {
        "source": ImportSource.CHESS_COM.value,
        "profile": profile.model_dump(mode="json"),
        "is_closed": profile.is_closed,
        "months_requested": window,
        "months_are_published": True,
        "months": [archive.model_dump(mode="json") for archive in reversed(archives)],
        "note": (
            "Only public games Chess.com publishes are readable. Caissa uses no "
            "credentials and stores nothing until you import a specific game."
        ),
    }


@router.get("/chesscom/{username}/games")
def list_chesscom_games(
    username: str,
    request: Request,
    year: int = Query(ge=2007, le=2100),
    month: int = Query(ge=1, le=12),
    time_class: str | None = Query(default=None, description="bullet | blitz | rapid | daily"),
    limit: int = Query(default=50, ge=1, le=MAX_GAMES_PER_MONTH),
    db: Session = Depends(get_db),
) -> dict:
    """List one month of a player's standard games (nothing is imported)."""
    with _client(request) as client:
        month_games = client.get_month_games(
            username, year, month, standard_only=True, time_class=time_class, limit=limit
        )

    imported = _imported_index(db, ImportSource.CHESS_COM, [game.url for game in month_games.games])
    return {
        "source": ImportSource.CHESS_COM.value,
        "username": month_games.username,
        "year": year,
        "month": month,
        "time_class": time_class,
        "count": len(month_games.games),
        "variant_count": month_games.variant_count,
        "truncated": month_games.truncated,
        "imported_count": len(imported),
        "games": [
            {
                **game.model_dump(mode="json"),
                "ply_count": game.move_count_estimate,
                "already_imported": game.url in imported,
                "game_id": imported.get(game.url),
            }
            for game in month_games.games
        ],
        "note": (
            f"{month_games.variant_count} non-standard game(s) in this month are excluded — "
            "Caissa analyzes standard chess only."
            if month_games.variant_count
            else "Only standard chess games are listed. Nothing is stored until you import."
        ),
    }


@router.post("/chesscom/import", dependencies=[Depends(rate_limited("import"))])
def import_chesscom_games(
    body: ChessComImportRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Import the selected games from one Chess.com month."""
    with _client(request) as client:
        month_games = client.get_month_games(
            body.username, body.year, body.month, standard_only=True
        )

    imported, skipped, failed = _import_games(
        request,
        background,
        db,
        source=ImportSource.CHESS_COM,
        games_by_url={game.url: game for game in month_games.games},
        requested_urls=list(body.game_urls),
        already=_imported_index(db, ImportSource.CHESS_COM, list(body.game_urls)),
        analyze=body.analyze,
        depth=body.depth,
        multipv=body.multipv,
        label="Chess.com",
        username=body.username,
    )

    return {
        "source": ImportSource.CHESS_COM.value,
        "username": body.username.lower(),
        "year": body.year,
        "month": body.month,
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "counts": {
            "requested": len(body.game_urls),
            "imported": len(imported),
            "skipped": len(skipped),
            "failed": len(failed),
        },
    }


# --- Lichess ----------------------------------------------------------------


@router.get("/lichess/{username}")
def get_lichess_player(
    username: str,
    request: Request,
    months: int | None = Query(default=None, ge=1, le=12),
) -> dict:
    """Look up a Lichess player and the months Caissa can read games from."""
    settings = request.app.state.settings
    window = months or settings.lichess_archive_months
    with _lichess_client(request) as client:
        profile = client.get_profile(username)
        months_available = months_for_profile(
            profile.username, profile.created_at, months=window
        )

    return {
        "source": ImportSource.LICHESS.value,
        "profile": profile.model_dump(mode="json"),
        "is_closed": False,  # Lichess accounts are readable while they exist
        "months_requested": window,
        "months_are_published": False,
        "months": [month.model_dump(mode="json") for month in months_available],
        "note": (
            "Lichess publishes every finished game and needs no credentials. It does not "
            "publish a list of months that contain games, so Caissa offers calendar months "
            "since the account was created; a month with no games simply comes back empty."
        ),
    }


@router.get("/lichess/{username}/games")
def list_lichess_games(
    username: str,
    request: Request,
    year: int = Query(ge=2007, le=2100),
    month: int = Query(ge=1, le=12),
    time_class: str | None = Query(
        default=None, description="bullet | blitz | rapid | classical | correspondence"
    ),
    limit: int = Query(default=50, ge=1, le=MAX_GAMES_PER_MONTH),
    db: Session = Depends(get_db),
) -> dict:
    """List one month of a player's standard games (nothing is imported)."""
    with _lichess_client(request) as client:
        month_games = client.get_month_games(
            username, year, month, standard_only=True, time_class=time_class, limit=limit
        )

    imported = _imported_index(db, ImportSource.LICHESS, [game.url for game in month_games.games])
    return {
        "source": ImportSource.LICHESS.value,
        "username": month_games.username,
        "year": year,
        "month": month,
        "time_class": time_class,
        "count": len(month_games.games),
        "variant_count": month_games.variant_count,
        "truncated": month_games.truncated,
        "imported_count": len(imported),
        "games": [
            {
                **game.model_dump(mode="json"),
                "ply_count": game.move_count_estimate,
                "already_imported": game.url in imported,
                "game_id": imported.get(game.url),
            }
            for game in month_games.games
        ],
        "note": (
            f"{month_games.variant_count} non-standard game(s) in this month are excluded — "
            "Caissa analyzes standard chess only."
            if month_games.variant_count
            else "Only standard chess games are listed. Nothing is stored until you import."
        ),
    }


@router.post("/lichess/import", dependencies=[Depends(rate_limited("import"))])
def import_lichess_games(
    body: LichessImportRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Import the selected games from one Lichess month."""
    with _lichess_client(request) as client:
        month_games = client.get_month_games(
            body.username, body.year, body.month, standard_only=True
        )

    imported, skipped, failed = _import_games(
        request,
        background,
        db,
        source=ImportSource.LICHESS,
        games_by_url={game.url: game for game in month_games.games},
        requested_urls=list(body.game_urls),
        already=_imported_index(db, ImportSource.LICHESS, list(body.game_urls)),
        analyze=body.analyze,
        depth=body.depth,
        multipv=body.multipv,
        label="Lichess",
        username=body.username,
    )

    return {
        "source": ImportSource.LICHESS.value,
        "username": body.username.lower(),
        "year": body.year,
        "month": body.month,
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "counts": {
            "requested": len(body.game_urls),
            "imported": len(imported),
            "skipped": len(skipped),
            "failed": len(failed),
        },
    }


# --- anything else: say so, instead of a bare 404 ---------------------------
#
# These two catch-alls are declared last on purpose: Starlette matches routes in
# registration order, so every real platform endpoint above wins, and a site
# Caissa cannot read gets an honest 422 listing what it *can* read.

_SUPPORTED_PLATFORM_PATHS = {
    "chesscom": ImportSource.CHESS_COM.value,
    "lichess": ImportSource.LICHESS.value,
}


def _unknown_source_error(source: str, username: str | None) -> UnsupportedSourceError:
    return UnsupportedSourceError(
        f"Caissa cannot read games from '{source}'.",
        details={
            "username": username,
            "supported": available_sources(),
            "platforms": sorted(_SUPPORTED_PLATFORM_PATHS),
            "hint": (
                "Chess.com and Lichess are read by username; anything else can still be "
                "imported as PGN."
            ),
        },
    )


@router.get("/{source}")
def explain_missing_username(source: str) -> dict:
    """Answer a lookup that forgot the username, and reject unknown sites."""
    if source not in _SUPPORTED_PLATFORM_PATHS:
        raise _unknown_source_error(source, None)
    raise ValidationError(
        f"Add the username: GET /api/sources/{source}/<username>.",
        details={"source": source},
    )


@router.get("/{source}/{username}")
def unsupported_source_lookup(source: str, username: str) -> dict:
    """Reject a username lookup for a site Caissa does not read, with the real list."""
    raise _unknown_source_error(source, username)
