"""Stored engine-version baselines (§45).

Analysis numbers depend on the exact Stockfish build. Every run already *records*
the engine version and configuration, which makes a change visible after the
fact — but nothing detected one. This module stores, per engine version, the
output the engine produced on a fixed set of positions, so the evaluation
framework can assert that the running engine still reproduces it.

Two different facts, deliberately kept apart:

* **No baseline for this version.** Not a failure — a new engine has simply not
  been recorded yet. The check is *skipped with that reason*, and the recorder
  script (`scripts/record_engine_baseline.py`) writes one.
* **A baseline that no longer reproduces.** That is a failure: either the engine
  binary changed under a version string that did not, or the wrapper's
  perspective/parsing changed. Both are exactly what a baseline exists to catch.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

#: Where baselines are stored, relative to the repository root.
DEFAULT_BASELINE_DIR = Path("evaluation") / "baselines"

#: The depth at which the standard positions are pinned. A baseline is only
#: comparable at the depth it was recorded at, so depth is part of the filename.
BASELINE_DEPTH = 8

#: The positions a baseline covers: the start position, a quiet winning position,
#: and a forced mate — a spread that exercises the score, the mate score and the
#: best-move path.
BASELINE_POSITIONS: tuple[str, ...] = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "8/5kpp/8/8/8/8/5PPP/6KR w - - 0 1",
    "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1",
)


class EngineSample(BaseModel):
    """The engine's verdict on one position: its score and its best move."""

    cp: int | None = None
    mate: int | None = None
    best_move: str | None = None


class EngineBaseline(BaseModel):
    """One engine version's recorded output over :data:`BASELINE_POSITIONS`."""

    engine_version: str
    depth: int
    positions: dict[str, EngineSample] = Field(default_factory=dict)


def _slug(version: str) -> str:
    """A filesystem-safe form of an engine version string."""
    return re.sub(r"[^a-z0-9]+", "-", version.strip().lower()).strip("-") or "unknown"


def baseline_path(
    engine_version: str, depth: int = BASELINE_DEPTH, *, directory: Path | None = None
) -> Path:
    """Where the baseline for a version and depth lives."""
    base = directory if directory is not None else DEFAULT_BASELINE_DIR
    return base / f"engine-{_slug(engine_version)}-d{depth}.json"


def load_baseline(
    engine_version: str, depth: int = BASELINE_DEPTH, *, directory: Path | None = None
) -> EngineBaseline | None:
    """The stored baseline for a version, or ``None`` when none is recorded."""
    path = baseline_path(engine_version, depth, directory=directory)
    if not path.exists():
        return None
    return EngineBaseline.model_validate_json(path.read_text())


def save_baseline(baseline: EngineBaseline, *, directory: Path | None = None) -> Path:
    """Write a baseline, creating its directory. Returns the path written."""
    path = baseline_path(baseline.engine_version, baseline.depth, directory=directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline.model_dump(), indent=2, sort_keys=True) + "\n")
    return path


def sample_position(engine, fen: str, depth: int = BASELINE_DEPTH) -> EngineSample:
    """Run the engine on one position and reduce it to a comparable sample.

    The transposition table is cleared first (when the engine supports it): a
    search that inherits earlier hash entries can return a slightly different
    score, or reorder two near-equal moves, which would make a baseline
    unreproducible for reasons that have nothing to do with the engine version.
    """
    clear_hash = getattr(engine, "clear_hash", None)
    if callable(clear_hash):
        clear_hash()
    analysis = engine.analyze_position(fen, depth=depth, multipv=1)
    line = analysis.lines[0] if analysis.lines else None
    return EngineSample(
        cp=line.cp if line is not None else None,
        mate=line.mate if line is not None else None,
        best_move=analysis.best_move_uci,
    )


def build_baseline(
    engine, positions: tuple[str, ...] = BASELINE_POSITIONS, *, depth: int = BASELINE_DEPTH
) -> EngineBaseline:
    """Record the running engine's output on ``positions`` as a baseline.

    The samples are taken *first*: Stockfish reports its version banner only once
    it has started, so reading the version before the first search would record
    ``unknown`` for every engine.
    """
    samples = {fen: sample_position(engine, fen, depth) for fen in positions}
    version = str(engine.info().get("version") or "unknown")
    return EngineBaseline(engine_version=version, depth=depth, positions=samples)


def compare_baseline(
    baseline: EngineBaseline, samples: dict[str, EngineSample]
) -> list[str]:
    """Differences between a stored baseline and fresh samples.

    An empty list means the engine reproduced the baseline exactly. Any entry is
    a real disagreement — never rounded, never tolerated.
    """
    differences: list[str] = []
    for fen, expected in baseline.positions.items():
        got = samples.get(fen)
        if got is None:
            differences.append(f"{fen[:24]}…: not sampled")
            continue
        if (got.cp, got.mate, got.best_move) != (expected.cp, expected.mate, expected.best_move):
            differences.append(
                f"{fen[:24]}…: expected cp={expected.cp} mate={expected.mate} "
                f"best={expected.best_move}, got cp={got.cp} mate={got.mate} best={got.best_move}"
            )
    return differences


__all__ = [
    "BASELINE_DEPTH",
    "BASELINE_POSITIONS",
    "EngineBaseline",
    "EngineSample",
    "baseline_path",
    "build_baseline",
    "compare_baseline",
    "load_baseline",
    "sample_position",
    "save_baseline",
]
