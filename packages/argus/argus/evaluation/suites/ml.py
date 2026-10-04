"""ML evaluation (§43/§44): metrics, calibration and the production gate.

Deterministic, hand-computable cases. The point is that the *machinery* that
would report a model's quality is itself correct, and that an unmeasured
property fails a gate rather than being treated as a pass.
"""

from __future__ import annotations

from argus.evaluation.model_regression import (
    ModelEvaluation,
    compare_against_production,
    compare_models,
)
from argus.evaluation.results import SuiteResult, check
from argus.ml.calibration import evaluate_calibration
from argus.ml.gating import evaluate_gates
from argus.ml.metrics import classification_metrics, majority_class_baseline
from argus.ml.tasks import get_task


def ml_evaluation_suite(context) -> SuiteResult:
    """Metric arithmetic, calibration and conservative gating."""
    checks = []

    # Perfect and wrong predictions.
    perfect = classification_metrics(["a", "b", "a", "b"], ["a", "b", "a", "b"])
    wrong = classification_metrics(["a", "b", "a", "b"], ["b", "a", "b", "a"])
    checks.append(
        check(
            "perfect predictions score 1.0",
            perfect.accuracy == 1.0 and perfect.balanced_accuracy == 1.0,
            detail=f"accuracy={perfect.accuracy}, balanced={perfect.balanced_accuracy}",
        )
    )
    checks.append(
        check(
            "all-wrong predictions score 0.0",
            wrong.accuracy == 0.0,
            detail=f"accuracy={wrong.accuracy}",
            critical=True,
        )
    )

    # The majority baseline must not be flattered by raw accuracy.
    baseline = majority_class_baseline(["a"] * 9 + ["b"])
    checks.append(
        check(
            "the majority baseline warns about raw accuracy",
            baseline.accuracy == 0.9 and any("flatter" in note for note in baseline.notes),
            detail=f"accuracy={baseline.accuracy}, balanced={baseline.balanced_accuracy}",
        )
    )
    checks.append(
        check(
            "the majority baseline has no real skill",
            baseline.balanced_accuracy is not None and baseline.balanced_accuracy <= 0.5 + 1e-9,
            detail=f"balanced_accuracy={baseline.balanced_accuracy}",
        )
    )

    # Calibration: a score that predicts the observed rate is calibrated.
    calibrated = evaluate_calibration([1, 0, 1, 0], [0.5, 0.5, 0.5, 0.5])
    checks.append(
        check(
            "a calibrated score has ~zero error",
            calibrated.expected_calibration_error is not None
            and calibrated.expected_calibration_error <= 0.05,
            detail=f"ece={calibrated.expected_calibration_error}",
        )
    )
    overconfident = evaluate_calibration([0, 0, 1, 0], [0.99, 0.99, 0.99, 0.99])
    checks.append(
        check(
            "an overconfident score is caught",
            overconfident.expected_calibration_error is not None
            and overconfident.expected_calibration_error > 0.05,
            detail=f"ece={overconfident.expected_calibration_error}",
            critical=True,
        )
    )

    # Gating: nothing measured ⇒ nothing approved.
    task = get_task("game_outcome")
    decision = evaluate_gates(
        task,
        model_name="argus-eval-candidate",
        test_metrics=baseline,
        majority_metrics=baseline,
        rating_metrics=None,
        calibration_ece=None,
        leakage_passed=None,
        subgroup_scores=None,
        reproducibility=None,
        dataset_stats=None,
    )
    checks.append(
        check(
            "an unmeasured model is not approved",
            decision.passed is False,
            detail=f"status={decision.level.value}, failures={len(decision.failures)}",
            critical=True,
        )
    )

    checks.extend(_regression_checks())

    return SuiteResult(
        suite="ml_evaluation",
        title="ML metrics, calibration and gating",
        checks=checks,
    )


def _regression_checks() -> list:
    """Model regression (§44): a candidate is compared, never auto-promoted."""
    production = ModelEvaluation(
        model_version="prod-1",
        dataset_version="chess-errors@1",
        metrics={"macro_f1": 0.50, "log_loss": 1.10},
        subgroups={"blitz": {"macro_f1": 0.48}, "rapid": {"macro_f1": 0.52}},
        leakage_passed=True,
        reproducible=True,
    )

    # A clean improvement is recommended, still as a decision rather than a promotion.
    better = ModelEvaluation(
        model_version="cand-2",
        dataset_version="chess-errors@1",
        metrics={"macro_f1": 0.55, "log_loss": 1.02},
        subgroups={"blitz": {"macro_f1": 0.50}, "rapid": {"macro_f1": 0.56}},
        leakage_passed=True,
        reproducible=True,
    )
    clean = compare_models(production, better)

    # The same average, but worse for one time control: not an improvement.
    lopsided = ModelEvaluation(
        model_version="cand-3",
        dataset_version="chess-errors@1",
        metrics={"macro_f1": 0.55, "log_loss": 1.05},
        subgroups={"blitz": {"macro_f1": 0.40}, "rapid": {"macro_f1": 0.60}},
        leakage_passed=True,
        reproducible=True,
    )
    subgroup = compare_models(production, lopsided)

    leaky = ModelEvaluation(
        model_version="cand-4",
        dataset_version="chess-errors@1",
        metrics={"macro_f1": 0.70, "log_loss": 0.90},
        leakage_passed=False,
        reproducible=True,
    )
    leakage = compare_models(production, leaky)

    return [
        check(
            "a strictly better candidate is recommended, not promoted",
            clean.recommendation == "candidate" and clean.promotable,
            detail=f"recommendation={clean.recommendation}",
        ),
        check(
            "a subgroup regression blocks the candidate",
            subgroup.recommendation == "baseline"
            and not subgroup.promotable
            and any("blitz" in reason for reason in subgroup.blocking_reasons),
            detail=f"subgroup_regressions={subgroup.subgroup_regressions}",
            critical=True,
        ),
        check(
            "a leakage failure blocks the candidate regardless of score",
            leakage.recommendation == "baseline"
            and any("leakage" in reason for reason in leakage.blocking_reasons),
            detail=f"recommendation={leakage.recommendation}",
            critical=True,
        ),
        check(
            "with no production model a candidate is not promoted",
            compare_against_production(leaky).recommendation == "insufficient-data",
            detail="no production model to regress against",
        ),
        check(
            "a calibration regression counts in the right direction",
            compare_models(
                ModelEvaluation("p", "d@1", metrics={"expected_calibration_error": 0.02}),
                ModelEvaluation("c", "d@1", metrics={"expected_calibration_error": 0.09}),
            ).recommendation
            == "baseline",
            detail="a higher ECE is a regression, not an improvement",
            critical=True,
        ),
    ]


__all__ = ["ml_evaluation_suite"]
