"""Live-game domain models and the state machine.

Phase 12 turns Caissa from a post-game analyst into a live system, and the rule
that keeps that safe is stated once here and enforced everywhere downstream: the
**server is the only source of truth**. A live game's position, clock, status and
version are computed and validated on the server; the browser sends an *intent*
(a UCI move, a resign, a draw offer) and receives the resulting state. Nothing in
this module trusts a client-supplied FEN, result, clock or version.

This module is pure — no database, no network, no engine. It defines:

* the explicit game states and the legal transitions between them
  (:data:`ALLOWED_TRANSITIONS`) — an illegal transition is refused, not applied;
* the configuration a game carries (time control, mode, analysis permission);
* the canonical :class:`LiveGameState` (position, clocks, version, draw offer);
* the event envelope with a monotonic ``sequence_number`` (:class:`LiveEvent`),
  which is how a reconnecting client detects a gap.

The chess rules themselves are not reimplemented: move legality, terminal
detection and PGN come from the existing ``argus.chess_core`` /
``python-chess`` layer (see :mod:`argus.live.game`).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from argus.shared.time import utcnow as _now

LIVE_METHODOLOGY_VERSION = "12.0"


class LiveStatus(str, Enum):
    """Every state a live game can be in. Explicit, never inferred from text."""

    WAITING = "waiting"        # created, waiting for an opponent
    READY = "ready"            # both players present, not started
    ACTIVE = "active"          # clocks running, moves accepted
    PAUSED = "paused"          # clocks stopped by an explicit action
    FINISHED = "finished"      # a normal chess ending (mate, stalemate, draw rules)
    RESIGNED = "resigned"      # a player resigned
    TIMEOUT = "timeout"        # a clock reached zero
    DRAW_AGREED = "draw_agreed"
    ABORTED = "aborted"        # cancelled before/without a result
    DISCONNECTED = "disconnected"  # play suspended because a player dropped


#: Terminal statuses — no further move may ever be applied.
TERMINAL_STATUSES = frozenset(
    {
        LiveStatus.FINISHED,
        LiveStatus.RESIGNED,
        LiveStatus.TIMEOUT,
        LiveStatus.DRAW_AGREED,
        LiveStatus.ABORTED,
    }
)

#: The legal transition graph. Anything not listed is refused with its reason,
#: so a client cannot, e.g., move an aborted game or start a finished one.
ALLOWED_TRANSITIONS: dict[LiveStatus, frozenset[LiveStatus]] = {
    LiveStatus.WAITING: frozenset({LiveStatus.READY, LiveStatus.ABORTED}),
    LiveStatus.READY: frozenset(
        {LiveStatus.ACTIVE, LiveStatus.ABORTED, LiveStatus.WAITING, LiveStatus.DISCONNECTED}
    ),
    LiveStatus.ACTIVE: frozenset(
        {
            LiveStatus.PAUSED,
            LiveStatus.FINISHED,
            LiveStatus.RESIGNED,
            LiveStatus.TIMEOUT,
            LiveStatus.DRAW_AGREED,
            LiveStatus.ABORTED,
            LiveStatus.DISCONNECTED,
        }
    ),
    LiveStatus.PAUSED: frozenset({LiveStatus.ACTIVE, LiveStatus.ABORTED}),
    LiveStatus.DISCONNECTED: frozenset({LiveStatus.ACTIVE, LiveStatus.ABORTED}),
    LiveStatus.FINISHED: frozenset(),
    LiveStatus.RESIGNED: frozenset(),
    LiveStatus.TIMEOUT: frozenset(),
    LiveStatus.DRAW_AGREED: frozenset(),
    LiveStatus.ABORTED: frozenset(),
}


class GameMode(str, Enum):
    LOCAL = "local"
    PRIVATE_MATCH = "private_match"
    TRAINING = "training"
    SANDBOX = "sandbox"


class AnalysisMode(str, Enum):
    """How much engine analysis a live game permits *during play*.

    Competitive modes default to :attr:`NO_ANALYSIS`; this is the fair-play
    switch, and it is a property of the game, chosen at creation, not something a
    client can flip mid-game.
    """

    NO_ANALYSIS = "no_analysis"
    POST_MOVE_ANALYSIS = "post_move_analysis"
    TRAINING_ANALYSIS = "training_analysis"
    SANDBOX_ANALYSIS = "sandbox_analysis"


class CoachLevel(str, Enum):
    """How much the in-game coach may say, independent of raw engine access."""

    OFF = "off"
    HINTS = "hints"
    CONCEPTUAL = "conceptual"
    FULL_ANALYSIS = "full_analysis"


class Visibility(str, Enum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class SeatKind(str, Enum):
    """Who fills a seat.

    An *open* seat is an empty chair a second person can take (hot-seat or an
    invited opponent). An *engine* seat is played by Stockfish through exactly
    the same validated move pipeline a human uses — the server never lets the
    engine bypass legality, clocks or event ordering.
    """

    HUMAN = "human"
    ENGINE = "engine"
    OPEN = "open"


class TrainingGameMode(str, Enum):
    """What kind of practice a training game is (§55).

    This is metadata plus defaults: it says which positions the game starts
    from and what assistance is appropriate, but the fair-play clamps in
    :func:`resolve_analysis_mode` remain the actual authority.
    """

    NONE = ""
    COACH_GAME = "coach_game"
    PUZZLE_GAME = "puzzle_game"
    PRACTICE_GAME = "practice_game"
    OPENING_PRACTICE = "opening_practice"
    ENDGAME_PRACTICE = "endgame_practice"
    FREE_ANALYSIS = "free_analysis"


#: The assistance each training mode allows. The *effective* permission is still
#: computed by :func:`resolve_analysis_mode`; this is the intent the UI shows.
TRAINING_MODE_ASSISTANCE: dict[TrainingGameMode, str] = {
    TrainingGameMode.NONE: "none",
    TrainingGameMode.COACH_GAME: "conceptual",
    TrainingGameMode.PUZZLE_GAME: "conceptual",
    TrainingGameMode.PRACTICE_GAME: "hints",
    TrainingGameMode.OPENING_PRACTICE: "hints",
    TrainingGameMode.ENDGAME_PRACTICE: "hints",
    TrainingGameMode.FREE_ANALYSIS: "full",
}


#: Which game modes may have an engine opponent at all. A competitive game may
#: never be played against the engine through the server (that would hand the
#: player an engine-assisted game under a competitive label).
ENGINE_OPPONENT_MODES = frozenset(
    {GameMode.TRAINING, GameMode.SANDBOX}
)


class Side(str, Enum):
    WHITE = "white"
    BLACK = "black"

    @property
    def other(self) -> "Side":
        return Side.BLACK if self is Side.WHITE else Side.WHITE


class GameResult(str, Enum):
    WHITE_WIN = "1-0"
    BLACK_WIN = "0-1"
    DRAW = "1/2-1/2"
    UNDECIDED = "*"


class EventType(str, Enum):
    GAME_CREATED = "GAME_CREATED"
    GAME_READY = "GAME_READY"
    GAME_STARTED = "GAME_STARTED"
    MOVE_MADE = "MOVE_MADE"
    CLOCK_UPDATED = "CLOCK_UPDATED"
    DRAW_OFFERED = "DRAW_OFFERED"
    DRAW_ACCEPTED = "DRAW_ACCEPTED"
    DRAW_DECLINED = "DRAW_DECLINED"
    PLAYER_RESIGNED = "PLAYER_RESIGNED"
    GAME_FINISHED = "GAME_FINISHED"
    GAME_ABORTED = "GAME_ABORTED"
    GAME_PAUSED = "GAME_PAUSED"
    GAME_RESUMED = "GAME_RESUMED"
    PLAYER_DISCONNECTED = "PLAYER_DISCONNECTED"
    PLAYER_RECONNECTED = "PLAYER_RECONNECTED"
    ANALYSIS_UPDATED = "ANALYSIS_UPDATED"
    COACH_MESSAGE = "COACH_MESSAGE"
    SYNC_REQUIRED = "SYNC_REQUIRED"


#: Time controls offered as presets (base seconds, increment seconds).
TIME_CONTROL_PRESETS: dict[str, tuple[int, int]] = {
    "1+0": (60, 0),
    "3+2": (180, 2),
    "5+0": (300, 0),
    "10+5": (600, 5),
    "15+10": (900, 10),
    "30+0": (1800, 0),
}

#: Modes whose analysis permission is mandatory regardless of what is requested.
#: A competitive game cannot opt into analysis.
_COMPETITIVE_MODES = frozenset({GameMode.LOCAL, GameMode.PRIVATE_MATCH})


class LiveGameError(ValueError):
    """A refused live-game operation, carrying a stable code and reason."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def default_analysis_mode(mode: GameMode) -> AnalysisMode:
    """The safe default: competitive games get no analysis, period."""
    if mode in _COMPETITIVE_MODES:
        return AnalysisMode.NO_ANALYSIS
    if mode is GameMode.SANDBOX:
        return AnalysisMode.SANDBOX_ANALYSIS
    return AnalysisMode.TRAINING_ANALYSIS


