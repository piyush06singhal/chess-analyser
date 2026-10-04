"""The server-authoritative chess clock.

A clock that is counted down in the browser is a clock that lies: it drifts, it
pauses when the tab is backgrounded, and it can be edited. Caissa therefore stores
only *how much time each side had* and *when the current turn started*, and
derives the live remaining time from the server clock. The frontend may
interpolate for display, but the number that decides a flag fall is the one
computed here.

Model
-----
* each side has ``white_ms`` / ``black_ms`` — the time banked *before* the
  current turn began;
* ``turn_started_at`` is when the side to move began thinking (``None`` until the
  clock is running);
* remaining for the side to move is ``bank - elapsed``;
* completing a move charges the elapsed time, adds the increment, and hands the
  turn (and the clock) to the opponent.

Nothing here is chess-specific, and nothing here reads a database or a client.
"""

from __future__ import annotations

from datetime import datetime, timezone

from argus.live.models import ClockConfig, ClockState, LiveGameError, Side
from argus.shared.time import utcnow as _now

#: A move that arrives within this window of the flag falling is treated as
#: having been made in time. Network latency is real; a player should not lose on
#: a 40 ms round trip that the server itself caused.
DEFAULT_LATENCY_GRACE_MS = 250


def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def elapsed_ms(turn_started_at: datetime | None, now: datetime) -> int:
    """Milliseconds since the side to move started thinking (0 when not running)."""
    if turn_started_at is None:
        return 0
    delta = (_ensure_aware(now) - _ensure_aware(turn_started_at)).total_seconds() * 1000.0
    return max(0, int(delta))


def side_bank(clock: ClockState, side: Side) -> int:
    return clock.white_ms if side is Side.WHITE else clock.black_ms


def remaining_ms(
    clock: ClockState, side: Side, *, now: datetime | None = None
) -> int:
    """Live remaining time for ``side`` as the server sees it.

    Only the side to move is being charged; the opponent's bank is static until
    their turn begins. The value is clamped at zero — a clock is never negative.
    """
    now = now or _now()
    if not clock.running or clock.turn_started_at is None or clock.turn_side is None:
        return max(0, side_bank(clock, side))
    # Only the side whose turn it is is being charged; the waiting side's bank is
    # frozen. Asking about any other side returns its bank unchanged.
    if side is not clock.turn_side:
        return max(0, side_bank(clock, side))
    return max(0, side_bank(clock, side) - elapsed_ms(clock.turn_started_at, now))


def is_flagged(clock: ClockState, side: Side, *, now: datetime | None = None) -> bool:
    """Whether ``side``'s clock has reached zero."""
    return remaining_ms(clock, side, now=now) <= 0 and clock.running


def apply_move(
    clock: ClockState,
    *,
    mover: Side,
    config: ClockConfig,
    now: datetime | None = None,
    latency_grace_ms: int = DEFAULT_LATENCY_GRACE_MS,
) -> ClockState:
    """Charge the mover for the time used and start the opponent's turn.

    Raises :class:`LiveGameError` with code ``timeout`` when the mover had already
    flagged (with a small grace window for the round trip). The increment is only
    credited when the move was made in time — the standard rule.
    """
    now = now or _now()
    if not clock.running or clock.turn_started_at is None:
        # A move in an unstarted clock is legal; it simply starts the mover's bank
        # and hands the turn to the opponent.
        bank = side_bank(clock, mover)
        updated = clock.model_copy(
            update={
                "white_ms": clock.white_ms if mover is not Side.WHITE else bank,
                "black_ms": clock.black_ms if mover is not Side.BLACK else bank,
                "turn_started_at": now,
                "turn_side": mover.other,
                "running": True,
            }
        )
        return updated

    used = elapsed_ms(clock.turn_started_at, now)
    bank = side_bank(clock, mover)
    over_time = used > bank + latency_grace_ms
    if over_time:
        raise LiveGameError(
            "timeout",
            "The move arrived after this side's clock had already reached zero.",
            side=mover.value,
            used_ms=used,
            remaining_ms=max(0, bank - used),
        )
    # A move within the grace window still cannot leave the clock negative.
    charged = min(used, bank)
    new_bank = max(0, bank - charged + config.increment_ms)
    if mover is Side.WHITE:
        updated = clock.model_copy(update={"white_ms": new_bank})
    else:
        updated = clock.model_copy(update={"black_ms": new_bank})
    return updated.model_copy(
        update={"turn_started_at": now, "turn_side": mover.other, "running": True}
    )


def snapshot(clock: ClockState, *, now: datetime | None = None) -> dict:
    """The clock as both a server fact and a client display value.

    ``remaining_ms`` is authoritative; ``display`` is a convenience the UI may use
    but must not treat as the truth.
    """
    now = now or _now()
    white = remaining_ms(clock, Side.WHITE, now=now)
    black = remaining_ms(clock, Side.BLACK, now=now)
    # Only the side to move is actually ticking; the other value is its bank.
    return {
        "white_ms": white,
        "black_ms": black,
        "running": clock.running,
        "turn_side": clock.turn_side.value if clock.turn_side else None,
        "turn_started_at": clock.turn_started_at.isoformat() if clock.turn_started_at else None,
        "server_time": now.isoformat(),
        "display": {"white": format_ms(white), "black": format_ms(black)},
    }


def format_ms(ms: int) -> str:
    """``m:ss`` above a minute, ``s.d`` below it — the standard clock readout."""
    ms = max(0, ms)
    total_seconds = ms // 1000
    if total_seconds < 60:
        return f"{total_seconds}.{ms % 1000 // 100}"
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def clock_enabled(config: ClockConfig) -> bool:
    """A zero base time means the game is untimed."""
    return config.base_ms > 0


__all__ = [
    "DEFAULT_LATENCY_GRACE_MS",
    "apply_move",
    "clock_enabled",
    "elapsed_ms",
    "format_ms",
    "is_flagged",
    "remaining_ms",
    "side_bank",
    "snapshot",
]
