"""scikit-learn implementations of the ML pipeline contracts.

This module makes :mod:`argus.ml.pipelines` concrete while keeping every
guardrail: datasets are validated against their :class:`DatasetSpec` before
training, splits enforce minimum sizes, and all reported metrics are measured
on held-out data. scikit-learn is an optional dependency — importing this
module without it raises a clear error.

Design note: scikit-learn is the first backend; the pipeline contracts in
:mod:`argus.ml.pipelines` stay framework-neutral so XGBoost/PyTorch
implementations can be added later without touching consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from argus.ml.dataset import split_dataset
from argus.ml.models import EvaluationMetrics, ModelMetadata, ProblemType
from argus.ml.pipelines import EvaluationPipeline, PredictionModel, PredictionResult, TrainingPipeline
from argus.shared.errors import AnalysisError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

Row = dict[str, str]

try:  # optional dependency: only required when this module is imported
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.metrics import (
        accuracy_score,
        f1_score,
        mean_absolute_error,
        r2_score,
    )
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    SKLEARN_AVAILABLE = True
    _SKLEARN_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - depends on environment
    SKLEARN_AVAILABLE = False
    _SKLEARN_IMPORT_ERROR = exc


def _require_sklearn() -> None:
    if not SKLEARN_AVAILABLE:
        raise AnalysisError(
            "scikit-learn is required for sklearn pipelines; install it with "
            "`pip install 'argus[ml]'`",
            details={"import_error": str(_SKLEARN_IMPORT_ERROR)},
        )


@dataclass(frozen=True)
class SklearnModelSpec:
    """Hyperparameters for the first sklearn backend (kept small on purpose)."""

    n_estimators: int = 300
    max_depth: int | None = None
    random_state: int = 42


def _feature_matrix(rows: Sequence[Row], feature_columns: list[str]) -> list[list[float]]:
    """Numericize feature rows; missing values are treated as 0.0 (documented)."""
    matrix: list[list[float]] = []
    for row in rows:
        vector: list[float] = []
        for column in feature_columns:
            raw = (row.get(column) or "").strip()
            try:
                vector.append(float(raw))
            except ValueError:
                vector.append(0.0)
        matrix.append(vector)
    return matrix


class SklearnPredictionModel(PredictionModel):
    """A trained scikit-learn model with versioned metadata."""

    def __init__(
        self,
        *,
        pipeline: Any,
        metadata: ModelMetadata,
        class_labels: list[str] | None,
    ) -> None:
        self._pipeline = pipeline
        self._metadata = metadata
        self._class_labels = class_labels

    def metadata(self) -> ModelMetadata:
        return self._metadata

    def predict(self, features: Sequence[Row]) -> PredictionResult:
        matrix = _feature_matrix(features, self._metadata.feature_columns)
        predictions = self._pipeline.predict(matrix)
        values: list[Any]
        if self._class_labels is not None:
            values = [self._class_labels[int(index)] for index in predictions]
        else:
            values = [float(value) for value in predictions]
        return PredictionResult(
            model_name=self._metadata.name,
            model_version=self._metadata.version,
            values=values,
        )


class SklearnTrainingPipeline(TrainingPipeline):
    """Random-forest training on validated, deterministically split data.

    The base class's ``train`` already refuses datasets that do not meet the
    spec; this implementation additionally performs the split itself so
    metrics on the held-out splits can never leak into training.
    """

    def __init__(
        self,
        spec: Any,
        *,
        model_spec: SklearnModelSpec | None = None,
    ) -> None:
        super().__init__(spec)
        self._model_spec = model_spec or SklearnModelSpec()

    def train_with_splits(
        self, rows: Sequence[Row], *, validation_share: float = 0.1, test_share: float = 0.1
    ) -> tuple[SklearnPredictionModel, dict[str, EvaluationMetrics]]:
        """Validate, split, fit, and evaluate — one auditable entry point."""
        _require_sklearn()
        splits = split_dataset(list(rows), self.spec, validation_share=validation_share, test_share=test_share)
        model = self.train(splits["train"])
        metrics = {
            split: self._evaluate_model(model, split_rows, split=split)
            for split, split_rows in splits.items()
            if split != "train"
        }
        return model, metrics

    def _train(self, rows: list[Row]) -> SklearnPredictionModel:
        _require_sklearn()
        spec = self.spec
        feature_columns = spec.feature_columns or [
            column for column in rows[0] if column != spec.label_column
        ]
        matrix = _feature_matrix(rows, feature_columns)
        labels = [(row.get(spec.label_column) or "").strip() for row in rows]

        if spec.problem_type is ProblemType.CLASSIFICATION:
            pipeline = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    (
                        "forest",
                        RandomForestClassifier(
                            n_estimators=self._model_spec.n_estimators,
                            max_depth=self._model_spec.max_depth,
                            random_state=self._model_spec.random_state,
                            n_jobs=-1,
                        ),
                    ),
                ]
            )
            pipeline.fit(matrix, labels)
            class_labels = [str(label) for label in pipeline.classes_]
            logger.info(
                "Trained classifier '%s' [rows=%d features=%d classes=%d]",
                spec.name, len(rows), len(feature_columns), len(class_labels),
            )
        else:
            class_labels = None
            pipeline = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    (
                        "forest",
                        RandomForestRegressor(
                            n_estimators=self._model_spec.n_estimators,
                            max_depth=self._model_spec.max_depth,
                            random_state=self._model_spec.random_state,
                            n_jobs=-1,
                        ),
                    ),
                ]
            )
            pipeline.fit(matrix, [float(label) for label in labels])
            logger.info(
                "Trained regressor '%s' [rows=%d features=%d]",
                spec.name, len(rows), len(feature_columns),
            )

        metadata = ModelMetadata(
            name=spec.name,
            version=f"sklearn-rf-{self._model_spec.random_state}",
            problem_type=spec.problem_type,
            trained_at=datetime.now(timezone.utc),
            dataset_name=spec.name,
            feature_columns=feature_columns,
            label_column=spec.label_column,
        )
        return SklearnPredictionModel(
            pipeline=pipeline, metadata=metadata, class_labels=class_labels
        )

    def _evaluate_model(
        self, model: SklearnPredictionModel, rows: list[Row], *, split: str
    ) -> EvaluationMetrics:
        return SklearnEvaluationPipeline(self.spec).evaluate(model, rows, split=split)


class SklearnEvaluationPipeline(EvaluationPipeline):
    """Measured evaluation on held-out data — metrics are computed, never claimed."""

    def __init__(self, spec: Any) -> None:
        self.spec = spec

    def evaluate(
        self, model: PredictionModel, rows: Sequence[Row], *, split: str = "test"
    ) -> EvaluationMetrics:
        _require_sklearn()
        if not isinstance(model, SklearnPredictionModel):
            raise AnalysisError("SklearnEvaluationPipeline requires a SklearnPredictionModel")
        spec = self.spec
        metadata = model.metadata()
        predictions = model.predict(rows).values

        if spec.problem_type is ProblemType.CLASSIFICATION:
            truth = [(row.get(spec.label_column) or "").strip() for row in rows]
            metrics = {
                "accuracy": float(accuracy_score(truth, predictions)),
                "f1_macro": float(f1_score(truth, predictions, average="macro")),
            }
        else:
            truth = [float(row.get(spec.label_column) or 0.0) for row in rows]
            metrics = {
                "mae": float(mean_absolute_error(truth, predictions)),
                "r2": float(r2_score(truth, predictions)),
            }
        logger.info("Evaluated '%s' on %s [rows=%d metrics=%s]", metadata.name, split, len(rows), metrics)
        return EvaluationMetrics(metrics=metrics, dataset_rows=len(rows), dataset_split=split)
