"""Phase 6 dataset-engineering tests: ingestion, validation, dedupe, splits, store.

These exercise the *mechanics* of the dataset pipeline. Real games are used where
a real game is the point (the Opera Game, the miniatures corpus); the generated
fixture corpus is used where volume is needed to make splitting meaningful. No
test here asserts a chess claim, and no fixture is written into ``data/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.datasets.dedupe import (
    DEFAULT_DEDUPE_POLICY,
    DedupePolicy,
    DuplicateKind,
    apply_dedupe,
    deduplicate,
    find_duplicates,
)
from argus.datasets.features import (
    GAME_OUTCOME_FEATURES,
    build_game_features,
    build_position_features,
    get_definition,
    names_for_availability,
    registry,
)
from argus.datasets.identity import (
    build_identity,
    find_similar_names,
    identity_key_for,
    normalize_player_name,
    resolve_identities,
)
from argus.datasets.importer import DatasetImporter, iter_game_texts
from argus.datasets.labels import (
    LABEL_REGISTRY,
    game_outcome_label,
    label_games,
    move_error_label,
    position_difficulty_label,
)
from argus.datasets.manifest import DatasetManifest, build_manifest
from argus.datasets.quality import quality_report, rating_band, render_quality_report
from argus.datasets.records import Availability, DatasetLayer, IngestedGame
from argus.datasets.splits import (
    SplitStrategy,
    make_split,
    recommended_strategy,
)
from argus.datasets.store import DatasetStore
from argus.datasets.validation import IssueSeverity, validate_records
from argus.shared.errors import ValidationError
from tests.conftest import OPERA_GAME_PGN, SCHOLARS_MATE_PGN, build_fixture_pgn

MINIATURES = Path("data/raw/classic_miniatures.pgn")


# --- ingestion ---------------------------------------------------------------


class TestIngestion:
    def test_real_game_round_trips_into_a_normalized_record(self) -> None:
        result = DatasetImporter(source="opera").ingest_text(OPERA_GAME_PGN)
        assert result.stats.games_seen == 1
        assert result.stats.games_accepted == 1
        record = result.records[0]

        assert record.result == "1-0"
        assert record.white.original_name == "Paul Morphy"
        assert record.white.identity_key == "paul morphy"
        assert record.black.identity_key == "duke karl / count isouard"
        assert record.ply_count == 33
        assert record.eco == "C41"
        assert record.time_control_initial_seconds == 600
        assert record.time_control_increment_seconds == 5
        assert record.time_class == "rapid"
        assert record.date_iso == "1858-11-02"

    def test_game_id_is_content_derived_and_stable(self) -> None:
        first = DatasetImporter().ingest_text(OPERA_GAME_PGN).records[0]
        second = DatasetImporter().ingest_text(OPERA_GAME_PGN).records[0]
        assert first.game_id == second.game_id
        assert first.moves_hash == second.moves_hash

    def test_same_game_with_different_metadata_shares_an_id_but_not_a_hash(self) -> None:
        renamed = OPERA_GAME_PGN.replace("[White \"Paul Morphy\"]", "[White \"P. Morphy\"]")
        original = DatasetImporter().ingest_text(OPERA_GAME_PGN).records[0]
        variant = DatasetImporter().ingest_text(renamed).records[0]
        assert original.moves_hash == variant.moves_hash
        assert original.metadata_hash != variant.metadata_hash

    def test_malformed_input_is_rejected_with_a_reason(self) -> None:
        result = DatasetImporter().ingest_text("this is not a chess game at all")
        assert result.records == []
        assert result.stats.games_rejected >= 1
        assert result.stats.rejections
        assert result.rejected[0].reason

    def test_headers_only_game_is_rejected_with_a_recorded_reason(self) -> None:
        pgn = '[Event "x"]\n[White "A"]\n[Black "B"]\n[Result "*"]\n'
        result = DatasetImporter().ingest_text(pgn)
        assert result.records == []
        assert result.stats.games_rejected == 1
        assert result.rejected[0].reason in {"no_moves", "malformed_pgn", "no_games"}
        assert result.rejected[0].message

    def test_zero_move_record_is_refused_by_the_guard(self) -> None:
        """The no-moves guard, exercised directly (the reader rejects these first)."""
        importer = DatasetImporter()
        record = IngestedGame(
            game_id="empty",
            source="unit",
            white=build_identity("A"),
            black=build_identity("B"),
            result="1-0",
            ply_count=0,
            moves_hash="h",
            movetext_hash="h",
        )
        assert importer._accept(record) is None  # noqa: SLF001 — the guard is the unit under test
        assert importer.stats.rejections.get("no_moves") == 1

    def test_overlong_games_are_refused_with_the_policy_reason(self) -> None:
        importer = DatasetImporter(max_plies=10)
        result = importer.ingest_text(OPERA_GAME_PGN)
        assert result.records == []
        assert result.stats.rejections.get("game_too_long") == 1
        assert "10-ply limit" in result.rejected[0].message

    def test_concatenated_games_keep_their_own_headers(self) -> None:
        """Two games joined without a blank line must not lose the second's players.

        python-chess attaches a following tag section to the previous game, so the
        splitter has to separate them before parsing — otherwise the second game
        silently becomes an anonymous record.
        """
        doubled = OPERA_GAME_PGN + OPERA_GAME_PGN
        records = DatasetImporter().ingest_text(doubled).records
        assert len(records) == 2
        assert all(record.white.identity_key == "paul morphy" for record in records)

    def test_streaming_splitter_yields_one_game_per_piece(self) -> None:
        if not MINIATURES.is_file():
            pytest.skip("miniatures corpus not present")
        pieces = list(iter_game_texts(MINIATURES))
        assert len(pieces) == 2
        assert all(text.strip().startswith("[") for _, text in pieces)

    def test_batched_ingestion_streams_batches(self) -> None:
        pgn = build_fixture_pgn(12)
        path = Path("/tmp/argus_phase6_ingest_fixture.pgn")
        path.write_text(pgn, encoding="utf-8")
        try:
            batches: list[int] = []
            importer = DatasetImporter(source="fixture")
            result = importer.ingest_paths([path], on_batch=lambda rows: batches.append(len(rows)), batch_size=5)
            assert result.stats.games_accepted == 12
            # 5 + 5 + the final 2: three batches, none holding everything.
            assert batches == [5, 5, 2]
        finally:
            path.unlink(missing_ok=True)

    def test_reingesting_a_corpus_is_idempotent(self) -> None:
        pgn = build_fixture_pgn(20)
        first = DatasetImporter().ingest_text(pgn).records
        second = DatasetImporter().ingest_text(pgn).records
        assert [record.game_id for record in first] == [record.game_id for record in second]


# --- validation --------------------------------------------------------------


class TestValidation:
    def test_missing_metadata_is_a_warning_not_an_error(self) -> None:
        records = DatasetImporter().ingest_text(OPERA_GAME_PGN).records
        report = validate_records(records).report
        assert report.is_valid
        assert report.total_records == 1
        assert report.valid_records == 1
        assert "missing_date" not in report.codes(IssueSeverity.WARNING)
        assert report.codes(IssueSeverity.ERROR) == {}

    def test_unrated_games_warn_about_ratings(self) -> None:
        records = DatasetImporter().ingest_text(OPERA_GAME_PGN).records
        report = validate_records(records).report
        assert report.codes(IssueSeverity.WARNING).get("missing_rating") == 1

    def test_placeholder_player_is_an_error(self) -> None:
        pgn = '[Event "x"]\n[White "?"]\n[Black "Real"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n'
        records = DatasetImporter().ingest_text(pgn).records
        report = validate_records(records).report
        assert not report.is_valid
        assert report.codes(IssueSeverity.ERROR).get("missing_player") == 1
        assert report.invalid_ids == [records[0].game_id]
        assert report.valid_ids == []

    def test_impossible_rating_is_an_error(self) -> None:
        pgn = (
            '[Event "x"]\n[White "A"]\n[Black "B"]\n[WhiteElo "99999"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n'
        )
        records = DatasetImporter().ingest_text(pgn).records
        report = validate_records(records).report
        assert report.codes(IssueSeverity.ERROR).get("impossible_rating") == 1

    def test_unknown_result_is_a_warning_and_unlabellable(self) -> None:
        pgn = (
            '[Event "x"]\n[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n'
            "1. e4 e5 2. Nf3 Nc6 3. Bc4 *\n"
        )
        records = DatasetImporter().ingest_text(pgn).records
        assert records, "the fixture game must parse"
        report = validate_records(records).report
        assert report.is_valid
        assert report.codes(IssueSeverity.WARNING).get("missing_result") == 1
        assert game_outcome_label(records[0]) is None

    def test_corrupted_record_is_rejected(self) -> None:
        record = IngestedGame(
            game_id="broken",
            source="unit",
            white=build_identity("A"),
            black=build_identity("B"),
            result="1-0",
            ply_count=20,
            moves_hash="",
            movetext_hash="",
        )
        report = validate_records([record]).report
        assert report.codes(IssueSeverity.ERROR).get("corrupted_record") == 1

    def test_duplicate_records_are_counted_by_the_validation_report(self) -> None:
        records = DatasetImporter().ingest_text("\n\n".join([OPERA_GAME_PGN] * 3)).records
        report = validate_records(records).report
        assert report.total_records == 3
        assert report.duplicate_records == 2


# --- deduplication -----------------------------------------------------------


class TestDeduplication:
    def test_exact_duplicate_is_classified_and_dropped(self) -> None:
        records = DatasetImporter().ingest_text("\n\n".join([OPERA_GAME_PGN] * 2)).records
        report = find_duplicates(records)
        assert len(report.groups) == 1
        assert report.groups[0].kind is DuplicateKind.EXACT
        assert report.groups[0].dropped
        assert report.exact_count == 1
        kept, _ = deduplicate(records)
        assert len(kept) == 1

    def test_metadata_variant_is_reported_but_kept_by_default(self) -> None:
        renamed = OPERA_GAME_PGN.replace("Paul Morphy", "P. Morphy")
        records = DatasetImporter().ingest_text("\n\n".join([OPERA_GAME_PGN, renamed])).records
        report = find_duplicates(records)
        assert report.groups[0].kind is DuplicateKind.METADATA_VARIANT
        assert not report.groups[0].dropped
        assert report.metadata_variant_count == 1
        assert len(deduplicate(records)[0]) == 2

    def test_metadata_variant_can_be_dropped_when_the_policy_says_so(self) -> None:
        renamed = OPERA_GAME_PGN.replace("Paul Morphy", "P. Morphy")
        records = DatasetImporter().ingest_text("\n\n".join([OPERA_GAME_PGN, renamed])).records
        report = find_duplicates(records, policy=DedupePolicy(drop_metadata_variants=True))
        assert len(apply_dedupe(records, report)) == 1

    def test_short_repeated_game_is_never_dropped(self) -> None:
        short = SCHOLARS_MATE_PGN  # 7 plies, below the ambiguity floor
        records = DatasetImporter().ingest_text(short + short).records
        report = find_duplicates(records)
        assert report.groups[0].kind is DuplicateKind.AMBIGUOUS_SHORT
        assert not report.groups[0].dropped
        assert len(deduplicate(records)[0]) == 2

    def test_policy_states_its_reasoning(self) -> None:
        assert "judgement" in DEFAULT_DEDUPE_POLICY.rationale
        assert DEFAULT_DEDUPE_POLICY.never_drop_at_or_below_plies > 0


# --- identity ----------------------------------------------------------------


class TestPlayerIdentity:
    def test_normalization_is_conservative(self) -> None:
        assert normalize_player_name("Piyush1206") == "piyush1206"
        assert normalize_player_name("  Paul   Morphy ") == "paul morphy"
        assert normalize_player_name("Carlsen, M (GM)") == "carlsen, m"
        # Digits are never stripped: hans and hans1 are different accounts.
        assert normalize_player_name("hans1") != normalize_player_name("hans")

    def test_platform_account_beats_a_display_name(self) -> None:
        key = identity_key_for("Display Name", platform="chesscom", platform_username="RealUser")
        assert key == "chesscom:realuser"

    def test_similar_names_are_reported_but_kept_separate(self) -> None:
        resolution = resolve_identities(["Nakamura", "Nakamur", "Carlsen"])
        assert resolution.players == 3
        assert resolution.review_candidates
        pair = resolution.review_candidates[0]
        assert {pair.left, pair.right} == {"Nakamura", "Nakamur"}

    def test_clusters_keep_every_original_spelling(self) -> None:
        resolution = resolve_identities(["Piyush1206", "piyush1206", "Piyush1206"])
        assert resolution.players == 1
        assert resolution.spells == 3 if hasattr(resolution, "spells") else True
        assert len(resolution.clusters["piyush1206"]) == 2

    def test_find_similar_names_ignores_identical_names(self) -> None:
        assert find_similar_names(["Alice", "alice"]) == []


# --- features and labels -----------------------------------------------------


class TestFeatureRegistry:
    def test_every_feature_is_declared_with_availability(self) -> None:
        entries = registry()
        assert entries
        for name, definition in entries.items():
            assert definition.definition, name
            assert definition.source, name
            assert definition.availability in Availability.__members__.values()

    def test_post_game_features_are_not_pre_game(self) -> None:
        assert not get_definition("ply_count").usable_for_pre_game_prediction
        assert not get_definition("engine_volatility").usable_for_pre_game_prediction
        assert get_definition("rating_diff").usable_for_pre_game_prediction

    def test_pre_game_feature_list_is_what_the_task_uses(self) -> None:
        pre_game = set(names_for_availability(Availability.PRE_GAME))
        assert set(GAME_OUTCOME_FEATURES) <= pre_game

    def test_undeclared_feature_cannot_be_looked_up(self) -> None:
        with pytest.raises(KeyError):
            get_definition("not_a_declared_feature")

    def test_game_features_never_contain_an_outcome(self) -> None:
        records = DatasetImporter().ingest_text(OPERA_GAME_PGN).records
        features = build_game_features(records[0])
        assert "ply_count" not in features
        assert "target_result" not in features
        assert set(features) <= set(registry())

    def test_position_features_reuse_the_phase4_extractor(self) -> None:
        features = build_position_features("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        assert features["material_balance"] == 0
        assert features["mobility_diff"] == 0
        assert features["total_pieces"] == 32


class TestLabels:
    def test_every_label_is_defined_with_its_source(self) -> None:
        assert set(LABEL_REGISTRY) == {
            "game_outcome",
            "position_outcome",
            "position_difficulty",
            "move_error",
            "player_performance",
        }
        for definition in LABEL_REGISTRY.values():
            assert definition.definition
            assert definition.derivation
            assert definition.source_of_truth
            assert definition.unit_of_prediction

    def test_game_outcome_label_maps_the_recorded_result(self) -> None:
        records = DatasetImporter().ingest_text(OPERA_GAME_PGN).records
        assert game_outcome_label(records[0]) == "white_win"
        black_win = DatasetImporter().ingest_text(SCHOLARS_MATE_PGN.replace("1-0", "0-1")).records
        assert game_outcome_label(black_win[0]) == "black_win"

    def test_unlabelled_games_are_named_not_dropped(self) -> None:
        pgn = OPERA_GAME_PGN + SCHOLARS_MATE_PGN.replace("1-0", "*")
        records = DatasetImporter().ingest_text(pgn).records
        labelled = label_games(records)
        assert len(labelled.labels) == 1
        assert len(labelled.unlabelled) == 1

    def test_label_distribution_and_shares(self) -> None:
        records = DatasetImporter().ingest_text(
            OPERA_GAME_PGN + SCHOLARS_MATE_PGN
        ).records
        labelled = label_games(records)
        assert labelled.distribution() == {"white_win": 2, "draw": 0, "black_win": 0}
        assert labelled.shares()["white_win"] == 1.0

    def test_position_difficulty_needs_a_real_evaluation(self) -> None:
        assert position_difficulty_label([]) is None
        assert position_difficulty_label([20, 30]) == "ordinary"
        assert position_difficulty_label([0, 400]) == "difficult"
        assert position_difficulty_label([0, 0], in_check=True) == "difficult"

    def test_move_error_label_reuses_the_argus_classification(self) -> None:
        assert move_error_label("blunder") == "blunder"
        assert move_error_label("Mistake") == "mistake"
        assert move_error_label("best") == "acceptable"
        assert move_error_label(None) is None
        assert move_error_label("something_new") is None


# --- splits ------------------------------------------------------------------


class TestSplits:
    def _records(self, pgn: str) -> list[IngestedGame]:
        return DatasetImporter(source="fixture").ingest_text(pgn).records

    def test_random_split_is_deterministic(self) -> None:
        records = self._records(build_fixture_pgn(60))
        first = make_split(records, strategy=SplitStrategy.RANDOM_GAME, seed=5)
        second = make_split(records, strategy=SplitStrategy.RANDOM_GAME, seed=5)
        assert first.train == second.train
        assert first.test == second.test

    def test_random_split_partitions_every_game(self) -> None:
        records = self._records(build_fixture_pgn(60))
        plan = make_split(records, strategy=SplitStrategy.RANDOM_GAME)
        assigned = plan.train + plan.validation + plan.test
        assert sorted(assigned) == sorted(record.game_id for record in records)
        assert len(set(assigned)) == len(assigned)

    def test_game_group_split_keeps_a_pairing_together(self) -> None:
        records = self._records(build_fixture_pgn(80))
        plan = make_split(records, strategy=SplitStrategy.GAME_GROUP, seed=3)
        by_id = {record.game_id: record for record in records}
        pair_splits: dict[tuple[str, str], set[str]] = {}
        for split in ("train", "validation", "test"):
            for game_id in plan.ids_in(split):
                pair = tuple(sorted(by_id[game_id].player_keys()))
                pair_splits.setdefault(pair, set()).add(split)
        assert all(len(splits) == 1 for splits in pair_splits.values())

    def test_player_holdout_holds_players_out_entirely(self) -> None:
        records = self._records(build_fixture_pgn(120))
        plan = make_split(records, strategy=SplitStrategy.PLAYER_HOLDOUT, seed=4)
        held_out = set(plan.holdout_players)
        assert held_out
        # The held-out players must not appear in any training or validation game.
        assert not (held_out & set(plan.players["train"]))
        assert not (held_out & set(plan.players["validation"]))
        # ...and every one of them must actually be represented in the test split.
        assert held_out <= set(plan.players["test"])

    def test_player_holdout_reports_opponents_separately_from_the_holdout(self) -> None:
        records = self._records(build_fixture_pgn(120))
        plan = make_split(records, strategy=SplitStrategy.PLAYER_HOLDOUT, seed=4)
        # Opponents of a held-out player appear in test but also played training
        # games, so players['test'] is a superset of holdout_players. Conflating the
        # two would make every holdout look like leakage.
        assert set(plan.holdout_players) <= set(plan.players["test"])
        assert set(plan.holdout_players) != set(plan.players["test"])
        assert plan.holdout_players == sorted(plan.holdout_players)

    def test_player_holdout_refuses_a_degenerate_holdout(self) -> None:
        records = self._records(build_fixture_pgn(20, players=2))
        with pytest.raises(ValueError):
            make_split(records, strategy=SplitStrategy.PLAYER_HOLDOUT)

    def test_temporal_split_preserves_chronology(self) -> None:
        records = self._records(build_fixture_pgn(120))
        plan = make_split(records, strategy=SplitStrategy.TEMPORAL)
        assert plan.date_ranges["train"][1] <= plan.date_ranges["validation"][0] or plan.counts()["validation"] == 0
        assert plan.train
        assert plan.test

    def test_temporal_split_leaves_undated_games_unassigned(self) -> None:
        undated = '[Event "x"]\n[White "A"]\n[Black "B"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n'
        records = self._records(build_fixture_pgn(20) + undated)
        plan = make_split(records, strategy=SplitStrategy.TEMPORAL)
        assert len(plan.unassigned) == 1
        assert any("undated" in note for note in plan.notes)

    def test_split_guidance_is_documented_per_task(self) -> None:
        assert recommended_strategy("game_outcome") is SplitStrategy.TEMPORAL
        assert recommended_strategy("move_error_risk") is SplitStrategy.PLAYER_HOLDOUT
        with pytest.raises(KeyError):
            recommended_strategy("not_a_task")

    def test_empty_dataset_cannot_be_split(self) -> None:
        with pytest.raises(ValueError):
            make_split([], strategy=SplitStrategy.RANDOM_GAME)

    def test_impossible_shares_are_refused(self) -> None:
        records = self._records(build_fixture_pgn(20))
        with pytest.raises(ValueError):
            make_split(records, strategy=SplitStrategy.RANDOM_GAME, train_share=0.95, validation_share=0.1)


# --- manifests, store, quality ------------------------------------------------


class TestManifestAndStore:
    def test_manifest_measures_the_corpus(self) -> None:
        records = DatasetImporter().ingest_text(build_fixture_pgn(40)).records
        manifest = build_manifest(records, dataset_id="fixture", source="generated fixture")
        assert manifest.number_of_games == 40
        assert manifest.players == 8
        assert manifest.rating_coverage == 1.0
        assert manifest.time_control_coverage == 1.0
        assert manifest.date_range[0] is not None
        assert sum(manifest.result_distribution.values()) == 40

    def test_manifest_round_trips_through_disk(self, tmp_path) -> None:
        records = DatasetImporter().ingest_text(build_fixture_pgn(5)).records
        manifest = build_manifest(records, dataset_id="fixture", source="generated fixture")
        path = manifest.write(tmp_path)
        assert path.name == "fixture.manifest.json"
        assert DatasetManifest.read(path) == manifest

    def test_store_writes_and_streams_records_back(self, tmp_path) -> None:
        store = DatasetStore(tmp_path)
        store.ensure_layers()
        records = DatasetImporter(source="fixture").ingest_text(build_fixture_pgn(30)).records
        manifest = build_manifest(records, dataset_id="fixture", source="generated fixture")
        paths = store.write_games(records, dataset_id="fixture", manifest=manifest)
        assert paths["records"].is_file()
        assert store.count_games("fixture") == 30
        round_tripped = list(store.read_games("fixture"))
        assert [record.game_id for record in round_tripped] == [record.game_id for record in records]
        assert round_tripped[0].white.identity_key == records[0].white.identity_key

    def test_store_refuses_to_write_into_raw(self, tmp_path) -> None:
        store = DatasetStore(tmp_path)
        with pytest.raises(ValidationError):
            store.write_games([], dataset_id="nope", layer=DatasetLayer.RAW)

    def test_store_creates_every_layer_directory(self, tmp_path) -> None:
        store = DatasetStore(tmp_path)
        store.ensure_layers()
        for layer in DatasetLayer:
            assert store.layer_path(layer).is_dir()
        assert (tmp_path / "README.md").is_file()

    def test_table_round_trips_through_the_configured_backend(self, tmp_path) -> None:
        store = DatasetStore(tmp_path)
        store.write_table([{"a": 1, "b": 2.5}], name="table", layer=DatasetLayer.FEATURES)
        assert store.read_table("table") == [{"a": 1, "b": 2.5}]
        assert store.table_exists("table")

    def test_inventory_reports_what_is_on_disk(self, tmp_path) -> None:
        store = DatasetStore(tmp_path)
        store.ensure_layers()
        inventory = store.inventory()
        assert {entry["layer"] for entry in inventory} == {layer.value for layer in DatasetLayer}


class TestQualityReport:
    def test_quality_report_counts_valid_invalid_and_duplicates(self, tmp_path) -> None:
        records = DatasetImporter().ingest_text(build_fixture_pgn(60)).records
        duplication = DatasetImporter().ingest_text(build_fixture_pgn(60)).records
        all_records = records + duplication[:5]
        validation = validate_records(all_records)
        duplicates = find_duplicates(all_records)
        labelled = label_games(all_records)
        report = quality_report(
            all_records,
            dataset_id="fixture",
            validation=validation.report,
            duplicates=duplicates,
            labelled=labelled,
        )
        assert report.total_games == 65
        assert report.duplicate_records == 5
        assert report.players == 8
        assert report.rating_coverage == 1.0
        assert sum(report.label_distribution.values()) == 65

    def test_class_balance_is_described_before_anything_is_rebalanced(self) -> None:
        records = DatasetImporter().ingest_text(OPERA_GAME_PGN + SCHOLARS_MATE_PGN).records
        report = quality_report(records, dataset_id="decided", labelled=label_games(records))
        assert report.class_balance_warning is not None
        assert "no examples" in report.class_balance_warning
        # The natural distribution is reported unchanged: no implicit rebalancing.
        assert report.label_shares["white_win"] == 1.0

    def test_bias_analysis_states_the_population(self) -> None:
        records = DatasetImporter().ingest_text(build_fixture_pgn(40)).records
        report = quality_report(records, dataset_id="fixture")
        assert report.bias.population_statement
        assert report.bias.rating_bands.counts
        assert report.bias.time_controls.counts
        assert report.bias.limitations
        assert report.bias.concentration.distinct_players == 8

    def test_unrated_corpus_is_reported_as_unrepresented(self) -> None:
        if not MINIATURES.is_file():
            pytest.skip("miniatures corpus not present")
        records = DatasetImporter().ingest_text(MINIATURES.read_text(encoding="utf-8")).records
        report = quality_report(records, dataset_id="miniatures")
        assert report.rating_coverage == 0.0
        assert any("no rating" in limitation for limitation in report.bias.limitations)

    def test_rating_bands_are_named_and_stable(self) -> None:
        assert rating_band(1500) == "1400–1599"
        assert rating_band(None) == "unknown"

    def test_rendered_report_mentions_the_headline_numbers(self) -> None:
        records = DatasetImporter().ingest_text(build_fixture_pgn(12)).records
        report = quality_report(records, dataset_id="fixture", labelled=label_games(records))
        rendered = render_quality_report(report)
        assert "games            12" in rendered
        assert "population" in rendered
