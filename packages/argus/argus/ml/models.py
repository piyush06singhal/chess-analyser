"""ML data models: dataset specs, validation results, model + evaluation metadata.

Requirements are declared per prediction problem — the system refuses training
when data does not meet them. Evaluation metrics are measured on held-out
data and are never fabricated.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ProblemType(str, Enum):
    """Supported prediction problem types."""

    CLASSIFICATION = "classification"
    REGRESSION = "regression"


class DatasetSpec(BaseModel):
    """Requirements a dataset must meet for a given prediction problem."""

    name: str
    description: str = ""
    problem_type: ProblemType
    label_column: str
    feature_columns: list[str] = Field(
        default_factory=list,
        description="Explicit feature columns; empty means all non-label columns",
    )
    required_columns: list[str] = Field(default_factory=list)
    min_samples: int = Field(default=1000, description="Minimum rows to allow training")
    min_validation_samples: int = 200
    min_test_samples: int = 200
    min_class_balance_share: float = Field(
        default=0.05, description="Minimum share of any class (classification only)"
    )


class DatasetValidationResult(BaseModel):
    """Outcome of validating a dataset against a spec."""

    is_valid: bool
    row_count: int
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ModelMetadata(BaseModel):
    """Versioned metadata of a trained model."""

    name: str
    version: str
    problem_type: ProblemType
    trained_at: datetime
    dataset_name: str
    feature_columns: list[str]
    label_column: str


class EvaluationMetrics(BaseModel):
    """Metrics measured on a held-out dataset split (never fabricated)."""

    metrics: dict[str, float]
    dataset_rows: int
    dataset_split: str = Field(description="'validation' or 'test'")
