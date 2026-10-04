"""Phase 8 unit tests: generator, scheduler, sessions, recommendations, progress.

The generator tests pin the *honesty* rules: exercises come from position
BEFORE the move, played-the-solution rows are skipped, categories are earned,
output is deterministic, duplicates are refused. The scheduler tests walk the
whole state machine, including the "never mastered after one success" rule.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.training.acceptance import AcceptanceOutcome  # noqa: E402
from argus.training.generator import (  # noqa: E402
    GenerationReport,
    TrainingPositionGenerator,
)
from argus.training.models import (  # noqa: E402
    Category,
    PositionType,
    TrainingPosition,
    TrainingState,
    can_transition,
)
from argus.training.progress import compute_progress  # noqa: E402
from argus.training.recommendations import (  # noqa: E402
    CategoryStats,
    RecommendationEngine,
)
from argus.training.scheduler import (  # noqa: E402
    INITIAL_INTERVAL_DAYS,
    apply_attempt,
    due_positions,
)
from argus.training.sessions import SESSION_KINDS, plan_session  # noqa: E402

POSITION_FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
SOLUTION_UCI = "h5f7"
SOLUTION_SAN = "Qxf7#"


def _analysis_row(**overrides) -> dict:
    """A stored move-analysis row in the API's persisted shape."""
    row = {
        "ply": 6,
        "move_number": 3,
        "mover": "white",
        "played_move_uci": "h5f6",
        "played_move_san": "Qxf6??",
        "fen_before": POSITION_FEN,
        "fen_after": "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 4 4",
        "best_move_uci": SOLUTION_UCI,
        "best_move_san": SOLUTION_SAN,
        "evaluation_before_cp": 30,
        "evaluation_before_mate": None,
        "played_eval_cp": -350,
        "played_eval_mate": None,
        "centipawn_loss": 380,
        "classification": "blunder",
        "phase": "opening",
        "principal_variation": [SOLUTION_UCI],
        "depth": 18,
        "engine": "stockfish",
        "engine_version": "16.4",
        "analysis_version": "3.1",
        "candidate_moves": [],
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class TestGeneratorBasics:
    def test_generates_from_position_before_the_move(self) -> None:
        candidate = TrainingPositionGenerator().generate_from_move_analysis(
            _analysis_row(), game_id="g1", player_id=7
        )
        assert candidate.accepted
        position = candidate.position
        assert position is not None
        # The puzzle starts BEFORE the played move: the played move is not on
        # the board of the puzzle position.
        assert position.fen == POSITION_FEN
        assert position.source_ply == 6
        assert position.source_game_id == "g1"
        assert position.player_id == 7
        assert position.played_move_uci == "h5f6"
        assert position.solution_uci == SOLUTION_UCI

    def test_played_the_solution_is_skipped(self) -> None:
        candidate = TrainingPositionGenerator().generate_from_move_analysis(
            _analysis_row(played_move_uci=SOLUTION_UCI, played_move_san=SOLUTION_SAN)
        )
        assert not candidate.accepted
        assert candidate.position is None
        assert "played move was already the best move" in candidate.report.reasons

    def test_missing_engine_best_move_is_skipped(self) -> None:
        candidate = TrainingPositionGenerator().generate_from_move_analysis(
            _analysis_row(best_move_uci=None, best_move_san=None)
        )
        assert not candidate.accepted
        assert "no stored engine best move" in candidate.report.reasons

    def test_mate_only_row_is_accepted(self) -> None:
        candidate = TrainingPositionGenerator().generate_from_move_analysis(
            _analysis_row(
                evaluation_before_cp=None,
                evaluation_before_mate=1,
                solution_san=SOLUTION_SAN,
            )
        )
        assert candidate.accepted, candidate.report.reasons
        assert candidate.position.solution_eval_mate == 1

    def test_deterministic_output(self) -> None:
        row = _analysis_row()
        one = TrainingPositionGenerator().generate_from_move_analysis(row).position
        two = TrainingPositionGenerator().generate_from_move_analysis(_analysis_row()).position
        assert one is not None and two is not None
        assert one.model_dump() == two.model_dump()


class TestGeneratorCategories:
    def test_forced_mate_is_tactical_with_mating_tag(self) -> None:
        candidate = TrainingPositionGenerator().generate_from_move_analysis(
            _analysis_row(evaluation_before_cp=None, evaluation_before_mate=1)
        )
        position = candidate.position
        assert position is not None
        assert position.category is Category.TACTICAL
        assert "mating_pattern" in position.tags

    def test_endgame_phase_maps_to_endgame_category(self) -> None:
        # A sparse endgame row with a quiet solution (no mate, no captures).
        endgame_fen = "8/4k3/8/8/8/8/4K3/3R4 w - - 0 1"
        row = _analysis_row(
            fen_before=endgame_fen,
            fen_after="8/4k3/8/8/8/8/4K3/3R5 b - - 1 1",
            played_move_uci="d1d2",
            played_move_san="Rd2??",
            best_move_uci="d1d7",
            best_move_san="Rd7+",
            phase="endgame",
            centipawn_loss=280,
            played_eval_cp=-240,
        )
        candidate = TrainingPositionGenerator().generate_from_move_analysis(row)
        position = candidate.position
        assert position is not None
        assert position.category is Category.ENDGAME

    def test_unevidenced_middlegame_row_is_calculation(self) -> None:
        # Dense middlegame, quiet solution, no mate: no tactical story provable.
        quiet_fen = "r1bq1rk1/pppp1ppp/8/8/8/8/PPPP1PPP/RNBQ1RK1 w - - 0 1"
        row = _analysis_row(
            fen_before=quiet_fen,
            fen_after="r1bq1rk1/pppp1ppp/8/8/8/P7/1PPP1PPP/RNBQ1RK1 b - - 0 1",
            played_move_uci="a2a3",
            played_move_san="a3??",
            best_move_uci="b1c3",
            best_move_san="Nc3",
            phase="middlegame",
            centipawn_loss=150,
            played_eval_cp=-120,
        )
        candidate = TrainingPositionGenerator().generate_from_move_analysis(row)
        position = candidate.position
        assert position is not None
        assert position.category is Category.CALCULATION

    def test_single_move_generation_never_assigns_conversion_or_recovery(self) -> None:
        # Conversion/recovery need whole-game arc evidence; a lone move cannot
        # produce them, even though the generator may when given a game result.
        row = _analysis_row(evaluation_before_cp=-500)
        candidate = TrainingPositionGenerator().generate_from_move_analysis(row)
        assert candidate.position is not None
        assert candidate.position.category not in (Category.CONVERSION, Category.RECOVERY)
        from argus.training.models import GENERATOR_CATEGORIES
        assert Category.CONVERSION in GENERATOR_CATEGORIES
        assert Category.RECOVERY in GENERATOR_CATEGORIES

    def test_worse_position_is_find_defense(self) -> None:
        row = _analysis_row(evaluation_before_cp=-500)
        candidate = TrainingPositionGenerator().generate_from_move_analysis(row)
        position = candidate.position
        assert position is not None
        assert position.position_type is PositionType.FIND_DEFENSE

    def test_mate_position_is_find_tactical_move(self) -> None:
        row = _analysis_row(evaluation_before_cp=None, evaluation_before_mate=1)
        candidate = TrainingPositionGenerator().generate_from_move_analysis(row)
        position = candidate.position
        assert position is not None
        assert position.position_type is PositionType.FIND_TACTICAL_MOVE


class TestGeneratorBatchAndDedupe:
    def test_batch_report_counts(self) -> None:
        rows = [
            _analysis_row(played_move_uci="h5f6", played_move_san="Qxf6??"),
            _analysis_row(ply=7, played_move_uci=SOLUTION_UCI, played_move_san=SOLUTION_SAN),
            _analysis_row(ply=8, best_move_uci=None),
        ]
        report = TrainingPositionGenerator().generate_from_game(rows, game_id="g1")
        assert isinstance(report, GenerationReport)
        assert report.total_seen == 3
        assert report.accepted_count == 1
        assert report.rejected_count == 0
        assert len(report.skipped) == 2

    def test_in_run_duplicate_fen_is_rejected(self) -> None:
        rows = [_analysis_row(), _analysis_row(ply=9)]
        report = TrainingPositionGenerator().generate_from_game(rows, game_id="g1")
        assert report.accepted_count == 1
        assert report.rejected_count == 1
        reasons = report.rejected[0].report.reasons
        assert any("duplicate_position" in reason for reason in reasons)

    def test_existing_fen_is_rejected(self) -> None:
        normalized = " ".join(POSITION_FEN.split()[:4])
        rows = [_analysis_row()]
        report = TrainingPositionGenerator().generate_from_game(
            rows, game_id="g1", existing_normalized_fens={normalized}
        )
        assert report.accepted_count == 0
        assert report.rejected_count == 1

    def test_reason_counts_summarize(self) -> None:
        rows = [_analysis_row(), _analysis_row(ply=9)]
        report = TrainingPositionGenerator().generate_from_game(rows, game_id="g1")
        counts = report.reason_counts()
        assert counts.get("duplicate_position") == 1

    def test_non_problem_move_rejected_by_gate(self) -> None:
        # Played move cost only 40cp: below the not-a-mistake threshold.
        candidate = TrainingPositionGenerator().generate_from_move_analysis(
            _analysis_row(centipawn_loss=40, played_eval_cp=-10)
        )
        assert not candidate.accepted
        assert any("not_a_mistake" in reason for reason in candidate.report.reasons)


# ---------------------------------------------------------------------------
# Scheduler / SRS
# ---------------------------------------------------------------------------


def _fresh_position(**overrides) -> TrainingPosition:
    defaults = dict(
        id=1,
        player_id=7,
        side_to_move="white",
        fen=POSITION_FEN,
        source_fen_normalized=" ".join(POSITION_FEN.split()[:4]),
        category=Category.TACTICAL,
        solution_uci=SOLUTION_UCI,
        solution_san=SOLUTION_SAN,
        state=TrainingState.NEW,
    )
    defaults.update(overrides)
    return TrainingPosition(**defaults)


class TestSchedulerStateMachine:
    def test_new_plus_correct_goes_learning_with_initial_interval(self) -> None:
        position = _fresh_position()
        now = datetime.now(timezone.utc)
        apply_attempt(position, AcceptanceOutcome.CORRECT, now=now)
        assert position.state is TrainingState.LEARNING
        assert position.streak == 1
        assert position.review_interval_days == INITIAL_INTERVAL_DAYS
        assert position.next_review_at == now + timedelta(days=INITIAL_INTERVAL_DAYS)
        assert position.attempts == 1
        assert position.correct_attempts == 1

    def test_never_mastered_after_one_success(self) -> None:
        position = _fresh_position()
        apply_attempt(position, AcceptanceOutcome.CORRECT)
        assert position.state is not TrainingState.MASTERED
        # Even a long streak promoted from learning cannot jump straight past
        # review: the ladder is new → learning → review → mastered.
        assert position.state is TrainingState.LEARNING

    def test_mastered_requires_streak_and_interval(self) -> None:
        position = _fresh_position(
            state=TrainingState.REVIEW, streak=3, review_interval_days=8.0
        )
        apply_attempt(position, AcceptanceOutcome.CORRECT)
        assert position.state is TrainingState.MASTERED  # streak 4, interval 16

    def test_review_correct_below_streak_stays_review(self) -> None:
        position = _fresh_position(state=TrainingState.REVIEW, streak=1, review_interval_days=2.0)
        apply_attempt(position, AcceptanceOutcome.CORRECT)
        assert position.state is TrainingState.REVIEW
        assert position.streak == 2
        assert position.review_interval_days == 4.0

    def test_incorrect_new_becomes_failed(self) -> None:
        position = _fresh_position()
        apply_attempt(position, AcceptanceOutcome.INCORRECT)
        assert position.state is TrainingState.FAILED
        assert position.streak == 0

    def test_incorrect_review_becomes_needs_review(self) -> None:
        position = _fresh_position(state=TrainingState.REVIEW, streak=3, review_interval_days=16.0)
        apply_attempt(position, AcceptanceOutcome.INCORRECT)
        assert position.state is TrainingState.NEEDS_REVIEW
        assert position.review_interval_days == INITIAL_INTERVAL_DAYS

    def test_incorrect_mastered_demotes(self) -> None:
        position = _fresh_position(
            state=TrainingState.MASTERED, streak=6, review_interval_days=45.0
        )
        apply_attempt(position, AcceptanceOutcome.INCORRECT)
        assert position.state is TrainingState.NEEDS_REVIEW

    def test_near_best_does_not_advance(self) -> None:
        position = _fresh_position(state=TrainingState.REVIEW, streak=3, review_interval_days=4.0)
        apply_attempt(position, AcceptanceOutcome.NEAR_BEST)
        assert position.state is TrainingState.REVIEW
        assert position.streak == 0  # reset: near-best is not mastery evidence
        assert position.correct_attempts == 0

    def test_near_best_from_new_stays_learningish(self) -> None:
        position = _fresh_position()
        apply_attempt(position, AcceptanceOutcome.NEAR_BEST)
        assert position.state is TrainingState.LEARNING

    def test_failed_recovers_through_learning(self) -> None:
        position = _fresh_position(state=TrainingState.FAILED)
        apply_attempt(position, AcceptanceOutcome.INCORRECT)
        assert position.state is TrainingState.FAILED
        apply_attempt(position, AcceptanceOutcome.CORRECT)
        assert position.state is TrainingState.LEARNING

    def test_needs_review_correct_returns_to_review(self) -> None:
        position = _fresh_position(state=TrainingState.NEEDS_REVIEW, review_interval_days=1.0)
        apply_attempt(position, AcceptanceOutcome.CORRECT)
        assert position.state is TrainingState.REVIEW

    def test_interval_cap_enforced(self) -> None:
        position = _fresh_position(
            state=TrainingState.MASTERED, streak=9, review_interval_days=170.0
        )
        apply_attempt(position, AcceptanceOutcome.CORRECT)
        assert position.review_interval_days <= 180.0

    def test_every_transition_is_in_the_allowed_graph(self) -> None:
        for from_state in TrainingState:
            for outcome in AcceptanceOutcome:
                probe = _fresh_position(state=from_state)
                decision = apply_attempt(probe, outcome)
                assert can_transition(decision.previous_state, decision.new_state), (
                    f"{from_state.value} + {outcome.value} → {decision.new_state.value}"
                )


class TestDueQueue:
    def test_due_ordering_and_limit(self) -> None:
        now = datetime.now(timezone.utc)
        overdue = _fresh_position(id=1, next_review_at=now - timedelta(days=2))
        nearly = _fresh_position(id=2, next_review_at=now - timedelta(days=1))
        future = _fresh_position(id=3, next_review_at=now + timedelta(days=5))
        fresh = _fresh_position(id=4)  # no schedule
        queue = due_positions([future, fresh, overdue, nearly], now=now)
        assert [p.id for p in queue] == [1, 2]
        with_new = due_positions([future, fresh, overdue, nearly], now=now, include_new=True)
        assert [p.id for p in with_new] == [1, 2, 4]
        limited = due_positions([future, fresh, overdue, nearly], now=now, include_new=True, limit=2)
        assert [p.id for p in limited] == [1, 2]


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _library(*ids_states) -> list[TrainingPosition]:
    positions = []
    for idx, (pid, state, category) in enumerate(ids_states):
        positions.append(
            TrainingPosition(
                id=pid,
                player_id=7,
                side_to_move="white",
                fen=POSITION_FEN,
                category=Category(category),
                solution_uci=SOLUTION_UCI,
                solution_san=SOLUTION_SAN,
                state=TrainingState(state),
            )
        )
    return positions


class TestSessionPlanning:
    def test_quick_session_is_five(self) -> None:
        positions = _library(
            (1, "review", "tactical"),
            (2, "review", "tactical"),
            (3, "review", "endgame"),
            (4, "review", "calculation"),
            (5, "learning", "tactical"),
            (6, "review", "opening"),
            (7, "review", "tactical"),
        )
        plan = plan_session("quick", positions, due_position_ids=[7, 6, 5, 4, 3, 2, 1])
        assert len(plan.planned_position_ids) == 5
        # Due order respected: most-due first.
        assert plan.planned_position_ids == [7, 6, 5, 4, 3]

    def test_daily_tops_up_with_new(self) -> None:
        positions = _library(
            (1, "review", "tactical"),
            (2, "new", "tactical"),
            (3, "new", "endgame"),
        )
        plan = plan_session("daily", positions, due_position_ids=[1])
        assert plan.planned_position_ids == [1, 2, 3]
        assert not plan.notes

    def test_tactical_kind_filters_category(self) -> None:
        positions = _library(
            (1, "review", "tactical"),
            (2, "review", "endgame"),
            (3, "review", "tactical"),
        )
        plan = plan_session("tactical", positions)
        assert plan.planned_position_ids == [1, 3]

    def test_endgame_kind_empty_library_is_honestly_empty(self) -> None:
        positions = _library((1, "review", "tactical"))
        plan = plan_session("endgame", positions)
        assert plan.planned_position_ids == []
        assert any("no 'endgame' exercises" in note for note in plan.notes)

    def test_game_review_groups_by_source(self) -> None:
        p1 = TrainingPosition(
            id=1, player_id=7, side_to_move="white", fen=POSITION_FEN,
            category=Category.TACTICAL, solution_uci=SOLUTION_UCI, solution_san=SOLUTION_SAN,
            source_game_id="g1", source_ply=20,
        )
        p2 = TrainingPosition(
            id=2, player_id=7, side_to_move="white", fen=POSITION_FEN,
            category=Category.TACTICAL, solution_uci=SOLUTION_UCI, solution_san=SOLUTION_SAN,
            source_game_id="g1", source_ply=6,
        )
        p3 = TrainingPosition(
            id=3, player_id=7, side_to_move="white", fen=POSITION_FEN,
            category=Category.TACTICAL, solution_uci=SOLUTION_UCI, solution_san=SOLUTION_SAN,
            source_game_id="g2", source_ply=9,
        )
        plan = plan_session("game_review", [p1, p2, p3], game_id="g1")
        assert plan.planned_position_ids == [2, 1]  # ply order

    def test_weakness_follows_recommendation_order(self) -> None:
        positions = _library(
            (1, "review", "endgame"),
            (2, "review", "tactical"),
            (3, "review", "endgame"),
        )
        plan = plan_session(
            "weakness", positions, due_position_ids=[], weakness_category_order=["endgame", "tactical"]
        )
        assert plan.planned_position_ids == [1, 3, 2]

    def test_custom_respects_explicit_ids_and_notes_missing(self) -> None:
        positions = _library((1, "review", "tactical"), (2, "review", "tactical"))
        plan = plan_session("custom", positions, custom_position_ids=[2, 1, 99])
        assert plan.planned_position_ids == [2, 1]
        assert any("99" in note for note in plan.notes)

    def test_plan_is_resumable(self) -> None:
        positions = _library((1, "review", "tactical"), (2, "review", "tactical"), (3, "review", "tactical"))
        plan = plan_session("quick", positions, due_position_ids=[1, 2, 3])
        plan.mark_completed(1)
        plan.mark_completed(1)  # idempotent
        assert plan.remaining_position_ids == [2, 3]
        assert plan.progress == pytest.approx(1 / 3)

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError):
            plan_session("marathon", [])

    def test_all_seven_kinds_declared(self) -> None:
        assert len(SESSION_KINDS) == 7


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class TestRecommendations:
    def _stats(self, **kwargs) -> CategoryStats:
        defaults = dict(category="tactical", attempts=12, correct=6, near_best=1, incorrect=5)
        defaults.update(kwargs)
        return CategoryStats(**defaults)

    def test_low_accuracy_recommended_with_evidence(self) -> None:
        now = datetime.now(timezone.utc)
        opportunities = RecommendationEngine().recommend(
            [self._stats()], library_categories=["tactical"], attempted_categories=["tactical"], now=now
        )
        assert len(opportunities) == 1
        opportunity = opportunities[0]
        assert opportunity.category == "tactical"
        assert any("6/11" in line for line in opportunity.evidence)
        assert opportunity.factors["accuracy"] == pytest.approx(5 / 11, abs=0.01)

    def test_high_accuracy_not_recommended(self) -> None:
        now = datetime.now(timezone.utc)
        opportunities = RecommendationEngine().recommend(
            [self._stats(correct=11, incorrect=0)], library_categories=["tactical"], attempted_categories=["tactical"], now=now
        )
        assert opportunities == []

    def test_insufficient_sample_skipped(self) -> None:
        now = datetime.now(timezone.utc)
        opportunities = RecommendationEngine().recommend(
            [self._stats(attempts=2, correct=0, incorrect=2)], library_categories=["tactical"], attempted_categories=["tactical"], now=now
        )
        assert opportunities == []

    def test_untried_category_boosted(self) -> None:
        now = datetime.now(timezone.utc)
        tried = RecommendationEngine().recommend(
            [self._stats()], library_categories=["tactical"], attempted_categories=["tactical"], now=now
        )
        untried = RecommendationEngine().recommend(
            [self._stats()], library_categories=["tactical"], attempted_categories=[], now=now
        )
        assert untried[0].priority > tried[0].priority

    def test_recency_weights_recent_failures(self) -> None:
        now = datetime.now(timezone.utc)
        recent = RecommendationEngine().recommend(
            [self._stats(last_attempt_at=now - timedelta(days=1))], library_categories=["tactical"], attempted_categories=["tactical"], now=now
        )
        old = RecommendationEngine().recommend(
            [self._stats(last_attempt_at=now - timedelta(days=120))], library_categories=["tactical"], attempted_categories=["tactical"], now=now
        )
        assert recent[0].priority > old[0].priority

    def test_categories_not_in_library_are_not_recommended(self) -> None:
        now = datetime.now(timezone.utc)
        opportunities = RecommendationEngine().recommend(
            [self._stats(category="endgame")], library_categories=["tactical"], attempted_categories=["endgame"], now=now
        )
        assert opportunities == []

    def test_recurring_pattern_evidence_only_for_real_counts(self) -> None:
        now = datetime.now(timezone.utc)
        opportunities = RecommendationEngine().recommend(
            [self._stats()],
            library_categories=["tactical"],
            attempted_categories=["tactical"],
            pattern_counts={"hanging_piece": 4, "pin": 1},
            now=now,
        )
        evidence = opportunities[0].evidence
        assert any("hanging_piece" in line and "4" in line for line in evidence)
        assert all("pin" not in line for line in evidence)


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


