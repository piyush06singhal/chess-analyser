"""Production gating — what a model must prove before a user may see it.

"There is a model" and "this may be shown to a person" are different facts. This
module is where the second one is decided, and the default answer is **no**: a
model is only ``PRODUCTION`` when every gate passes, each gate is measured
against a declared threshold, and the whole decision is recorded so it can be
audited later.

The gates exist because each one catches a specific way an ML feature ships
broken:

``data sufficiency``
    Enough games, rows and players for the task's own declared requirements.
``test performance``
    Balanced accuracy and macro F1 on the untouched test split.
``baseline lift``
    Beats the majority baseline *and* the rating baseline by a real margin.
    A model that merely matches a rating lookup is not a product.
``calibration``
    Expected calibration error within the threshold, so a stated probability is
    a probability.
``subgroup stability``
    No slice (rating band, time class, seen/unseen players) collapses. An
    aggregate score can hide a model that is useless for exactly the players who
    would use it.
``leakage``
    Every leakage check passed.
``reproducibility``
    Seed, dataset version and feature version recorded.

A task with no model that passes simply has no available prediction. That is a
supported outcome, not a failure — :class:`GateDecision` says why.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from argus.ml.metrics import ClassificationMetrics
from argus.ml.models import ModelStatus
from argus.ml.tasks import PredictionTaskDefinition


class GateThresholds(BaseModel):
    """Task-specific, documented thresholds.

    Every value is a *judgement*, and the judgement is written down so it can be
    argued with. Thresholds are per-task because the tasks are not equally hard:
    requiring 0.7 balanced accuracy on a draw-heavy outcome task would guarantee
    that nothing ever ships, which is not the same as being rigorous.
    """

    #: Data floors. These can only make the task's own declared requirements
    #: *stricter*; the task definition is the single source of truth for what the
    #: task needs, and these let an operator demand more before shipping.
    min_games: int = 20_000
    min_train_rows: int = 14_000
    min_test_rows: int = 3_000
    min_players: int = 500
    min_balanced_accuracy: float = 0.45
    min_macro_f1: float = 0.42
    max_log_loss: float = 1.02
    #: Absolute lift over the majority baseline on macro F1.
    min_macro_f1_lift_over_majority: float = 0.10
    #: Absolute lift over the rating baseline on macro F1. The important one.
    min_macro_f1_lift_over_rating: float = 0.03
    max_expected_calibration_error: float = 0.05
    max_subgroup_drop: float = 0.15
    require_leakage_checks_passed: bool = True
    require_reproducibility: bool = True
    rationale: str = ""

    def describe(self) -> dict[str, float | int | bool | str]:
        return self.model_dump()


#: Documented defaults per task. The outcome task is calibrated against a rating
#: baseline; the binary error-risk tasks are judged on PR-AUC and calibration
#: because their positives are rare.
TASK_THRESHOLDS: dict[str, GateThresholds] = {
    "game_outcome": GateThresholds(
        min_games=20_000,
        min_train_rows=14_000,
        min_test_rows=3_000,
        min_players=500,
        min_balanced_accuracy=0.45,
        min_macro_f1=0.42,
        max_log_loss=1.02,
        min_macro_f1_lift_over_majority=0.10,
        min_macro_f1_lift_over_rating=0.03,
        max_expected_calibration_error=0.05,
        rationale=(
            "A rating lookup already predicts chess outcomes well, so the bar is a "
            "real lift OVER the rating baseline, not over the majority class. Log "
            "loss must stay near the three-class uniform value (1.099) or better."
        ),
    ),
    "position_outcome": GateThresholds(
        min_games=2_000,
        min_train_rows=50_000,
        min_test_rows=8_000,
        min_players=200,
        min_balanced_accuracy=0.40,
        min_macro_f1=0.36,
        max_log_loss=1.05,
        min_macro_f1_lift_over_rating=0.02,
        rationale=(
            "Positions are correlated within a game, so the effective sample is the "
            "game count; thresholds are deliberately modest and the split must be "
            "game-based."
        ),
    ),
    "position_difficulty": GateThresholds(
        min_games=1_000,
        min_train_rows=20_000,
        min_test_rows=4_000,
        min_players=100,
        min_balanced_accuracy=0.60,
        min_macro_f1=0.60,
        max_log_loss=0.68,
        min_macro_f1_lift_over_majority=0.10,
        max_expected_calibration_error=0.05,
        rationale=(
            "A binary task with a genuinely learnable label: if board features cannot "
            "beat 0.60 balanced accuracy here, the feature set is the problem."
        ),
    ),
    "move_error_risk": GateThresholds(
        min_games=1_000,
        min_train_rows=30_000,
        min_test_rows=5_000,
        min_players=100,
        min_balanced_accuracy=0.58,
        min_macro_f1=0.55,
        max_log_loss=0.70,
        min_macro_f1_lift_over_majority=0.08,
        max_expected_calibration_error=0.05,
        max_subgroup_drop=0.15,
        rationale=(
            "Errors are rare-ish and player-dependent, so the model must hold up on "
            "UNSEEN players — hence the subgroup gate on the player-holdout split."
        ),
    ),
    "player_performance": GateThresholds(
        min_games=5_000,
        min_train_rows=1_000,
        min_test_rows=200,
        min_players=100,
        rationale="Not attempted in this phase; thresholds are declared for later work.",
    ),
}


class GateCheck(BaseModel):
    """One gate, its measured value, and the threshold it was judged against."""

    name: str
    passed: bool
    measured: float | int | str | bool | None = None
    threshold: float | int | str | bool | None = None
    required: bool = True
    message: str = ""


class GateDecision(BaseModel):
    """The full production-gating verdict for one candidate model."""

    task: str
    model: str
    passed: bool = False
    level: ModelStatus = ModelStatus.EXPERIMENTAL
    checks: list[GateCheck] = Field(default_factory=list)
    thresholds: dict[str, object] = Field(default_factory=dict)
    summary: str = ""

    @property
    def failures(self) -> list[GateCheck]:
        return [check for check in self.checks if not check.passed]

    def as_report(self) -> dict[str, object]:
        return {
            "task": self.task,
            "model": self.model,
            "passed": self.passed,
            "status": self.level.value,
            "summary": self.summary,
            "checks": [check.model_dump() for check in self.checks],
            "thresholds": self.thresholds,
        }


def _check(
    name: str,
    passed: bool,
    *,
    measured: float | int | str | bool | None = None,
    threshold: float | int | str | bool | None = None,
    message: str = "",
    required: bool = True,
) -> GateCheck:
    return GateCheck(
        name=name,
        passed=passed,
        measured=measured,
        threshold=threshold,
        message=message,
        required=required,
    )


def evaluate_gates(
    task: PredictionTaskDefinition,
    *,
    model_name: str,
    test_metrics: ClassificationMetrics,
    majority_metrics: ClassificationMetrics | None,
    rating_metrics: ClassificationMetrics | None = None,
    calibration_ece: float | None = None,
    leakage_passed: bool | None = None,
    subgroup_scores: dict[str, float] | None = None,
    reproducibility: dict[str, object] | None = None,
    dataset_stats: dict[str, object] | None = None,
    thresholds: GateThresholds | None = None,
) -> GateDecision:
    """Judge a candidate model against the task's production gates.

    ``None`` for a measurement is treated as *not demonstrated* and fails the
    corresponding gate. That is the conservative direction on purpose: an
    unmeasured property must not unlock production.
    """
    policy = thresholds or TASK_THRESHOLDS.get(task.task_name) or GateThresholds()
    stats = dataset_stats or {}
    checks: list[GateCheck] = []

    # --- data sufficiency ---------------------------------------------------
    # The task's own declaration is the floor; the policy may only tighten it,
    # never loosen it. Otherwise an operator could quietly pass a model on data
    # the task itself says is insufficient.
    requirement = task.data_requirements
    floors = {
        "games": max(requirement.min_games, policy.min_games),
        "train_rows": max(requirement.min_train_rows, policy.min_train_rows),
        "test_rows": max(requirement.min_test_rows, policy.min_test_rows),
        "players": max(requirement.min_players, policy.min_players),
    }
    measured = {name: int(stats.get(name, 0) or 0) for name in floors}
    checks.append(
        _check(
            "data_sufficiency",
            all(measured[name] >= floor for name, floor in floors.items()),
            measured=", ".join(f"{name}={value}" for name, value in measured.items()),
            threshold=", ".join(f"{name}>={floor}" for name, floor in floors.items()),
            message="The task's declared data requirements must be met in full.",
        )
    )

    # --- test performance ---------------------------------------------------
    checks.append(
        _check(
            "balanced_accuracy",
            (test_metrics.balanced_accuracy or 0.0) >= policy.min_balanced_accuracy,
            measured=test_metrics.balanced_accuracy,
            threshold=policy.min_balanced_accuracy,
            message="Balanced accuracy on the untouched test split.",
        )
    )
    checks.append(
        _check(
            "macro_f1",
            (test_metrics.macro_f1 or 0.0) >= policy.min_macro_f1,
            measured=test_metrics.macro_f1,
            threshold=policy.min_macro_f1,
            message="Macro F1 on the test split; every present class counts.",
        )
    )
    checks.append(
        _check(
            "log_loss",
            test_metrics.log_loss is not None and test_metrics.log_loss <= policy.max_log_loss,
            measured=test_metrics.log_loss,
            threshold=policy.max_log_loss,
            message="Log loss must not exceed the three-class uniform value.",
        )
    )

    # --- baseline lift ------------------------------------------------------
    majority_f1 = (majority_metrics.macro_f1 if majority_metrics else None)
    if majority_f1 is None:
        checks.append(
            _check(
                "lift_over_majority",
                False,
                message="No majority-class baseline was measured, so lift is not demonstrated.",
            )
        )
    else:
        lift = (test_metrics.macro_f1 or 0.0) - majority_f1
        checks.append(
            _check(
                "lift_over_majority",
                lift >= policy.min_macro_f1_lift_over_majority,
                measured=round(lift, 4),
                threshold=policy.min_macro_f1_lift_over_majority,
                message="Macro F1 lift over always predicting the commonest class.",
            )
        )

    rating_f1 = (rating_metrics.macro_f1 if rating_metrics else None)
    if rating_f1 is None:
        checks.append(
            _check(
                "lift_over_rating_baseline",
                False,
                message=(
                    "No rating baseline was measured. Without it there is no evidence "
                    "the model adds anything over a rating lookup."
                ),
            )
        )
    else:
        lift = (test_metrics.macro_f1 or 0.0) - rating_f1
        checks.append(
            _check(
                "lift_over_rating_baseline",
                lift >= policy.min_macro_f1_lift_over_rating,
                measured=round(lift, 4),
                threshold=policy.min_macro_f1_lift_over_rating,
                message="Macro F1 lift over the rating-only baseline.",
            )
        )

    # --- calibration --------------------------------------------------------
    checks.append(
        _check(
            "calibration",
            calibration_ece is not None
            and calibration_ece <= policy.max_expected_calibration_error,
            measured=calibration_ece,
            threshold=policy.max_expected_calibration_error,
            message=(
                "Expected calibration error, so a stated probability means what it "
                "says. An unmeasured ECE fails this gate."
            ),
        )
    )

    # --- subgroup stability -------------------------------------------------
    if not subgroup_scores:
        checks.append(
            _check(
                "subgroup_stability",
                False,
                message="No subgroup scores were supplied, so degradation is not ruled out.",
            )
        )
    else:
        worst_name, worst_value = min(subgroup_scores.items(), key=lambda item: item[1])
        best_value = max(subgroup_scores.values())
        drop = round(best_value - worst_value, 4)
        checks.append(
            _check(
                "subgroup_stability",
                drop <= policy.max_subgroup_drop,
                measured=drop,
                threshold=policy.max_subgroup_drop,
                message=(
                    f"Worst slice is '{worst_name}' at {worst_value:.3f}; a large gap "
                    "means the aggregate score hides a group the model fails."
                ),
            )
        )

    # --- leakage ------------------------------------------------------------
    if policy.require_leakage_checks_passed:
        checks.append(
            _check(
                "leakage_checks",
                leakage_passed is True,
                measured=leakage_passed,
                threshold=True,
                message="Every leakage check must pass, with no error-level finding.",
            )
        )

    # --- reproducibility ----------------------------------------------------
    if policy.require_reproducibility:
        payload = reproducibility or {}
        needed = ("seed", "dataset_version", "feature_version")
        missing = [key for key in needed if payload.get(key) in (None, "")]
        checks.append(
            _check(
                "reproducibility",
                not missing,
                measured=", ".join(f"{key}={payload.get(key)}" for key in needed),
                threshold="seed, dataset_version and feature_version recorded",
                message=(
                    "A result that cannot be reproduced is not evidence."
                    + (f" Missing: {', '.join(missing)}." if missing else "")
                ),
            )
        )

    failed = [check for check in checks if not check.passed and check.required]
    decision = GateDecision(
        task=task.task_name,
        model=model_name,
        passed=not failed,
        level=ModelStatus.PRODUCTION if not failed else ModelStatus.EXPERIMENTAL,
        checks=checks,
        thresholds=policy.describe(),
    )
    if failed:
        decision.summary = (
            f"{model_name} is NOT production-ready for '{task.task_name}': "
            f"{len(failed)} of {len(checks)} gate(s) failed — "
            + "; ".join(check.name for check in failed)
            + ". The prediction stays unavailable."
        )
    else:
        decision.summary = (
            f"{model_name} passed all {len(checks)} gates for '{task.task_name}' and may "
            "be promoted to production."
        )
    return decision


__all__ = [
    "TASK_THRESHOLDS",
    "GateCheck",
    "GateDecision",
    "GateThresholds",
    "evaluate_gates",
]