def resolve_analysis_mode(mode: GameMode, requested: AnalysisMode | None) -> AnalysisMode:
    """Clamp the requested analysis mode to what the game mode permits.

    A competitive game (local or private match) is forced to ``NO_ANALYSIS`` no
    matter what is requested — the fair-play boundary cannot be opted out of by a
    client. Training and sandbox games may choose, but still may not exceed their
    ceiling.
    """
    if mode in _COMPETITIVE_MODES:
        return AnalysisMode.NO_ANALYSIS
    allowed = (
        {AnalysisMode.NO_ANALYSIS, AnalysisMode.SANDBOX_ANALYSIS}
        if mode is GameMode.SANDBOX
        else {
            AnalysisMode.NO_ANALYSIS,
            AnalysisMode.POST_MOVE_ANALYSIS,
            AnalysisMode.TRAINING_ANALYSIS,
        }
    )
    if requested is None or requested not in allowed:
        return default_analysis_mode(mode)
    return requested


def resolve_coach_level(mode: GameMode, analysis_mode: AnalysisMode, requested: CoachLevel | None) -> CoachLevel:
    """Clamp the coach level to the analysis the game actually permits.

    A coach can never be more permissive than the analysis mode: no analysis
    means the coach may hint or explain concepts, but cannot quote engine lines.
    """
    if analysis_mode is AnalysisMode.NO_ANALYSIS:
        ceiling = CoachLevel.CONCEPTUAL
    elif analysis_mode is AnalysisMode.POST_MOVE_ANALYSIS:
        ceiling = CoachLevel.CONCEPTUAL
    else:
        ceiling = CoachLevel.FULL_ANALYSIS
    if mode in _COMPETITIVE_MODES:
        # Even if analysis were somehow enabled, a competitive game's coach may
        # not hand over a full engine analysis.
        ceiling = min(ceiling, CoachLevel.HINTS, key=_coach_rank)
    if requested is None:
        return ceiling
    return min(requested, ceiling, key=_coach_rank)