class TestProgress:
    def test_empty_library_reports_no_data(self) -> None:
        report = compute_progress([], [])
        payload = report.to_dict()
        assert payload["accuracy_overall"] is None
        assert payload["attempts_total"] == 0
        assert payload["library_size"] == 0

    def test_accuracy_always_carries_sample_size(self) -> None:
        positions = _library((1, "review", "tactical"), (2, "review", "endgame"))
        attempts = [
            {"training_position_id": 1, "correctness": "correct", "hints_used": 0, "response_time_ms": 3000},
            {"training_position_id": 1, "correctness": "incorrect", "hints_used": 1, "response_time_ms": 5000},
            {"training_position_id": 2, "correctness": "correct", "hints_used": 0, "response_time_ms": None},
        ]
        payload = compute_progress(positions, attempts).to_dict()
        assert payload["by_category"]["tactical"]["accuracy"] == pytest.approx(0.5)
        assert payload["by_category"]["tactical"]["decided"] == 2
        # Below the minimum sample: flagged, never silently averaged.
        assert "insufficient data" in payload["by_category"]["tactical"]["accuracy_note"]
        assert payload["accuracy_overall"] == pytest.approx(2 / 3, abs=0.001)

    def test_unknown_correctness_never_silently_relabeled(self) -> None:
        positions = _library((1, "review", "tactical"))
        payload = compute_progress(positions, [{"training_position_id": 1, "correctness": "weird"}]).to_dict()
        assert payload["attempts_total"] == 0

    def test_state_counts_and_mastery(self) -> None:
        positions = _library(
            (1, "mastered", "tactical"),
            (2, "review", "tactical"),
            (3, "new", "endgame"),
        )
        payload = compute_progress(positions, []).to_dict()
        assert payload["state_counts"] == {"mastered": 1, "review": 1, "new": 1}
        assert payload["mastered_count"] == 1

    def test_hint_and_response_stats(self) -> None:
        positions = _library((1, "review", "tactical"))
        attempts = [
            {"training_position_id": 1, "correctness": "correct", "hints_used": 2, "response_time_ms": 4000},
            {"training_position_id": 1, "correctness": "near_best", "hints_used": 0, "response_time_ms": 2000},
        ]
        payload = compute_progress(positions, attempts).to_dict()
        assert payload["hints"]["total_hints_used"] == 2
        assert payload["hints"]["attempts_with_hints"] == 1
        assert payload["response_time_ms"]["average"] == 3000
        assert payload["response_time_ms"]["samples"] == 2

    def test_orphan_attempt_counts_into_totals(self) -> None:
        # A position deleted after attempts: totals still count the attempt,
        # category shows as 'unknown' — history is never silently dropped.
        positions = _library((1, "review", "tactical"))
        attempts = [{"training_position_id": 999, "correctness": "correct"}]
        payload = compute_progress(positions, attempts).to_dict()
        assert payload["attempts_total"] == 1
        assert payload["correct_total"] == 1
        assert payload["by_category"]["unknown"]["decided"] == 1


