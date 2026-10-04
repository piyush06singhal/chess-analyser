"""Phase 7 performance measurement (spec §47).

The harness's job is to report measured numbers, so the tests insist on the
distinction that matters: a real measurement is a positive duration, and *no*
measurement is reported as absent rather than as zero.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.ai_agent.core.context import AgentContext  # noqa: E402
from argus.ai_agent.core.loop import CoachingAgent  # noqa: E402
from argus.ai_agent.performance import (  # noqa: E402
    PerformanceCase,
    PerformanceReport,
    measure_suite,
    measure_turn,
    percentile,
)
from argus.ai_agent.tools import AgentProviders, build_agent_toolbox  # noqa: E402

GAME_ID = "189d51ba-7404-4e6c-91b2-39119c103132"


def _agent() -> CoachingAgent:
    return CoachingAgent(build_agent_toolbox(AgentProviders()))


def test_percentile_matches_a_known_series():
    """Nearest-rank, so every reported value is one that actually occurred."""
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert percentile(values, 0) == 10.0
    assert percentile(values, 50) == 30.0
    assert percentile(values, 80) == 40.0
    assert percentile(values, 100) == 50.0


def test_percentile_of_nothing_is_none_not_zero():
    """A zero percentile would claim a measurement that never happened."""
    assert percentile([], 50) is None


def test_an_empty_report_reports_absence_not_zero():
    report = PerformanceReport(label="empty")
    assert report.count == 0
    assert report.mean_ms is None
    assert report.p50_ms is None
    assert report.p95_ms is None
    assert report.max_ms is None
    assert report.deterministic_share is None
    assert "nothing measured" in report.summary()


def test_a_measured_turn_records_a_real_positive_duration():
    measurement = measure_turn(
        _agent(), "Can you predict my win probability?", context=AgentContext()
    )
    assert measurement.total_ms > 0
    assert measurement.deterministic is True
    assert measurement.validation_passed is True
    # The prediction question is a deterministic fast path, so the turn succeeded
    # without a model call rather than failing.
    assert measurement.status == "ok"
    assert measurement.tool_calls >= 1
    # Stage timings come from the trace, so planning is accounted for separately.
    assert "plan" in measurement.stage_ms


def test_a_suite_aggregates_measured_turns_only():
    cases = [
        PerformanceCase(
            label="prediction",
            question="Can you predict my win probability?",
            context_factory=AgentContext,
        ),
        PerformanceCase(
            label="player_weakness",
            question="What is my biggest weakness?",
            context_factory=AgentContext,
        ),
    ]
    report = measure_suite(_agent, cases, label="no-llm")
    assert report.count == 2
    assert report.mean_ms is not None and report.mean_ms > 0
    assert report.p50_ms is not None
    assert report.p95_ms is not None
    # Every turn here is deterministic (no provider configured).
    assert report.deterministic_share == 1.0
    assert report.wall_clock_ms >= report.mean_ms
    assert report.failed_tool_calls == 0
    assert "no-llm" in report.summary()


def test_repeat_multiplies_the_measurements_without_sharing_state():
    report = measure_suite(
        _agent,
        [PerformanceCase(label="general", question="hello", context_factory=AgentContext)],
        repeat=3,
    )
    assert report.count == 3
    # Each turn built a fresh agent, so nothing was cached between them.
    assert len({id(item) for item in report.measurements}) == 3


def test_the_report_serialises_without_a_custom_encoder():
    import json

    report = measure_suite(
        _agent,
        [PerformanceCase(label="general", question="hello", context_factory=AgentContext)],
    )
    json.dumps(report.to_dict())


def test_tool_calls_are_counted_from_the_real_trace():
    cases = [
        PerformanceCase(
            label="move_question",
            question="Why was this move bad?",
            context_factory=lambda: AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
    ]
    report = measure_suite(_agent, cases)
    total = report.tool_calls + report.failed_tool_calls
    assert total >= 0
    # With no provider every tool that cannot run is a recorded absence rather than
    # a call, so the tool-call count must not be invented.
    assert report.tool_calls == sum(item.tool_calls for item in report.measurements)


def test_stage_totals_sum_the_per_turn_stages():
    report = measure_suite(
        _agent,
        [
            PerformanceCase(label="a", question="hello", context_factory=AgentContext),
            PerformanceCase(label="b", question="hello", context_factory=AgentContext),
        ],
    )
    totals = report.stage_totals()
    assert "plan" in totals
    # Planning is measured in microseconds and the trace rounds to 0.1 ms, so the
    # honest assertion is that the stage is accounted for — not that it is large.
    assert totals["plan"] >= 0
    # Stage timings are a subset of the wall clock: they can never exceed the turn.
    assert sum(totals.values()) <= sum(item.total_ms for item in report.measurements)
