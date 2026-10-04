"""Phase 12 live-game service: the API's adapter over ``argus.live``.

The chess reasoning lives in the pure ``argus.live`` package; this module's job is
to load a game from storage, hand it one operation, persist the whole validated
result, and describe it honestly. It is the only place the API layer touches live
state, so the rules for who may do what are stated once, here:

* **the server is authoritative** — a client sends an intent (a move, a resign)
  and receives the resulting state; it never sends a position, a clock, a result
  or a version that is trusted;
* **authorization precedes every action** — a spectator may read a public game and
  nothing more; a stranger may read nothing at all;
* **a refused operation changes nothing** — the transition is computed in memory
  and only written when it is legal, so a stale or illegal move cannot leave a
  half-applied game behind.

When a game reaches a terminal state the service turns it into a normal library
game (real PGN, real moves) so the entire Phase 3–11 pipeline — Stockfish
analysis, Game Intelligence, debrief, training extraction, player profile —
runs on live games unchanged.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import chess
from sqlalchemy.orm import Session

from argus.live import (
    ALLOWED_TRANSITIONS,
    LIVE_METHODOLOGY_VERSION,
    TERMINAL_STATUSES,
    TIME_CONTROL_PRESETS,
    TRAINING_MODE_ASSISTANCE,
    AnalysisMode,
    ClockConfig,
    ClockState,
    CoachLevel,
    GameMode,
    GameResult,
    LiveGame,
    LiveGameConfig,
    LiveGameError,
    LiveGameState,
    LiveMove,
    LivePlayer,
    LiveStatus,
    SeatKind,
    Side,
    TrainingGameMode,
    Visibility,
    build_state,
    coach_permissions,
    coaching_reply,
    snapshot as clock_snapshot,
)
from argus.shared.errors import (
    ArgusError,
    ConflictError,
    InvalidMoveError,
    NotFoundError,
    ValidationError,
)
from argus.shared.logging import get_logger
from argus.shared.time import as_utc as _utc

from argus_api.db.models import LiveGameRecord
from argus_api.services.live_hub import HUB

from argus_api.db.repository import (
    create_live_game,
    get_live_game,
    get_player,
    link_live_game_to_library,
    live_game_events,
    live_game_moves,
    list_live_games,
    save_game_with_analysis,
    save_live_game,
)

logger = get_logger(__name__)

#: How long an invitation stays valid.
INVITE_TTL = timedelta(days=7)
#: How many live games one person may have open at once (engine protection, §45).
MAX_OPEN_GAMES_PER_PLAYER = 8
#: Events returned by a sync request in one page.
SYNC_PAGE_SIZE = 500

#: Game modes a person may create directly from the UI.
CREATABLE_MODES = (
    GameMode.LOCAL,
    GameMode.PRIVATE_MATCH,
    GameMode.TRAINING,
    GameMode.SANDBOX,
)

_TERMINAL_VALUES = (
    LiveStatus.FINISHED.value,
    LiveStatus.RESIGNED.value,
    LiveStatus.TIMEOUT.value,
    LiveStatus.DRAW_AGREED.value,
    LiveStatus.ABORTED.value,
)

#: Domain error class per API code, so a refusal keeps its stable code and status.
_ERROR_CLASS_BY_CODE = {
    "conflict": ConflictError,
    "invalid_move": InvalidMoveError,
    "validation_error": ValidationError,
}



#: Live-game counters (§48). Cheap, in-process, and only ever *counted* — no
#: private game content is recorded here.
COUNTERS: dict[str, int] = {
    "live_games_created": 0,
    "live_games_started": 0,
    "live_games_completed": 0,
    "moves_submitted": 0,
    "moves_rejected": 0,
    "timeouts": 0,
    "draws": 0,
    "resignations": 0,
    "reconnections": 0,
    "sync_failures": 0,
    "analysis_requests": 0,
    "analysis_cancellations": 0,
    "coach_requests": 0,
    "postgame_pipelines": 0,
    "engine_moves": 0,
}


def bump(name: str, value: int = 1) -> None:
    """Increment one live-game counter."""
    COUNTERS[name] = COUNTERS.get(name, 0) + value


def metrics() -> dict:
    """The live-game counters plus the engine/connection gauges."""
    from argus_api.services import live_analysis
    from argus_api.services.live_hub import HUB

    return {
        "counters": dict(COUNTERS),
        "engine": live_analysis.LIMITER.stats(),
        "connections": HUB.stats(),
        "note": (
            "Counters are per process and hold no game content. With more than one "
            "worker, scrape each worker and sum."
        ),
    }


def method() -> dict:
    """The live-game methodology: modes, states, transitions, fair play, limits."""
    return {
        "methodology_version": LIVE_METHODOLOGY_VERSION,
        "modes": [mode.value for mode in GameMode],
        "statuses": [status.value for status in LiveStatus],
        "transitions": {
            status.value: sorted(target.value for target in targets)
            for status, targets in ALLOWED_TRANSITIONS.items()
        },
        "time_controls": {
            label: {"base_seconds": base, "increment_seconds": increment, "label": label}
            for label, (base, increment) in TIME_CONTROL_PRESETS.items()
        },
        "analysis_modes": [mode.value for mode in AnalysisMode],
        "coach_levels": [level.value for level in CoachLevel],
        "training_modes": [
            {"mode": preset.value, "assistance": TRAINING_MODE_ASSISTANCE[preset]}
            for preset in TrainingGameMode
            if preset is not TrainingGameMode.NONE
        ],
        "fair_play": {
            "rule": (
                "A competitive game (local or private match) can never receive engine "
                "move recommendations during play. Caissa forces its analysis mode to "
                "no_analysis and its coach to hints or less, whatever the request says."
            ),
            "competitive_modes": [GameMode.LOCAL.value, GameMode.PRIVATE_MATCH.value],
            "engine_opponent_modes": [GameMode.TRAINING.value, GameMode.SANDBOX.value],
        },
        "limits": {
            "max_open_games_per_player": MAX_OPEN_GAMES_PER_PLAYER,
            "invite_ttl_days": INVITE_TTL.days,
            "sync_page_size": SYNC_PAGE_SIZE,
        },
        "events": [
            "GAME_CREATED",
            "GAME_READY",
            "GAME_STARTED",
            "MOVE_MADE",
            "CLOCK_UPDATED",
            "DRAW_OFFERED",
            "DRAW_ACCEPTED",
            "DRAW_DECLINED",
            "PLAYER_RESIGNED",
            "GAME_FINISHED",
            "GAME_ABORTED",
            "GAME_PAUSED",
            "GAME_RESUMED",
            "PLAYER_DISCONNECTED",
            "PLAYER_RECONNECTED",
            "ANALYSIS_UPDATED",
            "COACH_MESSAGE",
            "SYNC_REQUIRED",
        ],
    }


# ---------------------------------------------------------------------------
# loading and authorization
# ---------------------------------------------------------------------------


def _player_name(db: Session, player_id: int | None) -> str | None:
    if player_id is None:
        return None
    try:
        return get_player(db, str(player_id)).name
    except NotFoundError:
        return f"Player {player_id}"


def _state_from_record(record: LiveGameRecord) -> LiveGameState:
    """Rebuild the canonical state from a stored row.

    Nothing is inferred from the request or the client: every field comes from the
    row the server wrote last time.
    """
    return LiveGameState(
        game_id=record.id,
        version=record.version,
        status=LiveStatus(record.status),
        mode=GameMode(record.mode),
        analysis_mode=AnalysisMode(record.analysis_mode),
        coach_level=CoachLevel(record.coach_level),
        visibility=Visibility(record.visibility),
        rated=bool(record.rated),
        initial_fen=record.initial_fen,
        current_fen=record.current_fen,
        side_to_move=Side(record.side_to_move),
        move_number=record.move_number,
        clock_config=ClockConfig(**(record.clock_config or {})),
        clock=ClockState(**(record.clock or {})),
        result=GameResult(record.result or "*"),
        result_reason=record.result_reason,
        draw_offer=Side(record.draw_offer) if record.draw_offer else None,
        sequence=record.sequence,
        updated_at=record.updated_at or datetime.now(timezone.utc),
        started_at=record.started_at,
        ended_at=record.ended_at,
        methodology_version=record.methodology_version or LIVE_METHODOLOGY_VERSION,
        seats=dict(record.seats or {}),
        engine=dict(record.engine or {}),
        training_mode=record.training_mode or "",
    )


def _seat(db: Session, player_id: int | None, side: Side, record: LiveGameRecord) -> LivePlayer | None:
    if player_id is None:
        return None
    if player_id == record.white_player_id and side is Side.WHITE:
        connected = bool(record.white_connected)
    elif player_id == record.black_player_id and side is Side.BLACK:
        connected = bool(record.black_connected)
    else:
        return None
    return LivePlayer(
        player_id=player_id,
        name=_player_name(db, player_id) or f"Player {player_id}",
        side=side,
        connected=connected,
    )


def load(db: Session, live_game_id: str) -> LiveGame:
    """Load a live game (state + seats + moves) from storage."""
    record = get_live_game(db, live_game_id)
    game = LiveGame(state=_state_from_record(record))
    game.white = _seat(db, record.white_player_id, Side.WHITE, record)
    game.black = _seat(db, record.black_player_id, Side.BLACK, record)
    game.moves = [
        LiveMove(
            ply=row.ply,
            move_number=row.move_number,
            side=Side(row.side),
            san=row.san,
            uci=row.uci,
            fen_before=row.fen_before,
            fen_after=row.fen_after,
            played_at=row.played_at,
            clock_white_ms=row.clock_white_ms,
            clock_black_ms=row.clock_black_ms,
            sequence_number=row.sequence_number,
        )
        for row in live_game_moves(db, live_game_id)
    ]
    if game.moves:
        game.state = game.state.model_copy(update={"last_move": game.moves[-1]})
    return game


def viewer_role(record: LiveGameRecord, player_id: int | None) -> str | None:
    """``"player"``, ``"owner"``, ``"spectator"`` or ``None`` (no access).

    A game that is not public is invisible to everyone but its owner and its
    seated players — authorization, never obscurity.
    """
    if player_id is not None:
        if player_id in (record.white_player_id, record.black_player_id):
            return "player"
        if player_id == record.owner_player_id:
            return "owner"
    if record.visibility == Visibility.PUBLIC.value:
        return "spectator"
    return None


def require_readable(db: Session, live_game_id: str, player_id: int | None) -> LiveGameRecord:
    """Load a live game the caller is allowed to see, or refuse."""
    record = get_live_game(db, live_game_id)
    if viewer_role(record, player_id) is None:
        # A game the caller cannot see does not exist for them.
        raise NotFoundError(
            f"Live game {live_game_id} was not found", details={"live_game_id": live_game_id}
        )
    return record


def require_actionable(db: Session, live_game_id: str, player_id: int | None) -> LiveGameRecord:
    """Load a live game the caller may act on (owner or seated player)."""
    if player_id is None:
        raise ValidationError("A player id is required for this action.")
    record = get_live_game(db, live_game_id)
    role = viewer_role(record, player_id)
    if role in (None, "spectator"):
        raise NotFoundError(
            f"Live game {live_game_id} was not found", details={"live_game_id": live_game_id}
        )
    return record


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def _commit(
    db: Session, record: LiveGameRecord, game: LiveGame, *, new_moves_start: int
) -> list[dict]:
    """Persist one validated transition and return the events to broadcast."""
    state_payload = game.state.model_dump(mode="json")
    new_moves = [move.model_dump(mode="json") for move in game.moves[new_moves_start:]]
    events = [event.to_payload() for event in game.events]
    save_live_game(db, record, state=state_payload, events=events, moves=new_moves)
    return events


def _seat_payload(game: LiveGame, side: Side) -> dict:
    seat = game.player_for_side(side)
    kind = game.state.seat_kind(side)
    return {
        "player_id": seat.player_id if seat else None,
        "name": seat.name if seat else ("Caissa Engine" if kind == SeatKind.ENGINE.value else None),
        "kind": kind,
        "connected": seat.connected if seat else False,
        "rating": seat.rating if seat else None,
    }


def _payload(
    db: Session,
    record: LiveGameRecord,
    game: LiveGame,
    *,
    viewer: str | None,
    extra: dict | None = None,
) -> dict:
    """The full state a client renders: board, clocks, seats, permissions."""
    state = game.state
    session_counts = HUB.session_counts(record.id)
    payload: dict[str, Any] = {
        "game_id": record.id,
        "status": state.status.value,
        "mode": state.mode.value,
        "analysis_mode": state.analysis_mode.value,
        "coach_level": state.coach_level.value,
        "training_mode": state.training_mode,
        "visibility": state.visibility.value,
        "rated": state.rated,
        "variant": record.variant,
        "initial_fen": state.initial_fen,
        "current_fen": state.current_fen,
        "side_to_move": state.side_to_move.value,
        "move_number": state.move_number,
        "version": state.version,
        "sequence": state.sequence,
        "result": state.result.value,
        "result_reason": state.result_reason,
        "draw_offer": state.draw_offer.value if state.draw_offer else None,
        "clock_config": state.clock_config.model_dump(),
        "clock": clock_snapshot(state.clock),
        "seats": dict(state.seats),
        "engine": dict(state.engine) if viewer in ("player", "owner") else {},
        "players": {
            "white": _seat_payload(game, Side.WHITE),
            "black": _seat_payload(game, Side.BLACK),
        },
        "permissions": coach_permissions(state),
        "moves": [move.model_dump(mode="json") for move in game.moves],
        "pgn_available": bool(game.moves),
        # Open sockets per side, so the UI can detect multiple tabs (§18).
        "session_counts": {
            "white": session_counts.get(record.white_player_id, 0) if record.white_player_id else 0,
            "black": session_counts.get(record.black_player_id, 0) if record.black_player_id else 0,
        },
        "library_game_id": record.library_game_id,
        "viewer": viewer,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "ended_at": state.ended_at.isoformat() if state.ended_at else None,
        "methodology_version": state.methodology_version,
        "turn_owner": "engine" if state.is_engine_turn() else "player",
        "legal_moves": (
            [move.uci() for move in game.board.legal_moves]
            if state.status is LiveStatus.ACTIVE
            else []
        ),
    }
    if viewer in ("player", "owner"):
        payload["invite_token"] = record.invite_token
        payload["invite_expires_at"] = (
            record.invite_expires_at.isoformat() if record.invite_expires_at else None
        )
    if extra:
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------


def _validate_start_fen(fen: str | None) -> str:
    """Validate a custom starting position (§59) or return the standard one."""
    if not fen:
        return chess.STARTING_FEN
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise ValidationError(f"That is not a valid FEN: {exc}") from exc
    if not board.is_valid():
        raise ValidationError(
            "That position is not legal (a king may be missing, or the side not to "
            "move may be in check)."
        )
    if board.is_game_over():
        raise ValidationError("That position is already over; there is nothing to play.")
    return board.fen()


def create(
    db: Session,
    *,
    player_id: int,
    mode: str = GameMode.PRIVATE_MATCH.value,
    colour: str = "white",
    time_control: str = "10+5",
    visibility: str = "private",
    rated: bool = False,
    opponent_player_id: int | None = None,
    analysis_mode: str | None = None,
    coach_level: str | None = None,
    training_mode: str = "",        engine_depth: int | None = None,
        start_fen: str | None = None,
    opponents: str | None = None,
) -> dict:
    """Create a live game and seat whoever is already known.

    Competitive modes are clamped to no analysis by ``argus.live``; training and
    sandbox games may enable it and may put the engine in the far seat. A game
    with both seats filled starts immediately — there is nobody left to wait for.
    """
    get_player(db, str(player_id))  # 404 for an unknown player
    try:
        game_mode = GameMode(mode)
    except ValueError:
        raise ValidationError(
            f"'{mode}' is not a live game mode.",
            details={"modes": [m.value for m in CREATABLE_MODES]},
        ) from None
    if game_mode not in CREATABLE_MODES:
        raise ValidationError(f"'{mode}' cannot be created directly.")
    try:
        visibility_enum = Visibility(visibility)
    except ValueError:
        raise ValidationError(f"'{visibility}' is not a visibility setting.") from None
    try:
        clock = ClockConfig.from_preset(time_control)
    except LiveGameError as exc:
        raise ValidationError(exc.message, details=exc.details) from exc
    if colour not in ("white", "black"):
        raise ValidationError("colour must be 'white' or 'black'.")
    creator_side = Side.WHITE if colour == "white" else Side.BLACK
    other_side = creator_side.other

    training_preset = TrainingGameMode.NONE
    if training_mode:
        try:
            training_preset = TrainingGameMode(training_mode)
        except ValueError:
            raise ValidationError(
                f"'{training_mode}' is not a training game mode.",
                details={
                    "modes": [
                        preset.value
                        for preset in TrainingGameMode
                        if preset is not TrainingGameMode.NONE
                    ]
                },
            ) from None

    # Who fills each seat? "engine" puts the engine opposite the creator; a named
    # opponent is seated immediately; a training game with nobody specified plays
    # the engine; everything else waits for a second person.
    wants_engine = (opponents or "").lower() == "engine" or (
        opponents is None and opponent_player_id is None and game_mode is GameMode.TRAINING
    )
    seats: dict[str, str] = {}
    seated: list[tuple[Side, int]] = []
    if wants_engine:
        if game_mode not in (GameMode.TRAINING, GameMode.SANDBOX):
            raise ValidationError(
                "An engine opponent is only available in a training or sandbox game."
            )
        seats = {creator_side.value: SeatKind.HUMAN.value, other_side.value: SeatKind.ENGINE.value}
        seated.append((creator_side, player_id))
    elif opponent_player_id is not None:
        if opponent_player_id == player_id:
            raise ValidationError("You cannot play yourself.")
        get_player(db, str(opponent_player_id))
        seats = {creator_side.value: SeatKind.HUMAN.value, other_side.value: SeatKind.HUMAN.value}
        seated.extend([(creator_side, player_id), (other_side, opponent_player_id)])
    else:
        seats = {creator_side.value: SeatKind.HUMAN.value, other_side.value: SeatKind.OPEN.value}
        seated.append((creator_side, player_id))
    if game_mode is GameMode.LOCAL:
        seats[other_side.value] = SeatKind.HUMAN.value

    open_games = [
        row
        for row in list_live_games(db, player_id=player_id, limit=MAX_OPEN_GAMES_PER_PLAYER + 1)
        if row.status in (LiveStatus.WAITING.value, LiveStatus.READY.value, LiveStatus.ACTIVE.value)
    ]
    if len(open_games) >= MAX_OPEN_GAMES_PER_PLAYER:
        error = ValidationError(
            f"You already have {MAX_OPEN_GAMES_PER_PLAYER} live games in progress. "
            "Finish or abort one before starting another.",
            details={"limit": MAX_OPEN_GAMES_PER_PLAYER, "open": len(open_games)},
        )
        error.code = "live_game_limit"
        raise error

    # Engine strength is set by search *depth* — a real, verifiable lever. Caissa
    # does not expose an Elo dial it cannot honour.
    engine_config: dict = {}
    if engine_depth is not None:
        engine_config["depth"] = max(1, min(int(engine_depth), 30))

    config = LiveGameConfig(
        mode=game_mode,
        visibility=visibility_enum,
        rated=bool(rated),
        clock=clock,
        analysis_mode=AnalysisMode(analysis_mode) if analysis_mode else None,
        coach_level=CoachLevel(coach_level) if coach_level else None,
        training_mode=training_preset,
        seats=seats,
        engine=engine_config,
    )
    game_id = str(uuid.uuid4())
    state = build_state(game_id=game_id, config=config, current_fen=_validate_start_fen(start_fen))

    white_id = next((pid for side, pid in seated if side is Side.WHITE), None)
    black_id = next((pid for side, pid in seated if side is Side.BLACK), None)
    record = create_live_game(
        db,
        game_id=game_id,
        owner_player_id=player_id,
        white_player_id=white_id,
        black_player_id=black_id,
        mode=state.mode.value,
        analysis_mode=state.analysis_mode.value,
        coach_level=state.coach_level.value,
        visibility=state.visibility.value,
        rated=state.rated,
        variant="standard",
        initial_fen=state.initial_fen,
        clock_config=state.clock_config.model_dump(),
        clock=state.clock.model_dump(mode="json"),
        seats=dict(state.seats),
        engine=dict(state.engine),
        training_mode=state.training_mode,
        invite_token=secrets.token_urlsafe(24),
        invite_expires_at=datetime.now(timezone.utc) + INVITE_TTL,
    )

    bump("live_games_created")
    game = LiveGame(state=_state_from_record(record))
    game.created()
    for side, pid in seated:
        game.join(
            LivePlayer(player_id=pid, name=_player_name(db, pid) or f"Player {pid}", side=side),
            side=side,
        )
    events = _save_with_seats(db, record, game, white_id=white_id, black_id=black_id)

    # A game whose seats are already decided starts at once; the start's events
    # join the creation events so a caller sees one ordered stream. The return
    # shape is the same ``{events, state}`` envelope every other action uses — a
    # client must never discover the response shape by whether the game happened
    # to be full.
    if game.state.status is LiveStatus.READY:
        started = start(db, live_game_id=game_id, player_id=player_id)
        events = [*events, *(started.get("events") or [])]
    return {
        "events": events,
        "state": get_payload(db, live_game_id=game_id, player_id=player_id),
    }


def _save_with_seats(
    db: Session,
    record: LiveGameRecord,
    game: LiveGame,
    *,
    white_id: int | None = None,
    black_id: int | None = None,
) -> list[dict]:
    """Persist a game whose seats were decided at creation, returning its events."""
    if white_id:
        record.white_player_id = white_id
    if black_id:
        record.black_player_id = black_id
    events = [event.to_payload() for event in game.events]
    save_live_game(
        db,
        record,
        state=game.state.model_dump(mode="json"),
        events=events,
        moves=[],
    )
    return events


def list_games(
    db: Session, *, player_id: int | None, status: str | None = None, limit: int = 50
) -> dict:
    """"My live games" (and, when no player is given, the public ones)."""
    rows = list_live_games(db, player_id=player_id, status=status, limit=limit)
    games = []
    for row in rows:
        if player_id is None and row.visibility != Visibility.PUBLIC.value:
            continue
        game = load(db, row.id)
        games.append(
            {
                "game_id": row.id,
                "status": row.status,
                "mode": row.mode,
                "visibility": row.visibility,
                "analysis_mode": row.analysis_mode,
                "training_mode": row.training_mode,
                "white": _player_name(db, row.white_player_id),
                "black": _player_name(db, row.black_player_id),
                "move_number": row.move_number,
                "result": row.result,
                "result_reason": row.result_reason,
                "clock": clock_snapshot(game.state.clock),
                "clock_config": game.state.clock_config.model_dump(),
                "version": row.version,
                "library_game_id": row.library_game_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return {"count": len(games), "games": games}


def get_payload(db: Session, *, live_game_id: str, player_id: int | None) -> dict:
    """The full state of a game the caller may read."""
    record = require_readable(db, live_game_id, player_id)
    game = load(db, live_game_id)
    return _payload(db, record, game, viewer=viewer_role(record, player_id))


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------


def _as_argus_error(exc: LiveGameError) -> ArgusError:
    """Map a live-game refusal to a domain error with an honest HTTP status."""
    code_by_kind = {
        "illegal_transition": "conflict",
        "stale_version": "conflict",
        "game_not_active": "conflict",
        "not_your_turn": "conflict",
        "game_full": "conflict",
        "seat_taken": "conflict",
        "seat_not_joinable": "conflict",
        "no_draw_offer": "conflict",
        "own_draw_offer": "conflict",
        "draw_not_claimable": "conflict",
        "timeout": "conflict",
        "not_a_player": "conflict",
        "analysis_not_permitted": "conflict",
        "illegal_move": "invalid_move",
        "missing_move": "invalid_move",
        "unknown_time_control": "validation_error",
        "unsupported_variant": "validation_error",
        "engine_opponent_not_allowed": "validation_error",
        "seat_not_engine": "conflict",
        "not_enough_players": "conflict",
    }
    code = code_by_kind.get(exc.code, "validation_error")
    error_class = _ERROR_CLASS_BY_CODE.get(code, ArgusError)
    return error_class(exc.message, details=dict(exc.details))


def _op(
    db: Session, *, live_game_id: str, player_id: int | None, action: Callable[[LiveGame], Any]
) -> dict:
    """Run one validated operation and persist it.

    The operation is applied to an in-memory copy; nothing is written unless it
    succeeds, so a refused action leaves the stored game untouched.
    """
    record = require_actionable(db, live_game_id, player_id)
    game = load(db, live_game_id)
    new_moves_start = len(game.moves)
    try:
        action(game)
    except LiveGameError as exc:
        raise _as_argus_error(exc) from exc
    events = _commit(db, record, game, new_moves_start=new_moves_start)
    record = get_live_game(db, live_game_id)
    return {
        "events": events,
        "state": _payload(db, record, game, viewer=viewer_role(record, player_id)),
    }


def join(
    db: Session, *, live_game_id: str, player_id: int, invite_token: str | None = None
) -> dict:
    """Take an open seat, or rejoin a game you are already in."""
    get_player(db, str(player_id))
    record = get_live_game(db, live_game_id)
    if viewer_role(record, player_id) is None:
        # Not a member yet: the invitation is the only way in, and it is checked
        # before the game is even loaded.
        if not invite_token or not secrets.compare_digest(invite_token, record.invite_token):
            raise NotFoundError(
                f"Live game {live_game_id} was not found", details={"live_game_id": live_game_id}
            )
        expires = _utc(record.invite_expires_at)
        if expires and expires < datetime.now(timezone.utc):
            raise ValidationError("That invitation has expired.")
    game = load(db, live_game_id)
    if game.is_member(player_id):
        return get_payload(db, live_game_id=live_game_id, player_id=player_id)
    new_moves_start = len(game.moves)
    try:
        game.join(_player_model(db, player_id))
    except LiveGameError as exc:
        raise _as_argus_error(exc) from exc
    seat = game.side_for(player_id)
    if seat is Side.WHITE:
        record.white_player_id = player_id
    elif seat is Side.BLACK:
        record.black_player_id = player_id
    events = _commit(db, record, game, new_moves_start=new_moves_start)
    record = get_live_game(db, live_game_id)
    return {
        "events": events,
        "state": _payload(db, record, game, viewer=viewer_role(record, player_id)),
    }


def _player_model(db: Session, player_id: int) -> LivePlayer:
    player = get_player(db, str(player_id))
    return LivePlayer(player_id=player.id, name=player.name)


def start(db: Session, *, live_game_id: str, player_id: int) -> dict:
    """Start the clocks."""
    result = _op(
        db,
        live_game_id=live_game_id,
        player_id=player_id,
        action=lambda g: g.start(player_id=player_id),
    )
    bump("live_games_started")
    return result


def play_move(
    db: Session,
    *,
    live_game_id: str,
    player_id: int,
    uci: str | None = None,
    san: str | None = None,
    expected_version: int | None = None,
) -> dict:
    """Submit a move for validation and application."""
    try:
        result = _op(
            db,
            live_game_id=live_game_id,
            player_id=player_id,
            action=lambda g: g.play(
                player_id=player_id, uci=uci, san=san, expected_version=expected_version
            ),
        )
    except ArgusError:
        bump("moves_rejected")
        raise
    bump("moves_submitted")
    if result["state"]["status"] in _TERMINAL_VALUES:
        bump("live_games_completed")
    return result


def resign(db: Session, *, live_game_id: str, player_id: int) -> dict:
    result = _op(
        db, live_game_id=live_game_id, player_id=player_id, action=lambda g: g.resign(player_id=player_id)
    )
    bump("resignations")
    bump("live_games_completed")
    return result


def offer_draw(db: Session, *, live_game_id: str, player_id: int) -> dict:
    return _op(
        db,
        live_game_id=live_game_id,
        player_id=player_id,
        action=lambda g: g.offer_draw(player_id=player_id),
    )


def accept_draw(db: Session, *, live_game_id: str, player_id: int) -> dict:
    result = _op(
        db,
        live_game_id=live_game_id,
        player_id=player_id,
        action=lambda g: g.accept_draw(player_id=player_id),
    )
    bump("draws")
    bump("live_games_completed")
    return result


def decline_draw(db: Session, *, live_game_id: str, player_id: int) -> dict:
    return _op(
        db,
        live_game_id=live_game_id,
        player_id=player_id,
        action=lambda g: g.decline_draw(player_id=player_id),
    )


def claim_draw(db: Session, *, live_game_id: str, player_id: int, rule: str) -> dict:
    result = _op(
        db,
        live_game_id=live_game_id,
        player_id=player_id,
        action=lambda g: g.claim_draw(player_id=player_id, rule=rule),
    )
    bump("draws")
    bump("live_games_completed")
    return result


def abort(db: Session, *, live_game_id: str, player_id: int, reason: str = "aborted") -> dict:
    return _op(
        db,
        live_game_id=live_game_id,
        player_id=player_id,
        action=lambda g: g.abort(reason=reason),
    )


def pause(db: Session, *, live_game_id: str, player_id: int) -> dict:
    return _op(db, live_game_id=live_game_id, player_id=player_id, action=lambda g: g.pause())


def resume(db: Session, *, live_game_id: str, player_id: int) -> dict:
    return _op(db, live_game_id=live_game_id, player_id=player_id, action=lambda g: g.resume())


def set_connection(
    db: Session, *, live_game_id: str, player_id: int, connected: bool
) -> dict | None:
    """Record presence. ``None`` when the caller is not a player in this game."""
    record = get_live_game(db, live_game_id)
    if record.status in _TERMINAL_VALUES:
        return None
    if player_id not in (record.white_player_id, record.black_player_id):
        return None
    # Only a genuine return counts as a reconnection: the seat starts connected,
    # so a first connection is not one and must not inflate the counter.
    was_connected = (
        record.white_connected
        if record.white_player_id == player_id
        else record.black_connected
    )
    if connected == was_connected:
        # Nothing changed: a first connection is not a reconnect, and a duplicate
        # tab must not emit a second presence event.
        return None
    if connected:
        bump("reconnections")
    game = load(db, live_game_id)
    new_moves_start = len(game.moves)
    try:
        game.set_connection(player_id=player_id, connected=connected)
    except LiveGameError:
        return None
    if record.white_player_id == player_id:
        record.white_connected = connected
    else:
        record.black_connected = connected
    events = _commit(db, record, game, new_moves_start=new_moves_start)
    record = get_live_game(db, live_game_id)
    return {
        "events": events,
        "state": _payload(db, record, game, viewer="player"),
    }


def check_timeout(db: Session, *, live_game_id: str) -> dict | None:
    """Flag the side to move if their clock has run out. ``None`` when in time."""
    record = get_live_game(db, live_game_id)
    if record.status != LiveStatus.ACTIVE.value:
        return None
    game = load(db, live_game_id)
    new_moves_start = len(game.moves)
    if game.check_timeout() is None:
        return None
    bump("timeouts")
    bump("live_games_completed")
    events = _commit(db, record, game, new_moves_start=new_moves_start)
    record = get_live_game(db, live_game_id)
    return {"events": events, "state": _payload(db, record, game, viewer="player")}


def apply_engine_move(db: Session, *, live_game_id: str, uci: str) -> dict | None:
    """Apply the engine's own move. Refused when the seat is not the engine's."""
    record = get_live_game(db, live_game_id)
    game = load(db, live_game_id)
    if not game.state.is_engine_turn():
        return None
    new_moves_start = len(game.moves)
    try:
        game.play_engine_move(uci=uci)
    except LiveGameError as exc:
        raise _as_argus_error(exc) from exc
    bump("engine_moves")
    events = _commit(db, record, game, new_moves_start=new_moves_start)
    if game.state.status in TERMINAL_STATUSES:
        bump("live_games_completed")
    record = get_live_game(db, live_game_id)
    return {"events": events, "state": _payload(db, record, game, viewer="player")}


# ---------------------------------------------------------------------------
# synchronization (§17, §47)
# ---------------------------------------------------------------------------


def sync(db: Session, *, live_game_id: str, player_id: int | None, after_sequence: int) -> dict:
    """Everything a reconnecting client missed, or a full state if it cannot be.

    A client that reports the last sequence it saw receives exactly the later
    events. If a gap cannot be filled, the reply carries the whole state and
    ``resync: true`` rather than a silently skipping stream.
    """
    record = require_readable(db, live_game_id, player_id)
    game = load(db, live_game_id)
    rows = live_game_events(db, live_game_id, after_sequence=after_sequence, limit=SYNC_PAGE_SIZE)
    events = [
        {
            "event_id": row.event_id,
            "game_id": row.live_game_id,
            "event_type": row.event_type,
            "sequence_number": row.sequence_number,
            "game_version": row.game_version,
            "timestamp": row.created_at.isoformat(),
            "payload": row.payload,
        }
        for row in rows
    ]
    # Walk the gap: any jump in sequence numbers means the client cannot catch up
    # from events alone.
    resync = False
    expected = after_sequence + 1
    for event in events:
        if event["sequence_number"] != expected:
            resync = True
            break
        expected += 1
    if record.sequence >= expected and events:
        resync = True
    if resync:
        bump("sync_failures")
    viewer = viewer_role(record, player_id)
    result: dict[str, Any] = {
        "game_id": record.id,
        "after_sequence": after_sequence,
        "server_sequence": record.sequence,
        "server_version": record.version,
        "count": len(events),
        "events": events,
        "resync": resync,
    }
    if resync:
        result["state"] = _payload(db, record, game, viewer=viewer)
    else:
        result["clock"] = clock_snapshot(game.state.clock)
        result["status"] = record.status
        result["version"] = record.version
    return result


def emit_events(
    db: Session, *, live_game_id: str, payloads: list[tuple[str, dict]]
) -> list[dict]:
    """Sequence and store out-of-band events (analysis, coach messages).

    The events are appended to the same log a move goes to, so a reconnecting
    client replays them in order and the frontend has one stream to reason about.
    """
    from argus.live import EventType

    record = get_live_game(db, live_game_id)
    game = load(db, live_game_id)
    events: list[dict] = []
    for event_type, payload in payloads:
        try:
            event = game.record(EventType(event_type), payload)
        except ValueError:
            continue
        events.append(event.to_payload())
    if not events:
        return []
    save_live_game(db, record, state=game.state.model_dump(mode="json"), events=events, moves=[])
    return events


def stored_analyses(db: Session, *, live_game_id: str, player_id: int | None) -> dict:
    """The analysis events already produced for this game (§25/§28)."""
    record = require_readable(db, live_game_id, player_id)
    rows = live_game_events(db, live_game_id, limit=SYNC_PAGE_SIZE)
    analyses = []
    for row in rows:
        if row.event_type != "ANALYSIS_UPDATED":
            continue
        analyses.append(
            {
                "sequence_number": row.sequence_number,
                "ply": row.payload.get("ply"),
                "analysis": row.payload.get("analysis"),
                "critical_moment": row.payload.get("critical_moment"),
            }
        )
    coaches = [
        {"sequence_number": row.sequence_number, **row.payload}
        for row in rows
        if row.event_type == "COACH_MESSAGE"
    ]
    return {
        "game_id": record.id,
        "analysis_mode": record.analysis_mode,
        "analysis_permitted": record.analysis_mode != AnalysisMode.NO_ANALYSIS.value,
        "count": len(analyses),
        "analyses": analyses,
        "coach_messages": coaches,
        "note": (
            "Live analysis runs only in training and sandbox games; a competitive "
            "game produces no engine analysis until it is over."
        ),
    }


def state_delta(db: Session, *, live_game_id: str, player_id: int | None) -> dict:
    """The minimal live view used by polling clients (clock, status, version)."""
    record = require_readable(db, live_game_id, player_id)
    game = load(db, live_game_id)
    state = game.state
    return {
        "game_id": record.id,
        "status": state.status.value,
        "version": state.version,
        "sequence": state.sequence,
        "current_fen": state.current_fen,
        "side_to_move": state.side_to_move.value,
        "move_number": state.move_number,
        "result": state.result.value,
        "result_reason": state.result_reason,
        "clock": clock_snapshot(state.clock),
        "draw_offer": state.draw_offer.value if state.draw_offer else None,
        "legal_moves": (
            [move.uci() for move in game.board.legal_moves]
            if state.status is LiveStatus.ACTIVE
            else []
        ),
        "turn_owner": "engine" if state.is_engine_turn() else "player",
        "permissions": coach_permissions(state),
        "library_game_id": record.library_game_id,
    }


def invite(db: Session, *, live_game_id: str, player_id: int) -> dict:
    """Return this game's invitation, issuing one if it has expired."""
    record = require_actionable(db, live_game_id, player_id)
    expires = _utc(record.invite_expires_at)
    if not expires or expires < datetime.now(timezone.utc):
        record.invite_token = secrets.token_urlsafe(24)
        record.invite_expires_at = datetime.now(timezone.utc) + INVITE_TTL
        db.commit()
    return {
        "game_id": record.id,
        "invite_token": record.invite_token,
        "invite_expires_at": (
            record.invite_expires_at.isoformat() if record.invite_expires_at else None
        ),
        "join_path": f"/play/{record.id}?invite={record.invite_token}",
        "visibility": record.visibility,
    }


