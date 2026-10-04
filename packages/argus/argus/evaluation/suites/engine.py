"""Stockfish integration benchmark (§8/§9). Requires a live engine.

Checks that the wrapper returns correct, perspective-normalised results: every
best move is legal, mate and terminal positions are handled explicitly, MultiPV
width is respected, and two identical requests agree. Skipped, never passed, when
no engine is available.
"""

from __future__ import annotations

import chess

from argus.evaluation.engine_baseline import (
    BASELINE_DEPTH,
    BASELINE_POSITIONS,
    compare_baseline,
    load_baseline,
    sample_position,
)
from argus.evaluation.fixtures import STARTPOS_FEN
from argus.evaluation.results import SuiteResult, check, skipped

MATE_IN_ONE_FEN = "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1"
CHECKMATE_FEN = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
STALEMATE_FEN = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"
#: White is a clean rook up with no immediate mate, so the top line must be a
#: positive centipawn score rather than a mate score.
ROOK_UP_FEN = "8/5kpp/8/8/8/8/5PPP/6KR w - - 0 1"


def engine_suite(context) -> SuiteResult:
    """Engine correctness, perspective, MultiPV, mate and consistency."""
    engine = context.engine
    checks = []

    # --- start position ------------------------------------------------------
    start = engine.analyze_position(STARTPOS_FEN, depth=8, multipv=3)
    board = chess.Board(STARTPOS_FEN)
    legal = {m.uci() for m in board.legal_moves}
    checks.append(
        check(
            "the best move is legal",
            start.best_move_uci in legal,
            detail=f"best={start.best_move_uci}",
            critical=True,
        )
    )
    checks.append(
        check(
            "MultiPV width is respected",
            1 <= len(start.lines) <= 3 and all(1 <= line.index <= len(start.lines) for line in start.lines),
            detail=f"lines={len(start.lines)}",
        )
    )
    checks.append(
        check(
            "every line's first move is legal",
            all(line.move_uci in legal for line in start.lines),
        )
    )

    # --- perspective ---------------------------------------------------------
    rook_up = engine.analyze_position(ROOK_UP_FEN, depth=8, multipv=1)
    top_cp = rook_up.lines[0].cp if rook_up.lines else None
    top_mate = rook_up.lines[0].mate if rook_up.lines else None
    winning = (top_cp is not None and top_cp > 300) or (top_mate is not None and top_mate > 0)
    checks.append(
        check(
            "a winning side's evaluation is positive for the side to move",
            winning,
            detail=f"white up a rook, cp={top_cp}, mate={top_mate}",
            critical=True,
        )
    )

    # --- terminal positions --------------------------------------------------
    mate = engine.analyze_position(CHECKMATE_FEN, depth=8)
    checks.append(
        check(
            "checkmate is terminal, not a search",
            mate.is_terminal and mate.terminal_reason == "checkmate",
            detail=f"terminal={mate.is_terminal}, reason={mate.terminal_reason}",
            critical=True,
        )
    )
    stalemate = engine.analyze_position(STALEMATE_FEN, depth=8)
    checks.append(
        check(
            "stalemate is terminal",
            stalemate.is_terminal and stalemate.terminal_reason == "stalemate",
            detail=f"terminal={stalemate.is_terminal}",
        )
    )

    # --- mate distance -------------------------------------------------------
    mate_in_one = engine.analyze_position(MATE_IN_ONE_FEN, depth=8, multipv=1)
    found_mate = (
        mate_in_one.lines
        and mate_in_one.lines[0].mate is not None
        and mate_in_one.lines[0].mate > 0
    )
    checks.append(
        check(
            "a mate in one is reported as mate",
            bool(found_mate) and mate_in_one.best_move_uci == "a1a8",
            detail=(
                f"best={mate_in_one.best_move_uci}, "
                f"mate={mate_in_one.lines[0].mate if mate_in_one.lines else None}"
            ),
            critical=True,
        )
    )

    # --- consistency ---------------------------------------------------------
    repeat = engine.analyze_position(STARTPOS_FEN, depth=8, multipv=3)
    checks.append(
        check(
            "identical requests agree on the best move",
            repeat.best_move_uci == start.best_move_uci,
            detail=f"{start.best_move_uci} == {repeat.best_move_uci}",
        )
    )

    # --- stored engine-version baseline (§45) --------------------------------
    # Reproducibility is only checkable against a recorded baseline. When the
    # running version has none, that is a skip with a reason (and the recorder
    # writes one) — not a pass, which would claim a comparison that never ran.
    samples = {fen: sample_position(engine, fen, BASELINE_DEPTH) for fen in BASELINE_POSITIONS}
    version = str(engine.info().get("version") or "")
    baseline = load_baseline(version, BASELINE_DEPTH) if version else None
    if baseline is None:
        checks.append(
            skipped(
                "engine output reproduces its stored baseline",
                f"no baseline stored for {version or 'unknown engine version'} at depth "
                f"{BASELINE_DEPTH}; record one with scripts/record_engine_baseline.py",
            )
        )
    else:
        differences = compare_baseline(baseline, samples)
        checks.append(
            check(
                "engine output reproduces its stored baseline",
                not differences,
                detail="; ".join(differences[:3])
                or f"{len(baseline.positions)} positions reproduce at depth {BASELINE_DEPTH}",
                critical=True,
            )
        )

    return SuiteResult(
        suite="engine",
        title="Stockfish correctness and consistency",
        checks=checks,
    )


__all__ = ["engine_suite"]
