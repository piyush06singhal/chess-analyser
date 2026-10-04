"""ML package: dataset handling, validation, evaluation, models and gating.

Two families live here, and the split matters:

*The Phase 1 scaffolding* (``dataset``, ``features``, ``pipelines``,
``model_store``, ``sklearn_pipelines``, ``build_dataset``, ``train``) provides
CSV dataset specs and guarded training for the evaluation-regression track.

*The Phase 6 foundation* adds declared prediction tasks, task-appropriate
metrics, probability calibration, a model interface with baselines, a model
registry with lifecycle statuses, append-only experiment tracking, error
analysis, and production gating — so that "is this model good enough to show a
user?" is a decision with recorded evidence.

The non-negotiable rule is unchanged and is enforced in code: nothing is
reported as accuracy that was not measured on held-out data, and nothing is
served to a user by a model that has not passed its production gate.
"""

from argus.ml.baselines import (
    BASELINE_LADDER,
    MODEL_CLASSES,
    BasePredictionModel,
    LogisticRegressionModel,
    MajorityClassModel,
    ModelCard,
    RatingBaselineModel,
    TreeEnsembleModel,
    make_model,
)
from argus.ml.calibration import (
    CalibrationReport,
    IsotonicCalibrator,
    PlattCalibrator,
    calibrate_and_evaluate,
    evaluate_calibration,
    reliability_curve,
)
from argus.ml.dataset import (
    load_csv,
    split_dataset,
    validate_dataset,
    validate_dataset_strict,
)
from argus.ml.error_analysis import (
    ErrorAnalysis,
    misclassified_examples,
    permutation_importance,
    slice_metrics,
)
from argus.ml.experiments import (
    Experiment,
    ExperimentRecord,
    ExperimentTracker,
    render_experiment_report,
)
from argus.ml.features import FeatureBuilder, MaterialAdvantageDurationBuilder
from argus.ml.gating import (
    TASK_THRESHOLDS,
    GateCheck,
    GateDecision,
    GateThresholds,
    evaluate_gates,
)
from argus.ml.metrics import (
    ClassificationMetrics,
    classification_metrics,
    majority_class_baseline,
)
from argus.ml.models import (
    BaselineKind,
    DatasetSpec,
    DatasetValidationResult,
    EvaluationMetrics,
    ModelMetadata,
    ModelStatus,
    ProblemType,
)
from argus.ml.pipelines import (
    EvaluationPipeline,
    PredictionModel,
    PredictionResult,
    TrainingPipeline,
)
from argus.ml.registry import ModelRegistry, RegisteredModel, model_id_for
from argus.ml.service import (
    DataCoverage,
    PredictionService,
    PredictionUnavailable,
    TaskAvailability,
)
from argus.ml.tasks import (
    GAME_OUTCOME,
    MOVE_ERROR_RISK,
    PLAYER_PERFORMANCE,
    POSITION_DIFFICULTY,
    POSITION_OUTCOME,
    TASK_REGISTRY,
    DataRequirement,
    PredictionTaskDefinition,
    get_task,
    production_ready_tasks,
    task_names,
)

__all__ = [
    "BASELINE_LADDER",
    "GAME_OUTCOME",
    "MODEL_CLASSES",
    "MOVE_ERROR_RISK",
    "PLAYER_PERFORMANCE",
    "POSITION_DIFFICULTY",
    "POSITION_OUTCOME",
    "TASK_REGISTRY",
    "TASK_THRESHOLDS",
    "BasePredictionModel",
    "BaselineKind",
    "CalibrationReport",
    "ClassificationMetrics",
    "DataCoverage",
    "DataRequirement",
    "DatasetSpec",
    "DatasetValidationResult",
    "ErrorAnalysis",
    "EvaluationMetrics",
    "EvaluationPipeline",
    "Experiment",
    "ExperimentRecord",
    "ExperimentTracker",
    "FeatureBuilder",
    "GateCheck",
    "GateDecision",
    "GateThresholds",
    "IsotonicCalibrator",
    "LogisticRegressionModel",
    "MajorityClassModel",
    "MaterialAdvantageDurationBuilder",
    "ModelCard",
    "ModelMetadata",
    "ModelRegistry",
    "ModelStatus",
    "PlattCalibrator",
    "PredictionModel",
    "PredictionResult",
    "PredictionService",
    "PredictionTaskDefinition",
    "PredictionUnavailable",
    "ProblemType",
    "RatingBaselineModel",
    "RegisteredModel",
    "TaskAvailability",
    "TrainingPipeline",
    "TreeEnsembleModel",
    "calibrate_and_evaluate",
    "classification_metrics",
    "evaluate_calibration",
    "evaluate_gates",
    "get_task",
    "load_csv",
    "majority_class_baseline",
    "make_model",
    "misclassified_examples",
    "model_id_for",
    "permutation_importance",
    "production_ready_tasks",
    "reliability_curve",
    "render_experiment_report",
    "slice_metrics",
    "split_dataset",
    "task_names",
    "validate_dataset",
    "validate_dataset_strict",
]
