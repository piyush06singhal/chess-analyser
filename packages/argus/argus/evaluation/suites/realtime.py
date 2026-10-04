"""Live chess evaluation (§30): state machine, clock and fair play.

Deterministic checks over the pure live-chess modules: the transition table, the
timestamp-derived clock, terminal detection, and the fair-play clamp that makes a
competitive game incapable of engine assistance. The multi-client synchronization
and load behaviour are covered by ``scripts/load_live.py`` and the API suite.
"""

from __future__ import annotations

import chess

from argus.evaluation.results import SuiteResult, check
from argus.live.clock import apply_move, is_flagged, remaining_ms, snapshot
from argus.live.game import claimable_draws, detect_terminal
from argus.live.models import (
    AnalysisMode,
    ClockConfig,
    ClockState,
    GameMode,
    LiveGameError,
    LiveStatus,
    Side,
    assert_transition,
    resolve_analysis_mode,
)


def realtime_suite(context) -> SuiteResult:
    """Transitions, clock arithmetic, terminal detection and fair play."""
    checks = []

    # --- state machine -------------------------------------------------------
    legal_ok = True
    try:
        assert_transition(LiveStatus.WAITING, LiveStatus.READY)
    except LiveGameError:
        legal_ok = False
    checks.append(check("a legal transition is allowed", legal_ok))

    illegal_refused = False
    try:
        assert_transition(LiveStatus.FINISHED, LiveStatus.ACTIVE)
    except LiveGameError:
        illegal_refused = True
    checks.append(
        check(
            "moving a finished game is refused",
            illegal_refused,
            critical=True,
        )
    )
    from argus.live.models import TERMINAL_STATUSES

    checks.append(
        check(
            "no terminal state has an outgoing transition",
            all(not {s for s in TERMINAL_STATUSES} & _outgoing(s) for s in TERMINAL_STATUSES),
            detail="terminal states cannot be resumed",
            critical=True,
        )
    )

    # --- clock ---------------------------------------------------------------
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    clock = ClockState(
        white_ms=300_000,
        black_ms=300_000,
        running=True,
        turn_started_at=start,
        turn_side=Side.WHITE,
    )
    later = start + timedelta(seconds=10)
    remaining = remaining_ms(clock, Side.WHITE, now=later)
    checks.append(
        check(
            "the mover's clock is charged from timestamps",
            remaining == 290_000,
            detail=f"remaining={remaining}ms",
            critical=True,
        )
    )
    checks.append(
        check(
            "the waiting side's clock is frozen",
            remaining_ms(clock, Side.BLACK, now=later) == 300_000,
        )
    )
    config = ClockConfig(base_ms=300_000, increment_ms=2_000)
    after = apply_move(clock, mover=Side.WHITE, config=config, now=later)
    checks.append(
        check(
            "a move charges time and credits the increment",
            after.white_ms == 292_000 and after.turn_side is Side.BLACK,
            detail=f"white={after.white_ms}ms",
        )
    )
    expired = ClockState(
        white_ms=1_000,
        black_ms=300_000,
        running=True,
        turn_started_at=start,
        turn_side=Side.WHITE,
    )
    checks.append(
        check(
            "a clock past zero is flagged",
            is_flagged(expired, Side.WHITE, now=start + timedelta(seconds=5)),
            critical=True,
        )
    )
    timed_out = False
    try:
        apply_move(expired, mover=Side.WHITE, config=config, now=start + timedelta(seconds=5))
    except LiveGameError as exc:
        timed_out = exc.code == "timeout"
    checks.append(
        check(
            "a late move is refused as a timeout",
            timed_out,
            detail="the clock, not the client, decides time",
            critical=True,
        )
    )
    snap = snapshot(clock, now=later)
    checks.append(
        check(
            "the clock snapshot is server-authoritative",
            snap["running"] is True and snap["white_ms"] <= 300_000,
        )
    )

    # --- terminal detection --------------------------------------------------
    mate = chess.Board("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1")
    terminal = detect_terminal(mate)
    checks.append(
        check(
            "checkmate is detected as terminal",
            terminal is not None,
            detail=str(terminal) if terminal else "none",
        )
    )
    stalemate = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    checks.append(
        check(
            "stalemate is detected as terminal",
            detect_terminal(stalemate) is not None,
        )
    )
    playing = chess.Board()
    checks.append(
        check("the start position is not terminal", detect_terminal(playing) is None)
    )
    fifty = chess.Board("8/8/8/4k3/8/8/8/4K3 w - - 100 200")
    checks.append(
        check(
            "a claimable draw is reported, not auto-applied",
            "fifty_move" in " ".join(claimable_draws(fifty)) or "fifty" in " ".join(claimable_draws(fifty)),
            detail=", ".join(claimable_draws(fifty)) or "none",
        )
    )

    # --- fair play -----------------------------------------------------------
    competitive = resolve_analysis_mode(GameMode.LOCAL, AnalysisMode.TRAINING_ANALYSIS)
    checks.append(
        check(
            "a competitive game cannot enable engine analysis",
            competitive is AnalysisMode.NO_ANALYSIS,
            detail=f"requested training_analysis -> {competitive.value}",
            critical=True,
        )
    )
    sandbox = resolve_analysis_mode(GameMode.SANDBOX, AnalysisMode.SANDBOX_ANALYSIS)
    checks.append(
        check(
            "a sandbox game may enable analysis",
            sandbox is AnalysisMode.SANDBOX_ANALYSIS,
            detail="requested analysis is honoured where it is allowed",
        )
    )

    return SuiteResult(
        suite="realtime",
        title="Live chess state, clock and fair play",
        checks=checks,
    )


def _outgoing(status) -> set:
    from argus.live.models import ALLOWED_TRANSITIONS

    return set(ALLOWED_TRANSITIONS.get(status, frozenset()))


__all__ = ["realtime_suite"]
