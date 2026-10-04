"""Prediction model interface and the baseline models.

The order of work here is deliberate and is the whole point of the phase:

1. ``majority_class`` — the trivial answer. Any model that cannot beat it has
   learned nothing, and a result that omits this comparison is meaningless.
2. ``rating_based`` — the strong, cheap, legitimate baseline for chess. Ratings
   encode an enormous amount of information; a model that cannot beat *this* on
   a pre-game task has no reason to exist.
3. ``logistic_regression`` — the first model that can only work if the
   engineered features carry linear signal.
4. ``tree_ensemble`` — nonlinearity and interactions, but also the first model
   capable of memorising. It is evaluated with the same splits as the others.

Missing values are handled honestly: a feature that is absent is imputed with a
**training-set median** (never a test-set statistic, which would leak), and the
imputation values are stored with the model. Tree models receive the raw NaNs,
because they can split on missingness instead of guessing a value.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from argus.ml.metrics import ClassificationMetrics, classification_metrics
from argus.ml.models import BaselineKind, ModelStatus, ProblemType
from argus.shared.errors import ValidationError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

Row = dict[str, Any]


class ModelCard(BaseModel):
    """What a model is, and everything needed to reproduce it."""

    name: str
    kind: str
    problem_type: ProblemType = ProblemType.CLASSIFICATION
    classes: list[str] = Field(default_factory=list)
    feature_names: list[str] = Field(default_factory=list)
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    #: Values used to impute missing features, fitted on the training split only.
    imputation: dict[str, float] = Field(default_factory=dict)
    #: Standardisation fitted on the training split only.
    standardisation: dict[str, tuple[float, float]] = Field(default_factory=dict)
    missing_indicator_columns: list[str] = Field(default_factory=list)
    fitted_rows: int = 0
    feature_version: str = ""
    dataset_version: str = ""
    status: ModelStatus = ModelStatus.EXPERIMENTAL
    notes: list[str] = Field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "classes": self.classes,
            "features": len(self.feature_names),
            "fitted_rows": self.fitted_rows,
            "status": self.status.value,
        }


class BasePredictionModel(ABC):
    """Common contract: fit, predict, evaluate, persist.

    Every model must expose ``predict_proba`` because a number without a
    probability cannot be calibrated, and an uncalibrated number must not be
    presented as a probability.
    """

    kind: str = "base"

    def __init__(
        self,
        *,
        name: str | None = None,
        classes: Sequence[str] | None = None,
        random_state: int = 42,
    ) -> None:
        self.name = name or self.kind
        self.random_state = random_state
        self.card = ModelCard(
            name=self.name,
            kind=self.kind,
            classes=list(classes or []),
        )
        self._params: dict[str, Any] = {}

    # --- interface ----------------------------------------------------------

    @abstractmethod
    def _fit(self, matrix: np.ndarray, labels: np.ndarray) -> None:
        """Fit on an already-prepared numeric matrix."""

    @abstractmethod
    def _probabilities(self, matrix: np.ndarray) -> np.ndarray:
        """Class probabilities for an already-prepared numeric matrix."""

    #: Tree models want raw NaNs (they can use missingness); linear models need
    #: imputation instead. Declared so the preparation step is not a guess.
    handles_missing_values: bool = False

    def fit(
        self,
        rows: Sequence[Row],
        labels: Sequence[str],
        *,
        feature_names: Sequence[str] | None = None,
        feature_version: str = "",
        dataset_version: str = "",
        **hyperparameters: Any,
    ) -> "BasePredictionModel":
        """Fit the model. Imputation and scaling are fitted here, on train only."""
        if not len(rows):
            raise ValidationError(f"{self.name}: cannot fit on zero rows")
        if len(rows) != len(labels):
            raise ValidationError(f"{self.name}: {len(rows)} rows but {len(labels)} labels")

        names = list(feature_names or sorted({key for row in rows for key in row}))
        resolved_classes = self.card.classes or sorted(set(labels))
        self.card = self.card.model_copy(
            update={
                "classes": resolved_classes,
                "feature_names": names,
                "fitted_rows": len(rows),
                "feature_version": feature_version,
                "dataset_version": dataset_version,
                "hyperparameters": {**hyperparameters, "random_state": self.random_state},
            }
        )
        self._params.update(hyperparameters)

        raw = self._raw_matrix(rows, names)
        prepared = self._prepare(raw, fitting=True)
        index = {value: position for position, value in enumerate(resolved_classes)}
        encoded = np.array([index.get(label, 0) for label in labels], dtype=int)
        self._fit(prepared, encoded)
        return self

    def predict_proba(self, rows: Sequence[Row]) -> list[list[float]]:
        """Class probabilities in the model's fixed class order."""
        if not rows:
            return []
        matrix = self._prepare(self._raw_matrix(rows, self.card.feature_names), fitting=False)
        return [[float(value) for value in row] for row in self._probabilities(matrix)]

    def predict(self, rows: Sequence[Row]) -> list[str]:
        """Most likely class per row."""
        probabilities = self.predict_proba(rows)
        classes = self.card.classes
        return [classes[int(np.argmax(row))] for row in probabilities]

    def evaluate(
        self,
        rows: Sequence[Row],
        labels: Sequence[str],
        *,
        split: str = "test",
        binary: bool = False,
        positive_class: str | None = None,
    ) -> ClassificationMetrics:
        """Measure the model on held-out rows."""
        predictions = self.predict(rows)
        probabilities = self.predict_proba(rows)
        metrics = classification_metrics(
            list(labels),
            predictions,
            probabilities,
            classes=self.card.classes,
            binary=binary,
            positive_class=positive_class,
        )
        metrics.notes.append(f"Measured on the '{split}' split of {len(rows)} row(s).")
        return metrics

    def feature_importance(self) -> dict[str, float]:
        """Relative importance per feature, when the model can say.

        An empty mapping means "this model does not expose importances" — which
        is honest, and better than a made-up ranking.
        """
        return {}

    # --- preparation --------------------------------------------------------

    def _raw_matrix(self, rows: Sequence[Row], names: Sequence[str]) -> np.ndarray:
        matrix = np.full((len(rows), len(names)), np.nan, dtype=float)
        for row_index, row in enumerate(rows):
            for column_index, name in enumerate(names):
                value = row.get(name)
                if value is None or value == "":
                    continue
                if isinstance(value, bool):
                    matrix[row_index, column_index] = 1.0 if value else 0.0
                elif isinstance(value, (int, float)):
                    matrix[row_index, column_index] = float(value)
                else:
                    # Non-numeric features (ECO codes) are not silently one-hot
                    # encoded here: that would create a feature the model card
                    # does not list, and a category unknown at prediction time
                    # would then map to all-zero columns without saying so.
                    matrix[row_index, column_index] = np.nan
        return matrix

    def _prepare(self, matrix: np.ndarray, *, fitting: bool) -> np.ndarray:
        if self.handles_missing_values:
            return matrix

        imputation = self.card.imputation
        standardisation = self.card.standardisation
        prepared = matrix.copy()
        for column, name in enumerate(self.card.feature_names):
            column_values = prepared[:, column]
            if fitting:
                finite = column_values[np.isfinite(column_values)]
                imputation[name] = float(np.median(finite)) if finite.size else 0.0
            missing = ~np.isfinite(column_values)
            prepared[missing, column] = imputation.get(name, 0.0)

        for column, name in enumerate(self.card.feature_names):
            column_values = prepared[:, column]
            if fitting:
                mean = float(column_values.mean())
                std = float(column_values.std())
                standardisation[name] = (mean, std if std > 1e-12 else 1.0)
            mean, std = standardisation.get(name, (0.0, 1.0))
            prepared[:, column] = (column_values - mean) / std

        if fitting:
            self.card = self.card.model_copy(
                update={"imputation": dict(imputation), "standardisation": dict(standardisation)}
            )
        return prepared

    # --- persistence --------------------------------------------------------

    def save(self, directory: str | Path) -> Path:
        """Persist parameters, preprocessing and the model card."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        payload = {
            "kind": self.kind,
            "name": self.name,
            "random_state": self.random_state,
            "params": self._params,
            "card": self.card.model_dump(mode="json"),
            "state": self._state(),
        }
        path = target / f"{self.name}.model.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, directory: str | Path, name: str) -> "BasePredictionModel":
        """Restore a model saved with :meth:`save`."""
        path = Path(directory) / f"{name}.model.json"
        if not path.is_file():
            raise FileNotFoundError(f"No persisted model at {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        model_class = MODEL_CLASSES.get(payload["kind"])
        if model_class is None:
            raise ValidationError(
                f"Persisted model kind {payload['kind']!r} is not registered; "
                f"known kinds: {', '.join(sorted(MODEL_CLASSES))}"
            )
        model = model_class(name=payload["name"], random_state=payload.get("random_state", 42))
        model.card = ModelCard.model_validate(payload["card"])
        model._params = payload.get("params", {})
        model._restore(payload.get("state", {}))
        return model

    def _state(self) -> dict[str, Any]:
        """Model-specific parameters to persist."""
        return {}

    def _restore(self, state: dict[str, Any]) -> None:
        """Restore model-specific parameters."""
        return None


# --- 1. majority class --------------------------------------------------------


class MajorityClassModel(BasePredictionModel):
    """Always predicts the commonest training class.

    Its value is as a *floor and a diagnostic*: on an imbalanced label it has a
    high accuracy and a near-zero macro F1, which is exactly the confusion that
    reporting accuracy alone creates.
    """

    kind = BaselineKind.MAJORITY_CLASS.value

    def _fit(self, matrix: np.ndarray, labels: np.ndarray) -> None:
        counts = np.bincount(labels, minlength=len(self.card.classes)).astype(float)
        self._params["training_distribution"] = {
            name: int(counts[index]) for index, name in enumerate(self.card.classes)
        }
        self._params["majority_class"] = self.card.classes[int(np.argmax(counts))]
        self._distribution = counts / counts.sum()

    def _probabilities(self, matrix: np.ndarray) -> np.ndarray:
        return np.tile(self._distribution, (len(matrix), 1))

    def _state(self) -> dict[str, Any]:
        return {"distribution": self._distribution.tolist()}

    def _restore(self, state: dict[str, Any]) -> None:
        self._distribution = np.asarray(state.get("distribution", []), dtype=float)


# --- 2. rating based ---------------------------------------------------------


class RatingBaselineModel(BasePredictionModel):
    """Multinomial logistic model on the rating difference alone.

    This is the baseline that matters. Ratings are known before the game, they
    are the strongest legitimate pre-game signal in chess, and any richer model
    on a pre-game task must beat them to justify itself.

    Implemented with gradient descent on the log loss rather than by calling a
    library, so it is deterministic and its behaviour is inspectable.
    """

    kind = BaselineKind.RATING_BASED.value

    def __init__(self, *, name: str | None = None, classes: Sequence[str] | None = None,
                 random_state: int = 42, rating_feature: str = "rating_diff") -> None:
        super().__init__(name=name or self.kind, classes=classes, random_state=random_state)
        self.rating_feature = rating_feature

    def fit(self, rows: Sequence[Row], labels: Sequence[str], **kwargs: Any) -> "RatingBaselineModel":  # type: ignore[override]
        # Only the rating difference is used, by construction: that is the point.
        kwargs.setdefault("feature_names", [self.rating_feature])
        super().fit(rows, labels, **kwargs)
        return self

    def _fit(self, matrix: np.ndarray, labels: np.ndarray) -> None:
        iterations = int(self._params.get("iterations", 400))
        learning_rate = float(self._params.get("learning_rate", 0.1))
        classes = len(self.card.classes)
        features = matrix.shape[1]
        weights = np.zeros((features, classes))
        bias = np.zeros(classes)
        # The rating scale spans hundreds of points, so the feature is rescaled by
        # the training median absolute value. Without this, plain gradient descent
        # diverges — and a baseline that does not converge would be a fake result.
        scale = float(np.median(np.abs(matrix))) or 1.0
        scaled = matrix / scale
        self._scale = scale

        for _ in range(iterations):
            logits = scaled @ weights + bias
            logits -= logits.max(axis=1, keepdims=True)
            probabilities = np.exp(logits)
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            one_hot = np.zeros_like(probabilities)
            one_hot[np.arange(len(labels)), labels] = 1.0
            error = (probabilities - one_hot) / len(labels)
            weights -= learning_rate * (scaled.T @ error)
            bias -= learning_rate * error.sum(axis=0)
        self._weights, self._bias = weights, bias

    def _probabilities(self, matrix: np.ndarray) -> np.ndarray:
        logits = (matrix / self._scale) @ self._weights + self._bias
        logits -= logits.max(axis=1, keepdims=True)
        probabilities = np.exp(logits)
        return probabilities / probabilities.sum(axis=1, keepdims=True)

    def _state(self) -> dict[str, Any]:
        return {
            "weights": self._weights.tolist(),
            "bias": self._bias.tolist(),
            "scale": self._scale,
        }

    def _restore(self, state: dict[str, Any]) -> None:
        self._weights = np.asarray(state["weights"], dtype=float)
        self._bias = np.asarray(state["bias"], dtype=float)
        self._scale = float(state.get("scale", 1.0))

    def feature_importance(self) -> dict[str, float]:
        spread = float(np.abs(self._weights).sum()) or 1.0
        return {
            name: round(float(np.abs(self._weights[index]).sum() / spread), 4)
            for index, name in enumerate(self.card.feature_names)
        }


# --- 3. logistic regression --------------------------------------------------


class LogisticRegressionModel(BasePredictionModel):
    """Multinomial logistic regression with L2 regularisation.

    Full-batch gradient descent on standardised features: no dependency, fully
    deterministic, and enough to answer "is the signal linear in these features?"
    """

    kind = BaselineKind.LOGISTIC_REGRESSION.value

    def __init__(self, *, name: str | None = None, classes: Sequence[str] | None = None,
                 random_state: int = 42, learning_rate: float = 0.5,
                 iterations: int = 600, l2: float = 1e-3) -> None:
        super().__init__(name=name or self.kind, classes=classes, random_state=random_state)
        self.learning_rate = learning_rate
        self.iterations = iterations
        self.l2 = l2

    def _fit(self, matrix: np.ndarray, labels: np.ndarray) -> None:
        samples, features = matrix.shape
        classes = len(self.card.classes)
        weights = np.zeros((features, classes))
        bias = np.zeros(classes)
        one_hot = np.zeros((samples, classes))
        one_hot[np.arange(samples), labels] = 1.0

        history: list[float] = []
        for _ in range(self.iterations):
            logits = matrix @ weights + bias
            logits -= logits.max(axis=1, keepdims=True)
            probabilities = np.exp(logits)
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            error = (probabilities - one_hot) / samples
            weights -= self.learning_rate * (matrix.T @ error + self.l2 * weights)
            bias -= self.learning_rate * error.sum(axis=0)
            if len(history) < 5 or len(history) % 50 == 0:
                history.append(
                    float(-np.mean(np.log(np.clip(probabilities[np.arange(samples), labels], 1e-12, 1.0))))
                )
        self._weights, self._bias = weights, bias
        self._params["final_training_log_loss"] = round(history[-1], 6)
        self._params["iterations"] = self.iterations
        self._params["learning_rate"] = self.learning_rate
        self._params["l2"] = self.l2

    def _probabilities(self, matrix: np.ndarray) -> np.ndarray:
        logits = matrix @ self._weights + self._bias
        logits -= logits.max(axis=1, keepdims=True)
        probabilities = np.exp(logits)
        return probabilities / probabilities.sum(axis=1, keepdims=True)

    def _state(self) -> dict[str, Any]:
        return {"weights": self._weights.tolist(), "bias": self._bias.tolist()}

    def _restore(self, state: dict[str, Any]) -> None:
        self._weights = np.asarray(state["weights"], dtype=float)
        self._bias = np.asarray(state["bias"], dtype=float)

    def feature_importance(self) -> dict[str, float]:
        spread = float(np.abs(self._weights).sum()) or 1.0
        return {
            name: round(float(np.abs(self._weights[index]).sum() / spread), 4)
            for index, name in enumerate(self.card.feature_names)
        }


# --- 4. tree ensemble --------------------------------------------------------


class TreeEnsembleModel(BasePredictionModel):
    """A scikit-learn tree ensemble (histogram gradient boosting by default).

    Trees receive raw NaNs rather than imputed values, because a tree can split
    on missingness. That makes them the natural choice when ratings are absent
    for a large share of a corpus — and the model card records the fact, so it is
    never mistaken for the linear models' preprocessing.
    """

    kind = BaselineKind.TREE_ENSEMBLE.value
    handles_missing_values = True

    def __init__(self, *, name: str | None = None, classes: Sequence[str] | None = None,
                 random_state: int = 42, max_iter: int = 200, learning_rate: float = 0.1,
                 max_depth: int | None = None, estimator: str = "hist_gradient_boosting") -> None:
        super().__init__(name=name or self.kind, classes=classes, random_state=random_state)
        self.max_iter = max_iter
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.estimator = estimator

    def _build(self) -> Any:
        try:
            from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
        except Exception as exc:  # noqa: BLE001 — optional dependency
            raise ValidationError(
                "A tree ensemble requires scikit-learn, which is not installed here. "
                "Install the 'ml' extra (pip install -e 'packages/argus[ml]') or report "
                "the baselines that do not need it — do not substitute a fabricated score."
            ) from exc

        if self.estimator == "random_forest":
            return RandomForestClassifier(
                n_estimators=self.max_iter,
                max_depth=self.max_depth,
                random_state=self.random_state,
            )
        return HistGradientBoostingClassifier(
            max_iter=self.max_iter,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            random_state=self.random_state,
        )

    def _fit(self, matrix: np.ndarray, labels: np.ndarray) -> None:
        self._model = self._build()
        self._model.fit(matrix, labels)
        self._params.update(
            {
                "estimator": self.estimator,
                "max_iter": self.max_iter,
                "learning_rate": self.learning_rate,
                "max_depth": self.max_depth,
            }
        )

    def _probabilities(self, matrix: np.ndarray) -> np.ndarray:
        probabilities = self._model.predict_proba(matrix)
        if probabilities.shape[1] != len(self.card.classes):
            # A class absent from the training split would misalign the class axis,
            # which would silently swap probabilities between outcomes.
            full = np.zeros((len(matrix), len(self.card.classes)))
            for index, label in enumerate(self._model.classes_):
                full[:, int(label)] = probabilities[:, index]
            return full
        return probabilities

    def feature_importance(self) -> dict[str, float]:
        importances = getattr(self._model, "feature_importances_", None)
        if importances is None:
            return {}
        total = float(np.sum(importances)) or 1.0
        return {
            name: round(float(importances[index] / total), 4)
            for index, name in enumerate(self.card.feature_names)
        }

    def save(self, directory: str | Path) -> Path:
        """Persist with joblib: a fitted ensemble is not JSON-serialisable."""
        import joblib

        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        path = super().save(target)
        joblib.dump(self._model, target / f"{self.name}.estimator.joblib")
        return path

    @classmethod
    def load(cls, directory: str | Path, name: str) -> "TreeEnsembleModel":
        import joblib

        model = super().load(directory, name)
        if not isinstance(model, TreeEnsembleModel):  # pragma: no cover - corrupt metadata
            raise TypeError(
                f"stored model {name!r} is {type(model).__name__}, expected TreeEnsembleModel"
            )
        model._model = joblib.load(Path(directory) / f"{name}.estimator.joblib")
        return model

    def _state(self) -> dict[str, Any]:
        return {}


MODEL_CLASSES: dict[str, type[BasePredictionModel]] = {
    BaselineKind.MAJORITY_CLASS.value: MajorityClassModel,
    BaselineKind.RATING_BASED.value: RatingBaselineModel,
    BaselineKind.LOGISTIC_REGRESSION.value: LogisticRegressionModel,
    BaselineKind.TREE_ENSEMBLE.value: TreeEnsembleModel,
}

#: The baseline ladder, in increasing complexity. Evaluated in this order so that
#: a later model can only be described as an improvement if it beats its
#: predecessors on the same split.
BASELINE_LADDER: list[str] = [
    BaselineKind.MAJORITY_CLASS.value,
    BaselineKind.RATING_BASED.value,
    BaselineKind.LOGISTIC_REGRESSION.value,
    BaselineKind.TREE_ENSEMBLE.value,
]


def make_model(kind: str, *, name: str | None = None, classes: Sequence[str] | None = None,
               **hyperparameters: Any) -> BasePredictionModel:
    """Instantiate a baseline by name.

    Raises:
        ValidationError: for an unknown kind, rather than quietly falling back to
            a default model that would then be reported under the wrong name.
    """
    model_class = MODEL_CLASSES.get(kind)
    if model_class is None:
        raise ValidationError(
            f"Unknown model kind {kind!r}; known kinds: {', '.join(sorted(MODEL_CLASSES))}"
        )
    return model_class(name=name or kind, classes=classes, **hyperparameters)


__all__ = [
    "BASELINE_LADDER",
    "MODEL_CLASSES",
    "BasePredictionModel",
    "LogisticRegressionModel",
    "MajorityClassModel",
    "ModelCard",
    "RatingBaselineModel",
    "Row",
    "TreeEnsembleModel",
    "make_model",
]
