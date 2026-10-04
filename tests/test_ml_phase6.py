"""Phase 6 ML tests: tasks, metrics, calibration, models, registry, gating, service.

Two kinds of assertion live here. The first are *known-value* checks: a metric is
verified against a case whose answer can be worked out by hand, so a metric
cannot silently drift. The second are *guardrail* checks: the system's refusals
are tested as behaviour, because a guardrail that is not tested is a comment.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from argus.ml.baselines import (
    BASELINE_LADDER,
    MODEL_CLASSES,
    MajorityClassModel,
    make_model,
)
from argus.ml.calibration import (
    IsotonicCalibrator,
    PlattCalibrator,
    calibrate_and_evaluate,
    evaluate_calibration,
    evaluate_multiclass_calibration,
    reliability_curve,
)
from argus.ml.error_analysis import (
    misclassified_examples,
    permutation_importance,
    slice_metrics,
)
from argus.ml.experiment_runner import MIN_EXPLORATORY_ROWS, evaluate_strategy
from argus.ml.experiments import ExperimentTracker
from argus.ml.gating import GateThresholds, evaluate_gates
from argus.ml.metrics import (
    ClassificationMetrics,
    average_precision_binary,
    brier_score,
    classification_metrics,
    confusion_matrix,
    log_loss,
    majority_class_baseline,
    roc_auc_binary,
)
from argus.ml.models import ModelStatus
from argus.ml.registry import ModelRegistry, RegisteredModel, model_id_for
from argus.ml.service import PredictionService, PredictionUnavailable
from argus.ml.tasks import TASK_REGISTRY, get_task, production_ready_tasks, task_names
from argus.datasets.importer import DatasetImporter
from argus.datasets.labels import label_games
from argus.datasets.splits import SplitStrategy
from argus.shared.errors import NotFoundError, ValidationError
from tests.conftest import build_fixture_pgn

CLASSES = ["white_win", "draw", "black_win"]


def _labelled_records(count: int = 200, **kwargs):
    records = DatasetImporter(source="fixture").ingest_text(build_fixture_pgn(count, **kwargs)).records
    return records, label_games(records).as_map()


# --- task definitions --------------------------------------------------------


class TestPredictionTasks:
    def test_all_five_candidate_tasks_are_declared(self) -> None:
        assert task_names() == [
            "game_outcome",
            "move_error_risk",
            "player_performance",
            "position_difficulty",
            "position_outcome",
        ]

    def test_every_task_states_its_target_and_unit(self) -> None:
        for name, task in TASK_REGISTRY.items():
            assert task.target, name
            assert task.target_values, name
            assert task.unit_of_prediction, name
            assert task.label_definition, name
            assert task.label_source, name
            assert task.evaluation_metrics, name
            assert task.required_metrics, name
            assert task.leakage_risks, name
            assert task.baseline_models, name
            assert task.hypothesis, name
            assert task.limitations, name

    def test_every_task_declares_data_requirements_and_a_rationale(self) -> None:
        for name, task in TASK_REGISTRY.items():
            assert task.data_requirements.rationale, name
            assert task.data_requirements.min_games >= 0
            # A task must require *something*, or "insufficient data" could never
            # be concluded.
            assert (
                task.data_requirements.min_games
                or task.data_requirements.min_train_rows
                or task.data_requirements.requires_per_player_history
            ), name

    def test_no_task_is_production_ready_yet(self) -> None:
        """Honest starting state: every task is experimental."""
        assert production_ready_tasks() == []
        assert all(task.production_status is ModelStatus.EXPERIMENTAL for task in TASK_REGISTRY.values())

    def test_readiness_lists_every_missing_requirement(self) -> None:
        readiness = get_task("game_outcome").readiness(games=10, train_rows=5, test_rows=1, players=2)
        assert readiness["attemptable"] is False
        assert len(readiness["missing_requirements"]) >= 4
        assert any("games" in item for item in readiness["missing_requirements"])

    def test_readiness_reports_satisfied_requirements_as_attemptable(self) -> None:
        requirement = get_task("game_outcome").data_requirements
        readiness = get_task("game_outcome").readiness(
            games=requirement.min_games,
            train_rows=requirement.min_train_rows,
            validation_rows=requirement.min_validation_rows,
            test_rows=requirement.min_test_rows,
            players=requirement.min_players,
            has_ratings=True,
            class_shares={"white_win": 0.4, "draw": 0.3, "black_win": 0.3},
        )
        assert readiness["attemptable"] is True
        assert readiness["missing_requirements"] == []

    def test_thin_class_is_a_missing_requirement(self) -> None:
        requirement = get_task("game_outcome").data_requirements
        readiness = get_task("game_outcome").readiness(
            games=requirement.min_games,
            train_rows=requirement.min_train_rows,
            validation_rows=requirement.min_validation_rows,
            test_rows=requirement.min_test_rows,
            players=requirement.min_players,
            has_ratings=True,
            class_shares={"white_win": 0.95, "draw": 0.03, "black_win": 0.02},
        )
        assert readiness["attemptable"] is False
        assert any("below the" in item for item in readiness["missing_requirements"])

    def test_undeclared_task_cannot_be_fetched(self) -> None:
        with pytest.raises(KeyError):
            get_task("predict_everything")


# --- metrics -----------------------------------------------------------------


class TestMetrics:
    def test_perfect_predictions_score_perfectly(self) -> None:
        truth = ["a", "b", "c"]
        metrics = classification_metrics(truth, truth, classes=["a", "b", "c"])
        assert metrics.accuracy == 1.0
        assert metrics.balanced_accuracy == 1.0
        assert metrics.macro_f1 == 1.0

    def test_confusion_matrix_orientation_is_truth_by_prediction(self) -> None:
        matrix = confusion_matrix(["a", "a", "b"], ["a", "b", "b"], ["a", "b"])
        assert matrix == [[1, 1], [0, 1]]

    def test_macro_f1_counts_an_unpredicted_class_as_zero(self) -> None:
        # The model never predicts 'b'; a macro average must include it as 0.
        metrics = classification_metrics(["a", "b", "a"], ["a", "a", "a"], classes=["a", "b"])
        assert metrics.macro_f1 is not None
        assert metrics.macro_f1 == pytest.approx(metrics.per_class["a"]["f1"] / 2, rel=1e-6)
        assert metrics.per_class["b"]["precision"] is None

    def test_balanced_accuracy_is_the_mean_recall(self) -> None:
        metrics = classification_metrics(["a", "b"], ["a", "a"], classes=["a", "b"])
        assert metrics.balanced_accuracy == pytest.approx(0.5)

    def test_log_loss_of_a_uniform_three_class_model_is_ln_3(self) -> None:
        truth = ["a", "b", "c", "a"]
        proba = [[1 / 3, 1 / 3, 1 / 3] for _ in truth]
        assert log_loss(truth, proba, ["a", "b", "c"]) == pytest.approx(math.log(3), rel=1e-9)

    def test_brier_of_a_uniform_three_class_model(self) -> None:
        # (1-1/3)^2 + 2*(1/3)^2 = 4/9 + 2/9 = 6/9
        score = brier_score(["a"], [[1 / 3, 1 / 3, 1 / 3]], ["a", "b", "c"])
        assert score == pytest.approx(6 / 9, rel=1e-9)

    def test_auc_of_a_perfect_ranking_is_one(self) -> None:
        assert roc_auc_binary([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1.0)

    def test_auc_of_a_reversed_ranking_is_zero(self) -> None:
        assert roc_auc_binary([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == pytest.approx(0.0)

    def test_auc_of_tied_scores_is_one_half(self) -> None:
        assert roc_auc_binary([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) == pytest.approx(0.5)

    def test_auc_is_none_when_a_class_is_absent(self) -> None:
        """Undefined must be None, not 0.0 and not 1.0."""
        assert roc_auc_binary([1, 1, 1], [0.1, 0.2, 0.3]) is None
        assert roc_auc_binary([0, 0], [0.1, 0.2]) is None

    def test_average_precision_rewards_early_positives(self) -> None:
        early = average_precision_binary([0, 0, 1, 1], [0.1, 0.2, 0.9, 0.8])
        late = average_precision_binary([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1])
        assert early is not None and late is not None
        assert early > late

    def test_majority_baseline_has_high_accuracy_and_low_macro_f1(self) -> None:
        truth = ["a"] * 90 + ["b"] * 10
        metrics = majority_class_baseline(truth)
        assert metrics.accuracy == pytest.approx(0.9)
        assert metrics.balanced_accuracy == pytest.approx(0.5)
        # The diagnostic: 0.9 accuracy, yet the minority class is never found, so
        # macro F1 is (0.9474 + 0.0) / 2 = 0.4737 rather than anything near 0.9.
        assert metrics.macro_f1 == pytest.approx(0.4737, abs=1e-4)
        assert metrics.macro_f1 < metrics.accuracy
        assert any("flatters" in note for note in metrics.notes)

    def test_empty_split_measures_nothing_and_says_so(self) -> None:
        metrics = classification_metrics([], [], classes=CLASSES)
        assert metrics.rows == 0
        assert metrics.accuracy is None
        assert any("nothing is measurable" in note for note in metrics.notes)

    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError):
            classification_metrics(["a"], ["a", "b"], classes=["a", "b"])

    def test_binary_framing_reports_auc_and_averaged_precision(self) -> None:
        truth = ["draw", "white_win", "draw", "black_win"]
        proba = [[0.1, 0.8, 0.1], [0.7, 0.2, 0.1], [0.2, 0.7, 0.1], [0.1, 0.1, 0.8]]
        metrics = classification_metrics(
            truth,
            ["white_win", "white_win", "draw", "black_win"],
            proba,
            classes=CLASSES,
            binary=True,
            positive_class="draw",
        )
        assert metrics.positive_class == "draw"
        assert metrics.roc_auc is not None
        assert metrics.average_precision is not None


# --- calibration -------------------------------------------------------------


class TestCalibration:
    def test_perfectly_calibrated_scores_have_low_error(self) -> None:
        rng = np.random.default_rng(0)
        probabilities = rng.random(4000)
        outcomes = (rng.random(4000) < probabilities).astype(int)
        report = evaluate_calibration(outcomes, probabilities, bins=10)
        assert report.is_calibrated
        assert report.expected_calibration_error is not None
        assert report.expected_calibration_error < 0.05

    def test_overconfident_scores_are_detected(self) -> None:
        rng = np.random.default_rng(1)
        probabilities = np.full(2000, 0.9)
        # Truth is 50/50, so 0.9 is a 0.4 overstatement.
        outcomes = rng.integers(0, 2, size=2000)
        report = evaluate_calibration(outcomes, probabilities, bins=10)
        assert report.expected_calibration_error is not None
        assert report.expected_calibration_error > 0.3
        assert not report.is_calibrated

    def test_reliability_curve_bins_predictions(self) -> None:
        curve = reliability_curve([0, 1, 1, 1], [0.05, 0.95, 0.92, 0.99], bins=10)
        assert len(curve) == 10
        assert sum(item.count for item in curve) == 4
        top = curve[-1]
        assert top.predicted_mean is not None and top.predicted_mean > 0.9
        assert top.observed_rate == 1.0
        assert top.gap is not None and top.gap > 0

    def test_isotonic_is_monotone_and_interpolates(self) -> None:
        scores = [0.1, 0.2, 0.3, 0.6, 0.7, 0.9]
        outcomes = [0, 0, 1, 0, 1, 1]
        calibrator = IsotonicCalibrator().fit(scores, outcomes)
        calibrated = calibrator.transform(scores)
        assert all(a <= b + 1e-9 for a, b in zip(calibrated, calibrated[1:]))
        assert all(0 <= value <= 1 for value in calibrated)

    def test_isotonic_pools_adjacent_violators(self) -> None:
        # 0,1,0 is not monotone: the first two blocks must pool.
        calibrator = IsotonicCalibrator().fit([0.1, 0.2, 0.3], [0, 1, 0])
        assert len(calibrator.x) < 3
        assert calibrator.transform([0.1])[0] <= calibrator.transform([0.3])[0] + 1e-9

    def test_platt_learns_the_direction_of_the_signal(self) -> None:
        rng = np.random.default_rng(2)
        scores = rng.random(3000)
        outcomes = (rng.random(3000) < scores**2).astype(int)
        calibrator = PlattCalibrator().fit(scores, outcomes)
        assert calibrator.a > 0
        assert calibrator.transform([0.9])[0] > calibrator.transform([0.1])[0]

    def test_calibration_is_fitted_on_validation_and_measured_on_test(self) -> None:
        rng = np.random.default_rng(3)
        validation_scores = rng.random(2000)
        validation_outcomes = (rng.random(2000) < validation_scores**2).astype(int)
        test_scores = rng.random(800)
        test_outcomes = (rng.random(800) < test_scores**2).astype(int)

        uncalibrated = evaluate_calibration(test_outcomes, test_scores)
        report, calibrated = calibrate_and_evaluate(
            validation_scores, validation_outcomes, test_scores, test_outcomes, method="isotonic"
        )
        assert report.calibrated is True
        assert report.calibrator == "isotonic"
        assert len(calibrated) == len(test_scores)
        assert report.expected_calibration_error is not None
        assert uncalibrated.expected_calibration_error is not None
        assert report.expected_calibration_error <= uncalibrated.expected_calibration_error + 0.05

    def test_unknown_calibration_method_is_refused(self) -> None:
        with pytest.raises(ValueError):
            calibrate_and_evaluate([0.5], [1], [0.5], [1], method="vibes")

    def test_multiclass_calibration_uses_top_label_confidence(self) -> None:
        probabilities = [[0.9, 0.05, 0.05], [0.05, 0.9, 0.05]]
        report = evaluate_multiclass_calibration(
            ["white_win", "black_win"], probabilities, CLASSES
        )
        assert report.rows == 2
        # One prediction was confident and wrong: the curve must show that.
        assert report.expected_calibration_error is not None
        assert report.expected_calibration_error > 0
        assert any("Top-label" in note for note in report.notes)

    def test_single_class_data_cannot_establish_calibration(self) -> None:
        report = evaluate_calibration([1, 1, 1, 1], [0.5, 0.6, 0.7, 0.8])
        assert any("only one class" in note.lower() for note in report.notes)


# --- models ------------------------------------------------------------------


class TestBaselines:
    def _trained(self, kind: str):
        records, labels = _labelled_records(160)
        rows = []
        from argus.datasets.features import GAME_OUTCOME_FEATURES, build_game_features

        for record in records:
            rows.append(build_game_features(record))
        features = ["rating_diff"] if kind == "rating_based" else GAME_OUTCOME_FEATURES
        model = make_model(kind, classes=CLASSES)
        model.fit(rows[:120], [labels[r.game_id] for r in records[:120]], feature_names=features)
        return model, rows[120:], [labels[r.game_id] for r in records[120:]]

    def test_the_ladder_is_ordered_from_simple_to_complex(self) -> None:
        assert BASELINE_LADDER == [
            "majority_class",
            "rating_based",
            "logistic_regression",
            "tree_ensemble",
        ]
        assert set(MODEL_CLASSES) == set(BASELINE_LADDER)

    def test_unknown_model_kind_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            make_model("deep_neural_net")

    def test_majority_model_predicts_the_training_distribution(self) -> None:
        model, rows, _ = self._trained("majority_class")
        probabilities = model.predict_proba(rows)
        assert all(abs(probabilities[0][0] - probabilities[index][0]) < 1e-12 for index in range(len(rows)))
        assert abs(sum(probabilities[0]) - 1.0) < 1e-9
        assert model._params["majority_class"] in CLASSES  # noqa: SLF001

    def test_rating_model_learns_that_ratings_matter(self) -> None:
        model = make_model("rating_based", classes=CLASSES)
        rows = [{"rating_diff": value} for value in (-400, -200, 0, 200, 400) * 40]
        labels = []
        for row in rows:
            labels.append(
                "white_win" if row["rating_diff"] > 100 else ("black_win" if row["rating_diff"] < -100 else "draw")
            )
        model.fit(rows, labels, feature_names=["rating_diff"])
        probabilities = model.predict_proba([{"rating_diff": 500}, {"rating_diff": -500}])
        assert probabilities[0][0] > probabilities[1][0]
        assert probabilities[1][2] > probabilities[0][2]

    def test_logistic_model_matches_rating_model_when_only_ratings_matter(self) -> None:
        """A consistency check: with one informative feature they must agree."""
        records, labels = _labelled_records(200)
        from argus.datasets.features import build_game_features

        rows = [build_game_features(record) for record in records]
        targets = [labels[record.game_id] for record in records]
        rating = make_model("rating_based", classes=CLASSES)
        rating.fit(rows, targets, feature_names=["rating_diff"])
        logistic = make_model("logistic_regression", classes=CLASSES)
        logistic.fit(rows, targets, feature_names=["rating_diff"])
        first = rating.predict_proba(rows[:20])
        second = logistic.predict_proba(rows[:20])
        # These are two different optimisers (plain gradient descent on a
        # median-scaled rating vs. L2-regularised descent on standardised
        # features), so agreement is asymptotic, not bit-exact.
        for left, right in zip(first, second):
            for a, b in zip(left, right):
                assert abs(a - b) < 1e-5

    def test_probabilities_are_a_distribution_and_predictions_follow_it(self) -> None:
        model, rows, _ = self._trained("logistic_regression")
        probabilities = model.predict_proba(rows)
        predictions = model.predict(rows)
        for row, prediction in zip(probabilities, predictions):
            assert abs(sum(row) - 1.0) < 1e-6
            assert all(0.0 <= value <= 1.0 for value in row)
            assert model.card.classes[int(np.argmax(row))] == prediction

    def test_fitted_model_round_trips_through_disk(self, tmp_path) -> None:
        model, rows, _ = self._trained("logistic_regression")
        model.save(tmp_path)
        restored = type(model).load(tmp_path, model.name)
        assert restored.predict(rows) == model.predict(rows)
        assert restored.card.feature_names == model.card.feature_names
        assert restored.card.imputation == model.card.imputation

    def test_imputation_and_scaling_come_from_the_training_rows_only(self) -> None:
        model = make_model("logistic_regression", classes=CLASSES)
        rows = [{"rating_diff": 100.0}, {"rating_diff": None}, {"rating_diff": 300.0}, {"rating_diff": None}]
        model.fit(rows, ["white_win", "draw", "black_win", "draw"], feature_names=["rating_diff"])
        # Median of the two observed values, not of anything else.
        assert model.card.imputation["rating_diff"] == pytest.approx(200.0)
        assert model.card.standardisation["rating_diff"][0] == pytest.approx(200.0)

    def test_feature_importance_sums_to_one_when_exposed(self) -> None:
        model, _, _ = self._trained("logistic_regression")
        importance = model.feature_importance()
        assert importance
        assert sum(importance.values()) == pytest.approx(1.0, rel=1e-3)

    def test_model_card_is_describable(self) -> None:
        model, _, _ = self._trained("majority_class")
        described = model.card.describe()
        assert described["fitted_rows"] == 120
        assert described["status"] == "experimental"

    def test_fitting_on_zero_rows_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            make_model("majority_class", classes=CLASSES).fit([], [])

    def test_fitting_with_mismatched_labels_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            make_model("majority_class", classes=CLASSES).fit([{"a": 1}], ["white_win", "draw"])


# --- error analysis ----------------------------------------------------------


class TestErrorAnalysis:
    def test_slices_below_the_floor_are_reported_as_unmeasured(self) -> None:
        truth = ["a", "b"] * 40
        predicted = ["a", "a"] * 40
        slices = ["big"] * 60 + ["tiny"] * 20
        analysis = slice_metrics(truth, predicted, None, slices, classes=["a", "b"])
        by_name = {item.slice_name: item for item in analysis.slices}
        assert by_name["big"].measured
        assert not by_name["tiny"].measured
        assert "below the" in by_name["tiny"].note

    def test_spread_across_slices_is_flagged(self) -> None:
        truth = ["a", "b"] * 100
        predicted = ["a", "a"] * 50 + ["a", "b"] * 50
        slices = ["hard"] * 100 + ["easy"] * 100
        analysis = slice_metrics(truth, predicted, None, slices, classes=["a", "b"])
        assert analysis.worst_slice == "hard"
        assert analysis.best_slice == "easy"
        assert analysis.spread is not None and analysis.spread > 0.15
        assert any("does not describe every group" in note for note in analysis.notes)

    def test_metrics_by_slice_feeds_the_gate(self) -> None:
        analysis = slice_metrics(
            ["a", "b"] * 60, ["a", "a"] * 60, None, ["x"] * 60 + ["y"] * 60, classes=["a", "b"]
        )
        scores = analysis.metrics_by_slice()
        assert set(scores) == {"x", "y"}
        assert all(0 <= value <= 1 for value in scores.values())

    def test_misclassified_examples_lead_with_the_most_confident_mistakes(self) -> None:
        truth = ["a", "b"]
        predicted = ["b", "b"]
        probabilities = [[0.4, 0.6], [0.1, 0.9]]
        examples = misclassified_examples(
            truth, predicted, probabilities, classes=["a", "b"], metadata=[{"game": "g1"}, {"game": "g2"}]
        )
        assert len(examples) == 1
        assert examples[0]["true"] == "a"
        assert examples[0]["probability_of_prediction"] == pytest.approx(0.6)
        assert examples[0]["game"] == "g1"

    def test_mismatched_slice_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError):
            slice_metrics(["a"], ["a"], None, ["x", "y"], classes=["a"])


class TestPermutationImportance:
    def _rows(self, count: int = 200):
        rng = np.random.default_rng(4)
        rows = [
            {"signal": float(rng.normal()), "noise": float(rng.normal())}
            for _ in range(count)
        ]
        labels = ["in" if row["signal"] > 0 else "out" for row in rows]
        return rows, labels

    def test_an_informative_feature_outweighs_noise(self) -> None:
        rows, labels = self._rows()
        model = make_model("logistic_regression", classes=["in", "out"])
        model.fit(rows, labels, feature_names=["signal", "noise"])
        importance = permutation_importance(
            model, rows, labels, feature_names=["signal", "noise"], repeats=3
        )
        assert importance["signal"] > importance["noise"]

    def test_importance_is_normalised_and_reproducible(self) -> None:
        rows, labels = self._rows()
        model = make_model("logistic_regression", classes=["in", "out"])
        model.fit(rows, labels, feature_names=["signal", "noise"])
        first = permutation_importance(model, rows, labels, feature_names=["signal", "noise"], repeats=3, seed=1)
        second = permutation_importance(model, rows, labels, feature_names=["signal", "noise"], repeats=3, seed=1)
        assert first == second
        total = sum(abs(value) for value in first.values())
        assert total == pytest.approx(1.0, rel=1e-3)


# --- registry ----------------------------------------------------------------


class TestModelRegistry:
    def _registered(self, tmp_path: Path) -> tuple[ModelRegistry, RegisteredModel]:
        registry = ModelRegistry.load(tmp_path)
        model = RegisteredModel(
            model_id=model_id_for("game_outcome", "logistic_regression", "1"),
            task="game_outcome",
            model_type="logistic_regression",
            version="1",
            metrics={"macro_f1": 0.4},
            trained_rows=100,
        )
        return registry, registry.register(model)

    def test_registration_is_append_only(self, tmp_path) -> None:
        registry, model = self._registered(tmp_path)
        with pytest.raises(ValidationError, match="already registered"):
            registry.register(model)
        assert len(registry.models) == 1

    def test_explicit_overwrite_is_allowed_and_recorded(self, tmp_path) -> None:
        registry, model = self._registered(tmp_path)
        registry.register(model, overwrite=True)
        assert len(registry.models) == 1
        assert any(entry["status"] == "reregistered" for entry in registry.models[0].status_history)

    def test_a_new_model_starts_experimental_and_is_not_servable(self, tmp_path) -> None:
        registry, model = self._registered(tmp_path)
        assert model.status is ModelStatus.EXPERIMENTAL
        assert not model.is_servable()
        assert registry.production("game_outcome") == []

    def test_promotion_to_production_without_a_gate_is_refused(self, tmp_path) -> None:
        registry, model = self._registered(tmp_path)
        with pytest.raises(ValidationError, match="no production-gate decision"):
            registry.promote(model.model_id, ModelStatus.PRODUCTION)

    def test_promotion_with_a_failing_gate_is_refused(self, tmp_path) -> None:
        registry, model = self._registered(tmp_path)
        weak_task = get_task("game_outcome")
        decision = evaluate_gates(
            weak_task,
            model_name="logistic_regression",
            test_metrics=ClassificationMetrics(rows=0, classes=CLASSES),
            majority_metrics=None,
        )
        with pytest.raises(ValidationError, match="does not pass|did not pass"):
            registry.promote(model.model_id, ModelStatus.PRODUCTION, gate=decision)

    def test_promotion_with_a_passing_gate_is_allowed(self, tmp_path) -> None:
        registry, model = self._registered(tmp_path)
        task = get_task("game_outcome")
        requirement = task.data_requirements
        good = ClassificationMetrics(
            rows=5000,
            classes=CLASSES,
            balanced_accuracy=0.55,
            macro_f1=0.55,
            log_loss=0.95,
        )
        baseline = ClassificationMetrics(rows=5000, classes=CLASSES, balanced_accuracy=0.33, macro_f1=0.25)
        rating = ClassificationMetrics(rows=5000, classes=CLASSES, balanced_accuracy=0.45, macro_f1=0.45)
        decision = evaluate_gates(
            task,
            model_name="logistic_regression",
            test_metrics=good,
            majority_metrics=baseline,
            rating_metrics=rating,
            calibration_ece=0.02,
            leakage_passed=True,
            subgroup_scores={"a": 0.5, "b": 0.55, "c": 0.52},
            reproducibility={"seed": 42, "dataset_version": "v1", "feature_version": "6.0"},
            dataset_stats={
                "games": requirement.min_games,
                "train_rows": requirement.min_train_rows,
                "test_rows": requirement.min_test_rows,
                "players": requirement.min_players,
            },
            thresholds=GateThresholds(min_games=0, min_train_rows=0, min_test_rows=0, min_players=0),
        )
        assert decision.passed, decision.summary
        promoted = registry.promote(model.model_id, ModelStatus.PRODUCTION, gate=decision, reason="passed gates")
        assert promoted.status is ModelStatus.PRODUCTION
        assert promoted.is_servable()
        assert registry.production("game_outcome")

    def test_registry_persists_and_reloads(self, tmp_path) -> None:
        registry, model = self._registered(tmp_path)
        registry.save()
        reloaded = ModelRegistry.load(tmp_path)
        assert [entry.model_id for entry in reloaded.models] == [model.model_id]
        assert reloaded.get(model.model_id) is not None
        assert reloaded.summary()["registered"] == 1

    def test_unknown_model_lookup_returns_none_and_promotion_raises(self, tmp_path) -> None:
        registry = ModelRegistry.load(tmp_path)
        assert registry.get("missing") is None
        with pytest.raises(NotFoundError):
            registry.promote("missing", ModelStatus.VALIDATED)


# --- gating ------------------------------------------------------------------


class TestProductionGating:
    def _decision(self, **overrides):
        task = get_task("game_outcome")
        kwargs = {
            "model_name": "candidate",
            "test_metrics": ClassificationMetrics(rows=100, classes=CLASSES, balanced_accuracy=0.5, macro_f1=0.5, log_loss=1.0),
            "majority_metrics": ClassificationMetrics(rows=100, classes=CLASSES, balanced_accuracy=0.33, macro_f1=0.3),
            "rating_metrics": ClassificationMetrics(rows=100, classes=CLASSES, balanced_accuracy=0.45, macro_f1=0.45),
            "calibration_ece": 0.02,
            "leakage_passed": True,
            "subgroup_scores": {"a": 0.5, "b": 0.52},
            "reproducibility": {"seed": 1, "dataset_version": "v", "feature_version": "6.0"},
            "dataset_stats": {"games": 10, "train_rows": 5, "test_rows": 5, "players": 2},
        }
        kwargs.update(overrides)
        return evaluate_gates(task, **kwargs)

    def test_undersized_data_fails_the_sufficiency_gate(self) -> None:
        decision = self._decision()
        assert not decision.passed
        assert any(check.name == "data_sufficiency" for check in decision.failures)

    def test_every_gate_is_named_and_recorded(self) -> None:
        decision = self._decision()
        names = {check.name for check in decision.checks}
        assert {
            "data_sufficiency",
            "balanced_accuracy",
            "macro_f1",
            "log_loss",
            "lift_over_majority",
            "lift_over_rating_baseline",
            "calibration",
            "subgroup_stability",
            "leakage_checks",
            "reproducibility",
        } <= names
        assert decision.thresholds

    def test_an_unmeasured_property_fails_its_gate(self) -> None:
        decision = self._decision(calibration_ece=None, subgroup_scores=None, leakage_passed=None)
        failed = {check.name for check in decision.failures}
        assert {"calibration", "subgroup_stability", "leakage_checks"} <= failed

    def test_missing_rating_baseline_blocks_production(self) -> None:
        decision = self._decision(rating_metrics=None)
        failing = {check.name: check for check in decision.failures}
        assert "lift_over_rating_baseline" in failing
        # The failure must say *why*, in terms a reader can act on.
        assert "rating lookup" in (failing["lift_over_rating_baseline"].message or "")

    def test_candidate_below_the_rating_baseline_is_rejected(self) -> None:
        decision = self._decision(
            test_metrics=ClassificationMetrics(rows=100, classes=CLASSES, balanced_accuracy=0.4, macro_f1=0.40, log_loss=1.0),
            rating_metrics=ClassificationMetrics(rows=100, classes=CLASSES, balanced_accuracy=0.45, macro_f1=0.45),
        )
        assert any(check.name == "lift_over_rating_baseline" for check in decision.failures)

    def test_subgroup_collapse_is_rejected(self) -> None:
        decision = self._decision(subgroup_scores={"strong": 0.6, "weak": 0.3})
        assert any(check.name == "subgroup_stability" for check in decision.failures)

    def test_a_perfect_candidate_passes_and_is_labelled_production(self) -> None:
        requirement = get_task("game_outcome").data_requirements
        decision = self._decision(
            test_metrics=ClassificationMetrics(rows=9000, classes=CLASSES, balanced_accuracy=0.6, macro_f1=0.6, log_loss=0.95),
            dataset_stats={
                "games": requirement.min_games,
                "train_rows": requirement.min_train_rows,
                "test_rows": requirement.min_test_rows,
                "players": requirement.min_players,
            },
            thresholds=GateThresholds(min_games=0, min_train_rows=0, min_test_rows=0, min_players=0),
        )
        assert decision.passed
        assert decision.level is ModelStatus.PRODUCTION
        assert "passed all" in decision.summary

    def test_decision_report_is_machine_readable(self) -> None:
        report = self._decision().as_report()
        assert report["task"] == "game_outcome"
        assert report["passed"] is False
        assert isinstance(report["checks"], list)


# --- experiments -------------------------------------------------------------


class TestExperimentTracking:
    def test_experiment_directory_is_created_and_never_reused(self, tmp_path) -> None:
        tracker = ExperimentTracker(tmp_path)
        tracker.create(experiment_id="e1", task="game_outcome", model="ladder")
        with pytest.raises(ValidationError, match="already exists"):
            tracker.create(experiment_id="e1", task="game_outcome", model="ladder")

    def test_every_required_evidence_file_can_be_written(self, tmp_path) -> None:
        tracker = ExperimentTracker(tmp_path)
        experiment = tracker.create(experiment_id="e1", task="game_outcome", model="ladder")
        experiment.write_config({"seed": 1})
        experiment.write_manifest({"games": 1})
        experiment.write_metrics({"test": {"accuracy": 0.5}})
        experiment.write_confusion_matrix({"test": [[1, 0], [0, 1]]})
        experiment.write_calibration({"expected_calibration_error": 0.1})
        experiment.write_feature_importance({"rating_diff": 1.0})
        experiment.write_gate(None)
        experiment.write_report("# report")
        written = sorted(path.name for path in experiment.directory.iterdir())
        assert written == [
            "calibration.json",
            "config.json",
            "confusion_matrix.json",
            "dataset_manifest.json",
            "feature_importance.json",
            "gate.json",
            "metrics.json",
            "report.md",
        ]

    def test_index_is_append_only_and_readable(self, tmp_path) -> None:
        tracker = ExperimentTracker(tmp_path)
        first = tracker.create(experiment_id="e1", task="game_outcome", model="ladder")
        first.write_metrics({"test": {"macro_f1": 0.4}})
        first.finish(status="completed", headline={"macro_f1": 0.4})
        tracker.record(first)
        # A refusal is recorded too: it is a result, and it must be auditable.
        second = tracker.create(experiment_id="e2", task="game_outcome", model="ladder")
        second.finish(status="refused_insufficient_data")
        tracker.record(second)
        index = tracker.index()
        assert [entry.experiment_id for entry in index] == ["e1", "e2"]
        assert tracker.load_metrics("e1") == {"test": {"macro_f1": 0.4}}
        assert tracker.summary()[0]["macro_f1"] == 0.4

    def test_missing_metrics_read_raises(self, tmp_path) -> None:
        tracker = ExperimentTracker(tmp_path)
        with pytest.raises(ValidationError):
            tracker.load_metrics("nope")


# --- prediction service -------------------------------------------------------


class TestPredictionService:
    def test_with_no_registered_models_every_task_is_unavailable(self, tmp_path) -> None:
        service = PredictionService(ModelRegistry.load(tmp_path), models_dir=tmp_path)
        for task in task_names():
            result = service._predict(task, [{"rating_diff": 100.0}])  # noqa: SLF001
            assert isinstance(result, PredictionUnavailable)
            assert result.available is False
            assert result.reason

    def test_unavailable_response_explains_the_requirement(self, tmp_path) -> None:
        service = PredictionService(ModelRegistry.load(tmp_path), models_dir=tmp_path)
        unavailable = service.predict_game_outcome([{"rating_diff": 100.0}])
        assert isinstance(unavailable, PredictionUnavailable)
        assert unavailable.requirements
        assert "does not serve unvalidated predictions" in unavailable.detail

    def test_an_experimental_model_is_not_served(self, tmp_path) -> None:
        models_dir = tmp_path / "models"
        model = MajorityClassModel(classes=CLASSES)
        model.fit(
            [{"rating_diff": 0.0}] * 6,
            ["white_win", "draw", "black_win"] * 2,
            feature_names=["rating_diff"],
        )
        model.save(models_dir / "game_outcome")
        registry = ModelRegistry.load(tmp_path)
        registry.register(
            RegisteredModel(
                model_id=model_id_for("game_outcome", "majority_class", "1"),
                task="game_outcome",
                model_type="majority_class",
                version="1",
                status=ModelStatus.EXPERIMENTAL,
                artifact=str(models_dir / "game_outcome" / "majority_class.model.json"),
                split_strategy="random_game",
                metrics={"macro_f1": 0.3},
            )
        )
        service = PredictionService(registry, models_dir=models_dir)
        result = service.predict_game_outcome([{"rating_diff": 0.0}])
        assert isinstance(result, PredictionUnavailable)
        assert "none has passed the production gate" in result.reason

    def test_a_production_model_serves_a_prediction_with_separate_coverage(self, tmp_path) -> None:
        models_dir = tmp_path / "models"
        model = MajorityClassModel(classes=CLASSES)
        model.fit([{"rating_diff": 0.0}] * 6, ["white_win", "draw", "black_win"] * 2, feature_names=["rating_diff"])
        saved = model.save(models_dir / "game_outcome")

        registry = ModelRegistry.load(tmp_path)
        registry.register(
            RegisteredModel(
                model_id=model_id_for("game_outcome", "majority_class", "9"),
                task="game_outcome",
                model_type="majority_class",
                version="9",
                status=ModelStatus.PRODUCTION,
                artifact=str(saved),
                split_strategy="temporal",
                trained_rows=6,
                metrics={"macro_f1": 0.5, "balanced_accuracy": 0.4, "log_loss": 1.0},
                calibration_metrics={"expected_calibration_error": 0.03, "calibrated": True},
            )
        )
        service = PredictionService(registry, models_dir=models_dir)
        result = service.predict_game_outcome([{"rating_diff": 0.0}])
        assert not isinstance(result, PredictionUnavailable)
        assert result.available is True
        assert result.prediction in CLASSES
        assert abs(sum(result.probabilities.values()) - 1.0) < 1e-6
        # Coverage is reported beside the probability, never folded into it.
        assert result.data_coverage is not None
        assert result.data_coverage.label() == "insufficient sample"
        assert result.data_coverage.expected_calibration_error == 0.03

    def test_availability_summary_covers_every_task(self, tmp_path) -> None:
        service = PredictionService(ModelRegistry.load(tmp_path), models_dir=tmp_path)
        summary = service.summary()
        assert len(summary["tasks"]) == len(task_names())
        assert all(entry["available"] is False for entry in summary["tasks"])


# --- experiment runner -------------------------------------------------------


class TestExperimentRunner:
    def test_undersized_data_is_refused_with_the_missing_requirements(self) -> None:
        records = DatasetImporter(source="fixture").ingest_text(build_fixture_pgn(6)).records
        outcome = evaluate_strategy(records, strategy=SplitStrategy.RANDOM_GAME)
        assert outcome.skipped is True
        assert "Refused" in outcome.skip_reason
        assert outcome.readiness["attemptable"] is False
        assert outcome.metrics == {}
        # The leakage suite still runs and reports, even when training does not.
        assert outcome.leakage.passed

    def test_exploratory_mode_also_refuses_below_the_row_floor(self) -> None:
        records = DatasetImporter(source="fixture").ingest_text(build_fixture_pgn(4)).records
        outcome = evaluate_strategy(records, strategy=SplitStrategy.RANDOM_GAME, exploratory=True)
        assert outcome.skipped is True
        assert str(MIN_EXPLORATORY_ROWS) in outcome.skip_reason

    def test_strategy_evaluation_produces_a_split_plan_either_way(self) -> None:
        records = DatasetImporter(source="fixture").ingest_text(build_fixture_pgn(30)).records
        outcome = evaluate_strategy(records, strategy=SplitStrategy.TEMPORAL)
        assert outcome.split.strategy is SplitStrategy.TEMPORAL
        assert outcome.split.counts()["train"] > 0

    def test_a_refusal_produces_no_model_and_no_metric(self) -> None:
        """The headline guardrail: refusing must not leave a partial result behind."""
        records = DatasetImporter(source="fixture").ingest_text(build_fixture_pgn(6)).records
        outcome = evaluate_strategy(records, strategy=SplitStrategy.RANDOM_GAME)
        assert outcome.models == {}
        assert outcome.metrics == {}
        assert outcome.calibration is None
        assert outcome.error_analysis is None
        assert outcome.gate is None
