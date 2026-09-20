"""ML package: dataset handling, validation, pipelines, versioning.

Deliberately model-free in Phase 1: interfaces define the contract later
implementations (scikit-learn first, then XGBoost/PyTorch) must follow. The
system refuses model training when a dataset does not meet its declared
requirements — no fabricated accuracy, ever.
"""

from argus.ml.dataset import (
    load_csv,
    split_dataset,
    validate_dataset,
    validate_dataset_strict,
)
from argus.ml.features import FeatureBuilder, MaterialAdvantageDurationBuilder
from argus.ml.models import (
    DatasetSpec,
    DatasetValidationResult,
    EvaluationMetrics,
    ModelMetadata,
    ProblemType,
)
from argus.ml.pipelines import (
    EvaluationPipeline,
    PredictionModel,
    PredictionResult,
    TrainingPipeline,
)

__all__ = [
    "DatasetSpec",
    "DatasetValidationResult",
    "EvaluationMetrics",
    "EvaluationPipeline",
    "FeatureBuilder",
    "MaterialAdvantageDurationBuilder",
    "ModelMetadata",
    "PredictionModel",
    "PredictionResult",
    "ProblemType",
    "TrainingPipeline",
    "load_csv",
    "split_dataset",
    "validate_dataset",
    "validate_dataset_strict",
]
