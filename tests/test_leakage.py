"""Data leakage test suite (spec §40) — a first-class suite, not an afterthought.

Leakage is the failure mode that makes every other number meaningless, so each
guarantee is tested in both directions:

* the *positive* case, on splits the pipeline actually produces; and
* the *negative* case, on a deliberately broken plan, so the validator is proven
  to catch the violation rather than merely being present.

The seven guarantees: same game not in two splits; positions of a game not
crossing splits; no future information in historical features; test labels never
used for training; test statistics never used to choose features; a player
holdout really holds players out; a temporal split preserves chronology.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.datasets.features import GAME_OUTCOME_FEATURES, build_game_features, get_definition
from argus.datasets.importer import DatasetImporter
from argus.datasets.leakage import (
    LeakageCheck,
    LeakageValidator,
    assert_no_leakage,
)
from argus.datasets.positions import SamplingSpec, build_positions
from argus.datasets.records import Availability, IngestedGame, PositionRecord
from argus.datasets.splits import SplitStrategy, make_split
from argus.ml.baselines import make_model
from tests.conftest import build_fixture_pgn

MINIATURES = Path("data/raw/classic_miniatures.pgn")


def _records(count: int = 120, **kwargs) -> list[IngestedGame]:
    return DatasetImporter(source="fixture").ingest_text(
        build_fixture_pgn(count, **kwargs)
    ).records


def _positions(records: list[IngestedGame]) -> list[PositionRecord]:
    """Positions whose game_ids come from the same records (so ids line up)."""
    rows: list[PositionRecord] = []
    for record in records:
        rows.append(
            PositionRecord(
                position_id=f"{record.game_id}:0",
                game_id=record.game_id,
                ply=0,
                move_number=1,
                side_to_move="white",
                fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                position_hash="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w",
            )
        )
    return rows


# --- 1. game leakage ---------------------------------------------------------


class TestGameLeakage:
    def test_no_game_appears_in_two_splits(self) -> None:
        records = _records(150)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME, seed=11)
        finding = LeakageValidator(plan, records=records).check_game_disjointness()
        assert finding.passed, finding.message
        assert finding.details["games"] == 150

    def test_a_duplicated_game_across_splits_is_caught(self) -> None:
        records = _records(40)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME, seed=11)
        # Deliberately corrupt the plan the way a positional split would.
        plan.test = [*plan.test, plan.train[0]]
        finding = LeakageValidator(plan, records=records).check_game_disjointness()
        assert not finding.passed
        assert plan.train[0] in finding.details["clashes"]

    def test_positions_of_one_game_cannot_be_split_by_position(self) -> None:
        records = _records(30)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME, seed=2)
        positions = _positions(records)
        # Put a position of a training game into the test split, as a positional
        # split would.
        stray = positions[0]
        stray_split = plan.split_of(stray.game_id)
        assert stray_split == "train"
        positions = positions + [
            stray.model_copy(update={"position_id": f"{stray.game_id}:1", "ply": 1})
        ]
        plan.test = [*plan.test, stray.game_id]
        report = LeakageValidator(plan, records=records, positions=positions).validate()
        assert not report.passed


# --- 2. position leakage -----------------------------------------------------


class TestPositionLeakage:
    def test_positions_inherit_their_games_split(self) -> None:
        records = _records(60)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME, seed=3)
        report = LeakageValidator(plan, records=records, positions=_positions(records)).validate()
        position_check = next(
            finding for finding in report.findings if finding.check is LeakageCheck.POSITION
        )
        assert position_check.passed, position_check.message
        assert position_check.details["positions"] == 60

    def test_every_position_game_must_have_a_split(self) -> None:
        records = _records(20)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME, seed=3)
        orphan = PositionRecord(
            position_id="orphan:0",
            game_id="not-in-the-split",
            ply=0,
            move_number=1,
            side_to_move="white",
            fen="8/8/8/8/8/8/8/K6k w - - 0 1",
        )
        finding = LeakageValidator(
            plan, records=records, positions=[*_positions(records), orphan]
        ).check_position_disjointness()
        assert not finding.passed
        assert "not assigned to any split" in finding.message

    def test_identical_positions_across_games_are_reported_not_treated_as_leakage(self) -> None:
        if not MINIATURES.is_file():
            pytest.skip("miniatures corpus not present")
        positions = build_positions([MINIATURES], spec=SamplingSpec("every_n_plies", every_n=2))
        records = DatasetImporter(source="mini").ingest_file(MINIATURES).records
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME, seed=1)
        finding = LeakageValidator(plan, records=records, positions=positions).check_position_disjointness()
        assert finding.passed
        # Both games open from the same start position: sharing it is normal and is
        # counted rather than silently ignored.
        assert finding.details["positions_reached_by_multiple_games"] >= 1

    def test_position_ids_agree_with_the_importer(self) -> None:
        if not MINIATURES.is_file():
            pytest.skip("miniatures corpus not present")
        records = DatasetImporter(source="mini").ingest_file(MINIATURES).records
        positions = build_positions([MINIATURES], spec=SamplingSpec("every_n_plies", every_n=8))
        assert {position.game_id for position in positions} == {record.game_id for record in records}


# --- 3. temporal leakage -----------------------------------------------------


class TestTemporalLeakage:
    def test_temporal_split_preserves_chronology(self) -> None:
        records = _records(160)
        plan = make_split(records, strategy=SplitStrategy.TEMPORAL)
        finding = LeakageValidator(plan, records=records).check_temporal_ordering()
        assert finding.passed, finding.message
        assert finding.details["train"][1] <= finding.details["test"][0]

    def test_a_shuffled_plan_is_caught(self) -> None:
        records = _records(160)
        plan = make_split(records, strategy=SplitStrategy.TEMPORAL)
        # Swap the extremes: the newest game in train, the oldest in test.
        plan.train = [*plan.train, plan.test[-1]]
        plan.test = [*plan.test[:-1], plan.train[0]]
        finding = LeakageValidator(plan, records=records).check_temporal_ordering()
        assert not finding.passed

    def test_non_temporal_strategy_makes_no_chronological_claim(self) -> None:
        records = _records(40)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        finding = LeakageValidator(plan, records=records).check_temporal_ordering()
        assert finding.passed
        assert finding.severity == "warning"


# --- 4. player leakage -------------------------------------------------------


class TestPlayerLeakage:
    def test_holdout_players_are_absent_from_training(self) -> None:
        records = _records(200)
        plan = make_split(records, strategy=SplitStrategy.PLAYER_HOLDOUT, seed=9)
        finding = LeakageValidator(plan, records=records).check_player_holdout()
        assert finding.passed, finding.message
        assert finding.details["holdout_players"] > 0

    def test_a_leaked_holdout_player_is_caught(self) -> None:
        records = _records(200)
        plan = make_split(records, strategy=SplitStrategy.PLAYER_HOLDOUT, seed=9)
        # Force a held-out player's game into training.
        holdout = plan.holdout_players[0]
        victim = next(
            record for record in records if holdout in record.player_keys() and record.game_id in plan.test
        )
        plan.train = [*plan.train, victim.game_id]
        finding = LeakageValidator(plan, records=records).check_player_holdout()
        assert not finding.passed
        assert holdout in finding.details["leaked"]

    def test_random_split_is_flagged_as_unable_to_claim_generalisation(self) -> None:
        records = _records(60)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        finding = LeakageValidator(plan, records=records).check_player_holdout()
        assert finding.passed
        assert finding.severity == "warning"
        assert "player-generalisation must not be claimed" in finding.message


# --- 5. feature availability leakage -----------------------------------------


class TestFeatureAvailabilityLeakage:
    def test_pre_game_task_accepts_only_pre_game_features(self) -> None:
        records = _records(20)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        finding = LeakageValidator(plan, records=records).check_feature_availability(
            GAME_OUTCOME_FEATURES, task_availability=Availability.PRE_GAME
        )
        assert finding.passed, finding.message

    def test_a_post_game_feature_is_rejected_for_a_pre_game_task(self) -> None:
        records = _records(20)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        # ply_count and the engine volatility summarise the finished game; using
        # either to predict the result is the canonical leak.
        finding = LeakageValidator(plan, records=records).check_feature_availability(
            [*GAME_OUTCOME_FEATURES, "ply_count"], task_availability=Availability.PRE_GAME
        )
        assert not finding.passed
        assert "ply_count" in finding.details["unavailable_features"]

    def test_final_evaluation_available_to_a_pre_game_task_is_rejected(self) -> None:
        records = _records(20)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        finding = LeakageValidator(plan, records=records).check_feature_availability(
            ["engine_evaluation_cp"], task_availability=Availability.PRE_GAME
        )
        assert not finding.passed

    def test_an_undeclared_feature_cannot_be_verified_and_therefore_fails(self) -> None:
        records = _records(20)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        finding = LeakageValidator(plan, records=records).check_feature_availability(
            ["definitely_not_registered"], task_availability=Availability.PRE_GAME
        )
        assert not finding.passed
        assert finding.details["undeclared_features"] == ["definitely_not_registered"]

    def test_built_game_features_are_all_pre_game(self) -> None:
        record = _records(1)[0]
        for name in build_game_features(record):
            assert (
                get_definition(name).availability is not Availability.POST_GAME
            ), f"{name} is post-game and must not be in the pre-game feature set"

    def test_engine_evaluation_is_declared_at_position_not_pre_game(self) -> None:
        definition = get_definition("engine_evaluation_cp")
        assert definition.availability is Availability.AT_POSITION
        assert not definition.usable_for_pre_game_prediction


# --- 6. label leakage -------------------------------------------------------


class TestLabelLeakage:
    def test_label_column_is_not_an_input(self) -> None:
        records = _records(20)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        finding = LeakageValidator(plan, records=records).check_label_not_in_features(
            GAME_OUTCOME_FEATURES, "target_result"
        )
        assert finding.passed

    def test_the_label_in_the_inputs_is_caught(self) -> None:
        records = _records(20)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        finding = LeakageValidator(plan, records=records).check_label_not_in_features(
            [*GAME_OUTCOME_FEATURES, "target_result"], "target_result"
        )
        assert not finding.passed

    def test_test_labels_are_not_used_during_training(self) -> None:
        """A model fitted on training rows must not see a single test label."""
        records = _records(120)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME, seed=6)
        from argus.datasets.labels import label_games

        labels = label_games(records).as_map()
        train = [r for r in records if plan.split_of(r.game_id) == "train"]
        test = [r for r in records if plan.split_of(r.game_id) == "test"]
        assert not ({r.game_id for r in train} & {r.game_id for r in test})

        model = make_model("majority_class", classes=["white_win", "draw", "black_win"])
        model.fit([build_game_features(r) for r in train], [labels[r.game_id] for r in train])
        # The training distribution is exactly the training labels' distribution:
        # a single test label would shift it.
        training_counts = {value: 0 for value in model.card.classes}
        for record in train:
            training_counts[labels[record.game_id]] += 1
        assert model._params["training_distribution"] == training_counts  # noqa: SLF001

    def test_test_statistics_are_not_used_to_choose_features(self) -> None:
        """Imputation/scaling statistics must come from the training split only."""
        records = _records(120)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME, seed=6)
        from argus.datasets.labels import label_games

        labels = label_games(records).as_map()
        train = [r for r in records if plan.split_of(r.game_id) == "train"]
        test = [r for r in records if plan.split_of(r.game_id) == "test"]
        train_rows = [build_game_features(r) for r in train]

        model = make_model("logistic_regression", classes=["white_win", "draw", "black_win"])
        model.fit(train_rows, [labels[r.game_id] for r in train])

        # Recomputing the same median from the *test* rows would give a different
        # value, which is exactly the leak being ruled out.
        import numpy as np

        test_medians = {
            name: float(np.median([row[name] for row in [build_game_features(r) for r in test]]))
            for name in ("rating_diff", "rating_mean")
        }
        for name, median in test_medians.items():
            if name in model.card.imputation:
                assert model.card.imputation[name] != median, (
                    f"{name}'s imputation value equals the test median: the fitted "
                    "statistic must come from the training split"
                )


# --- aggregate ---------------------------------------------------------------


class TestLeakageSuite:
    def test_full_suite_passes_for_a_clean_split(self) -> None:
        records = _records(120)
        plan = make_split(records, strategy=SplitStrategy.GAME_GROUP, seed=8)
        report = LeakageValidator(plan, records=records).validate(
            feature_names=GAME_OUTCOME_FEATURES,
            label_column="target_result",
            task_availability=Availability.PRE_GAME,
        )
        assert report.passed, report.summary()
        assert set(report.summary()["checks"]) == {check.value for check in LeakageCheck}

    def test_full_suite_passes_for_a_player_holdout(self) -> None:
        records = _records(240)
        plan = make_split(records, strategy=SplitStrategy.PLAYER_HOLDOUT, seed=13)
        report = LeakageValidator(plan, records=records).validate(
            feature_names=GAME_OUTCOME_FEATURES,
            label_column="target_result",
            task_availability=Availability.PRE_GAME,
        )
        assert report.passed, report.summary()

    def test_assert_no_leakage_raises_on_an_error(self) -> None:
        records = _records(40)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        plan.test = [*plan.test, plan.train[0]]
        report = LeakageValidator(plan, records=records).validate()
        with pytest.raises(ValueError, match="Leakage checks failed"):
            assert_no_leakage(report)

    def test_assert_no_leakage_is_silent_when_clean(self) -> None:
        records = _records(40)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        assert_no_leakage(LeakageValidator(plan, records=records).validate())

    def test_report_summary_is_machine_readable(self) -> None:
        records = _records(40)
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        summary = LeakageValidator(plan, records=records).validate().summary()
        assert summary["passed"] is True
        assert summary["errors"] == []