_COACH_ORDER = {
    CoachLevel.OFF: 0,
    CoachLevel.HINTS: 1,
    CoachLevel.CONCEPTUAL: 2,
    CoachLevel.FULL_ANALYSIS: 3,
}


def _coach_rank(level: CoachLevel) -> int:
    return _COACH_ORDER[level]


#: The coach level each training-mode assistance level permits. The assistance map
#: states the intent; this makes that intent binding, so a practice game really is
#: hint-only rather than merely labelled one.
_ASSISTANCE_CEILING: dict[str, CoachLevel] = {
    "hints": CoachLevel.HINTS,
    "conceptual": CoachLevel.CONCEPTUAL,
    "full": CoachLevel.FULL_ANALYSIS,
}


def training_mode_ceiling(training_mode: TrainingGameMode) -> CoachLevel | None:
    """The coach ceiling a training mode imposes, or ``None`` when there is no mode.

    ``free_analysis`` returns ``FULL_ANALYSIS`` (a ceiling that clamps nothing),
    while ``practice_game``/``opening_practice``/``endgame_practice`` return
    ``HINTS`` and ``coach_game``/``puzzle_game`` return ``CONCEPTUAL``.
    """
    if training_mode is TrainingGameMode.NONE:
        return None
    assistance = TRAINING_MODE_ASSISTANCE.get(training_mode, "none")
    return _ASSISTANCE_CEILING.get(assistance)


def assert_transition(current: LiveStatus, target: LiveStatus) -> None:
    """Refuse an illegal status transition with the real reason."""
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise LiveGameError(
            "illegal_transition",
            f"A game in '{current.value}' cannot move to '{target.value}'.",
            **{"from": current.value, "to": target.value},
        )


class ClockConfig(BaseModel):
    """Base time and increment, in milliseconds."""

    base_ms: int = Field(default=300_000, ge=0)
    increment_ms: int = Field(default=0, ge=0)

    @property
    def label(self) -> str:
        base = self.base_ms // 1000
        inc = self.increment_ms // 1000
        if base % 60 == 0 and base >= 60:
            minutes = base // 60
            return f"{minutes}+{inc}"
        return f"{base}+{inc}"

    @classmethod
    def from_preset(cls, label: str) -> "ClockConfig":
        if label not in TIME_CONTROL_PRESETS:
            raise LiveGameError(
                "unknown_time_control",
                f"'{label}' is not one of the supported time controls.",
                supported=sorted(TIME_CONTROL_PRESETS),
            )
        base, increment = TIME_CONTROL_PRESETS[label]
        return cls(base_ms=base * 1000, increment_ms=increment * 1000)