def rotate_invite(db: Session, *, live_game_id: str, player_id: int) -> dict:
    """Invalidate the old invitation and issue a new one."""
    record = require_actionable(db, live_game_id, player_id)
    record.invite_token = secrets.token_urlsafe(24)
    record.invite_expires_at = datetime.now(timezone.utc) + INVITE_TTL
    db.commit()
    return invite(db, live_game_id=live_game_id, player_id=player_id)


def set_visibility(db: Session, *, live_game_id: str, player_id: int, visibility: str) -> dict:
    """Change who may watch: private, unlisted or public."""
    try:
        value = Visibility(visibility)
    except ValueError:
        raise ValidationError(f"'{visibility}' is not a visibility setting.") from None
    record = require_actionable(db, live_game_id, player_id)
    if record.owner_player_id != player_id:
        raise ValidationError("Only the game's owner can change who may watch it.")
    record.visibility = value.value
    db.commit()
    return {"game_id": record.id, "visibility": record.visibility}


# ---------------------------------------------------------------------------
# export and post-game
# ---------------------------------------------------------------------------


def _headers(game: LiveGame) -> dict:
    headers = {
        "Event": "Caissa Live Game",
        "Site": "Caissa",
        "Round": "-",
        "White": game.white.name if game.white else "White",
        "Black": game.black.name if game.black else "Black",
        "Result": game.state.result.value,
    }
    if game.state.clock_config.base_ms:
        headers["TimeControl"] = game.state.clock_config.label
    return headers


