"""Tests for the scikit-learn ML pipelines and the dataset builder.

Guardrail invariants under test:
- training refuses datasets that fail their DatasetSpec (InsufficientDataError)
- reported metrics are measured on held-out splits
- dataset rows come from real analyzed games, with real result labels only
"""

from __future__ import annotations

import csv
import io

import pytest

from argus.ml.models import DatasetSpec, ProblemType
from argus.ml.sklearn_pipelines import (
    SklearnEvaluationPipeline,
    SklearnModelSpec,
    SklearnTrainingPipeline,
)
from argus.ml.build_dataset import build_rows_for_game, FEATURE_COLUMNS
from argus.ml.dataset import validate_dataset
from argus.chess_core.pgn import parse_first_game
from argus.shared.errors import InsufficientDataError

from tests.conftest import OPERA_GAME_PGN

try:
    import sklearn  # noqa: F401

    SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover
    SKLEARN_AVAILABLE = False

pytestmark = pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")


REGRESSION_SPEC = DatasetSpec(
    name="test_regression",
    problem_type=ProblemType.REGRESSION,
    label_column="target",
    feature_columns=["a", "b"],
    min_samples=100,
    min_validation_samples=10,
    min_test_samples=10,
)


def _regression_rows(count: int, *, noise: int = 10, seed: int = 7) -> list[dict[str, str]]:
    """Synthetic-but-labeled rows for pipeline mechanics tests (not chess claims)."""
    import random

    rng = random.Random(seed)
    rows = []
    for _ in range(count):
        a = rng.randint(-50, 50)
        b = rng.randint(-20, 20)
        rows.append({"a": str(a), "b": str(b), "target": str(2 * a + b + rng.randint(-noise, noise))})
    return rows


class TestTrainingGuardrails:
    def test_insufficient_data_is_refused(self):
        pipeline = SklearnTrainingPipeline(REGRESSION_SPEC)
        with pytest.raises(InsufficientDataError):
            pipeline.train_with_splits(_regression_rows(50))

    def test_split_minimums_are_enforced(self):
        # 100 rows: 10% test = 10 rows is exactly at the minimum, so this passes
        # validation but a smaller validation split would fail; verify the floor.
        pipeline = SklearnTrainingPipeline(REGRESSION_SPEC)
        with pytest.raises(InsufficientDataError):
            pipeline.train_with_splits(_regression_rows(100), test_share=0.2, validation_share=0.05)

    def test_valid_data_trains_and_reports_measured_metrics(self):
        pipeline = SklearnTrainingPipeline(
            REGRESSION_SPEC, model_spec=SklearnModelSpec(n_estimators=40, random_state=3)
        )
        model, metrics = pipeline.train_with_splits(_regression_rows(400))
        assert set(metrics) == {"validation", "test"}
        for split, evaluation in metrics.items():
            assert evaluation.dataset_split == split
            assert evaluation.dataset_rows > 0
            assert "mae" in evaluation.metrics and "r2" in evaluation.metrics
        # Sanity on synthetic data: the model should beat predicting the mean.
        assert metrics["test"].metrics["r2"] > 0.5
        predictions = model.predict(_regression_rows(5)[:5])
        assert predictions.model_version.startswith("sklearn-rf-")
        assert len(predictions.values) == 5

    def test_evaluation_pipeline_requires_its_own_model_type(self):
        from argus.ml.pipelines import PredictionModel
        from argus.ml.models import ModelMetadata
        from datetime import datetime, timezone

        class ForeignModel(PredictionModel):
            def metadata(self) -> ModelMetadata:
                return ModelMetadata(
                    name="foreign", version="0", problem_type=ProblemType.REGRESSION,
                    trained_at=datetime.now(timezone.utc), dataset_name="x",
                    feature_columns=["a", "b"], label_column="target",
                )

            def predict(self, features):
                raise AssertionError("should not be called")

        from argus.shared.errors import AnalysisError

        evaluator = SklearnEvaluationPipeline(REGRESSION_SPEC)
        with pytest.raises(AnalysisError):
            evaluator.evaluate(ForeignModel(), _regression_rows(20), split="test")


