"""Phase 5 core tests: player aggregation, patterns, DNA, features, sanity.

Two kinds of test live here, and the distinction matters:

*Tests on real analysis* (marked ``engine``) import real, well-known games,
run the existing Stockfish pipeline over them, map the resulting **stored
reports** into ``PlayerGameInput`` through the production mapper, and only then
assert on the player-level aggregates. The numbers the assertions use are
therefore produced by the real engine, not by a fixture pretending to be one.

*Scaling tests* duplicate those mapped inputs with distinct game ids to check
that aggregation behaves at 5 / 20 / 100 games. No chess analysis is invented in
that step — only the number of games is synthesized — and each test says so.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from argus.player_intelligence import (
    DEFAULT_POLICY,
    FEATURE_VERSION,
    METHODOLOGY_VERSION,
    PROFILE_VERSION,
    GameOutcome,
    InsightCategory,
    PlayerGameInput,
    PlayerInsightPolicy,
    PlayerProfile,
    ProfileSanityError,
    TimeClass,
    TrajectoryInput,
    assert_profile_consistent,
    build_profile,
    classify_time_control,
    extract_features,
    opening_family,
    validate_profile,
)
from argus.player_intelligence.aggregate import trends as compute_trends
from argus.player_intelligence.models import (
    MaterialInput,
    PlayerErrorEvent,
    PositionalEventInput,
)
from argus.player_intelligence.policy import ClaimLevel, Coverage
from argus_api.services.player_profile_service import build_inputs, game_input_from_row
from tests.conftest import OPERA_GAME_PGN, SCHOLARS_MATE_PGN

TEST_PLAYER = "Caissa Tester"


# --- real-analysis fixtures ---------------------------------------------------


def _analyzed_row(client: TestClient, pgn: str, *, color: str, player: str, opponent: str,
                  result: str, date: str, time_control: str = "600+5") -> dict:
    """Import + analyze a real game through the API and return a mapper row.

    The row is the same shape ``player_game_rows`` produces from the database,
    so the test exercises the production mapping path.
    """
    game_id = client.post(
        "/api/games/import", json={"pgn_text": pgn, "run_analysis": False}
    ).json()["game_id"]
    # Analyse with the real engine pipeline (same path the report API uses).
    started = client.post(f"/api/analysis/games/{game_id}", json={"depth": 6, "multipv": 2})
    assert started.status_code == 202, started.text
    progress = client.get(f"/api/analysis/games/{game_id}/progress").json()
    assert progress["status"] == "completed", progress

    report = client.post(f"/api/intelligence/games/{game_id}/report")
    assert report.status_code == 200, report.text
    payload = report.json()
    status = client.get(f"/api/games/{game_id}").json()
    return {
        "game_id": game_id,
        "color": color,
        "opponent": opponent,
        "player_rating": 1500,
        "opponent_rating": 1500,
        "result": result,
        "date": date,
        "time_control": time_control,
        "eco_code": status.get("eco_code"),
        "opening_name": status.get("opening_name"),
        "move_count": status.get("move_count") or len(status.get("moves", [])),
        "source": status.get("source"),
        "analysis_status": "analyzed",
        "analysis_depth": 6,
        "analysis_updated_at": "2026-01-01T00:00:00+00:00",
        "report_version": payload.get("report_version"),
        "analysis_version": payload.get("provenance", {}).get("analysis_version"),
        "engine": payload.get("provenance", {}).get("engine"),
        "engine_version": payload.get("provenance", {}).get("engine_version"),
        "report_payload": payload,
        "moves": [
            {"san": move["san"], "ply": move["ply"], "color": move["color"]}
            for move in status.get("moves", [])
        ],
    }


@pytest.mark.engine
def test_mapper_uses_real_measured_values(client: TestClient) -> None:
    """The mapper reads accuracy, phases, material and trajectory out of a real report."""
    row = _analyzed_row(
        client,
        OPERA_GAME_PGN,
        color="white",
        player=TEST_PLAYER,
        opponent="Duke Karl",
        result="1-0",
        date="1858.11.02",
    )
    mapped = game_input_from_row(0, row, policy=DEFAULT_POLICY)
    assert mapped is not None
    report = row["report_payload"]

    # Accuracy is the report's own number for this colour, not a recomputation.
    assert mapped.accuracy == report["accuracy"]["analysis"]["white"]["accuracy"]
    assert mapped.scored_moves == report["accuracy"]["analysis"]["white"]["scored_moves"]
    assert mapped.color == "white"
    assert mapped.outcome is GameOutcome.WIN
    assert mapped.time_class is TimeClass.RAPID  # from "600+5"
    assert mapped.move_count == report["summary"]["moves"]

    # The trajectory bands come from the stored advantage states.
    assert mapped.trajectory.evaluated_plies > 0
    assert mapped.trajectory.final_band is not None

    # Every mapped error carries at least one category, and every error/event
    # belongs to the player's own side (opponent events are never attributed).
    assert mapped.errors
    for error in mapped.errors:
        assert error.categories
        assert error.game_id == row["game_id"]
    for event in mapped.tactical_events:
        assert event.direction in {"created", "allowed"}
    assert mapped.phase_performance

    # Castling is detected from the stored SAN (Morphy castled queenside on
    # move 12, which is ply 23 → move 12).
    assert mapped.castled is True
    assert mapped.castling_ply == 12


@pytest.mark.engine
def test_one_game_profile_is_honest_about_insufficient_data(client: TestClient) -> None:
    """A single game must not produce a profile — only the coverage statement."""
    row = _analyzed_row(
        client,
        OPERA_GAME_PGN,
        color="white",
        player=TEST_PLAYER,
        opponent="Duke Karl",
        result="1-0",
        date="1858.11.02",
    )
    inputs, excluded = build_inputs([row], policy=DEFAULT_POLICY)
    profile = build_profile(inputs, player_id="1", display_name=TEST_PLAYER, imported_games=1)

    assert profile.sufficient_data is False
    assert profile.coverage is Coverage.INSUFFICIENT
    assert profile.analyzed_games == 1
    assert profile.insights == []
    assert profile.notes and "Not enough analyzed games" in profile.notes[0]
    assert excluded == []
    assert validate_profile(profile) == []


@pytest.mark.engine
def test_two_real_games_produce_a_measured_profile(client: TestClient) -> None:
    """Two real analyzed games: aggregates, colour split and coverage, all checked."""
    win = _analyzed_row(
        client, OPERA_GAME_PGN, color="white", player=TEST_PLAYER, opponent="Duke Karl",
        result="1-0", date="1858.11.02",
    )
    loss = _analyzed_row(
        client, SCHOLARS_MATE_PGN, color="black", player=TEST_PLAYER, opponent="White Rookie",
        result="1-0", date="2026.01.05",
    )
    inputs, _ = build_inputs([win, loss], policy=DEFAULT_POLICY)
    assert len(inputs) == 2

    profile = build_profile(inputs, player_id="1", display_name=TEST_PLAYER, imported_games=2)
    assert profile.sufficient_data is True
    assert profile.coverage is Coverage.LIMITED
    assert profile.analyzed_games == 2

    # Record reconciles exactly with the two real results.
    assert profile.games.wins == 1 and profile.games.losses == 1 and profile.games.draws == 0
    assert profile.games.win_rate == 0.5
    assert profile.games.wins + profile.games.draws + profile.games.losses == profile.games.analyzed_games

    colours = {entry.color: entry for entry in profile.by_color}
    assert colours["white"].games == 1
    assert colours["black"].games == 1
    assert colours["white"].wins == 1
    assert colours["black"].losses == 1

    # Aggregates are the mean of the measured per-game values.
    expected_accuracy = round(sum(game.accuracy for game in inputs) / 2, 2)
    assert profile.games.average_accuracy == expected_accuracy
    assert profile.games.accuracy_sample == 2

    # No tendency may be claimed from two games: every insight is an observation.
    assert profile.insights
    assert all(insight.claim_level in (ClaimLevel.OBSERVATION, ClaimLevel.INSUFFICIENT)
               for insight in profile.insights)
    assert validate_profile(profile) == []


@pytest.mark.engine
def test_evidence_is_traceable_to_real_positions(client: TestClient) -> None:
    """Every pattern claim points at a game, a ply and a move that exist."""
    rows = [
        _analyzed_row(client, OPERA_GAME_PGN, color="white", player=TEST_PLAYER,
                      opponent="Duke Karl", result="1-0", date="1858.11.02"),
        _analyzed_row(client, SCHOLARS_MATE_PGN, color="black", player=TEST_PLAYER,
                      opponent="White Rookie", result="1-0", date="2026.01.05"),
    ]
    inputs, _ = build_inputs(rows, policy=DEFAULT_POLICY)

    # A pattern needs recurrence across games, so the real inputs are duplicated
    # with distinct ids: this scales the *number of games*, it does not invent
    # analysis — the moves, plies and evaluations are the engine's own.
    scaled: list[PlayerGameInput] = []
    for index in range(6):
        for base in inputs:
            clone = base.model_copy(deep=True)
            clone.game_id = f"{base.game_id}-copy{index}"
            scaled.append(clone)

    profile = build_profile(scaled, player_id="1", display_name=TEST_PLAYER, imported_games=len(scaled))
    pattern_insights = [
        insight for insight in profile.insights
        if insight.claim_level in (ClaimLevel.PATTERN, ClaimLevel.TENDENCY)
    ]
    assert pattern_insights, "expected at least one recurring pattern at 12 games"
    valid_ids = {game.game_id for game in scaled}
    for insight in pattern_insights:
        assert insight.evidence, f"{insight.id} claims a pattern with no evidence"
        for ref in insight.evidence:
            assert ref.game_id in valid_ids
            assert ref.ply >= 0
    assert validate_profile(profile) == []


@pytest.mark.engine
def test_profile_rebuild_is_deterministic(client: TestClient) -> None:
    """Same inputs, same numbers — a rebuild is reproducible, not random."""
    row = _analyzed_row(client, OPERA_GAME_PGN, color="white", player=TEST_PLAYER,
                        opponent="Duke Karl", result="1-0", date="1858.11.02")
    inputs, _ = build_inputs([row], policy=DEFAULT_POLICY)

    first = build_profile(inputs, player_id="1", display_name=TEST_PLAYER, imported_games=1)
    second = build_profile(inputs, player_id="1", display_name=TEST_PLAYER, imported_games=1)
    assert first.model_dump(exclude={"generated_at", "last_updated_at"}) == second.model_dump(
        exclude={"generated_at", "last_updated_at"}
    )
    assert first.profile_version == PROFILE_VERSION
    assert first.methodology_version == METHODOLOGY_VERSION
    assert first.feature_version == FEATURE_VERSION


# --- pure logic (no engine needed) -------------------------------------------


def _game(
    game_id: str,
    *,
    color: str = "white",
    outcome: GameOutcome = GameOutcome.WIN,
    accuracy: float | None = 80.0,
    cpl: float | None = 60.0,
    date: str = "2026.01.01",
    time_class: TimeClass = TimeClass.RAPID,
    peak: int | None = 3,
    worst: int | None = -1,
    final: int | None = 3,
    blunders: int = 0,
    mistakes: int = 0,
    inaccuracies: int = 0,
    castled: bool | None = True,
    rating: int | None = 1500,
    opponent_rating: int | None = 1500,
) -> PlayerGameInput:
    """A minimal measured input — used only for arithmetic and policy tests."""
    return PlayerGameInput(
        game_id=game_id,
        date=date,
        opponent_name="Opponent",
        color=color,
        player_rating=rating,
        opponent_rating=opponent_rating,
        result_raw="1-0" if outcome is GameOutcome.WIN else "0-1",
        outcome=outcome,
        time_class=time_class,
        move_count=40,
        accuracy=accuracy,
        average_centipawn_loss=cpl,
        scored_moves=30,
        blunders=blunders,
        mistakes=mistakes,
        inaccuracies=inaccuracies,
        trajectory=TrajectoryInput(evaluated_plies=40, peak_band=peak, worst_band=worst, final_band=final),
        material=MaterialInput(total_captures=15, exchanges=3, final_balance=1),
        castled=castled,
        castling_ply=12 if castled else None,
    )


class TestPolicy:
    def test_coverage_bands_are_documented(self) -> None:
        policy = PlayerInsightPolicy()
        assert policy.coverage_for(0) is Coverage.INSUFFICIENT
        assert policy.coverage_for(1) is Coverage.INSUFFICIENT
        assert policy.coverage_for(2) is Coverage.LIMITED
        assert policy.coverage_for(4) is Coverage.LIMITED
        assert policy.coverage_for(5) is Coverage.MODERATE
        assert policy.coverage_for(19) is Coverage.MODERATE
        assert policy.coverage_for(20) is Coverage.ROBUST

    def test_pattern_thresholds_must_all_hold(self) -> None:
        policy = PlayerInsightPolicy()
        assert policy.pattern_is_supported(occurrences=4, games=4, coverage=0.5, consistency=0.5)
        assert not policy.pattern_is_supported(occurrences=3, games=4, coverage=0.5, consistency=0.5)
        assert not policy.pattern_is_supported(occurrences=4, games=3, coverage=0.5, consistency=0.5)
        assert not policy.pattern_is_supported(occurrences=4, games=4, coverage=0.1, consistency=0.5)
        assert not policy.pattern_is_supported(occurrences=4, games=4, coverage=0.5, consistency=0.05)

    def test_time_control_classification(self) -> None:
        assert classify_time_control(60, 0) is TimeClass.BULLET
        assert classify_time_control(180, 0) is TimeClass.BLITZ
        assert classify_time_control(600, 5) is TimeClass.RAPID
        assert classify_time_control(1800, 0) is TimeClass.CLASSICAL
        assert classify_time_control(None, None) is TimeClass.UNKNOWN

    def test_opening_family_is_descriptive(self) -> None:
        assert opening_family("B20", "Sicilian Defense") == "Sicilian"
        assert opening_family("D30", "Queen's Gambit Declined") == "Queen's Gambit"
        assert opening_family("A00", None) == "ECO A"
        assert opening_family(None, None) is None


class TestMinimumData:
    def test_below_minimum_games_produces_no_sections(self) -> None:
        policy = PlayerInsightPolicy()
        profile = build_profile(
            [_game("a")], player_id="1", display_name="P", policy=policy, imported_games=1
        )
        assert profile.sufficient_data is False
        assert profile.games.analyzed_games == 0  # deliberately empty, not zero-filled
        assert profile.chess_dna.dimensions == []
        assert profile.insights == []
        assert validate_profile(profile) == []

    def test_excluded_games_are_counted_and_named(self) -> None:
        inputs = [_game("a"), _game("b")]
        profile = build_profile(
            inputs, player_id="1", display_name="P", imported_games=3,
            excluded_game_ids=["unanalyzed-game"],
        )
        assert profile.imported_games == 3
        assert profile.analyzed_games == 2
        assert profile.excluded_games == 1
        assert profile.excluded_game_ids == ["unanalyzed-game"]
        assert validate_profile(profile) == []


class TestAggregates:
    def test_rates_and_reconciliation(self) -> None:
        inputs = [
            _game("a", outcome=GameOutcome.WIN, accuracy=90.0, cpl=40.0, date="2026.01.01"),
            _game("b", outcome=GameOutcome.DRAW, accuracy=80.0, cpl=60.0, date="2026.01.02"),
            _game("c", outcome=GameOutcome.LOSS, accuracy=70.0, cpl=80.0, date="2026.01.03"),
        ]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=3)
        assert profile.games.wins == 1 and profile.games.draws == 1 and profile.games.losses == 1
        assert profile.games.win_rate == round(1 / 3, 4)
        assert profile.games.average_accuracy == 80.0
        assert profile.games.median_accuracy == 80.0
        assert profile.games.average_centipawn_loss == 60.0
        assert profile.games.median_centipawn_loss == 60.0
        assert profile.games.time_span == ("2026.01.01", "2026.01.03")
        assert validate_profile(profile) == []

    def test_no_division_by_zero_without_accuracy(self) -> None:
        inputs = [_game("a", accuracy=None, cpl=None), _game("b", accuracy=None, cpl=None)]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=2)
        assert profile.games.average_accuracy is None
        assert profile.games.average_centipawn_loss is None
        assert profile.games.median_accuracy is None
        assert profile.games.accuracy_sample == 0
        assert validate_profile(profile) == []

    def test_white_and_black_are_separated_with_own_samples(self) -> None:
        inputs = [_game(f"w{i}", color="white") for i in range(4)] + [
            _game(f"b{i}", color="black") for i in range(2)
        ]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=6)
        colours = {entry.color: entry for entry in profile.by_color}
        assert colours["white"].games == 4
        assert colours["black"].games == 2
        # Below the per-colour threshold the split is flagged as descriptive.
        assert colours["black"].sample.claim_level is ClaimLevel.OBSERVATION
        assert colours["black"].sample.note and "5 games as black" in colours["black"].sample.note
        assert validate_profile(profile) == []

    def test_conversion_and_recovery_use_documented_bands(self) -> None:
        policy = PlayerInsightPolicy()
        inputs = [
            # opportunity (peak reached winning), converted by winning the game
            _game("a", peak=3, final=3, worst=-1, outcome=GameOutcome.WIN),
            # opportunity (peak forced mate) that was thrown away
            _game("b", peak=4, final=0, worst=-1, outcome=GameOutcome.DRAW),
            # no opportunity; was losing and improved off the worst point
            _game("c", peak=1, worst=-3, final=-1, outcome=GameOutcome.DRAW),
            # no opportunity; was losing and never improved
            _game("d", peak=1, worst=-4, final=-4, outcome=GameOutcome.LOSS),
            # balanced throughout: neither a conversion nor a recovery case
            _game("e", peak=1, worst=-1, final=0, outcome=GameOutcome.DRAW),
        ]
        profile = build_profile(inputs, player_id="1", display_name="P", policy=policy,
                                imported_games=5)
        assert profile.conversion.opportunities == 2
        assert profile.conversion.conversions == 1
        assert profile.conversion.conversion_rate == 0.5
        assert profile.conversion.advantage_lost == 1
        assert profile.recovery.situations == 2
        assert profile.recovery.improvements == 1
        assert profile.recovery.saved_games == 1
        assert validate_profile(profile) == []

    def test_material_is_reported_as_characteristic(self) -> None:
        inputs = [_game("a"), _game("b")]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=2)
        assert profile.material.average_captures == 15.0
        assert profile.material.average_exchanges == 3.0
        assert profile.material.sample.games == 2
        assert validate_profile(profile) == []

    def test_opponent_context_preserved_in_buckets(self) -> None:
        inputs = [
            _game("a", rating=1500, opponent_rating=1300),  # weaker opponent
            _game("b", rating=1500, opponent_rating=1750),  # stronger opponent
            _game("c", rating=None, opponent_rating=None),  # unknown
        ]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=3)
        assert profile.opponents.games_with_rating == 2
        assert profile.opponents.average_opponent_rating == 1525.0
        assert set(profile.opponents.by_bucket) == {"weaker_opponent", "stronger_opponent"}
        assert profile.opponents.sample.events == 2
        assert validate_profile(profile) == []

    def test_time_controls_only_report_categories_with_data(self) -> None:
        inputs = [
            _game("a", time_class=TimeClass.RAPID),
            _game("b", time_class=TimeClass.RAPID),
            _game("c", time_class=TimeClass.BLITZ),
        ]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=3)
        classes = {entry.time_class: entry for entry in profile.time_controls.entries}
        assert set(classes) == {TimeClass.RAPID, TimeClass.BLITZ}
        assert classes[TimeClass.RAPID].games == 2
        assert classes[TimeClass.RAPID].sample.claim_level is ClaimLevel.OBSERVATION
        assert validate_profile(profile) == []


class TestTrends:
    def test_trend_refuses_below_threshold(self) -> None:
        inputs = [_game(f"g{i}", date=f"2026.01.{i + 1:02d}") for i in range(6)]
        computed = compute_trends(inputs, DEFAULT_POLICY)
        assert all(not entry.supported for entry in computed.entries)
        assert computed.entries[0].note and "Needs at least" in computed.entries[0].note

    def test_trend_measures_recent_vs_baseline(self) -> None:
        # 15 games: older ones at CPL 80, the most recent five at CPL 40.
        inputs = [
            _game(f"g{i}", date=f"2026.01.{i + 1:02d}", cpl=80.0 if i < 10 else 40.0)
            for i in range(15)
        ]
        computed = compute_trends(inputs, DEFAULT_POLICY)
        entry = next(item for item in computed.entries if item.window == 5)
        assert entry.supported is True
        assert entry.recent_average_cpl == 40.0
        assert entry.baseline_average_cpl == 80.0
        assert entry.direction == "lower_cpl"
        assert entry.relative_change == -0.5
        assert entry.recent_games == 5 and entry.baseline_games == 10

    def test_trend_never_calls_it_improvement(self) -> None:
        inputs = [
            _game(f"g{i}", date=f"2026.01.{i + 1:02d}", cpl=80.0 if i < 10 else 40.0)
            for i in range(15)
        ]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=15)
        trend_insights = [
            insight for insight in profile.insights
            if insight.category is InsightCategory.IMPROVEMENT_TREND
        ]
        assert trend_insights
        for insight in trend_insights:
            assert "not a judgement" in insight.statement
            assert "improved" not in insight.statement.lower()


class TestPatterns:
    def test_recurring_pattern_requires_occurrences_across_games(self) -> None:
        from argus.player_intelligence.models import PlayerErrorEvent

        def with_errors(game_id: str, count: int) -> PlayerGameInput:
            game = _game(game_id)
            game.errors = [
                PlayerErrorEvent(
                    game_id=game_id, ply=10 + index, move_number=5 + index, san="Qd5",
                    phase="middlegame", classification="blunder", categories=["tactical"],
                    severity="high", centipawn_loss=300,
                )
                for index in range(count)
            ]
            return game

        # Three games with one tactical error each: below min_pattern_games (4).
        sparse = [with_errors(f"g{i}", 1) for i in range(3)]
        profile = build_profile(sparse, player_id="1", display_name="P", imported_games=3)
        tactical = [i for i in profile.insights if i.id == "pattern-tactical"]
        assert tactical and tactical[0].claim_level is ClaimLevel.OBSERVATION

        # Four games + four occurrences clears every documented threshold.
        dense = [with_errors(f"g{i}", 1) for i in range(4)] + [with_errors("g4", 2)]
        profile = build_profile(dense, player_id="1", display_name="P", imported_games=5)
        tactical = [i for i in profile.insights if i.id == "pattern-tactical"]
        assert tactical[0].claim_level is ClaimLevel.PATTERN
        assert tactical[0].occurrences == 6
        assert tactical[0].games == 5
        assert len(tactical[0].evidence) <= 8
        assert validate_profile(profile) == []

    def test_error_can_carry_multiple_categories(self) -> None:
        from argus.player_intelligence.models import PlayerErrorEvent

        inputs = []
        for index in range(5):
            game = _game(f"g{index}")
            game.errors = [
                PlayerErrorEvent(
                    game_id=game.game_id, ply=20, move_number=10, san="Nf6",
                    phase="opening", classification="mistake",
                    categories=["opening", "tactical"], certainty="confirmed",
                )
            ]
            inputs.append(game)
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=5)
        ids = {insight.id for insight in profile.insights}
        assert "pattern-opening" in ids
        assert "pattern-tactical" in ids
        assert validate_profile(profile) == []


class TestChessDna:
    def test_dimensions_are_interpretable_and_defined(self) -> None:
        from argus.player_intelligence.dna import DIMENSION_DEFINITIONS

        inputs = [_game(f"g{i}", date=f"2026.01.{i + 1:02d}") for i in range(6)]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=6)
        assert profile.chess_dna.dimensions
        for dimension in profile.chess_dna.dimensions:
            assert dimension.definition == DIMENSION_DEFINITIONS[dimension.key]
            assert dimension.unit
            assert dimension.games >= 0
            # No arbitrary 0-100 score anywhere: values are raw metrics.
            assert dimension.claim_level is not ClaimLevel.TENDENCY or dimension.games >= 20

    def test_dna_absent_below_minimum_games(self) -> None:
        profile = build_profile([_game("a")], player_id="1", display_name="P", imported_games=1)
        assert profile.chess_dna.dimensions == []
        assert profile.chess_dna.derived_from_games == 1
        assert profile.chess_dna.notes and "Not enough analyzed games" in profile.chess_dna.notes[0]

    def test_opening_diversity_note_pluralises_counts(self) -> None:
        """The DNA note is printed verbatim, so "1 games" is a visible bug.

        Two distinct openings seen once each is a real (and common) shape: the
        most-played opening then has a count of one.
        """
        first = _game("a", date="2026.01.01").model_copy(
            update={"opening_name": "Queen's Pawn Game", "opening_family": "Queen's Pawn"}
        )
        second = _game("b", date="2026.01.02").model_copy(
            update={"opening_name": "Sicilian Defence", "opening_family": "Sicilian Defence"}
        )
        profile = build_profile([first, second], player_id="1", display_name="P", imported_games=2)
        dimension = next(d for d in profile.chess_dna.dimensions if d.key == "opening_diversity")

        assert dimension.note is not None
        assert "(1 game)" in dimension.note
        assert "1 games" not in dimension.note
        assert "1 distinct openings" not in dimension.note


class TestFeatures:
    def test_feature_set_marks_user_data_and_versions(self) -> None:
        inputs = [_game(f"g{i}", date=f"2026.01.{i + 1:02d}") for i in range(5)]
        profile = build_profile(inputs, player_id="7", display_name="P", imported_games=5)
        features = extract_features(profile, inputs, games_analyzed=5)

        assert features.feature_version == FEATURE_VERSION
        assert features.player_id == "7"
        assert features.games_analyzed == 5
        assert features.data_range == ("2026.01.01", "2026.01.05")
        names = {feature.name for feature in features.features}
        assert {"games_analyzed", "average_cpl", "conversion_rate", "opening_deviation_rate"} <= names
        for feature in features.features:
            assert feature.definition
            assert feature.user_specific is True
            assert feature.training_eligible is False
            if feature.value is not None:
                assert feature.sample_size >= 0
        assert any("user-specific" in note for note in features.notes)


class TestSanityChecks:
    def test_inconsistent_profile_is_rejected(self) -> None:
        inputs = [_game("a"), _game("b")]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=2)
        # Corrupt the record: a sum that no longer reconciles must be caught.
        profile.games.wins = 2
        profile.games.losses = 2
        issues = validate_profile(profile)
        assert any("must equal analyzed_games" in issue for issue in issues)
        with pytest.raises(ProfileSanityError):
            assert_profile_consistent(profile)

    def test_conversion_cannot_exceed_its_universe(self) -> None:
        inputs = [_game("a", peak=3, final=3), _game("b", peak=3, final=3)]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=2)
        profile.conversion.conversions = 5
        issues = validate_profile(profile)
        assert any("conversions cannot exceed opportunities" in issue for issue in issues)

    def test_rate_out_of_range_is_caught(self) -> None:
        inputs = [_game("a"), _game("b")]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=2)
        profile.games.win_rate = 1.7
        assert any("share in [0, 1]" in issue for issue in validate_profile(profile))

    def test_trend_claim_without_averages_is_caught(self) -> None:
        inputs = [_game(f"g{i}", date=f"2026.01.{i + 1:02d}") for i in range(12)]
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=12)
        for entry in profile.trends.entries:
            entry.supported = True
            entry.recent_average_cpl = None
        issues = validate_profile(profile)
        assert any("supported trend needs both averages" in issue for issue in issues)


class TestScaling:
    """Aggregation at 20 and 100 games (game count synthesized from real inputs)."""

    @staticmethod
    def _scaled(count: int) -> list[PlayerGameInput]:
        base = [
            _game("base-win", outcome=GameOutcome.WIN, cpl=50.0, accuracy=85.0),
            _game("base-loss", outcome=GameOutcome.LOSS, cpl=70.0, accuracy=75.0,
                  time_class=TimeClass.BLITZ, worst=-3, final=-3),
        ]
        scaled: list[PlayerGameInput] = []
        for index in range(count):
            source = base[index % len(base)].model_copy(deep=True)
            source.game_id = f"g{index:03d}"
            source.date = f"2026.{(index % 12) + 1:02d}.01"
            scaled.append(source)
        return scaled

    def test_twenty_games_reaches_tendency_coverage(self) -> None:
        inputs = self._scaled(20)
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=20)
        assert profile.coverage is Coverage.ROBUST
        assert profile.sufficient_data is True
        assert validate_profile(profile) == []
        assert any(insight.claim_level is ClaimLevel.TENDENCY for insight in profile.insights)
        assert profile.chess_dna.dimensions

    def test_hundred_games_stays_consistent(self) -> None:
        inputs = self._scaled(100)
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=100)
        assert profile.analyzed_games == 100
        assert profile.games.wins + profile.games.draws + profile.games.losses == 100
        assert sum(entry.games for entry in profile.by_color) == 100
        assert validate_profile(profile) == []
        features = extract_features(profile, inputs, games_analyzed=100)
        assert features.as_dict()["games_analyzed"] == 100.0

    def test_service_surface_is_complete(self) -> None:
        from argus.player_intelligence import PlayerIntelligenceService

        inputs = self._scaled(20)
        profile = build_profile(inputs, player_id="1", display_name="P", imported_games=20)
        service = PlayerIntelligenceService(profile, inputs)

        expected = {
            "get_player_profile",
            "get_player_statistics",
            "get_player_opening_profile",
            "get_player_phase_statistics",
            "get_player_tactical_profile",
            "get_player_positional_profile",
            "get_player_trends",
            "get_player_insights",
            "get_player_evidence",
            "get_player_features",
        }
        assert expected <= set(service.available_tools())
        stats = service.get_player_statistics()
        assert stats["analyzed_games"] == 20
        assert stats["coverage"] == "robust"
        evidence = service.get_player_evidence()
        assert evidence["player_id"] == "1"
        assert isinstance(PlayerProfile.model_validate(service.get_player_profile()), PlayerProfile)


class TestEvidenceTraceability:
    """Every claim points at real positions, and no position twice (spec §26)."""

    @staticmethod
    def _minimal_game(game_id: str) -> PlayerGameInput:
        """A game with no events — the minimum an aggregation needs to run."""
        return PlayerGameInput(
            game_id=game_id,
            date="2026.01.02",
            opponent_name="Opponent",
            color="black",
            result_raw="0-1",
            outcome=GameOutcome.WIN,
        )

    def test_evidence_refs_are_unique_and_point_at_games(self) -> None:
        # Two games, because a profile below the minimum sample computes no
        # sections at all — the evidence question is about a computed profile.
        inputs = [
            PlayerGameInput(
                game_id="ev-1",
                date="2026.01.01",
                opponent_name="Opponent",
                color="white",
                result_raw="1-0",
                outcome=GameOutcome.WIN,
                positional_events=[
                    PositionalEventInput(
                        game_id="ev-1",
                        ply=17,
                        move_number=9,
                        san="fxg5",
                        feature="doubled_pawn",
                        kind="error_candidate",
                        direction="created",
                    ),
                    # The same feature on the same move: one move can change one
                    # structure once, so this must not produce two evidence rows.
                    PositionalEventInput(
                        game_id="ev-1",
                        ply=17,
                        move_number=9,
                        san="fxg5",
                        feature="doubled_pawn",
                        kind="error_candidate",
                        direction="created",
                    ),
                    # A different feature on the same move is genuinely separate.
                    PositionalEventInput(
                        game_id="ev-1",
                        ply=17,
                        move_number=9,
                        san="fxg5",
                        feature="semi_open_file",
                        kind="error_candidate",
                        direction="created",
                    ),
                ],
            ),
            self._minimal_game("ev-2"),
        ]
        profile = build_profile(inputs, player_id="9", display_name="E", imported_games=2)

        evidence = profile.positional.evidence
        keys = [(ref.game_id, ref.ply, ref.label) for ref in evidence]
        assert len(keys) == len(set(keys)), "evidence must not repeat the same position"
        assert all(ref.game_id == "ev-1" for ref in evidence)
        assert all(ref.ply == 17 for ref in evidence)
        # The label names the feature, so two rows on one ply stay distinguishable.
        assert {ref.label for ref in evidence} == {
            "positional error candidate · doubled_pawn",
            "positional error candidate · semi_open_file",
        }

    def test_insight_title_never_claims_more_than_the_evidence(self) -> None:
        """Few occurrences must be titled as an observation, not a pattern."""

        def game_with_tactical_error(game_id: str) -> PlayerGameInput:
            return PlayerGameInput(
                game_id=game_id,
                date="2026.01.01",
                opponent_name="Opponent",
                color="white",
                result_raw="0-1",
                outcome=GameOutcome.LOSS,
                errors=[
                    PlayerErrorEvent(
                        game_id=game_id,
                        ply=21,
                        move_number=11,
                        san="Qh5",
                        classification="blunder",
                        categories=["tactical"],
                    )
                ],
            )

        # Two occurrences across two games: below the documented pattern
        # thresholds (4 occurrences across 4 games), so it stays an observation.
        inputs = [game_with_tactical_error("t-1"), game_with_tactical_error("t-2")]
        profile = build_profile(inputs, player_id="9", display_name="T", imported_games=2)

        tactical = [
            insight
            for insight in profile.insights
            if insight.metric == "error_category"
        ]
        assert tactical, "an observed error category should still be reported"
        for insight in tactical:
            assert insight.claim_level is ClaimLevel.OBSERVATION
            assert not insight.title.startswith("Recurring")
            assert "observed" in insight.title
