"""Prediction service — the only door between a model and a user.

The service is deliberately narrow. It can:

* list the tasks and say, per task, whether a prediction is available and why not;
* serve a prediction, but **only** from a model whose registry status is
  ``PRODUCTION``.

It cannot invent a probability, and it has no fallback path to one. When no
production model exists for a task it returns
:class:`PredictionUnavailable` with the reason — which is the honest answer, and
the expected one for a phase that has not yet validated a model.

Two concepts stay separate, because conflating them is how products end up
claiming "94% confidence":

``model probability``
    The calibrated probability the model outputs, with its calibration error.
``data coverage``
    How much data the model and its inputs rest on — reported from the registry,
    never mixed into the probability.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

#: Probabilities are reported at this precision so a response is readable.
_PROBABILITY_DECIMALS = 6


def _distribution(probabilities: dict[str, float]) -> dict[str, float]:
    """Round a probability vector for reporting without breaking the sum.

    Rounding alone can leave a vector summing to 0.999999, which tells a client
    the outputs are not a distribution. The rounding residual is therefore put
    back onto the most probable class — a deterministic book-keeping step, not
    an adjustment of the model's opinion.
    """
    if not probabilities:
        return {}
    rounded = {
        name: round(float(value), _PROBABILITY_DECIMALS)
        for name, value in probabilities.items()
    }
    residual = round(1.0 - sum(rounded.values()), _PROBABILITY_DECIMALS)
    if residual:
        best = max(rounded, key=lambda name: rounded[name])
        rounded[best] = round(rounded[best] + residual, _PROBABILITY_DECIMALS)
    return rounded

from argus.ml.baselines import BasePredictionModel
from argus.ml.models import ModelStatus
from argus.ml.registry import ModelRegistry, RegisteredModel
from argus.ml.tasks import PredictionTaskDefinition, TASK_REGISTRY
from argus.shared.errors import ValidationError
from argus.shared.logging import get_logger

logger = get_logger(__name__)


class DataCoverage(BaseModel):
    """What the prediction rests on — deliberately not a probability."""

    trained_rows: int = 0
    dataset_version: str = ""
    feature_version: str = ""
    split_strategy: str = ""
    test_balanced_accuracy: float | None = None
    test_macro_f1: float | None = None
    test_log_loss: float | None = None
    expected_calibration_error: float | None = None
    calibration_applied: bool = False
    notes: list[str] = Field(default_factory=list)

    def label(self) -> str:
        """A coarse, honest description of how much the model rests on."""
        if self.trained_rows >= 100_000:
            return "large sample"
        if self.trained_rows >= 20_000:
            return "moderate sample"
        if self.trained_rows >= 2_000:
            return "small sample"
        return "insufficient sample"


class TaskAvailability(BaseModel):
    """Whether a task can be answered right now, and if not, why not."""

    task: str
    description: str
    unit_of_prediction: str
    target_values: list[str] = Field(default_factory=list)
    available: bool = False
    production_models: list[str] = Field(default_factory=list)
    registered_models: int = 0
    status: str = ModelStatus.EXPERIMENTAL.value
    reason: str = ""
    requirements: dict[str, Any] = Field(default_factory=dict)


class PredictionResult(BaseModel):
    """A served prediction, with everything needed to read it honestly."""

    available: bool = True
    task: str
    model_id: str
    model_status: str
    prediction: str
    probabilities: dict[str, float] = Field(default_factory=dict)
    data_coverage: DataCoverage = Field(default_factory=DataCoverage)
    disclaimer: str = (
        "A measured model output, not a guarantee. Probabilities are calibrated on "
        "held-out games and reflect the model's error rate, not a certainty."
    )


class PredictionUnavailable(BaseModel):
    """The honest answer when a task has no validated model."""

    available: bool = False
    task: str
    reason: str
    detail: str = ""
    requirements: dict[str, Any] = Field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - presentation helper
        return f"{self.task}: {self.reason}"


class PredictionService:
    """Serves predictions from production models only."""

    def __init__(self, registry: ModelRegistry, *, models_dir: str | Path) -> None:
        self.registry = registry
        self.models_dir = Path(models_dir)
        self._cache: dict[str, BasePredictionModel] = {}

    # --- availability -------------------------------------------------------

    def task_availability(self, task_name: str) -> TaskAvailability:
        """Whether one task can be answered, with the reason when it cannot."""
        task: PredictionTaskDefinition = TASK_REGISTRY[task_name]
        production = self.registry.production(task_name)
        registered = self.registry.list_models(task=task_name)
        availability = TaskAvailability(
            task=task_name,
            description=task.description,
            unit_of_prediction=task.unit_of_prediction,
            target_values=task.target_values,
            available=bool(production),
            production_models=[model.model_id for model in production],
            registered_models=len(registered),
            status=(
                ModelStatus.PRODUCTION.value if production else task.production_status.value
            ),
            requirements=task.data_requirements.model_dump(),
        )
        if production:
            availability.reason = "A production model is registered for this task."
        elif registered:
            availability.reason = (
                f"{len(registered)} model(s) are registered for this task but none has "
                "passed the production gate, so no prediction is served."
            )
        else:
            availability.reason = (
                "No model has been trained and validated for this task in this phase, so "
                "no prediction is served."
            )
        return availability

    def available_tasks(self) -> list[TaskAvailability]:
        return [self.task_availability(name) for name in sorted(TASK_REGISTRY)]

    # --- serving ------------------------------------------------------------

    def _load(self, model: RegisteredModel) -> BasePredictionModel:
        if model.model_id in self._cache:
            return self._cache[model.model_id]
        artifact_dir = Path(model.artifact).parent if model.artifact else self.models_dir
        loaded = BasePredictionModel.load(artifact_dir, model.model_type)
        self._cache[model.model_id] = loaded
        return loaded

    def _predict(
        self,
        task_name: str,
        rows: list[dict[str, Any]],
        *,
        positive_class: str | None = None,
    ) -> PredictionResult | PredictionUnavailable:
        production = self.registry.production(task_name)
        if not production:
            return self.unavailable(task_name)

        # The newest production model for the task answers; alternatives remain
        # registered and auditable.
        model = sorted(production, key=lambda entry: entry.registered_at)[-1]
        if not rows:
            raise ValidationError("A prediction needs at least one row of features")
        loaded = self._load(model)

        probabilities = loaded.predict_proba(rows)
        classes = loaded.card.classes
        row = probabilities[0]
        ranking = sorted(
            zip(classes, row, strict=True), key=lambda item: item[1], reverse=True
        )
        coverage = DataCoverage(
            trained_rows=model.trained_rows,
            dataset_version=model.dataset_version,
            feature_version=model.feature_version,
            split_strategy=model.split_strategy,
            test_balanced_accuracy=model.metrics.get("balanced_accuracy"),
            test_macro_f1=model.metrics.get("macro_f1"),
            test_log_loss=model.metrics.get("log_loss"),
            expected_calibration_error=model.calibration_metrics.get(
                "expected_calibration_error"
            ),
            calibration_applied=bool(model.calibration_metrics.get("calibrated")),
        )
        if task_name == "game_outcome":
            coverage.notes.append(
                "Pre-game prediction: uses only information available before the first move."
            )
        return PredictionResult(
            task=task_name,
            model_id=model.model_id,
            model_status=model.status.value,
            prediction=ranking[0][0],
            probabilities=_distribution(dict(ranking)),
            data_coverage=coverage,
        )

    def unavailable(self, task_name: str) -> PredictionUnavailable:
        """The unavailable response for a task, with its full reason."""
        availability = self.task_availability(task_name)
        task = TASK_REGISTRY[task_name]
        return PredictionUnavailable(
            task=task_name,
            reason=availability.reason,
            detail=(
                "This task is declared with its target, label and requirements, but has "
                "no validated model. Caissa does not serve unvalidated predictions."
            ),
            requirements=task.data_requirements.model_dump(),
        )

    # --- public operations --------------------------------------------------

    def predict_game_outcome(
        self, rows: list[dict[str, Any]]
    ) -> PredictionResult | PredictionUnavailable:
        """Pre-game outcome prediction (unavailable until a model passes gating)."""
        return self._predict("game_outcome", rows)

    def predict_position_outcome(
        self, rows: list[dict[str, Any]]
    ) -> PredictionResult | PredictionUnavailable:
        return self._predict("position_outcome", rows)

    def predict_position_difficulty(
        self, rows: list[dict[str, Any]]
    ) -> PredictionResult | PredictionUnavailable:
        return self._predict("position_difficulty", rows)

    def predict_error_risk(
        self, rows: list[dict[str, Any]]
    ) -> PredictionResult | PredictionUnavailable:
        return self._predict("move_error_risk", rows)

    def summary(self) -> dict[str, Any]:
        """Everything an admin endpoint needs in one payload."""
        return {
            "tasks": [entry.model_dump() for entry in self.available_tasks()],
            "registry": self.registry.summary(),
        }


__all__ = [
    "DataCoverage",
    "PredictionResult",
    "PredictionService",
    "PredictionUnavailable",
    "TaskAvailability",
]
