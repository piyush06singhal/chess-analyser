"""Phase 12 live routes: REST actions plus the WebSocket event stream.

The split is deliberate and is what keeps live play safe and testable:

* **actions are REST** — create, join, start, move, resign, draw, abort, invite.
  Each one is a transactional, server-authoritative operation with an ordinary
  HTTP status, so the authorization, the error mapping and the audit trail are
  the same ones every other part of Caissa uses.
* **events are a WebSocket** — a game's whole event stream, in sequence order,
  pushed to everyone watching it. A client that misses an event (or reconnects)
  reports the last sequence it saw and receives exactly the difference, or a full
  state when the gap cannot be filled.

A WebSocket connection authenticates *before* it is accepted: an unauthenticated
socket is closed, never upgraded. Nothing the client sends over the socket is
trusted — the only messages it may send are a synchronization request and a ping.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, WebSocket
from fastapi import WebSocketDisconnect
from sqlalchemy.orm import Session

from argus.analysis.pipeline import build_analysis_config
from argus.chess_core.models import AnalysisStatus
from argus.shared.errors import ArgusError, NotFoundError
from argus.shared.logging import get_logger

from argus_api.deps import get_db
from argus_api.observability import AUDIT_LIVE_GAME_CREATED, audit
from argus_api.schemas import (
    LiveClaimDrawRequest,
    LiveCoachRequest,
    LiveGameCreateRequest,
    LiveJoinRequest,
    LiveMoveRequest,
    LivePlayerRequest,
    LiveVisibilityRequest,
)
from argus_api.services import (
    live_analysis,
    live_service,
    player_profile_service,
    training_service,
)
from argus_api.services.analysis_jobs import AnalysisJobRunner
from argus_api.services.live_hub import HUB

logger = get_logger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])


# ---------------------------------------------------------------------------
# methodology and metrics
# ---------------------------------------------------------------------------


@router.get("/method")
def get_method() -> dict:
    """Modes, states, legal transitions, time controls, and the fair-play rule."""
    return live_service.method()


@router.get("/metrics")
def get_metrics() -> dict:
    """Live-game counters and gauges (§48). Counts only — never game content."""
    return live_service.metrics()


# ---------------------------------------------------------------------------
# creation and listing
# ---------------------------------------------------------------------------


@router.post("/games", status_code=201)
def create_game(
    payload: LiveGameCreateRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Create a live game.

    The fair-play permission is fixed here, at creation: a competitive game is
    forced to ``no_analysis`` whatever the request asks for, and only a training
    or sandbox game may put the engine in the far seat.
    """
    result = live_service.create(
        db,
        player_id=payload.player_id,
        mode=payload.mode,
        colour=payload.colour,
        time_control=payload.time_control,
        visibility=payload.visibility,
        rated=payload.rated,
        opponent_player_id=payload.opponent_player_id,
        analysis_mode=payload.analysis_mode,
        coach_level=payload.coach_level,
        training_mode=payload.training_mode,
        engine_depth=payload.engine_depth,
        start_fen=payload.start_fen,
        opponents=payload.opponents,
    )
    # Creating a live game is audited: it fixes the fair-play permission (a
    # competitive game is forced to no_analysis), so the decision should be
    # traceable to the caller and the mode it was created in.
    audit(
        AUDIT_LIVE_GAME_CREATED,
        live_game_id=result["state"]["game_id"],
        mode=payload.mode,
        visibility=payload.visibility,
        rated=payload.rated,
    )
    # Creation can ready (and start) the game when both seats are filled, so it
    # runs the same tail as every other action: engine reply, broadcast, pipeline.
    return _after_action(
        request,
        background,
        db,
        result["state"]["game_id"],
        result,
        player_id=payload.player_id,
    )