class ClockState(BaseModel):
    """Server-authoritative clock.

    ``turn_started_at`` is when the side to move began thinking; remaining time
    is *derived* from it, never stored as a decrementing counter, so a dropped
    connection or a restarted process cannot drift the clock.
    """

    white_ms: int = 0
    black_ms: int = 0
    #: When the current turn began. ``None`` until the clock first runs.
    turn_started_at: datetime | None = None
    #: Whose turn is being charged. Only this side's time ticks down; the other
    #: side's bank is frozen until its turn begins. Without this the clock could
    #: not tell a ticking side from a waiting one.
    turn_side: Side | None = None
    running: bool = False

    def start(self, side: Side, now: datetime | None = None) -> "ClockState":
        return self.model_copy(
            update={"turn_started_at": now or _now(), "turn_side": side, "running": True}
        )

    def stop(self) -> "ClockState":
        # Stopping freezes the clock but keeps ``turn_side`` so the readout still
        # shows which side was on the move when play stopped.
        return self.model_copy(update={"running": False})


class LivePlayer(BaseModel):
    """One seat at the board. ``player_id`` is a real Caissa player.

    ``side`` is optional so a player can be offered to :meth:`LiveGame.join`
    without a seat: the aggregate assigns the first open one and always leaves a
    seated player with a concrete side.
    """

    player_id: int
    name: str
    side: Side | None = None
    connected: bool = True
    joined_at: datetime | None = None
    rating: int | None = None


class LiveMove(BaseModel):
    """One stored live move, with the clock after it — enough for instant review."""

    ply: int
    move_number: int
    side: Side
    san: str
    uci: str
    fen_before: str
    fen_after: str
    played_at: datetime
    clock_white_ms: int
    clock_black_ms: int
    sequence_number: int


class LiveEvent(BaseModel):
    """The event envelope every real-time message uses.

    ``sequence_number`` is monotonic per game; a client that receives 100 then 102
    knows it missed 101 and asks for a synchronization instead of silently
    drifting.
    """

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    game_id: str
    event_type: EventType
    sequence_number: int
    game_version: int
    timestamp: datetime = Field(default_factory=_now)
    payload: dict = Field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "event_id": self.event_id,
            "game_id": self.game_id,
            "event_type": self.event_type.value,
            "sequence_number": self.sequence_number,
            "game_version": self.game_version,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }


