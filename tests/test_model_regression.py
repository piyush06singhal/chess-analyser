"""Model-regression comparison tests (§44).

These pin the two decisions that matter: a candidate is never promoted
automatically, and a gain on the average that hides a loss for a subgroup, a
leakage failure or a worse calibration is not an improvement.
"""

from __future__ import annotations

from argus.evaluation.model_regression import (
    ModelEvaluation,
    compare_against_production,
    compare_models,
)


def _model(version: str, **kwargs) -> ModelEvaluation:
    return ModelEvaluation(model_version=version, dataset_version="d@1", **kwargs)


def test_a_clean_improvement_is_recommended_but_not_automatic() -> None:
    production = _model("prod", metrics={"macro_f1": 0.5})
    candidate = _model("cand", metrics={"macro_f1": 0.6})
    comparison = compare_models(production, candidate)
    assert comparison.recommendation == "candidate"
    assert comparison.promotable is True
    assert "caller" in comparison.note


def test_a_subgroup_regression_is_a_regression() -> None:
    production = _model("prod", metrics={"macro_f1": 0.5}, subgroups={"blitz": {"macro_f1": 0.5}})
    candidate = _model("cand", metrics={"macro_f1": 0.6}, subgroups={"blitz": {"macro_f1": 0.4}})
    comparison = compare_models(production, candidate)
    assert comparison.recommendation == "baseline"
    assert comparison.promotable is False
    assert "blitz.macro_f1" in comparison.subgroup_regressions


def test_lower_is_better_metrics_regress_in_the_right_direction() -> None:
    production = _model("prod", metrics={"log_loss": 1.0, "expected_calibration_error": 0.02})
    worse = _model("cand", metrics={"log_loss": 1.2, "expected_calibration_error": 0.09})
    better = _model("cand", metrics={"log_loss": 0.8, "expected_calibration_error": 0.01})
    assert compare_models(production, worse).recommendation == "baseline"
    assert compare_models(production, better).recommendation == "candidate"


def test_leakage_and_irreproducibility_block_promotion() -> None:
    production = _model("prod", metrics={"macro_f1": 0.5})
    leaky = _model("cand", metrics={"macro_f1": 0.9}, leakage_passed=False)
    assert compare_models(production, leaky).recommendation == "baseline"
    unstable = _model("cand", metrics={"macro_f1": 0.9}, reproducible=False)
    assert compare_models(production, unstable).recommendation == "baseline"


def test_models_measured_on_different_datasets_are_not_comparable() -> None:
    production = _model("prod", metrics={"macro_f1": 0.5})
    candidate = ModelEvaluation("cand", "other@2", metrics={"macro_f1": 0.9})
    comparison = compare_models(production, candidate)
    assert comparison.recommendation == "insufficient-data"
    assert comparison.promotable is False


def test_with_no_production_model_there_is_nothing_to_regress_against() -> None:
    comparison = compare_against_production(_model("first", metrics={"macro_f1": 0.9}))
    assert comparison.recommendation == "insufficient-data"
    assert comparison.promotable is False
    assert "first model" in comparison.note


def test_the_ml_suite_exercises_the_regression_guardrails() -> None:
    """The comparison is exercised by the evaluation run, not merely importable."""
    from argus.evaluation import build_framework

    report = build_framework(engine=None).run(only=["ml_evaluation"])
    names = {check.name for check in report.suites[0].checks}
    assert "a subgroup regression blocks the candidate" in names
    assert report.suites[0].failed == 0
