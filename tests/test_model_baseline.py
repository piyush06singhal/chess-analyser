"""Stored model-version baseline tests (§44).

The mechanism must do two things honestly: refuse to baseline a task that has no
production model, and — when one exists — record exactly the measured metrics so a
future candidate is compared against real numbers. The fixture model here is a
*synthetic* registry entry used only to exercise the mechanism; it is never
served and its metrics are never presented as a result.
"""

from __future__ import annotations

import pytest

from argus.evaluation.model_baseline import (
    ModelBaseline,
    compare_candidate_to_baseline,
    evaluation_from_registered,
    load_model_baseline,
    metrics_from_registered,
    model_baseline_path,
    record_model_baseline,
    save_model_baseline,
)
from argus.evaluation.model_regression import ModelEvaluation
from argus.ml.models import ModelStatus
from argus.ml.registry import ModelRegistry, RegisteredModel, model_id_for
from argus.shared.errors import ValidationError


def _production_model(
    metrics: dict[str, float | None] | None = None,
    calibration_metrics: dict[str, float | None] | None = None,
) -> RegisteredModel:
    """A synthetic production entry, used only as a baseline fixture."""
    return RegisteredModel(
        model_id=model_id_for("game_outcome", "majority_class", "fixture"),
        task="game_outcome",
        model_type="majority_class",
        version="fixture",
        status=ModelStatus.PRODUCTION,
        dataset_version="chess-outcomes@1",
        feature_version="features@1",
        metrics=metrics
        if metrics is not None
        else {"macro_f1": 0.52, "balanced_accuracy": 0.50, "log_loss": 1.01},
        calibration_metrics=calibration_metrics
        if calibration_metrics is not None
        else {"expected_calibration_error": 0.03},
    )


def test_recording_refuses_when_no_model_is_in_production(tmp_path) -> None:
    registry = ModelRegistry.load(tmp_path)
    with pytest.raises(ValidationError) as excinfo:
        record_model_baseline(registry, "game_outcome", directory=tmp_path)
    assert "nothing to baseline" in excinfo.value.message
    # Nothing was written: a refusal must not leave an empty baseline behind.
    assert load_model_baseline("game_outcome", "chess-outcomes@1", directory=tmp_path) is None


def test_recording_refuses_when_the_production_model_has_no_metrics(tmp_path) -> None:
    registry = ModelRegistry.load(tmp_path)
    registry.register(_production_model(metrics={}, calibration_metrics={}))
    with pytest.raises(ValidationError) as excinfo:
        record_model_baseline(registry, "game_outcome", directory=tmp_path)
    assert "no measured metrics" in excinfo.value.message


def test_a_promoted_model_is_baselined_from_its_recorded_metrics(tmp_path) -> None:
    registry = ModelRegistry.load(tmp_path)
    registry.register(_production_model())
    baseline = record_model_baseline(registry, "game_outcome", directory=tmp_path)

    assert baseline.model_id.endswith("fixture")
    assert baseline.dataset_version == "chess-outcomes@1"
    assert baseline.metrics["macro_f1"] == 0.52
    assert baseline.metrics["expected_calibration_error"] == 0.03
    assert baseline.recorded_at

    stored = load_model_baseline("game_outcome", "chess-outcomes@1", directory=tmp_path)
    assert stored is not None
    assert stored.metrics == baseline.metrics


def test_an_existing_baseline_is_not_overwritten_without_force(tmp_path) -> None:
    baseline = ModelBaseline(
        task="game_outcome",
        model_id="game_outcome:majority_class:1",
        dataset_version="chess-outcomes@1",
        metrics={"macro_f1": 0.5},
    )
    save_model_baseline(baseline, directory=tmp_path)
    with pytest.raises(ValidationError):
        save_model_baseline(baseline, directory=tmp_path)
    # force=True is the deliberate path.
    save_model_baseline(baseline, directory=tmp_path, force=True)


def test_only_real_numbers_are_recorded() -> None:
    """``None`` and booleans are dropped, never coerced to zero."""
    model = _production_model(
        metrics={"macro_f1": 0.5, "log_loss": None},  # type: ignore[dict-item]
        calibration_metrics={"calibrated": True, "expected_calibration_error": 0.03},  # type: ignore[dict-item]
    )
    metrics = metrics_from_registered(model)
    assert metrics["macro_f1"] == 0.5
    # A None metric is dropped, not coerced to a perfect 0.0.
    assert "log_loss" not in metrics
    # A boolean flag is not a metric, even though the registry stores it as 1.0.
    assert "calibrated" not in metrics
    assert metrics["expected_calibration_error"] == 0.03


def test_a_candidate_is_compared_against_the_stored_baseline(tmp_path) -> None:
    registry = ModelRegistry.load(tmp_path)
    registry.register(_production_model())
    baseline = record_model_baseline(registry, "game_outcome", directory=tmp_path)

    better = ModelEvaluation(
        model_version="candidate-2",
        dataset_version="chess-outcomes@1",
        metrics={"macro_f1": 0.60, "log_loss": 0.95},
    )
    worse = ModelEvaluation(
        model_version="candidate-3",
        dataset_version="chess-outcomes@1",
        metrics={"macro_f1": 0.40, "log_loss": 1.20},
    )
    assert compare_candidate_to_baseline(baseline, better).recommendation == "candidate"
    assert compare_candidate_to_baseline(baseline, worse).recommendation == "baseline"


def test_a_baseline_on_a_different_benchmark_is_not_comparable(tmp_path) -> None:
    registry = ModelRegistry.load(tmp_path)
    registry.register(_production_model())
    baseline = record_model_baseline(registry, "game_outcome", directory=tmp_path)
    elsewhere = ModelEvaluation(
        model_version="candidate",
        dataset_version="other-benchmark@2",
        metrics={"macro_f1": 0.99},
    )
    comparison = compare_candidate_to_baseline(baseline, elsewhere)
    assert comparison.recommendation == "insufficient-data"


def test_baseline_path_is_keyed_on_task_and_dataset() -> None:
    path = model_baseline_path("game_outcome", "chess-outcomes@1")
    assert path.name == "model-game-outcome-chess-outcomes-1.json"


def test_evaluation_from_registered_reports_what_was_measured() -> None:
    evaluation = evaluation_from_registered(_production_model())
    assert evaluation.metrics["macro_f1"] == 0.52
    # A missing gate means leakage was not demonstrated, so it is None, not True.
    assert evaluation.leakage_passed is None