# ---------------------------------------------------------------------------
# Game-arc evidence: conversion / recovery (methodology 8.1)
# ---------------------------------------------------------------------------


class TestGameArc:
    """Conversion and recovery are provable only from a whole game's trajectory."""

    @staticmethod
    def _rows(values: list[int | None]) -> list[dict]:
        return [
            {
                "ply": index * 2 + 1,
                "mover": "white",
                "evaluation_before_cp": value,
            }
            for index, value in enumerate(values)
        ]

    def test_failed_conversion_is_labelled_from_the_result(self) -> None:
        from argus.training.gamearc import classify_arc

        labels = classify_arc(
            self._rows([300, 250, -50]), player_color="white", result="1/2-1/2"
        )
        assert 1 in labels
        assert labels[1].category is Category.CONVERSION
        assert "depending" not in labels[1].reason  # plain, factual text

    def test_no_conversion_label_when_the_player_won(self) -> None:
        from argus.training.gamearc import classify_arc

        labels = classify_arc(
            self._rows([300, 250, 100]), player_color="white", result="1-0"
        )
        assert labels == {}

    def test_held_defence_is_labelled_recovery(self) -> None:
        from argus.training.gamearc import classify_arc

        labels = classify_arc(
            self._rows([-400, -300, -50]), player_color="white", result="1-0"
        )
        assert 1 in labels
        assert labels[1].category is Category.RECOVERY

    def test_unknown_result_produces_no_label(self) -> None:
        from argus.training.gamearc import classify_arc

        assert classify_arc(self._rows([300, -50]), player_color="white", result="*") == {}

    def test_batch_generation_applies_the_arc_label(self) -> None:
        rows = [
            _analysis_row(ply=1, evaluation_before_cp=300),
            _analysis_row(ply=3, evaluation_before_cp=250),
            _analysis_row(ply=5, evaluation_before_cp=-50),
        ]
        report = TrainingPositionGenerator().generate_from_game(
            rows, game_result="1/2-1/2", player_color="white"
        )
        categories = {candidate.position.category for candidate in report.generated if candidate.position}
        if Category.CONVERSION in categories:
            labelled = next(
                candidate
                for candidate in report.generated
                if candidate.position and candidate.position.category is Category.CONVERSION
            )
            assert "Conversion" in labelled.position.source_reason