def pgn(db: Session, *, live_game_id: str, player_id: int | None) -> dict:
    """A standards-compliant PGN of the moves played (§51/§52)."""
    record = require_readable(db, live_game_id, player_id)
    game = load(db, live_game_id)
    text = game.to_pgn(headers=_headers(game))
    return {
        "game_id": record.id,
        "pgn": text,
        "move_count": len(game.moves),
        "result": game.state.result.value,
        "library_game_id": record.library_game_id,
        "analysis_available": record.library_game_id is not None,
    }


def owner_of(db: Session, *, live_game_id: str) -> int | None:
    """The player who created (and therefore owns) a live game.

    Used by the post-game pipeline to attribute the extracted training material
    and the profile refresh to the right person.
    """
    return get_live_game(db, live_game_id).owner_player_id


def needs_postgame(record: LiveGameRecord) -> bool:
    """Whether this game has finished and has not yet become library data."""
    return (
        record.status in _TERMINAL_VALUES
        and record.status != LiveStatus.ABORTED.value
        and bool(record.white_player_id or record.black_player_id)
        and record.library_game_id is None
        and record.status != LiveStatus.WAITING.value
    )


def finish_pipeline(db: Session, *, live_game_id: str) -> dict:
    """Turn a finished live game into a library game (§29).

    The PGN is generated from the stored moves, parsed by the same importer the
    rest of Caissa uses, and saved as an ordinary game with ``source='live_game'``.
    Everything downstream — Stockfish analysis, Game Intelligence, debrief,
    training extraction, player profile — then works on it with no special case.
    Idempotent: a game that already has a library id returns it.
    """
    from argus.chess_core.pgn import parse_first_game

    record = get_live_game(db, live_game_id)
    if record.library_game_id:
        return {
            "live_game_id": record.id,
            "library_game_id": record.library_game_id,
            "created": False,
        }
    game = load(db, live_game_id)
    if not game.moves:
        raise ValidationError(
            "This game has no moves, so there is nothing to analyse.", code="no_moves"
        )

    pgn_text = game.to_pgn(headers=_headers(game))
    parsed = parse_first_game(pgn_text)
    orm_game = save_game_with_analysis(
        db, parsed, None, pgn_text=pgn_text, source="live_game", source_game_id=record.id
    )
    link_live_game_to_library(db, record, orm_game.id)
    bump("postgame_pipelines")
    logger.info(
        "Live game became a library game [live_game=%s library_game=%s plies=%d result=%s]",
        record.id,
        orm_game.id,
        len(game.moves),
        game.state.result.value,
    )
    return {
        "live_game_id": record.id,
        "library_game_id": orm_game.id,
        "created": True,
        "pgn": pgn_text,
        "result": game.state.result.value,
        "result_reason": game.state.result_reason,
        "move_count": len(game.moves),
    }


