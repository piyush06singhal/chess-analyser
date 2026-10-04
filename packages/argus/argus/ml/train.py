"""Train and evaluate a model on an engine-evaluated dataset.

Refuses to train when the dataset fails validation against its spec; metrics
are measured on held-out validation/test splits and reported as measured. The
trained pipeline is persisted with joblib next to its metadata in the model
store so predictions can always be traced to a model version.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from argus.ml.dataset import load_csv, validate_dataset
from argus.ml.models import DatasetSpec, ProblemType
from argus.ml.model_store import ModelStore
from argus.ml.sklearn_pipelines import SklearnModelSpec, SklearnTrainingPipeline
from argus.shared.errors import InsufficientDataError
from argus.shared.logging import configure_logging, get_logger

logger = get_logger(__name__)

# Declared requirements for the per-move evaluation-regression problem. These
# are honest starting minimums — they are raised as the dataset grows.
EVALUATION_REGRESSION_SPEC = DatasetSpec(
    name="move_evaluation_regression",
    description=(
        "Predict a position's engine evaluation (centipawns, White perspective) "
        "from board features. Baseline reference model for the ML track."
    ),
    problem_type=ProblemType.REGRESSION,
    label_column="evaluation_cp",
    feature_columns=[
        "material_balance",
        "mobility_diff",
        "king_safety_diff",
        "isolated_pawns_diff",
        "doubled_pawns_diff",
        "passed_pawns_diff",
        "center_occupied_diff",
        "center_attacked_diff",
        "undeveloped_diff",
        "hanging_diff",
        "total_pieces",
    ],
    min_samples=2000,
    min_validation_samples=300,
    min_test_samples=300,
)


def train_from_csv(csv_path: Path, *, models_dir: Path, random_state: int) -> dict:
    """Load, validate, train, evaluate, persist. Returns a summary dict."""
    rows = load_csv(csv_path)
    spec = EVALUATION_REGRESSION_SPEC
    validation = validate_dataset(rows, spec)
    print(f"Dataset validation: {validation.model_dump_json(indent=2)}")
    if not validation.is_valid:
        raise InsufficientDataError(
            f"Dataset '{csv_path}' does not meet the requirements for "
            f"'{spec.name}'; refusing to train",
            details=validation.model_dump(),
        )

    pipeline = SklearnTrainingPipeline(spec, model_spec=SklearnModelSpec(random_state=random_state))
    model, metrics = pipeline.train_with_splits(rows)

    store = ModelStore(models_dir)
    store.save(model.metadata())
    import joblib

    artifact_path = models_dir / spec.name / f"{model.metadata().version}.joblib"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "pipeline": model._pipeline,  # noqa: SLF001 — persistence boundary of this module
            "feature_columns": model.metadata().feature_columns,
            "label_column": model.metadata().label_column,
            "problem_type": model.metadata().problem_type.value,
        },
        artifact_path,
    )

    evaluation_path = models_dir / spec.name / f"{model.metadata().version}.eval.json"
    evaluation_path.write_text(
        json.dumps(
            {
                "model": spec.name,
                "version": model.metadata().version,
                "dataset": str(csv_path),
                "dataset_rows": len(rows),
                "measured_metrics": {split: m.metrics for split, m in metrics.items()},
                "rows_per_split": {split: m.dataset_rows for split, m in metrics.items()},
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Model artifact: {artifact_path}")
    print(f"Evaluation:     {evaluation_path}")
    for split, metric in metrics.items():
        print(f"  {split}: {metric.metrics}  (rows={metric.dataset_rows})")
    return {
        "model": spec.name,
        "version": model.metadata().version,
        "artifact": str(artifact_path),
        "metrics": {split: m.metrics for split, m in metrics.items()},
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: `python -m argus.ml.train ...`."""
    parser = argparse.ArgumentParser(
        description="Train the evaluation-regression baseline on an engine-evaluated dataset."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/processed/positions_dataset.csv"),
        help="Feature CSV produced by argus.ml.build_dataset",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path("data/models"),
        help="Model store directory (default: data/models)",
    )
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args(argv)

    configure_logging("INFO", "text")
    try:
        summary = train_from_csv(
            args.dataset, models_dir=args.models_dir, random_state=args.random_state
        )
    except InsufficientDataError as exc:
        print(f"TRAINING REFUSED: {exc.message}")
        if exc.details:
            print(json.dumps(exc.details, indent=2))
        print(
            "Bring a larger, real dataset (see data/README.md) and re-run. "
            "No model was trained and no metrics were produced."
        )
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