class TestWholeGameReplay:
    """WHAT_WENT_WRONG and RECONSTRUCTION, built from a game's own analyses.

    Methodology 8.2: the types Phase 8 declared but never emitted. They are built
    from the *sequence* of a game's stored analyses, so these tests assert that
    every move they show is a move the game really contained — the played move,
    the stored best move, and, for a review, the real continuation.
    """

    #: A real opening line, so every stored move is legal in sequence.
    LINE = (
        "e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6",
        "O-O", "Be7", "Re1", "b5", "Bb3", "d6", "c3", "O-O",
    )

    @classmethod
    def _rows(cls, *, alternate: dict[int, tuple[str, int]] | None = None) -> list[dict]:
        """Stored move analyses for the line; ``alternate`` overrides (best, loss)."""
        import chess

        board = chess.Board()
        rows: list[dict] = []
        for index, san in enumerate(cls.LINE):
            move = board.parse_san(san)
            fen_before = board.fen()
            best_uci, loss = move.uci(), 0
            if alternate and index in alternate:
                best_uci, loss = alternate[index]
            board.push(move)
            rows.append(
                {
                    "ply": index + 1,
                    "move_number": index // 2 + 1,
                    "mover": "white" if index % 2 == 0 else "black",
                    "fen_before": fen_before,
                    "fen_after": board.fen(),
                    "played_move_uci": move.uci(),
                    "played_move_san": san,
                    "best_move_uci": best_uci,
                    "best_move_san": None,
                    "evaluation_before_cp": 30,
                    "evaluation_before_mate": None,
                    "played_eval_cp": -loss,
                    "centipawn_loss": loss,
                    "classification": "blunder" if loss >= 150 else "best",
                    "phase": "opening",
                    "principal_variation": [best_uci],
                    "depth": 16,
                    "engine": "stockfish",
                    "engine_version": "16.4",
                    "analysis_version": "3.1",
                    "candidate_moves": [],
                }
            )
        return rows

    def test_what_went_wrong_carries_the_real_continuation(self) -> None:
        from argus.training.replay import build_what_went_wrong

        # At ply 5 (index 4) the real move was Bb5; the engine preferred Bc4.
        rows = self._rows(alternate={4: ("f1c4", 300)})
        positions, refusals = build_what_went_wrong(
            game_id="g1", rows=rows, player_color="white", result="1-0"
        )
        assert refusals == []
        assert len(positions) == 1
        position = positions[0]
        assert position.position_type is PositionType.WHAT_WENT_WRONG
        assert position.category is Category.OPENING  # from the stored phase
        assert position.solution_uci == "f1c4"
        assert position.played_move_uci == "f1b5"
        assert position.played_loss_cp == 300
        context = position.replay_context
        assert context["kind"] == "what_went_wrong"
        assert context["better"]["uci"] == "f1c4"
        assert context["played"]["san"] == "Bb5"
        # The aftermath is the game's own next plies, in order.
        assert [entry["uci"] for entry in context["actual_continuation"]] == [
            row["played_move_uci"] for row in rows[5:13]
        ]
        assert context["actual_continuation"][0]["san"] == "a6"
        assert context["evaluation_trajectory"][0]["ply"] == 1

    def test_review_reuses_the_positions_own_category(self) -> None:
        from argus.training.replay import build_what_went_wrong

        rows = self._rows(alternate={4: ("f1c4", 300)})
        fen = rows[4]["fen_before"]
        normalized = " ".join(fen.split()[:4])
        positions, _ = build_what_went_wrong(
            game_id="g1",
            rows=rows,
            player_color="white",
            result="1-0",
            category_overrides={normalized: Category.TACTICAL},
        )
        assert positions[0].category is Category.TACTICAL

    def test_a_move_the_engine_agreed_with_is_not_a_review(self) -> None:
        from argus.training.replay import build_what_went_wrong

        # Big loss but the stored move IS the best move: nothing to teach.
        positions, _ = build_what_went_wrong(
            game_id="g1", rows=self._rows(), player_color="white", result="1-0"
        )
        assert positions == []

    def test_review_refuses_when_the_best_move_is_missing(self) -> None:
        from argus.training.replay import build_what_went_wrong

        rows = self._rows(alternate={4: ("f1c4", 300)})
        rows[4]["best_move_uci"] = None
        positions, refusals = build_what_went_wrong(
            game_id="g1", rows=rows, player_color="white", result="1-0"
        )
        assert positions == []
        assert any("missing stored position or move" in entry["reason"] for entry in refusals)

    def test_reconstruction_grades_the_games_own_moves(self) -> None:
        from argus.training.replay import build_reconstruction

        positions, refusals = build_reconstruction(
            game_id="g1", rows=self._rows(), player_color="white"
        )
        assert refusals == []
        assert positions
        position = positions[0]
        assert position.position_type is PositionType.RECONSTRUCTION
        assert position.fen == self._rows()[0]["fen_before"]
        # Solver turns are the odd indexes: the opponent's replies come from the
        # stored line, exactly as the continuation grader expects.
        solver_moves = position.continuation_line[1::2]
        assert len(solver_moves) >= 2
        assert position.replay_context["solver_moves"] == len(solver_moves)
        # Every move of the line is legal in sequence from the exercise's FEN.
        import chess

        board = chess.Board(position.fen)
        for uci in [position.solution_uci, *position.continuation_line]:
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves
            board.push(move)

    def test_reconstruction_refuses_a_ply_without_a_stored_best_move(self) -> None:
        from argus.training.replay import build_reconstruction

        rows = self._rows()
        rows[3]["best_move_uci"] = None
        positions, refusals = build_reconstruction(
            game_id="g1", rows=rows, player_color="white"
        )
        assert any("no best move" in entry["reason"] for entry in refusals)
        # A refusal is a refusal: it never becomes an exercise with a gap in it.
        # Repeated plies are only dropped from the *end*, so every emitted line
        # still starts with the opponent's reply and has enough solver turns.
        for position in positions:
            assert position.replay_context["solver_moves"] >= 2

    def test_reconstruction_never_grades_a_move_the_engine_disliked(self) -> None:
        from argus.training.replay import build_reconstruction

        # A bad white move at ply 5 (index 4) truncates any line through it.
        rows = self._rows(alternate={4: ("f1c4", 400)})
        positions, _ = build_reconstruction(
            game_id="g1", rows=rows, player_color="white"
        )
        for position in positions:
            line = [position.solution_uci, *position.continuation_line]
            assert "f1b5" not in line