@router.get("/games")
def list_games(
    player_id: int | None = Query(default=None, ge=1),
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """Live games: a player's own, or the public ones when no player is given."""
    return live_service.list_games(db, player_id=player_id, status=status, limit=limit)


@router.get("/games/{live_game_id}")
def get_game(
    live_game_id: str,
    player_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """The full server state: board, clocks, seats, permissions and moves."""
    return live_service.get_payload(db, live_game_id=live_game_id, player_id=player_id)


@router.get("/games/{live_game_id}/state")
def get_state(
    live_game_id: str,
    player_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """The cheap live view (clocks, version, legal moves) polling clients use."""
    return live_service.state_delta(db, live_game_id=live_game_id, player_id=player_id)


@router.get("/games/{live_game_id}/sync")
def sync(
    live_game_id: str,
    after_sequence: int = Query(default=0, ge=0),
    player_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """The events a client missed, or the whole state when a gap cannot be filled."""
    return live_service.sync(
        db, live_game_id=live_game_id, player_id=player_id, after_sequence=after_sequence
    )


@router.get("/games/{live_game_id}/analysis")
def get_analysis(
    live_game_id: str,
    player_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """The analyses and coach messages this game has produced."""
    return live_service.stored_analyses(db, live_game_id=live_game_id, player_id=player_id)


@router.get("/games/{live_game_id}/pgn")
def get_pgn(
    live_game_id: str,
    player_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """A standards-compliant PGN of the moves played."""
    return live_service.pgn(db, live_game_id=live_game_id, player_id=player_id)


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------


def _after_action(
    request: Request,
    background: BackgroundTasks,
    db: Session,
    live_game_id: str,
    result: dict,
    *,
    player_id: int,
) -> dict:
    """Finish an action: engine reply, broadcast, then the post-game pipeline.

    The engine's reply is made *inside* the request because the client's board is
    wrong until it arrives; the post-game analysis is queued because it is slow
    and the client polls for it.
    """
    events = list(result.get("events") or [])
    if result["state"].get("turn_owner") == "engine":
        game = live_service.load(db, live_game_id)
        try:
            uci = live_analysis.engine_move_uci(request.app.state.engine, game)
            reply = live_service.apply_engine_move(db, live_game_id=live_game_id, uci=uci)
        except ArgusError as exc:
            # The engine could not reply: the game keeps the human's move and the
            # client is told, rather than being shown a stalled but "live" board.
            result["engine_error"] = exc.to_dict()
            reply = None
        if reply:
            events.extend(reply.get("events") or [])
            result["state"] = live_service.get_payload(
                db, live_game_id=live_game_id, player_id=player_id
            )
            result["engine_reply"] = (
                reply["state"]["moves"][-1] if reply["state"]["moves"] else None
            )
    HUB.publish(live_game_id, events)
    _schedule_postgame(request, background, db, live_game_id)
    return result


def _schedule_postgame(
    request: Request, background: BackgroundTasks, db: Session, live_game_id: str
) -> None:
    """Queue the post-game pipeline when a game has just finished (§29)."""
    record = _record(db, live_game_id)
    if not live_service.needs_postgame(record):
        return
    logger.info("Scheduling the post-game pipeline [live_game=%s]", live_game_id)
    background.add_task(
        _run_postgame,
        request.app.state.session_factory,
        request.app.state.engine,
        request.app.state.settings,
        live_game_id,
    )


def _record(db: Session, live_game_id: str):
    from argus_api.db.repository import get_live_game

    return get_live_game(db, live_game_id)


def _run_postgame(session_factory, engine, settings, live_game_id: str) -> None:
    """Background: live game → library game → full Stockfish analysis (§29/§30)."""
    try:
        with session_factory.session_scope() as session:
            info = live_service.finish_pipeline(session, live_game_id=live_game_id)
        library_game_id = info.get("library_game_id")
        if not library_game_id:
            return
        config = build_analysis_config(
            settings.analysis_profile,
            depth=settings.engine_depth,
            multipv=settings.analysis_multipv,
            movetime_ms=settings.engine_movetime_ms or None,
        )
        AnalysisJobRunner(session_factory, engine, settings).run(
            library_game_id, config=config
        )
        # §29/§31: a finished live game automatically produces training material
        # and refreshes the player's profile — the live→training loop, rather than
        # an on-demand action the player has to remember to trigger. Both run from
        # the stored analysis, so a failure here never loses the game or its
        # analysis; it is recorded and the completion event still fires.
        training_created = 0
        try:
            with session_factory.session_scope() as session:
                owner_id = live_service.owner_of(session, live_game_id=live_game_id)
                if owner_id is not None:
                    generated = training_service.generate_for_game(
                        session,
                        library_game_id,
                        player_id=owner_id,
                        data_source="personalized",
                    )
                    training_created = int(generated.get("accepted") or 0)
                    player_profile_service.rebuild_player_profile(session, str(owner_id))
                    # Phase 13: the new game, its positions, the refreshed patterns
                    # and the new training are reflected in the intelligence graph
                    # immediately, so the coach can connect them in the next turn.
                    try:
                        from argus_api.services import graph_intelligence, graph_service

                        graph_service.update_game(session, library_game_id)
                        graph_intelligence.update_player_patterns(session, int(owner_id))
                        graph_intelligence.update_player_training(session, int(owner_id))
                    except Exception as graph_exc:  # noqa: BLE001 — never undo the game
                        logger.warning(
                            "Graph update after live game failed [live_game=%s]: %s",
                            live_game_id,
                            graph_exc,
                        )
        except Exception as exc:  # noqa: BLE001 — training must never undo the game
            logger.warning(
                "Post-game training/profile update failed [live_game=%s]: %s",
                live_game_id,
                exc,
            )
        # Announce it on the game's own event stream, sequenced like everything
        # else, so a client watching the board learns the review is ready.
        with session_factory.session_scope() as session:
            events = live_service.emit_events(
                session,
                live_game_id=live_game_id,
                payloads=[
                    (
                        "ANALYSIS_UPDATED",
                        {
                            "postgame": True,
                            "library_game_id": library_game_id,
                            "analysis_status": AnalysisStatus.ANALYZED.value,
                            "training_positions": training_created,
                            "debrief_path": f"/api/coaching/games/{library_game_id}/debrief",
                        },
                    )
                ],
            )
        if events:
            HUB.publish(live_game_id, events)
    except Exception as exc:  # noqa: BLE001 — a background failure never breaks a game
        logger.warning("Post-game pipeline failed [live_game=%s]: %s", live_game_id, exc)


@router.post("/games/{live_game_id}/join")
def join_game(
    live_game_id: str,
    payload: LiveJoinRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Take an open seat (with an invitation when you are not already a member)."""
    result = live_service.join(
        db, live_game_id=live_game_id, player_id=payload.player_id, invite_token=payload.invite_token
    )
    return _after_action(request, background, db, live_game_id, result, player_id=payload.player_id)


@router.post("/games/{live_game_id}/start")
def start_game(
    live_game_id: str,
    payload: LivePlayerRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Start the clocks."""
    result = live_service.start(db, live_game_id=live_game_id, player_id=payload.player_id)
    return _after_action(request, background, db, live_game_id, result, player_id=payload.player_id)


@router.post("/games/{live_game_id}/move")
def submit_move(
    live_game_id: str,
    payload: LiveMoveRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Submit one move intent. The server validates it and answers with the result."""
    result = live_service.play_move(
        db,
        live_game_id=live_game_id,
        player_id=payload.player_id,
        uci=payload.uci,
        san=payload.san,
        expected_version=payload.expected_version,
    )
    played = result["state"]["moves"][-1] if result["state"]["moves"] else None
    result = _after_action(
        request, background, db, live_game_id, result, player_id=payload.player_id
    )
    # Analyse the human's move — never the engine's, and only where the game
    # permits analysis at all. The engine's reply is made inside the request, so
    # by now the board has moved on; the analysis still targets the position the
    # human actually faced.
    if played:
        game = live_service.load(db, live_game_id)
        seat_kind = game.state.seats.get(played["side"])
        if live_analysis.analysis_allowed(game.state) and seat_kind == "human":
            live_service.bump("analysis_requests")
            background.add_task(
                _run_live_analysis,
                request.app.state.session_factory,
                request.app.state.engine,
                live_game_id,
                played["ply"],
                played["fen_before"],
                played["uci"],
                played["side"],
            )
    return result


@router.post("/games/{live_game_id}/resign")
def resign_game(
    live_game_id: str,
    payload: LivePlayerRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Resign the game."""
    result = live_service.resign(db, live_game_id=live_game_id, player_id=payload.player_id)
    return _after_action(request, background, db, live_game_id, result, player_id=payload.player_id)


@router.post("/games/{live_game_id}/abort")
def abort_game(
    live_game_id: str,
    payload: LivePlayerRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Abort a game that has not produced a result."""
    result = live_service.abort(db, live_game_id=live_game_id, player_id=payload.player_id)
    HUB.publish(live_game_id, result.get("events") or [])
    return result


@router.post("/games/{live_game_id}/pause")
def pause_game(
    live_game_id: str,
    payload: LivePlayerRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Stop the clocks by explicit action."""
    result = live_service.pause(db, live_game_id=live_game_id, player_id=payload.player_id)
    HUB.publish(live_game_id, result.get("events") or [])
    return result


@router.post("/games/{live_game_id}/resume")
def resume_game(
    live_game_id: str,
    payload: LivePlayerRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Restart the clocks after a pause."""
    result = live_service.resume(db, live_game_id=live_game_id, player_id=payload.player_id)
    return _after_action(request, background, db, live_game_id, result, player_id=payload.player_id)


@router.post("/games/{live_game_id}/draw/offer")
def offer_draw(
    live_game_id: str,
    payload: LivePlayerRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Offer a draw."""
    result = live_service.offer_draw(
        db, live_game_id=live_game_id, player_id=payload.player_id
    )
    HUB.publish(live_game_id, result.get("events") or [])
    return result


@router.post("/games/{live_game_id}/draw/accept")
def accept_draw(
    live_game_id: str,
    payload: LivePlayerRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Accept the pending draw offer."""
    result = live_service.accept_draw(
        db, live_game_id=live_game_id, player_id=payload.player_id
    )
    return _after_action(request, background, db, live_game_id, result, player_id=payload.player_id)


@router.post("/games/{live_game_id}/draw/decline")
def decline_draw(
    live_game_id: str,
    payload: LivePlayerRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Decline the pending draw offer."""
    result = live_service.decline_draw(
        db, live_game_id=live_game_id, player_id=payload.player_id
    )
    HUB.publish(live_game_id, result.get("events") or [])
    return result


@router.post("/games/{live_game_id}/draw/claim")
def claim_draw(
    live_game_id: str,
    payload: LiveClaimDrawRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Claim a threefold-repetition or fifty-move draw."""
    result = live_service.claim_draw(
        db, live_game_id=live_game_id, player_id=payload.player_id, rule=payload.rule
    )
    return _after_action(request, background, db, live_game_id, result, player_id=payload.player_id)


@router.post("/games/{live_game_id}/invite/rotate")
def rotate_invite(
    live_game_id: str, payload: LivePlayerRequest, db: Session = Depends(get_db)
) -> dict:
    """Invalidate the old invitation and issue a new one."""
    return live_service.rotate_invite(
        db, live_game_id=live_game_id, player_id=payload.player_id
    )


@router.get("/games/{live_game_id}/invite")
def get_invite(
    live_game_id: str, player_id: int = Query(ge=1), db: Session = Depends(get_db)
) -> dict:
    """The invitation to this game, if the caller may share it."""
    return live_service.invite(db, live_game_id=live_game_id, player_id=player_id)


@router.post("/games/{live_game_id}/visibility")
def set_visibility(
    live_game_id: str, payload: LiveVisibilityRequest, db: Session = Depends(get_db)
) -> dict:
    """Change who may watch the game (owner only)."""
    return live_service.set_visibility(
        db, live_game_id=live_game_id, player_id=payload.player_id, visibility=payload.visibility
    )


@router.post("/games/{live_game_id}/coach")
def ask_coach(
    live_game_id: str,
    payload: LiveCoachRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Ask the in-game coach.

    In a competitive game this returns a hint and the refusal — never an engine
    move. The decision is made by ``argus.live.fairplay``, so the endpoint, the AI
    agent and the UI cannot disagree about it.
    """
    live_service.bump("coach_requests")
    answer = live_service.coach_answer(
        db, live_game_id=live_game_id, player_id=payload.player_id, question=payload.question
    )
    if answer.get("kind") != "analysis_permitted":
        return answer
    # Analysis is permitted in this game, so the engine may answer — but only
    # about the position that is actually on the board.
    game = live_service.load(db, live_game_id)
    engine = request.app.state.engine
    if not engine.info().get("available"):
        answer["analysis"] = {
            "available": False,
            "reason": "engine_unavailable",
            "detail": "Stockfish is not reachable, so Caissa cannot analyse this position.",
        }
        return answer
    result = engine.analyze_position(
        game.state.current_fen,
        depth=live_analysis.DEFAULT_LIVE_DEPTH,
        multipv=3,
    )
    answer["analysis"] = {
        "available": True,
        "fen": game.state.current_fen,
        "side_to_move": game.state.side_to_move.value,
        "depth": result.depth,
        "best_move_uci": result.best_move_uci,
        "best_move_san": result.best_move_san,
        "is_terminal": result.is_terminal,
        "terminal_reason": result.terminal_reason,
        "lines": [
            {
                "rank": line.index,
                "move_uci": line.move_uci,
                "move_san": line.move_san,
                "cp": line.cp,
                "mate": line.mate,
                "pv": line.pv,
            }
            for line in result.lines
        ],
        "engine": result.engine,
        "engine_version": result.engine_version,
    }
    return answer


def _run_live_analysis(
    session_factory,
    engine,
    live_game_id: str,
    ply: int,
    fen_before: str,
    played_uci: str,
    side: str,
) -> None:
    """Background: score one move and broadcast the coach's reading of it."""
    try:
        with session_factory.session_scope() as session:
            game = live_service.load(session, live_game_id)
            if not live_analysis.analysis_allowed(game.state):
                return
            analysis = live_analysis.run_move_analysis(
                engine, game=game, played_uci=played_uci, fen_before=fen_before
            )
            payloads = live_analysis.event_payloads(
                game=game, analysis=analysis, played_uci=played_uci, ply=ply, side=side
            )
            if not analysis.get("available") and analysis.get("reason") in (
                "obsolete",
                "engine_busy",
            ):
                live_service.bump("analysis_cancellations")
                return
            events = live_service.emit_events(
                session, live_game_id=live_game_id, payloads=payloads
            )
        if events:
            HUB.publish(live_game_id, events)
    except Exception as exc:  # noqa: BLE001 — analysis may never break a live game
        logger.warning("Live analysis task failed [live_game=%s]: %s", live_game_id, exc)


# ---------------------------------------------------------------------------
# the WebSocket event stream
# ---------------------------------------------------------------------------


def _record_presence(session_factory, live_game_id: str, player_id: int | None, connected: bool) -> None:
    """Record connection presence and broadcast it (§38, §48).

    A seated player's presence is durable: the opponent sees it, and a disconnect
    stops the clock so time is not lost to a network outage. A spectator's is not
    recorded (there is nothing to persist for someone who is only watching).
    """
    if player_id is None or not session_factory.enabled:
        return
    try:
        with session_factory.session_scope() as session:
            result = live_service.set_connection(
                session, live_game_id=live_game_id, player_id=player_id, connected=connected
            )
        if result and result.get("events"):
            HUB.publish(live_game_id, result["events"])
    except Exception as exc:  # noqa: BLE001 — presence must never break a socket
        logger.debug("Presence update failed [live_game=%s]: %s", live_game_id, exc)


def _authenticate_socket(
    websocket: WebSocket, live_game_id: str, player_id: int | None
) -> str | None:
    """Resolve the socket's role, or ``None`` when the connection is refused.

    The identity is taken from the connection request, never from a message the
    client sends later — a socket cannot promote itself by asking.
    """
    factory = websocket.app.state.session_factory
    if not factory.enabled:
        return None
    with factory.session_scope() as session:
        try:
            record = live_service.require_readable(session, live_game_id, player_id)
        except (NotFoundError, ArgusError):
            return None
        return live_service.viewer_role(record, player_id)


@router.websocket("/games/{live_game_id}/ws")
async def live_socket(
    websocket: WebSocket,
    live_game_id: str,
    player_id: int | None = Query(default=None, ge=1),
    after_sequence: int = Query(default=0, ge=0),
) -> None:
    """Stream a game's events. Authenticated before the upgrade, replayed on connect."""
    role = _authenticate_socket(websocket, live_game_id, player_id)
    if role is None:
        # Refuse before accepting: an unauthenticated socket is never upgraded.
        await websocket.close(code=4401, reason="Not authorized for this game")
        return
    await websocket.accept()
    subscriber = HUB.subscribe(game_id=live_game_id, player_id=player_id, role=role)
    # Announce the connection before the first frame, so a reconnecting client and
    # its opponent both learn of it through the same sequenced event stream.
    await asyncio.to_thread(
        _record_presence, websocket.app.state.session_factory, live_game_id, player_id, True
    )
    try:
        # The first thing a client receives is exactly what it missed.
        await websocket.send_json(
            {
                "type": "sync",
                **_sync_message(
                    websocket.app.state.session_factory, live_game_id, player_id, after_sequence
                ),
            }
        )
        sender = asyncio.create_task(_pump(websocket, subscriber))
        try:
            while True:
                raw = await websocket.receive_json()
                if not isinstance(raw, dict):
                    await websocket.send_json({"type": "error", "error": "bad_message"})
                    continue
                kind = raw.get("type")
                if kind == "ping":
                    await websocket.send_json({"type": "pong"})
                elif kind == "sync":
                    since = raw.get("after_sequence")
                    if not isinstance(since, int) or isinstance(since, bool) or since < 0:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "error": "after_sequence_must_be_a_non_negative_integer",
                            }
                        )
                        continue
                    await websocket.send_json(
                        {
                            "type": "sync",
                            **_sync_message(
                                websocket.app.state.session_factory,
                                live_game_id,
                                player_id,
                                since,
                            ),
                        }
                    )
                else:
                    # Actions are not accepted over the socket: they are
                    # transactional REST calls with authorization and status.
                    await websocket.send_json(
                        {
                            "type": "error",
                            "error": "unsupported_message",
                            "detail": (
                                "This socket carries events only. Send actions to the "
                                "REST endpoints, which validate and persist them."
                            ),
                        }
                    )
        finally:
            sender.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        HUB.unsubscribe(subscriber)
        await asyncio.to_thread(
            _record_presence, websocket.app.state.session_factory, live_game_id, player_id, False
        )


async def _pump(websocket: WebSocket, subscriber) -> None:
    """Forward queued events to one socket, in order, until it closes."""
    try:
        while True:
            event = await subscriber.queue.get()
            await websocket.send_json({"type": "event", "event": event})
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        return


def _sync_message(
    session_factory, live_game_id: str, player_id: int | None, after_sequence: int
) -> dict:
    """The sync reply for a socket. A WebSocket has no ``Request`` to depend on,
    so the factory comes from the app state."""
    with session_factory.session_scope() as session:
        return live_service.sync(
            session, live_game_id=live_game_id, player_id=player_id, after_sequence=after_sequence
        )