class TestDatasetBuilder:
    def _opera_rows(self):
        """Analyze the Opera Game with the stub-free route: parse only.

        The full GameAnalyzer requires a live engine; the dataset builder's
        row-flattening logic is tested with a real game object and a fake
        analysis matching its interface.
        """
        game = parse_first_game(OPERA_GAME_PGN)

        class FakeFeatures:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class FakeMove:
            pass

        analysis_moves = []
        for move in game.moves:
            fb = FakeFeatures(
                material_balance=0, mobility_white=20, mobility_black=20,
                king_safety_white=2, king_safety_black=2, isolated_pawns_white=0,
                isolated_pawns_black=0, doubled_pawns_white=0, doubled_pawns_black=0,
                passed_pawns_white=0, passed_pawns_black=0, center_occupied_white=1,
                center_occupied_black=0, center_attacked_white=2, center_attacked_black=1,
                undeveloped_pieces_white=4, undeveloped_pieces_black=4,
                hanging_pieces_white=0, hanging_pieces_black=0, total_pieces=32,
            )
            fa = FakeFeatures(
                material_balance=0, mobility_white=21, mobility_black=19,
                king_safety_white=2, king_safety_black=2, isolated_pawns_white=0,
                isolated_pawns_black=0, doubled_pawns_white=0, doubled_pawns_black=0,
                passed_pawns_white=0, passed_pawns_black=0, center_occupied_white=1,
                center_occupied_black=0, center_attacked_white=2, center_attacked_black=1,
                undeveloped_pieces_white=3, undeveloped_pieces_black=4,
                hanging_pieces_white=0, hanging_pieces_black=0, total_pieces=32,
            )
            m = FakeMove()
            m.ply, m.move_number = move.ply, move.move_number
            m.color, m.san = move.color, move.san
            m.phase = type("Phase", (), {"value": "middlegame"})()
            m.evaluation_after_cp = 25
            m.features_before, m.features_after = fb, fa
            analysis_moves.append(m)

        analysis = type("Analysis", (), {"moves": analysis_moves})
        return build_rows_for_game(game, analysis)

    def test_rows_carry_real_game_data(self):
        rows = self._opera_rows()
        assert len(rows) > 30  # Opera Game has 33 plies
        assert {row["result"] for row in rows} == {"1-0"}
        assert {row["game_id"] for row in rows} == {""}  # unsaved game → empty id
        for row in rows:
            assert int(row["ply"]) >= 1
            assert row["color"] in ("white", "black")
            assert row["evaluation_cp"] == "25" or row["evaluation_cp"] == "-25"

    def test_csv_round_trip(self):
        rows = self._opera_rows()
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["game_id", "ply", "move_number", "color",
                                                    "phase", "evaluation_cp", *FEATURE_COLUMNS, "result"])
        writer.writeheader()
        writer.writerows(rows)
        text = buffer.getvalue()
        assert text.splitlines()[0].startswith("game_id,ply")
        assert len(text.splitlines()) == len(rows) + 1

    def test_game_without_result_is_rejected(self):
        game = parse_first_game('[Result "*"]\n\n1. e4 e5 2. Nf3 *\n')
        # The parser records "*" (unknown); the builder must refuse it as a label.
        with pytest.raises(ValueError):
            build_rows_for_game(game, type("Analysis", (), {"moves": []}))


class TestSpecHonesty:
    def test_default_spec_minimums_are_not_trivial(self):
        from argus.ml.train import EVALUATION_REGRESSION_SPEC

        assert EVALUATION_REGRESSION_SPEC.min_samples >= 1000
        assert EVALUATION_REGRESSION_SPEC.min_test_samples >= 200
        assert len(EVALUATION_REGRESSION_SPEC.feature_columns) == len(FEATURE_COLUMNS)
        assert validate_dataset(_regression_rows(1500), EVALUATION_REGRESSION_SPEC).is_valid is False
