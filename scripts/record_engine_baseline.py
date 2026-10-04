#!/usr/bin/env python
"""Record a Stockfish baseline for the running engine version (§45).

Run this **once per engine build, on purpose**, after confirming the results are
what you expect — for example right after a deliberate Stockfish upgrade. The
evaluation framework then compares every future run against it, so a binary that
changes under a version string that did not is caught instead of silently moving
every number.

    python scripts/record_engine_baseline.py [--depth 8] [--out evaluation/baselines]

It refuses to overwrite an existing baseline unless ``--force`` is given: a
baseline is a commitment, and replacing it to make a failing check pass would
defeat the point.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from argus.analysis.engine.stockfish import StockfishEngine, locate_stockfish
from argus.evaluation.engine_baseline import (
    BASELINE_DEPTH,
    BASELINE_POSITIONS,
    baseline_path,
    build_baseline,
    save_baseline,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a Stockfish evaluation baseline.")
    parser.add_argument("--depth", type=int, default=BASELINE_DEPTH)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "evaluation" / "baselines",
        help="Directory to write the baseline into.",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing baseline")
    args = parser.parse_args()

    if locate_stockfish() is None:
        print("FAIL: Stockfish binary not found. Set ARGUS_STOCKFISH_PATH or install Stockfish.")
        return 1

    engine = StockfishEngine()
    try:
        # Sample first: the version banner is only known once the engine has
        # started, so nothing can be keyed on it before the first search.
        baseline = build_baseline(engine, BASELINE_POSITIONS, depth=args.depth)
    finally:
        engine.close()

    if baseline.engine_version == "unknown":
        print("FAIL: the engine did not report a version; refusing to record an unlabeled baseline.")
        return 1

    target = baseline_path(baseline.engine_version, args.depth, directory=args.out)
    if target.exists() and not args.force:
        print(f"A baseline already exists: {target}")
        print("Refusing to overwrite it. Re-run with --force after reviewing the change.")
        return 1

    path = save_baseline(baseline, directory=args.out)

    print(f"Recorded {len(baseline.positions)} position(s) for {baseline.engine_version} at depth {args.depth}")
    print(f"  written: {path}")
    print("Commit this file so future runs compare against it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
