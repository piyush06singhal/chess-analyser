"""Dataset loading + validation.

Phase 1 uses the standard library's CSV reader (no pandas dependency yet);
parquet/pandas loading arrives with the ML phase. Validation checks a dataset
against its declared :class:`DatasetSpec` and reports errors/warnings without
raising; ``validate_dataset_strict`` raises :class:`InsufficientDataError` so
pipelines can refuse under-documented data.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from argus.ml.models import DatasetSpec, DatasetValidationResult, ProblemType
from argus.shared.errors import InsufficientDataError

Row = dict[str, str]


def load_csv(path: str | Path) -> list[Row]:
    """Load a CSV file as rows of string fields (a header row is required).

    Raises:
        FileNotFoundError: when the file does not exist.
        InsufficientDataError: when the file has no header.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Dataset file not found: {file_path}")
    with file_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise InsufficientDataError(f"Dataset file has no header row: {file_path}")
        return [dict(row) for row in reader]
def _label_values(rows: list[Row], label_column: str) -> list[str]:
    return [
        (row.get(label_column) or "").strip()
        for row in rows
        if (row.get(label_column) or "").strip()
    ]


def validate_dataset(rows: list[Row], spec: DatasetSpec) -> DatasetValidationResult:
    """Validate rows against a spec; returns errors and warnings, never raises."""
    errors: list[str] = []
    warnings: list[str] = []
    row_count = len(rows)

    if row_count == 0:
        errors.append("Dataset is empty")
        return DatasetValidationResult(is_valid=False, row_count=0, errors=errors)

    columns = set(rows[0].keys())
    required = spec.required_columns or [spec.label_column, *spec.feature_columns]
    for column in required:
        if column not in columns:
            errors.append(f"Missing required column: '{column}'")

    if spec.label_column in columns:
        label_present = _label_values(rows, spec.label_column)
        missing = row_count - len(label_present)
        if missing:
            errors.append(
                f"{missing} of {row_count} rows are missing the label '{spec.label_column}'"
            )
        if spec.problem_type is ProblemType.CLASSIFICATION and label_present:
            counts: dict[str, int] = {}
            for value in label_present:
                counts[value] = counts.get(value, 0) + 1
            for value, count in counts.items():
                share = count / len(label_present)
                if share < spec.min_class_balance_share:
                    warnings.append(
                        f"Class '{value}' has share {share:.3f} "
                        f"(below the {spec.min_class_balance_share} balance floor)"
                    )

    if row_count < spec.min_samples:
        errors.append(
            f"Dataset has {row_count} rows; at least {spec.min_samples} are required "
            f"to train '{spec.name}'"
        )
    if row_count and row_count < spec.min_validation_samples:
        warnings.append(
            f"Fewer rows ({row_count}) than the {spec.min_validation_samples} "
            "validation samples target"
        )
    if row_count and row_count < spec.min_test_samples:
        warnings.append(
            f"Fewer rows ({row_count}) than the {spec.min_test_samples} test samples target"
        )

    return DatasetValidationResult(
        is_valid=not errors, row_count=row_count, errors=errors, warnings=warnings
    )


def validate_dataset_strict(rows: list[Row], spec: DatasetSpec) -> DatasetValidationResult:
    """Validate rows and raise :class:`InsufficientDataError` when invalid."""
    result = validate_dataset(rows, spec)
    if not result.is_valid:
        raise InsufficientDataError(
            f"Dataset '{spec.name}' does not meet its requirements",
            details=result.model_dump(),
        )
    return result


def split_dataset(
    rows: list[Row],
    spec: DatasetSpec,
    *,
    validation_share: float = 0.1,
    test_share: float = 0.1,
    seed: int = 42,
) -> dict[str, list[Row]]:
    """Split rows into train/validation/test sets (deterministic shuffle).

    Raises:
        InsufficientDataError: when any split falls below its spec minimum.
    """
    if not 0 < validation_share < 1 or not 0 < test_share < 1:
        raise ValueError("validation_share and test_share must be in (0, 1)")
    if validation_share + test_share >= 1:
        raise ValueError("validation_share + test_share must leave room for training")

    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)

    total = len(shuffled)
    test_count = int(total * test_share)
    validation_count = int(total * validation_share)
    splits = {
        "train": shuffled[: total - test_count - validation_count],
        "validation": shuffled[total - test_count - validation_count : total - test_count],
        "test": shuffled[total - test_count :],
    }

    failures = []
    if len(splits["train"]) < spec.min_samples:
        failures.append(
            f"train split has {len(splits['train'])} rows; {spec.min_samples} required"
        )
    if len(splits["validation"]) < spec.min_validation_samples:
        failures.append(
            f"validation split has {len(splits['validation'])} rows; "
            f"{spec.min_validation_samples} required"
        )
    if len(splits["test"]) < spec.min_test_samples:
        failures.append(
            f"test split has {len(splits['test'])} rows; {spec.min_test_samples} required"
        )
    if failures:
        raise InsufficientDataError(
            f"Dataset '{spec.name}' cannot be split for training",
            details={"failures": failures, "total_rows": total},
        )
    return splits