def coach_answer(
    db: Session, *, live_game_id: str, player_id: int, question: str | None = None
) -> dict:
    """What the coach may say about the live game right now (§54).

    In a competitive game this never returns an engine move — the safety decision
    is made by ``argus.live.fairplay``, not here, so the endpoint, the agent and
    the UI cannot disagree about it.
    """
    record = require_readable(db, live_game_id, player_id)
    game = load(db, live_game_id)
    state = game.state
    answer = coaching_reply(state, question=question)
    answer["game_id"] = record.id
    answer["status"] = state.status.value
    answer["version"] = state.version
    answer["side_to_move"] = state.side_to_move.value
    answer["training_mode"] = state.training_mode
    if answer["kind"] == "analysis_permitted":
        answer["message"] = (
            "Analysis is permitted in this game. Ask the coach to analyse the "
            "current position and Caissa will run the engine."
        )
    return answer


__all__ = [
    "CREATABLE_MODES",
    "INVITE_TTL",
    "MAX_OPEN_GAMES_PER_PLAYER",
    "abort",
    "accept_draw",
    "apply_engine_move",
    "check_timeout",
    "claim_draw",
    "coach_answer",
    "create",
    "decline_draw",
    "emit_events",
    "finish_pipeline",
    "get_payload",
    "invite",
    "join",
    "list_games",
    "load",
    "COUNTERS",
    "bump",
    "method",
    "metrics",
    "needs_postgame",
    "offer_draw",
    "owner_of",
    "pause",
    "pgn",
    "play_move",
    "resign",
    "resume",
    "rotate_invite",
    "set_connection",
    "set_visibility",
    "start",
    "state_delta",
    "stored_analyses",
    "sync",
    "viewer_role",
]
