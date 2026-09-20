"""ML pipeline interfaces: PredictionModel, TrainingPipeline, EvaluationPipeline.

No predictive model exists in Phase 1 — these interfaces define the contract
that later implementations (scikit-learn first, then XGBoost/PyTorch) must
follow. Under-specified data can never silently produce a model:
``TrainingPipeline.train`` enforces dataset validation (raising
:class:`InsufficientDataError`) before any training happens.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence

from pydantic import BaseModel, Field

from argus.ml.models import (
    DatasetSpec,
    DatasetValidationResult,
    EvaluationMetrics,
    ModelMetadata,
)

Row = dict[str, str]


class PredictionResult(BaseModel):
    """Output of a trained model; always carries the model version used."""

    model_name: str
    model_version: str
    values: list[Any] = Field(description="One prediction per input feature row")


class PredictionModel(ABC):
    """A trained model together with its versioned metadata."""

    @abstractmethod
    def metadata(self) -> ModelMetadata:
        """Versioned metadata of this model."""

    @abstractmethod
    def predict(self, features: Sequence[dict[str, Any]]) -> PredictionResult:
        """Predict for a batch of feature rows."""


class TrainingPipeline(ABC):
    """Training pipeline contract.

    Subclasses implement ``_train``; ``train`` validates the dataset against
    the pipeline's spec first, so insufficient data can never produce a model.
    """

    def __init__(self, spec: DatasetSpec) -> None:
        self._spec = spec

    @property
    def spec(self) -> DatasetSpec:
        """The dataset requirements this pipeline enforces."""
        return self._spec

    def validate_requirements(self, rows: Sequence[Row]) -> DatasetValidationResult:
        """Validate rows against the spec; raises on insufficient data."""
        from argus.ml.dataset import validate_dataset_strict

        return validate_dataset_strict(list(rows), self._spec)

    @abstractmethod
    def _train(self, rows: list[Row]) -> PredictionModel:
        """Train on already-validated rows (implementations only)."""

    def train(self, rows: Sequence[Row]) -> PredictionModel:
        """Validate the dataset, then train. Refuses under-specified data."""
        self.validate_requirements(rows)
        return self._train(list(rows))


class EvaluationPipeline(ABC):
    """Evaluation pipeline contract.

    Metrics must be measured on held-out data (validation/test) and reported
    as measured — never estimated or fabricated.
    """

    @abstractmethod
    def evaluate(
        self, model: PredictionModel, rows: Sequence[Row], *, split: str = "test"
    ) -> EvaluationMetrics:
        """Evaluate a model on a held-out dataset split."""
