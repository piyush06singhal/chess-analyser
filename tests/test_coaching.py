"""Phase 11 unit tests: the coaching layer's honesty rules.

The coaching package composes measured data into context, priority, debriefs and
a feed. These tests pin the properties that make that safe:

* the context contains only what was passed, and lists what is missing;
* a mode change actually changes exposure and tooling;
* a priority factor is dropped rather than guessed, and evidence caps promotion;
* a debrief section with no data says why instead of padding;
* a feed card always names its evidence and links to a real page;
* "what should I work on?" refuses when there is nothing measured to point at.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.coaching import (  # noqa: E402
    CoachMode,
    InsightCandidate,
    InsightPriority,
    Situation,
    assemble_context,
    build_debrief,
    build_feed,
    build_training_plan,
    context_brief,
    explain_method,
    feed_method,
    infer_situation,
    prioritize,
    resolve_mode,
    score_insight,
)
from argus.coaching.feed import answer_focus  # noqa: E402

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


class TestCoachContext:
    def test_situation_is_inferred_from_what_is_present(self) -> None:
        training, _ = infer_situation(training_position_id=5)
        assert training is Situation.TRAINING
        live, _ = infer_situation(game_id="g1", game_is_analysed=False)
        assert live is Situation.LIVE_GAME
        review, _ = infer_situation(game_id="g1", game_is_analysed=True)
        assert review is Situation.GAME_REVIEW
        prep, _ = infer_situation(opponent_id=3)
        assert prep is Situation.OPPONENT_PREPARATION
        opening, _ = infer_situation(fen="8/8/8/8/8/8/8/K6k w - - 0 1", phase="opening")
        assert opening is Situation.OPENING_STUDY
        endgame, _ = infer_situation(fen="8/8/8/8/8/8/8/K6k w - - 0 1", phase="endgame")
        assert endgame is Situation.ENDGAME_STUDY
        general, notes = infer_situation()
        assert general is Situation.GENERAL_COACHING
        assert notes  # the reasoning is reported, not hidden

    def test_a_training_session_outranks_an_open_game(self) -> None:
        # Someone mid-exercise must not be silently moved into game review.
        situation, _ = infer_situation(game_id="g1", game_is_analysed=True, training_position_id=9)
        assert situation is Situation.TRAINING

    def test_mode_defaults_to_the_situation_and_can_be_overridden(self) -> None:
        mode, reason = resolve_mode(Situation.ENDGAME_STUDY)
        assert mode is CoachMode.ENDGAME_COACH
        assert "default for situation" in reason
        explicit, reason = resolve_mode(Situation.ENDGAME_STUDY, requested=CoachMode.ANALYST)
        assert explicit is CoachMode.ANALYST
        assert "explicitly requested" in reason

    def test_context_records_gaps_instead_of_inventing_values(self) -> None:
        context = assemble_context(
            user_id=1,
            fen="8/8/8/8/8/8/8/K6k w - - 0 1",
            phase="endgame",
            now=NOW,
        )
        assert context.player_profile is None
        assert any("player profile" in gap for gap in context.gaps)
        assert context.assembled_at is None or context.assembled_at == NOW
        assert context.evidence_keys() == ["current_fen"]

    def test_a_populated_context_lists_its_evidence(self) -> None:
        context = assemble_context(
            user_id=1,
            game_id="g1",
            game={"game_id": "g1"},
            game_is_analysed=True,
            player_profile={"insights": []},
            training_history={"attempts_total": 3},
            now=NOW,
        )
        assert set(context.evidence_keys()) >= {"current_game_id", "player_profile", "training_history"}
        brief = context_brief(context)
        # A game in review leads with the game, not with the engine.
        assert brief["lead_with"] == "current_game"

    def test_modes_change_exposure_and_tooling(self) -> None:
        beginner = assemble_context(requested_mode=CoachMode.BEGINNER, now=NOW)
        advanced = assemble_context(requested_mode=CoachMode.ADVANCED, now=NOW)
        assert beginner.profile.expose_principal_variation is False
        assert advanced.profile.expose_principal_variation is True
        assert "engine" not in beginner.profile.allowed_tool_families
        assert "engine" in advanced.profile.allowed_tool_families
        # A training mode may not consult the engine at all: no solution hunting.
        training = assemble_context(
            requested_mode=CoachMode.TRAINING, training_position={"id": 4}, now=NOW
        )
        assert "scenario" not in training.profile.allowed_tool_families

    def test_the_brief_never_carries_more_than_the_evidence(self) -> None:
        context = assemble_context(user_id=1, now=NOW)
        brief = context_brief(context)
        assert brief["evidence_available"] == []
        assert brief["gaps"]
        assert brief["exposure"] == {
            "evaluation": True,
            "candidate_moves": True,
            "principal_variation": False,
            "statistics": True,
        }


# ---------------------------------------------------------------------------
# prioritisation
# ---------------------------------------------------------------------------


class TestPrioritisation:
    def test_factors_are_only_used_when_measured(self) -> None:
        scored = score_insight(
            InsightCandidate(
                key="k",
                title="t",
                statement="s",
                source="player_profile",
                occurrences=3,
                # No games, no denominator, no last_seen, never trained.
            ),
            now=NOW,
        )
        assert "recurrence" in scored.factors
        assert set(scored.missing_factors) >= {"frequency", "recency", "training_need", "confidence"}

    def test_nothing_measurable_means_nothing_claimed(self) -> None:
        scored = score_insight(
            InsightCandidate(key="k", title="t", statement="s", source="player_profile"),
            now=NOW,
        )
        assert scored.score == 0.0
        assert scored.priority is InsightPriority.LOW
        assert scored.capped_by == "no measurable factor was available"

    def test_severity_comes_from_centipawns_when_measured(self) -> None:
        candidate = InsightCandidate(
            key="k", title="t", statement="s", source="game_report",
            value=300.0, unit="cp",
        )
        scored = score_insight(candidate, now=NOW)
        assert scored.factors["severity"] == 1.0
        # A label is only used when there is no measurement.
        labelled = score_insight(
            InsightCandidate(key="k2", title="t", statement="s", source="x", severity="medium"),
            now=NOW,
        )
        assert labelled.factors["severity"] == 0.6

    def test_recency_decays_in_documented_bands(self) -> None:
        for days, expected in ((1, 1.0), (20, 0.7), (60, 0.4), (200, 0.2), (800, 0.1)):
            scored = score_insight(
                InsightCandidate(
                    key=f"k{days}", title="t", statement="s", source="x",
                    occurrences=1, last_seen=NOW - timedelta(days=days),
                ),
                now=NOW,
            )
            assert scored.factors["recency"] == expected, days

    def test_insufficient_coverage_caps_the_priority(self) -> None:
        strong = InsightCandidate(
            key="k", title="t", statement="s", source="player_profile",
            claim_level="tendency", coverage="insufficient",
            games=40, occurrences=20, total_events=40, value=300, unit="cp",
            last_seen=NOW, severity="high",
        )
        scored = score_insight(strong, now=NOW)
        assert scored.score >= 78  # it *scored* like a critical finding
        assert scored.priority is InsightPriority.LOW  # but the evidence refuses
        assert "insufficient" in (scored.capped_by or "")

    def test_an_observation_can_never_be_critical(self) -> None:
        observation = InsightCandidate(
            key="k", title="t", statement="s", source="game_report",
            claim_level="observation", coverage="robust",
            games=40, occurrences=20, total_events=40, value=300, unit="cp", last_seen=NOW,
        )
        scored = score_insight(observation, now=NOW)
        assert scored.priority is not InsightPriority.CRITICAL
        assert "observation" in (scored.capped_by or "")

    def test_training_history_moves_a_pattern_up(self) -> None:
        base = dict(
            key="k", title="t", statement="s", source="player_profile",
            claim_level="pattern", coverage="robust",
            games=20, occurrences=4, total_events=20, severity="high",
            last_seen=NOW, training_attempts=5, training_accuracy=0.2,
        )
        with_history = score_insight(InsightCandidate(**base), now=NOW)
        without = score_insight(
            InsightCandidate(**{**base, "training_attempts": None, "training_accuracy": None}),
            now=NOW,
        )
        assert "training_need" in with_history.factors
        assert "training_need" in without.missing_factors
        assert with_history.score > without.score

    def test_ordering_is_deterministic_and_dismissals_are_honoured(self) -> None:
        candidates = [
            InsightCandidate(
                key=f"k{index}", title="t", statement="s", source="x",
                occurrences=index, severity="high", claim_level="pattern",
                coverage="robust", games=20, last_seen=NOW,
            )
            for index in range(1, 4)
        ]
        first = [item.key for item in prioritize(candidates, now=NOW)]
        second = [item.key for item in prioritize(candidates, now=NOW)]
        assert first == second == ["k3", "k2", "k1"]
        dismissed = [item.key for item in prioritize(candidates, now=NOW, dismissed={"k3"})]
        assert dismissed == ["k2", "k1"]

    def test_the_methodology_is_published(self) -> None:
        method = explain_method()
        assert method["factor_weights"] and sum(method["factor_weights"].values()) > 0
        assert method["bands"]["critical"] > method["bands"]["high"] > method["bands"]["normal"]
        assert any("not the player's ability" in rule for rule in method["rules"])


# ---------------------------------------------------------------------------
# debrief
# ---------------------------------------------------------------------------

REPORT = {
    "generated_at": "2026-09-30T10:00:00Z",
    "summary": {
        "facts": [
            {"key": "result", "statement": "White won.", "source": "argus_derived_feature"},
            {"key": "opening", "statement": "The game opened as a Queen's Pawn Game.", "source": "argus_derived_feature"},
            {"key": "flagged_moves_white", "statement": "White had 2 blunders.", "evidence": {"counts": {"blunder": 2}}},
        ]
    },
    "turning_points": [
        {
            "ply": 20,
            "move_number": 10,
            "san": "Qd6",
            "side": "black",
            "severity": "high",
            "statement": "Black's evaluation fell 474cp after 10... Qd6.",
            "evidence": {"type": "evaluation_swing", "swing_cp": -474},
        }
    ],
    "critical_moments": {
        "engine_critical_moments": [
            {
                "ply": 20,
                "move_number": 10,
                "side": "black",
                "severity": "high",
                "statement": "Engine classified the move a blunder.",
                "evidence": {"classification": "blunder", "severity_score": 474, "swing_cp": -474},
            }
        ]
    },
}


class TestGameDebrief:
    def test_sections_read_the_stored_report(self) -> None:
        debrief = build_debrief(
            game_id="g1", report=REPORT, user_id=1, side="white", result="1-0", now=NOW
        )
        assert debrief.summary.available and len(debrief.summary.observations) == 3
        assert debrief.critical_moments.available
        # Both stored containers describe ply 20: the turning point and the engine's
        # critical position. Both are listed; neither replaces the other.
        assert debrief.critical_moments.sample_size == 2
        assert debrief.critical_moments.observations[0]["san"] == "Qd6"
        decisions = debrief.biggest_decisions
        assert decisions.available and decisions.observations[0]["classification"] == "blunder"
        assert decisions.observations[0]["loss_cp"] == 474
        assert debrief.steps[0] == "summary"
        assert debrief.methodology_version

    def test_missing_san_is_filled_from_the_game_not_invented(self) -> None:
        # The report stores the moment without a move name; the game's own ply does.
        report = {
            "summary": {"facts": []},
            "critical_moments": {
                "engine_critical_moments": [
                    {"ply": 20, "side": "black", "evidence": {"classification": "blunder"}}
                ]
            },
        }
        debrief = build_debrief(
            game_id="g1", report=report, san_by_ply={20: "Qd6"}, now=NOW
        )
        assert debrief.critical_moments.observations[0]["san"] == "Qd6"
        # Without that lookup, the field is reported absent rather than guessed.
        bare = build_debrief(game_id="g1", report=report, now=NOW)
        assert bare.critical_moments.observations[0]["san"] is None

    def test_a_game_without_a_report_says_so(self) -> None:
        debrief = build_debrief(game_id="g1", report=None, now=NOW)
        assert debrief.summary.available is False
        assert "no Game Intelligence report" in (debrief.summary.reason or "") or debrief.gaps
        assert debrief.gaps
        # Every section still exists, so the UI renders a reason rather than a hole.
        assert all(section.reason or section.available for section in debrief.sections())

    def test_patterns_need_the_repetition_threshold(self) -> None:
        profile = {
            "coverage": "moderate",
            "insights": [
                {
                    "id": "p1", "title": "King safety", "statement": "You allow checks.",
                    "category": "recurring_pattern", "claim_level": "pattern",
                    "games": 12, "occurrences": 5, "severity": "high",
                },
                {
                    "id": "p2", "title": "One-off", "statement": "Seen once.",
                    "category": "tactical_pattern", "claim_level": "observation",
                    "games": 1, "occurrences": 1,
                },
            ],
        }
        debrief = build_debrief(game_id="g1", report=REPORT, player_profile=profile, now=NOW)
        assert debrief.recurring_patterns.available
        keys = [row["key"] for row in debrief.recurring_patterns.observations]
        assert keys == ["profile:p1"]  # the observation is not called recurring

    def test_no_patterns_is_reported_not_padded(self) -> None:
        debrief = build_debrief(game_id="g1", report=REPORT, player_profile={"insights": []}, now=NOW)
        assert debrief.recurring_patterns.available is False
        assert debrief.recurring_patterns.reason

    def test_counterfactuals_report_the_candidate_storage_gap(self) -> None:
        empty = build_debrief(
            game_id="g1",
            report=REPORT,
            counterfactual_summary={"turning_points": [{"ply": 5, "what_if_available": False}]},
            now=NOW,
        )
        assert empty.counterfactuals.available is False
        assert "what-if" in (empty.counterfactuals.reason or "")
        assert empty.counterfactuals.observations  # the moments are still listed

    def test_improvement_tracking_present_when_there_is_history(self) -> None:
        debrief = build_debrief(
            game_id="g1",
            report=REPORT,
            training_queue={"count": 2},
            training_recommendations={"training": {"attempts_total": 7}},
            now=NOW,
        )
        assert debrief.improvement_tracking.available
        assert {row["metric"] for row in debrief.improvement_tracking.observations} == {
            "due_reviews",
            "attempts_on_these_patterns",
        }
        assert "never as a claim that training caused it" in (
            debrief.improvement_tracking.reason or ""
        )


# ---------------------------------------------------------------------------
# feed / focus / plan
# ---------------------------------------------------------------------------

PROFILE = {
    "coverage": "moderate",
    "insights": [
        {
            "id": "king_safety", "title": "King safety", "statement": "You allow checks early.",
            "category": "recurring_pattern", "claim_level": "pattern", "coverage": "moderate",
            "games": 12, "occurrences": 6, "severity": "high",
            "evidence": [{"game_id": "g1", "ply": 20}],
        }
    ],
}


class TestCoachingFeed:
    def test_cards_name_their_evidence_and_link_to_real_pages(self) -> None:
        feed = build_feed(
            user_id=1,
            game_id="g1",
            game_report=REPORT,
            player_profile=PROFILE,
            training_progress={"library_size": 6, "attempts_total": 3, "due_count": 0},
            now=NOW,
        )
        cards = [card for section in feed.sections for card in section.cards]
        assert cards
        for card in cards:
            assert card.actions, card.key
            for action in card.actions:
                assert action.href.startswith("/")
            assert card.statement
        keys = [section.key for section in feed.sections]
        assert keys == [
            "recent_game",
            "important_mistakes",
            "recurring_patterns",
            "training_recommendations",
            "opponent_preparation",
            "opening_work",
            "progress",
        ]
        assert feed.counts["normal"] >= 1

    def test_empty_sections_explain_themselves(self) -> None:
        feed = build_feed(now=NOW)
        for section in feed.sections:
            if not section.cards:
                assert section.available is False
                assert section.reason
        assert feed.gaps

    def test_no_advice_without_a_denominator(self) -> None:
        # An empty feed must not contain the generic "practice tactics" filler.
        feed = build_feed(now=NOW)
        text = " ".join(card.statement for section in feed.sections for card in section.cards)
        assert "practice" not in text.lower()
        assert "study more" not in text.lower()

    def test_an_opponent_card_is_never_a_prediction(self) -> None:
        feed = build_feed(now=NOW, opponent_id=3, opponent_profile={"identity": {"display_name": "Rival"}, "insights": []})
        section = next(s for s in feed.sections if s.key == "opponent_preparation")
        assert section.available
        statement = section.cards[0].statement.lower()
        assert "nothing is claimed" in statement
        assert "will play" not in statement

    def test_feed_method_is_published(self) -> None:
        method = feed_method()
        assert method["sections"][0] == "recent_game"
        assert any("say why they are empty" in rule for rule in method["rules"])


class TestFocusAnswer:
    def test_focus_names_the_evidence_or_refuses(self) -> None:
        answer = answer_focus(player_profile=PROFILE, now=NOW)
        assert answer.status == "ok"
        assert answer.primary_focus is not None
        assert answer.primary_focus.key == "profile:king_safety"
        assert answer.evidence
        assert answer.affected_games == [{"game_id": "g1", "ply": 20}]
        assert answer.how_progress_will_be_measured

    def test_focus_refuses_without_measured_patterns(self) -> None:
        answer = answer_focus(player_profile=None, now=NOW)
        assert answer.status == "insufficient_evidence"
        assert answer.reason
        assert answer.primary_focus is None

    def test_progress_measure_names_a_metric_that_exists(self) -> None:
        answer = answer_focus(player_profile=PROFILE, now=NOW)
        measure = answer.how_progress_will_be_measured[0].lower()
        assert "centipawn loss" in measure or "occurrences" in measure


class TestTrainingPlan:
    def test_plan_is_derived_from_measured_focus(self) -> None:
        focus = prioritize(
            [
                InsightCandidate(
                    key="profile:king_safety", title="King safety", statement="s",
                    source="player_profile", category="recurring_pattern",
                    claim_level="pattern", coverage="moderate", games=12, occurrences=6,
                    severity="high", last_seen=NOW,
                )
            ],
            now=NOW,
        )
        plan = build_training_plan(player_id=1, focus=focus, now=NOW)
        assert plan.focus_areas and plan.focus_areas[0]["category"] == "recurring_pattern"
        assert plan.training_sessions[0]["kind"] == "targeted_session"
        assert plan.target_metrics[0]["measure"]
        assert plan.review_date == NOW + timedelta(days=7)

    def test_an_empty_plan_is_a_data_limitation(self) -> None:
        plan = build_training_plan(player_id=1, focus=[], now=NOW)
        assert plan.focus_areas == []
        assert any("data limitation" in note for note in plan.limitations)
