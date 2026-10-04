"""Phase 9 performance measurement: opponent intelligence at library scale.

The opponent report is pure aggregation over stored analysis — no engine, no
network. These tests measure that it stays that way and that its cost grows with
the library rather than exploding:

* a realistic library (tens of games, hundreds of plies) is aggregated within a
  documented budget;
* the cost scales roughly linearly in the number of games, not quadratically;
* the package contains no engine call at all (the invariant that makes the
  budget meaningful);
* a measurement that never happened is reported as absent, never as zero.

Numbers are printed, not asserted away: the point of the harness is to report
what was measured.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import chess

from argus.opponent_intelligence import (
    OpponentGameInput,
    OpponentIntelligenceService,
    OpponentMoveInput,
    PlayerIdentity,
    coverage_for,
)
from argus.player_intelligence.models import GameOutcome

PACKAGE_DIR = (
    Path(__file__).resolve().parents[1] / "packages" / "argus" / "argus" / "opponent_intelligence"
)

IDENTITY = PlayerIdentity(player_id=1, name="Rival")

#: A generous ceiling for a synthetic library of this size on any dev machine.
BUDGET_MS = 4000.0


def _synthetic_game(index: int, *, plies: int = 60) -> OpponentGameInput:
    """A deterministic, legal, non-trivial game built with python-chess.

    The move chosen at each ply is a function of the ply and the game index, so
    games differ but the whole fixture is reproducible.
    """
    board = chess.Board()
    moves: list[OpponentMoveInput] = []
    for ply in range(1, plies + 1):
        legal = sorted(board.legal_moves, key=lambda move: move.uci())
        if not legal:
            break
        chosen = legal[(ply * 7 + index * 13) % len(legal)]
        san = board.san(chosen)
        moves.append(
            OpponentMoveInput(
                ply=ply,
                move_number=(ply + 1) // 2,
                color="white" if ply % 2 == 1 else "black",
                san=san,
                uci=chosen.uci(),
                fen_before=board.fen(),
                phase="opening" if ply <= 10 else ("endgame" if ply > 40 else "middlegame"),
                centipawn_loss=(ply * 11 + index) % 260,
                best_move_uci=legal[0].uci(),
            )
        )
        board.push(chosen)
    return OpponentGameInput(
        game_id=f"perf-{index}",
        color="white" if index % 2 == 0 else "black",
        opponent_name=f"Rival {index}",
        result="1-0" if index % 3 else "1/2-1/2",
        outcome=GameOutcome.WIN if index % 3 else GameOutcome.DRAW,
        analysis_status="analyzed",
        opening_name="Italian Game",
        time_control="600+5",
        moves=moves,
    )


def _library(size: int) -> list[OpponentGameInput]:
    return [_synthetic_game(index) for index in range(size)]


def _percentile(values: list[float], percent: int) -> float | None:
    """Nearest-rank (ceil): every reported value is one that actually occurred."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(percent / 100 * len(ordered))))
    return ordered[rank - 1]


def _measure(games: list[OpponentGameInput], repeats: int = 3) -> list[float]:
    service = OpponentIntelligenceService()
    durations: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        service.profile(games, IDENTITY)
        durations.append((time.perf_counter() - started) * 1000)
    return durations


def test_a_measurement_that_did_not_happen_is_absent_not_zero() -> None:
    assert _percentile([], 95) is None


def test_percentile_is_nearest_rank() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(values, 0) == 10.0
    assert _percentile(values, 50) == 30.0
    assert _percentile(values, 100) == 50.0


def test_the_package_never_calls_an_engine() -> None:
    """The budget is only meaningful because there is no engine in the path."""
    offenders = []
    for path in PACKAGE_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for needle in ("ChessEngine", "analyze_position", "compare_moves", "subprocess"):
            if needle in source:
                offenders.append(f"{path.name}:{needle}")
    assert offenders == [], offenders


def test_a_realistic_library_is_aggregated_within_budget(capsys) -> None:
    games = _library(40)
    plies = sum(len(game.moves) for game in games)
    durations = _measure(games)
    p50 = _percentile(durations, 50)
    p95 = _percentile(durations, 95)
    print(
        f"\nopponent report: {len(games)} games / {plies} plies → "
        f"p50={p50:.1f}ms p95={p95:.1f}ms"
    )
    assert p50 is not None and p50 > 0
    assert p50 < BUDGET_MS, f"p50 {p50:.1f}ms exceeded the {BUDGET_MS}ms budget"
    assert coverage_for(len(games)) is not None


def test_cost_scales_roughly_linearly_in_the_number_of_games() -> None:
    small = _measure(_library(10), repeats=3)
    large = _measure(_library(40), repeats=3)
    small_p50 = _percentile(small, 50)
    large_p50 = _percentile(large, 50)
    assert small_p50 and large_p50
    ratio = large_p50 / small_p50
    print(f"\nscaling: 4× the games → {ratio:.1f}× the time (linear ≈ 4×)")
    # Quadrupling the games must not blow up far beyond linear; the ceiling is
    # deliberately loose so a loaded CI machine does not fail the suite.
    assert ratio < 12.0, f"ratio {ratio:.1f} suggests super-linear cost"


def test_tendencies_and_phase_statistics_share_the_same_cheap_path() -> None:
    games = _library(20)
    service = OpponentIntelligenceService()
    started = time.perf_counter()
    service.tendencies(games)
    service.phase_statistics(games)
    elapsed = (time.perf_counter() - started) * 1000
    print(f"\ntendencies + phase statistics (20 games): {elapsed:.1f}ms")
    assert elapsed > 0
    assert elapsed < BUDGET_MS
