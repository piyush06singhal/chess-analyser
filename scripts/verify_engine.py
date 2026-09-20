"""Engine detection and smoke-test utility.

Verifies that a chess engine can be located, started, and that it returns a
structured analysis response. Used during setup and CI.

Usage:
    python scripts/verify_engine.py [--depth 8] [--fen <FEN>]

Exit codes: 0 = engine verified, 1 = verification failed.
"""

from __future__ import annotations

import argparse
import sys

from argus.analysis.engine.stockfish import StockfishEngine, locate_stockfish
from argus.shared.errors import ArgusError, EngineError

DEFAULT_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Stockfish engine availability")
    parser.add_argument("--depth", type=int, default=8, help="Search depth for the smoke test")
    parser.add_argument("--fen", default=DEFAULT_FEN, help="Position to analyze")
    parser.add_argument(
        "--multipv", type=int, default=2, help="Number of lines to request (default 2)"
    )
    args = parser.parse_args()

    path = locate_stockfish()
    if path is None:
        print("FAIL: Stockfish binary not found. Set ARGUS_STOCKFISH_PATH or install Stockfish.")
        return 1
    print(f"Stockfish binary found at: {path}")

    engine = StockfishEngine()
    info = engine.info()
    print(f"Engine info: {info}")
    if not info.get("available"):
        print("FAIL: engine reports unavailable")
        return 1

    try:
        analysis = engine.analyze_position(args.fen, depth=args.depth, multipv=args.multipv)
    except (ArgusError, EngineError) as exc:
        print(f"FAIL: analysis error: {exc}")
        return 1
    finally:
        engine.close()

    print(f"Depth reached: {analysis.depth}")
    print(f"Best move: {analysis.best_move_san} ({analysis.best_move_uci})")
    for line in analysis.lines:
        score = f"cp {line.cp}" if line.cp is not None else f"mate {line.mate}"
        print(f"  line {line.index}: {line.move_san} [{score}] pv={' '.join(line.pv[:5])}")
    if analysis.best_move_uci is None or not analysis.lines:
        print("FAIL: engine returned no usable analysis")
        return 1
    print("OK: engine verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
