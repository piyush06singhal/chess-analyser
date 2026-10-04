"""The live-game aggregate: the server-side move pipeline.

Every change to a live game goes through :class:`LiveGame`. It is pure — it holds
a :class:`~argus.live.models.LiveGameState` and the moves played, and each method
returns the new state plus the events to broadcast. The API layer persists the
result and fans the events out; this layer decides whether the operation is
allowed at all.

The move pipeline mirrors Phase 12 §7 exactly:

    client move → authorization (API) → version check → side-to-move check →
    legal-move validation → position update → clock update → persist (API) →
    broadcast (API)

No step is skipped, and the client's FEN is never consulted: the board is always
rebuilt from the stored FEN, and only the client's *intent* (a UCI/SAN move) is
trusted as input. Chess rules (legality, mate, stalemate, repetition, fifty-move,
insufficient material) come from python-chess — the same library the rest of
Caissa uses — so live rules cannot diverge from post-game rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import chess
import chess.pgn

from argus.live import clock as clock_mod
from argus.live.models import (
    EventType,
    GameMode,
    GameResult,
    LiveEvent,
    LiveGameError,
    LiveGameState,
    LiveMove,
    LivePlayer,
    LiveStatus,
    Side,
    assert_transition,
)
from argus.shared.time import utcnow as _now

START_FEN = chess.STARTING_FEN


def _side_to_move(board: chess.Board) -> Side:
    return Side.WHITE if board.turn == chess.WHITE else Side.BLACK


@dataclass
class Transition:
    """The result of an operation: the new state and the events to publish."""

    state: LiveGameState
    events: list[LiveEvent] = field(default_factory=list)
    move: LiveMove | None = None


@dataclass
class TerminalInfo:
    status: LiveStatus
    result: GameResult
    reason: str


def detect_terminal(board: chess.Board) -> TerminalInfo | None:
    """The automatic ending of a position, or ``None`` if play continues.

    Claimable endings (threefold, fifty-move) are reported separately by
    :func:`claimable_draws`; only the automatic ones end a game here.
    """
    if board.is_checkmate():
        # The side to move is mated; the opponent won.
        winner = "black" if board.turn == chess.WHITE else "white"
        return TerminalInfo(
            status=LiveStatus.FINISHED,
            result=GameResult.WHITE_WIN if winner == "white" else GameResult.BLACK_WIN,
            reason="checkmate",
        )
    if board.is_stalemate():
        return TerminalInfo(LiveStatus.FINISHED, GameResult.DRAW, "stalemate")
    if board.is_insufficient_material():
        return TerminalInfo(LiveStatus.FINISHED, GameResult.DRAW, "insufficient_material")
    if board.is_fivefold_repetition():
        return TerminalInfo(LiveStatus.FINISHED, GameResult.DRAW, "fivefold_repetition")
    if board.is_seventyfive_moves():
        return TerminalInfo(LiveStatus.FINISHED, GameResult.DRAW, "seventyfive_move_rule")
    return None


def claimable_draws(board: chess.Board) -> list[str]:
    """Draws a player may *claim* (not automatic): threefold and fifty-move."""
    claims: list[str] = []
    if board.can_claim_threefold_repetition():
        claims.append("threefold_repetition")
    if board.can_claim_fifty_moves():
        claims.append("fifty_move_rule")
    return claims


@dataclass
class LiveGame:
    """A live game in memory: state, seats, moves, and the event history."""

    state: LiveGameState
    white: LivePlayer | None = None
    black: LivePlayer | None = None
    moves: list[LiveMove] = field(default_factory=list)
    events: list[LiveEvent] = field(default_factory=list)

    # --- helpers -------------------------------------------------------------

    @property
    def board(self) -> chess.Board:
        """The position with full move history.

        Rebuilt by replaying every stored move from the initial position rather
        than from a bare FEN: repetition and the fifty-move rule need the move
        stack, and a ``Board(fen)`` has none. Replaying also means the board is a
        pure function of stored state — it can never disagree with ``current_fen``.
        """
        board = chess.Board(self.state.initial_fen or START_FEN)
        for move in self.moves:
            board.push(chess.Move.from_uci(move.uci))
        return board

    def player_for(self, player_id: int) -> LivePlayer | None:
        for seat in (self.white, self.black):
            if seat is not None and seat.player_id == player_id:
                return seat
        return None

    def player_for_side(self, side: Side) -> LivePlayer | None:
        return self.white if side is Side.WHITE else self.black

    def seat_filled(self, side: Side) -> bool:
        """Whether a seat is occupied — by a person, the engine, or, in a
        hot-seat game, the same person who took the other chair."""
        if self.state.seat_kind(side) == "engine":
            return True
        if self.player_for_side(side) is not None:
            return True
        return self.state.mode is GameMode.LOCAL and (self.white or self.black) is not None

    def all_seats_filled(self) -> bool:
        return all(self.seat_filled(side) for side in (Side.WHITE, Side.BLACK))

    def side_for(self, player_id: int) -> Side | None:
        seat = self.player_for(player_id)
        return seat.side if seat else None

    def is_member(self, player_id: int) -> bool:
        return self.player_for(player_id) is not None

    def _emit(self, event_type: EventType, payload: dict | None = None) -> LiveEvent:
        """Advance the sequence and version, and produce the next event."""
        self.state = self.state.model_copy(
            update={
                "sequence": self.state.sequence + 1,
                "version": self.state.version + 1,
                "updated_at": _now(),
            }
        )
        event = LiveEvent(
            game_id=self.state.game_id,
            event_type=event_type,
            sequence_number=self.state.sequence,
            game_version=self.state.version,
            payload=payload or {},
        )
        self.events.append(event)
        return event

    def record(self, event_type: EventType, payload: dict | None = None) -> LiveEvent:
        """Append an event that describes the game without changing its position.

        Used for out-of-band information (analysis, coach messages, presence) that
        must still be sequenced: a client replaying events has to see it in the
        same order everyone else did.
        """
        return self._emit(event_type, payload)

    def _transition(self, target: LiveStatus) -> None:
        assert_transition(self.state.status, target)
        self.state = self.state.model_copy(update={"status": target})

    # --- lifecycle -----------------------------------------------------------

    def created(self) -> Transition:
        """Announce a freshly created game as the first event.

        The creator needs an event to acknowledge the game exists, and the
        sequence has to start at 1 so a reconnecting client can tell "no events
        yet" from "I missed everything".
        """
        return Transition(
            state=self.state,
            events=[self._emit(EventType.GAME_CREATED, {"seats": self.seats_payload()})],
        )

    def join(self, player: LivePlayer, *, side: Side | None = None) -> Transition:
        """Seat a player. The first two players take white then black.

        A seat the engine owns is not open to a person: seating a human there
        would silently turn an engine game into a two-human game.
        """
        if self.is_member(player.player_id):
            return Transition(state=self.state, events=[])  # already seated
        if side is not None:
            if self.player_for_side(side) is not None:
                raise LiveGameError("seat_taken", f"The {side.value} seat is already taken.")
            if self.state.seat_kind(side) == "engine":
                raise LiveGameError(
                    "seat_not_joinable", f"The {side.value} seat is played by the engine."
                )
            target = side
            player = player.model_copy(update={"side": side})
        elif self.white is None and self.state.seat_kind(Side.WHITE) != "engine":
            target = Side.WHITE
            player = player.model_copy(update={"side": Side.WHITE})
        elif self.black is None and self.state.seat_kind(Side.BLACK) != "engine":
            target = Side.BLACK
            player = player.model_copy(update={"side": Side.BLACK})
        else:
            raise LiveGameError("game_full", "This game already has two players.")
        if target is Side.WHITE:
            self.white = player
        else:
            self.black = player
        events: list[LiveEvent] = []
        if self.all_seats_filled():
            if self.state.status is LiveStatus.WAITING:
                self._transition(LiveStatus.READY)
            events.append(self._emit(EventType.GAME_READY, {"seats": self.seats_payload()}))
        return Transition(state=self.state, events=events)

    def seats_payload(self) -> dict:
        return {
            "white": self.white.model_dump(mode="json") if self.white else None,
            "black": self.black.model_dump(mode="json") if self.black else None,
        }

    def start(self, *, player_id: int | None = None, now: datetime | None = None) -> Transition:
        now = now or _now()
        # A seat is filled either by a person or by the engine — nothing else may
        # start, so a half-empty game cannot begin and then hang.
        if not self.all_seats_filled():
            raise LiveGameError(
                "not_enough_players",
                "Two players are required before the game can start.",
            )
        self._transition(LiveStatus.ACTIVE)
        running_clock = self.state.clock.start(Side.WHITE, now=now)
        self.state = self.state.model_copy(
            update={
                "clock": running_clock,
                "started_at": now,
                "side_to_move": _side_to_move(self.board),
            }
        )
        event = self._emit(
            EventType.GAME_STARTED,
            {"clock": clock_mod.snapshot(self.state.clock, now=now), "fen": self.state.current_fen},
        )
        return Transition(state=self.state, events=[event])

    # --- the move pipeline ---------------------------------------------------

    def play(
        self,
        *,
        player_id: int,
        uci: str | None = None,
        san: str | None = None,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> Transition:
        """Validate and apply one move, returning the new state and events."""
        now = now or _now()
        if self.state.mode is GameMode.LOCAL:
            # Hot-seat: one person moves both sides, so any member may move for
            # whoever is on the move.
            if not self.is_member(player_id):
                raise LiveGameError("not_a_player", "You are not a player in this game.")
            side = self.state.side_to_move
        else:
            side = self.side_for(player_id)
            if side is None:
                raise LiveGameError("not_a_player", "You are not a player in this game.")
        if expected_version is not None and expected_version != self.state.version:
            raise LiveGameError(
                "stale_version",
                "Another move already changed the game; synchronize and try again.",
                current_version=self.state.version,
            )
        if self.state.status is not LiveStatus.ACTIVE:
            raise LiveGameError(
                "game_not_active",
                f"The game is '{self.state.status.value}' and is not accepting moves.",
                status=self.state.status.value,
            )
        if side is not self.state.side_to_move:
            raise LiveGameError(
                "not_your_turn",
                "It is not your turn.",
                side_to_move=self.state.side_to_move.value,
            )

        if not self.state.clock.running and clock_mod.clock_enabled(self.state.clock_config):
            # A move while the clock is stopped would leave the turn uncharged. In
            # a real game the clock runs from the first move, so start it here
            # rather than silently granting unlimited time.
            self.state = self.state.model_copy(
                update={"clock": self.state.clock.start(side, now=now)}
            )
        return self._apply(uci=uci, san=san, side=side, by="human", now=now)

    def play_engine_move(self, *, uci: str, now: datetime | None = None) -> Transition:
        """Apply the engine's own move in a game whose seat the engine holds.

        This deliberately shares :meth:`_apply` with human moves: the engine gets
        no shortcut past legality, the clock or event ordering, and an engine
        move can never be made in a seat the engine does not own.
        """
        now = now or _now()
        side = self.state.side_to_move
        if self.state.seat_kind(side) != "engine":
            raise LiveGameError(
                "seat_not_engine",
                f"The {side.value} seat is not played by the engine.",
                side=side.value,
            )
        if self.state.status is not LiveStatus.ACTIVE:
            raise LiveGameError(
                "game_not_active",
                f"The game is '{self.state.status.value}' and is not accepting moves.",
            )
        return self._apply(uci=uci, san=None, side=side, by="engine", now=now)

    def _apply(
        self,
        *,
        uci: str | None,
        san: str | None,
        side: Side,
        by: str,
        now: datetime,
    ) -> Transition:
        """Validate and apply one move for ``side`` — the shared pipeline body."""
        board = self.board
        move = self._parse_move(board, uci=uci, san=san)
        fen_before = board.fen()
        san_text = board.san(move)

        # Clock first: a move after the flag falls is a timeout, not a move.
        try:
            new_clock = clock_mod.apply_move(
                self.state.clock, mover=side, config=self.state.clock_config, now=now
            )
        except LiveGameError as exc:
            if exc.code != "timeout":
                raise
            return self._flag(side, now=now)

        board.push(move)
        ply = len(self.moves) + 1
        live_move = LiveMove(
            ply=ply,
            move_number=(ply + 1) // 2,
            side=side,
            san=san_text,
            uci=move.uci(),
            fen_before=fen_before,
            fen_after=board.fen(),
            played_at=now,
            clock_white_ms=new_clock.white_ms,
            clock_black_ms=new_clock.black_ms,
            sequence_number=self.state.sequence + 1,
        )
        self.moves.append(live_move)
        self.state = self.state.model_copy(
            update={
                "current_fen": board.fen(),
                "side_to_move": _side_to_move(board),
                "move_number": board.fullmove_number,
                "last_move": live_move,
                "clock": new_clock,
                "draw_offer": None,  # a move implicitly declines a pending offer
            }
        )
        events: list[LiveEvent] = [
            self._emit(
                EventType.MOVE_MADE,
                {"move": live_move.model_dump(mode="json"), "by": by},
            )
        ]
        events.append(
            self._emit(
                EventType.CLOCK_UPDATED, {"clock": clock_mod.snapshot(new_clock, now=now)}
            )
        )

        terminal = detect_terminal(board)
        if terminal is not None:
            events.extend(self._finish(terminal.status, terminal.result, terminal.reason, now=now))
        return Transition(state=self.state, events=events, move=live_move)

    def _parse_move(self, board: chess.Board, *, uci: str | None, san: str | None) -> chess.Move:
        if uci:
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                raise LiveGameError("illegal_move", f"'{uci}' is not a valid move.") from None
            if move not in board.legal_moves:
                raise LiveGameError(
                    "illegal_move", f"'{uci}' is not legal in this position."
                )
            return move
        if san:
            try:
                return board.parse_san(san)
            except ValueError:
                raise LiveGameError("illegal_move", f"'{san}' is not legal in this position.") from None
        raise LiveGameError("missing_move", "A move (uci or san) is required.")

    # --- endings -------------------------------------------------------------

    def _finish(
        self, status: LiveStatus, result: GameResult, reason: str, *, now: datetime
    ) -> list[LiveEvent]:
        self._transition(status)
        self.state = self.state.model_copy(
            update={
                "result": result,
                "result_reason": reason,
                "ended_at": now,
                "clock": self.state.clock.stop(),
            }
        )
        return [
            self._emit(
                EventType.GAME_FINISHED,
                {"result": result.value, "reason": reason, "status": status.value},
            )
        ]

    def _flag(self, side: Side, *, now: datetime) -> Transition:
        """The side to move ran out of time and loses (standard flag fall)."""
        winner = GameResult.BLACK_WIN if side is Side.WHITE else GameResult.WHITE_WIN
        self.state = self.state.model_copy(
            update={"clock": self._zero_clock(side)}
        )
        events = self._finish(LiveStatus.TIMEOUT, winner, "timeout", now=now)
        return Transition(state=self.state, events=events)

    def _zero_clock(self, side: Side):
        if side is Side.WHITE:
            return self.state.clock.model_copy(update={"white_ms": 0, "running": False})
        return self.state.clock.model_copy(update={"black_ms": 0, "running": False})

    def check_timeout(self, *, now: datetime | None = None) -> Transition | None:
        """Flag the side to move if their clock has run out. ``None`` otherwise."""
        now = now or _now()
        if self.state.status is not LiveStatus.ACTIVE or not self.state.clock.running:
            return None
        side = self.state.side_to_move
        if clock_mod.remaining_ms(self.state.clock, side, now=now) > 0:
            return None
        return self._flag(side, now=now)

    def resign(self, *, player_id: int, now: datetime | None = None) -> Transition:
        now = now or _now()
        side = self.side_for(player_id)
        if side is None:
            raise LiveGameError("not_a_player", "You are not a player in this game.")
        if self.state.status is not LiveStatus.ACTIVE:
            raise LiveGameError("game_not_active", "Only an active game can be resigned.")
        winner = GameResult.BLACK_WIN if side is Side.WHITE else GameResult.WHITE_WIN
        events = [
            self._emit(EventType.PLAYER_RESIGNED, {"side": side.value})
        ]
        events.extend(self._finish(LiveStatus.RESIGNED, winner, "resignation", now=now))
        return Transition(state=self.state, events=events)

    def offer_draw(self, *, player_id: int) -> Transition:
        side = self.side_for(player_id)
        if side is None:
            raise LiveGameError("not_a_player", "You are not a player in this game.")
        if self.state.status is not LiveStatus.ACTIVE:
            raise LiveGameError("game_not_active", "Draws can only be offered in an active game.")
        self.state = self.state.model_copy(update={"draw_offer": side})
        return Transition(
            state=self.state,
            events=[self._emit(EventType.DRAW_OFFERED, {"side": side.value})],
        )

    def accept_draw(self, *, player_id: int, now: datetime | None = None) -> Transition:
        now = now or _now()
        side = self.side_for(player_id)
        if side is None:
            raise LiveGameError("not_a_player", "You are not a player in this game.")
        if self.state.draw_offer is None:
            raise LiveGameError("no_draw_offer", "There is no draw offer to accept.")
        if self.state.draw_offer is side:
            raise LiveGameError("own_draw_offer", "You cannot accept your own draw offer.")
        events = [self._emit(EventType.DRAW_ACCEPTED, {"side": side.value})]
        events.extend(self._finish(LiveStatus.DRAW_AGREED, GameResult.DRAW, "agreement", now=now))
        return Transition(state=self.state, events=events)

    def decline_draw(self, *, player_id: int) -> Transition:
        side = self.side_for(player_id)
        if side is None:
            raise LiveGameError("not_a_player", "You are not a player in this game.")
        if self.state.draw_offer is None:
            raise LiveGameError("no_draw_offer", "There is no draw offer to decline.")
        self.state = self.state.model_copy(update={"draw_offer": None})
        return Transition(
            state=self.state,
            events=[self._emit(EventType.DRAW_DECLINED, {"side": side.value})],
        )

    def claim_draw(self, *, player_id: int, rule: str, now: datetime | None = None) -> Transition:
        """Claim a threefold-repetition or fifty-move draw (§14)."""
        now = now or _now()
        side = self.side_for(player_id)
        if side is None:
            raise LiveGameError("not_a_player", "You are not a player in this game.")
        if self.state.status is not LiveStatus.ACTIVE:
            raise LiveGameError("game_not_active", "Only an active game can be drawn.")
        available = claimable_draws(self.board)
        if rule not in available:
            raise LiveGameError(
                "draw_not_claimable",
                f"The {rule} draw is not available in this position.",
                available=available,
            )
        events = [self._emit(EventType.DRAW_ACCEPTED, {"side": side.value, "rule": rule})]
        events.extend(self._finish(LiveStatus.FINISHED, GameResult.DRAW, rule, now=now))
        return Transition(state=self.state, events=events)

    def abort(self, *, reason: str = "aborted", now: datetime | None = None) -> Transition:
        now = now or _now()
        self._transition(LiveStatus.ABORTED)
        self.state = self.state.model_copy(
            update={"result": GameResult.UNDECIDED, "result_reason": reason, "ended_at": now}
        )
        return Transition(
            state=self.state, events=[self._emit(EventType.GAME_ABORTED, {"reason": reason})]
        )

    def pause(self) -> Transition:
        self._transition(LiveStatus.PAUSED)
        self.state = self.state.model_copy(update={"clock": self.state.clock.stop()})
        return Transition(state=self.state, events=[self._emit(EventType.GAME_PAUSED)])

    def resume(self, *, now: datetime | None = None) -> Transition:
        now = now or _now()
        self._transition(LiveStatus.ACTIVE)
        self.state = self.state.model_copy(
            update={"clock": self.state.clock.start(self.state.side_to_move, now=now)}
        )
        return Transition(
            state=self.state,
            events=[
                self._emit(EventType.GAME_RESUMED, {"clock": clock_mod.snapshot(self.state.clock, now=now)})
            ],
        )

    def set_connection(self, *, player_id: int, connected: bool) -> Transition:
        seat = self.player_for(player_id)
        if seat is None:
            raise LiveGameError("not_a_player", "You are not a player in this game.")
        updated = seat.model_copy(update={"connected": connected})
        if updated.side is Side.WHITE:
            self.white = updated
        else:
            self.black = updated
        event_type = EventType.PLAYER_RECONNECTED if connected else EventType.PLAYER_DISCONNECTED
        others_present = any(
            other is not None and other.player_id != player_id and other.connected
            for other in (self.white, self.black)
        )
        payload = {"player_id": player_id, "side": updated.side.value}
        if not connected and self.state.status is LiveStatus.ACTIVE and not others_present:
            # Nobody is connected: stop the clock so time is not lost to a network
            # outage. Reconnection resumes it.
            self.state = self.state.model_copy(update={"clock": self.state.clock.stop()})
        elif connected and self.state.status is LiveStatus.ACTIVE and not self.state.clock.running:
            self.state = self.state.model_copy(
                update={"clock": self.state.clock.start(self.state.side_to_move)}
            )
        return Transition(state=self.state, events=[self._emit(event_type, payload)])

    # --- export --------------------------------------------------------------

    def to_pgn(self, *, headers: dict | None = None) -> str:
        """A standards-compliant PGN of the moves played (never analysis metadata)."""
        game = chess.pgn.Game()
        board = chess.Board(self.state.initial_fen or START_FEN)
        node = game
        for move in self.moves:
            node = node.add_variation(chess.Move.from_uci(move.uci))
            board.push(chess.Move.from_uci(move.uci))
        white_name = self.white.name if self.white else "White"
        black_name = self.black.name if self.black else "Black"
        game.headers["Event"] = (headers or {}).get("Event", "Caissa Live Game")
        game.headers["Site"] = (headers or {}).get("Site", "Caissa")
        game.headers["Date"] = (self.state.started_at or _now()).strftime("%Y.%m.%d")
        game.headers["White"] = white_name
        game.headers["Black"] = black_name
        game.headers["Result"] = self.state.result.value
        if self.state.clock_config.base_ms:
            game.headers["TimeControl"] = self.state.clock_config.label
        for key, value in (headers or {}).items():
            if key not in game.headers:
                game.headers[key] = value
        exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
        return game.accept(exporter)


__all__ = [
    "START_FEN",
    "LiveGame",
    "TerminalInfo",
    "Transition",
    "claimable_draws",
    "detect_terminal",
]
