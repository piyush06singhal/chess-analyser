#!/usr/bin/env python
"""Record the production model's baseline for a task (§44).

Run this **once, on purpose**, right after a model is promoted to production. The
metrics it was promoted on are written to
``evaluation/baselines/model-<task>-<dataset>.json``; every future candidate is
then compared against those numbers, on the same benchmark, by
``argus.evaluation.model_regression``.

    python scripts/record_model_baseline.py game_outcome
    python scripts/record_model_baseline.py game_outcome --force

It **refuses** when the task has no production model: there is nothing to
baseline, and inventing one would make the comparison meaningless. It also refuses
to overwrite an existing baseline unless ``--force`` is given — a baseline is a
commitment, and replacing it to make a comparison pass would defeat the point.

This script does not promote anything and does not train anything. It records what
is already true.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "argus"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from argus.evaluation.model_baseline import (  # noqa: E402
    model_baseline_path,
    record_model_baseline,
)
from argus.ml.registry import ModelRegistry  # noqa: E402
from argus.shared.errors import ValidationError  # noqa: E402

DEFAULT_MODELS_DIR = REPO_ROOT / "data" / "models"


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a production model baseline (§44).")
    parser.add_argument("task", help="the prediction task to baseline, e.g. game_outcome")
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=DEFAULT_MODELS_DIR,
        help="the model store holding model_registry.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "evaluation" / "baselines",
        help="directory to write the baseline into",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing baseline")
    args = parser.parse_args()

    registry = ModelRegistry.load(args.models_dir)
    try:
        baseline = record_model_baseline(
            registry, args.task, directory=args.out, force=args.force
        )
    except ValidationError as exc:
        print(f"REFUSED: {exc.message}")
        print(
            "No baseline was written. Promote a model that passed its production gate, "
            "then re-run this script."
        )
        return 1

    path = model_baseline_path(baseline.task, baseline.dataset_version, directory=args.out)
    print(f"Recorded baseline for '{baseline.task}' from {baseline.model_id}")
    for key, value in sorted(baseline.metrics.items()):
        print(f"  {key}: {value}")
    print(f"  written: {path}")
    print("Commit this file so future candidates are compared against it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
