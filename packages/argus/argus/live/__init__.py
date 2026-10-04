"""Phase 12: live chess, real-time state, and the in-game coach.

The package is deliberately pure. It decides *whether* an operation is legal and
*what* the resulting state and events are; the API layer persists and broadcasts
them. Keeping the rules here (rather than in a route handler) is what makes them
testable without a database, a WebSocket or an engine — and what keeps the client
from ever being the source of truth.

Modules:

* :mod:`argus.live.models` — states, transitions, configuration, events, and the
  fair-play clamps applied at creation.
* :mod:`argus.live.clock` — the server-authoritative chess clock.
* :mod:`argus.live.game` — the aggregate and the server-side move pipeline.
* :mod:`argus.live.fairplay` — what the coach may say during a game.
"""

from __future__ import annotations

from argus.live.clock import (
    DEFAULT_LATENCY_GRACE_MS,
    apply_move,
    clock_enabled,
    elapsed_ms,
    format_ms,
    is_flagged,
    remaining_ms,
    snapshot,
)
from argus.live.fairplay import (
    COMPETITIVE_REFUSAL,
    SAFE_HINTS,
    coach_permissions,
    coaching_reply,
    is_engine_request_permitted,
    refuse_if_not_permitted,
)
from argus.live.game import (
    START_FEN,
    LiveGame,
    TerminalInfo,
    Transition,
    claimable_draws,
    detect_terminal,
)
from argus.live.models import (
    ALLOWED_TRANSITIONS,
    ENGINE_OPPONENT_MODES,
    LIVE_METHODOLOGY_VERSION,
    TERMINAL_STATUSES,
    TIME_CONTROL_PRESETS,
    TRAINING_MODE_ASSISTANCE,
    AnalysisMode,
    ClockConfig,
    ClockState,
    CoachLevel,
    EventType,
    GameMode,
    GameResult,
    LiveEvent,
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
    assert_transition,
    build_state,
    default_analysis_mode,
    resolve_analysis_mode,
    resolve_coach_level,
    training_mode_ceiling,
)


def events_after(events: list[LiveEvent], after_sequence: int) -> tuple[list[LiveEvent], bool]:
    """Events a client missed, plus whether a full resync is needed.

    A client reports the last ``sequence_number`` it saw. If every later event is
    still available, it receives exactly those. If a gap cannot be filled (events
    were pruned, or the request is from before the game's history), the second
    return value is ``True`` and the caller must send the full state instead —
    silently skipping events is how a live board drifts out of sync.
    """
    ordered = sorted(events, key=lambda event: event.sequence_number)
    if after_sequence < 0:
        return ordered, False
    if not ordered:
        return [], after_sequence > 0
    first = ordered[0].sequence_number
    if after_sequence + 1 < first:
        # The client is behind the oldest event we still hold: resync.
        return ordered, True
    missed = [event for event in ordered if event.sequence_number > after_sequence]
    expected = after_sequence + 1
    for event in missed:
        if event.sequence_number != expected:
            return missed, True
        expected += 1
    return missed, False


__all__ = [
    "ALLOWED_TRANSITIONS",
    "COMPETITIVE_REFUSAL",
    "DEFAULT_LATENCY_GRACE_MS",
    "ENGINE_OPPONENT_MODES",
    "LIVE_METHODOLOGY_VERSION",
    "SAFE_HINTS",
    "START_FEN",
    "TERMINAL_STATUSES",
    "TIME_CONTROL_PRESETS",
    "TRAINING_MODE_ASSISTANCE",
    "AnalysisMode",
    "ClockConfig",
    "ClockState",
    "CoachLevel",
    "EventType",
    "GameMode",
    "GameResult",
    "LiveEvent",
    "LiveGame",
    "LiveGameConfig",
    "LiveGameError",
    "LiveGameState",
    "LiveMove",
    "LivePlayer",
    "LiveStatus",
    "SeatKind",
    "Side",
    "TerminalInfo",
    "TrainingGameMode",
    "Transition",
    "Visibility",
    "apply_move",
    "assert_transition",
    "build_state",
    "claimable_draws",
    "clock_enabled",
    "coach_permissions",
    "coaching_reply",
    "default_analysis_mode",
    "detect_terminal",
    "elapsed_ms",
    "events_after",
    "format_ms",
    "is_engine_request_permitted",
    "is_flagged",
    "refuse_if_not_permitted",
    "remaining_ms",
    "resolve_analysis_mode",
    "resolve_coach_level",
    "snapshot",
    "training_mode_ceiling",
]