class LiveGameState(BaseModel):
    """The canonical, server-authoritative state of a live game."""

    game_id: str
    version: int = 0
    status: LiveStatus = LiveStatus.WAITING
    mode: GameMode = GameMode.PRIVATE_MATCH
    analysis_mode: AnalysisMode = AnalysisMode.NO_ANALYSIS
    coach_level: CoachLevel = CoachLevel.OFF
    visibility: Visibility = Visibility.PRIVATE
    rated: bool = False
    #: The position the game started from. The board is reconstructed by replaying
    #: the moves from here, so repetition and fifty-move rules have real history
    #: (a board rebuilt from a bare FEN has none).
    initial_fen: str = ""
    current_fen: str
    side_to_move: Side = Side.WHITE
    move_number: int = 1
    last_move: LiveMove | None = None
    clock_config: ClockConfig = Field(default_factory=ClockConfig)
    clock: ClockState = Field(default_factory=ClockState)
    result: GameResult = GameResult.UNDECIDED
    result_reason: str | None = None
    draw_offer: Side | None = None
    sequence: int = 0
    updated_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    methodology_version: str = LIVE_METHODOLOGY_VERSION
    #: Who sits in each seat: ``{"white": "human"|"engine"|"open", ...}``.
    seats: dict[str, str] = Field(default_factory=dict)
    #: Engine opponent configuration (depth / skill / elo). Never sent to a
    #: competitive opponent — it is only used to make the engine's own move.
    engine: dict = Field(default_factory=dict)
    #: Which training-game preset this is (§55). Empty for a normal game.
    training_mode: str = ""

    def seat_kind(self, side: Side) -> str:
        return self.seats.get(side.value, "open")

    def is_engine_turn(self) -> bool:
        """Whether the engine must move next in this position."""
        return (
            self.status is LiveStatus.ACTIVE
            and not self.is_terminal
            and self.seat_kind(self.side_to_move) == "engine"
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_competitive(self) -> bool:
        return self.mode in _COMPETITIVE_MODES

    def analysis_permitted(self) -> bool:
        """Whether live engine analysis may run for this game right now."""
        return self.analysis_mode is not AnalysisMode.NO_ANALYSIS

    def to_payload(self) -> dict:
        return self.model_dump(mode="json")


class LiveGameConfig(BaseModel):
    """Everything needed to create a game, validated up front."""

    mode: GameMode = GameMode.PRIVATE_MATCH
    visibility: Visibility = Visibility.PRIVATE
    rated: bool = False
    clock: ClockConfig = Field(default_factory=ClockConfig)
    analysis_mode: AnalysisMode | None = None
    coach_level: CoachLevel | None = None
    variant: str = "standard"
    training_mode: TrainingGameMode = TrainingGameMode.NONE
    #: ``{"white": SeatKind, "black": SeatKind}``. Anything unset becomes
    #: ``open`` for competitive games and ``engine`` for a solo training game.
    seats: dict[str, SeatKind] = Field(default_factory=dict)
    #: Engine opponent settings: ``depth``, ``skill`` (0-20) or ``elo``.
    engine: dict = Field(default_factory=dict)

    def resolved_seats(self) -> dict[Side, SeatKind]:
        """The seats, defaulted sensibly for the mode and validated.

        Competitive games leave both seats open for a second person; a solo
        training or sandbox game defaults the far seat to the engine.
        """
        # A seat defaults to *open* — an empty chair. Who fills it is decided
        # when the game is created (the serving layer knows which colour the
        # creator took) or when a second person joins; nothing is assumed here.
        # Local hot-seat games are the one exception: one person moves both sides.
        default_kind = SeatKind.HUMAN if self.mode is GameMode.LOCAL else SeatKind.OPEN
        resolved: dict[Side, SeatKind] = {}
        for side in (Side.WHITE, Side.BLACK):
            raw = self.seats.get(side.value, default_kind)
            resolved[side] = raw if isinstance(raw, SeatKind) else SeatKind(raw or default_kind)
        if self.mode in _COMPETITIVE_MODES:
            # A competitive game with an engine seat would be engine assistance
            # by construction, so it is refused rather than silently downgraded.
            for side, kind in resolved.items():
                if kind is SeatKind.ENGINE:
                    raise LiveGameError(
                        "engine_opponent_not_allowed",
                        f"A '{self.mode.value}' game cannot have an engine opponent.",
                        side=side.value,
                    )
        return resolved


def build_state(
    *,
    game_id: str,
    config: LiveGameConfig,
    current_fen: str,
    now: datetime | None = None,
) -> LiveGameState:
    """Create a fresh state with the fair-play clamps already applied."""
    now = now or _now()
    if config.variant != "standard":
        raise LiveGameError(
            "unsupported_variant",
            "Only standard chess is supported for live games.",
            variant=config.variant,
        )
    analysis_mode = resolve_analysis_mode(config.mode, config.analysis_mode)
    coach_level = resolve_coach_level(config.mode, analysis_mode, config.coach_level)
    # A training mode's assistance level is binding, not decorative: the chosen
    # exercise sets the ceiling the coach may not exceed (§55).
    mode_ceiling = training_mode_ceiling(config.training_mode)
    if mode_ceiling is not None:
        coach_level = min(coach_level, mode_ceiling, key=_coach_rank)
    seats = config.resolved_seats()
    return LiveGameState(
        game_id=game_id,
        status=LiveStatus.WAITING,
        mode=config.mode,
        analysis_mode=analysis_mode,
        coach_level=coach_level,
        visibility=config.visibility,
        rated=config.rated,
        initial_fen=current_fen,
        current_fen=current_fen,
        clock_config=config.clock,
        clock=ClockState(
            white_ms=config.clock.base_ms, black_ms=config.clock.base_ms, running=False
        ),
        seats={side.value: kind.value for side, kind in seats.items()},
        engine=dict(config.engine),
        training_mode=config.training_mode.value,
        updated_at=now,
    )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "LIVE_METHODOLOGY_VERSION",
    "TERMINAL_STATUSES",
    "TIME_CONTROL_PRESETS",
    "AnalysisMode",
    "ClockConfig",
    "ClockState",
    "CoachLevel",
    "ENGINE_OPPONENT_MODES",
    "TRAINING_MODE_ASSISTANCE",
    "EventType",
    "training_mode_ceiling",
    "GameMode",
    "GameResult",
    "LiveEvent",
    "LiveGameConfig",
    "LiveGameError",
    "LiveGameState",
    "LiveMove",
    "LivePlayer",
    "LiveStatus",
    "SeatKind",
    "Side",
    "TrainingGameMode",
    "Visibility",
    "assert_transition",
    "build_state",
    "default_analysis_mode",
    "resolve_analysis_mode",
    "resolve_coach_level",
]
