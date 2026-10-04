"""Performance measurement for agent turns (spec §47).

An agent's latency is the sum of things it chose to do: how many tools it called,
whether any of them searched with the engine, and whether it consulted a language
model at all. So the useful measurement is not "how fast is the agent" but "where did
this turn's milliseconds go" — which is exactly what the per-turn trace already
records (``timings_ms``), and what this module aggregates.

Two rules, both about honesty:

**Measure, never estimate.** Every number here comes from
:func:`time.perf_counter` around a real turn. When there is nothing to measure the
report says so (``None``, not ``0``) — a 0 ms percentile is a lie about a run that
did not happen.

**Separate the deterministic path.** A deterministic turn makes no model call, so
averaging it together with a generated turn produces a number that describes neither.
The report therefore breaks out the deterministic share and the per-stage totals, so
a reader can see that the no-LLM path is near-instant and the model call is the cost
that matters.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.core.loop import CoachingAgent
from argus.ai_agent.memory.conversation import ConversationMemory
from argus.shared.stats import percentile


@dataclass
class TurnMeasurement:
    """One measured turn."""

    question: str
    total_ms: float
    stage_ms: dict[str, float] = field(default_factory=dict)
    tool_calls: int = 0
    failed_tool_calls: int = 0
    deterministic: bool = True
    validation_passed: bool = True
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "total_ms": round(self.total_ms, 1),
            "stage_ms": {key: round(value, 1) for key, value in self.stage_ms.items()},
            "tool_calls": self.tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "deterministic": self.deterministic,
            "validation_passed": self.validation_passed,
            "status": self.status,
        }


def measure_turn(
    agent: CoachingAgent,
    question: str,
    *,
    context: AgentContext | None = None,
    memory: ConversationMemory | None = None,
) -> TurnMeasurement:
    """Time one real turn, using the trace's own stage timings."""
    started = time.perf_counter()
    answer = agent.ask(question, context=context, memory=memory)
    elapsed_ms = (time.perf_counter() - started) * 1000
    trace = answer.trace or {}
    return TurnMeasurement(
        question=question,
        total_ms=elapsed_ms,
        stage_ms={
            str(key): float(value) for key, value in (trace.get("timings_ms") or {}).items()
        },
        tool_calls=int(trace.get("tool_count") or 0),
        failed_tool_calls=int(trace.get("failed_tool_count") or 0),
        deterministic=bool(answer.deterministic),
        validation_passed=bool(answer.validation.passed),
        status=str(trace.get("status") or "ok"),
    )


class PerformanceReport(BaseModel):
    """An aggregate of measured turns."""

    label: str = "agent"
    measurements: list[TurnMeasurement] = Field(default_factory=list)
    #: Sum of the wall-clock time of every measured turn, for context on the sample.
    wall_clock_ms: float = 0.0

    # --- aggregates (None when there is nothing to measure) -------------------

    @property
    def count(self) -> int:
        return len(self.measurements)

    @property
    def mean_ms(self) -> float | None:
        if not self.measurements:
            return None
        return sum(item.total_ms for item in self.measurements) / len(self.measurements)

    @property
    def p50_ms(self) -> float | None:
        return percentile([item.total_ms for item in self.measurements], 50)

    @property
    def p95_ms(self) -> float | None:
        return percentile([item.total_ms for item in self.measurements], 95)

    @property
    def max_ms(self) -> float | None:
        values = [item.total_ms for item in self.measurements]
        return max(values) if values else None

    @property
    def tool_calls(self) -> int:
        return sum(item.tool_calls for item in self.measurements)

    @property
    def failed_tool_calls(self) -> int:
        return sum(item.failed_tool_calls for item in self.measurements)

    @property
    def deterministic_share(self) -> float | None:
        """Fraction of turns answered with no model call."""
        if not self.measurements:
            return None
        return sum(1 for item in self.measurements if item.deterministic) / len(self.measurements)

    def stage_totals(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for item in self.measurements:
            for stage, value in item.stage_ms.items():
                totals[stage] = round(totals.get(stage, 0.0) + value, 1)
        return totals

    def summary(self) -> str:
        if not self.measurements:
            return f"{self.label}: nothing measured (0 turns)."
        return (
            f"{self.label}: {self.count} turn(s) · mean {self.mean_ms:.0f} ms · "
            f"p50 {self.p50_ms:.0f} ms · p95 {self.p95_ms:.0f} ms · "
            f"max {self.max_ms:.0f} ms · {self.tool_calls} tool call(s) "
            f"({self.failed_tool_calls} failed) · "
            f"{self.deterministic_share:.0%} deterministic · "
            f"stages {self.stage_totals()}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "count": self.count,
            "mean_ms": self.mean_ms,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "max_ms": self.max_ms,
            "tool_calls": self.tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "deterministic_share": self.deterministic_share,
            "wall_clock_ms": round(self.wall_clock_ms, 1),
            "stage_totals_ms": self.stage_totals(),
            "turns": [item.to_dict() for item in self.measurements],
        }


#: A scenario the harness can measure: a label, a question, and how to build the
#: context, so one report can cover the shapes of turn the product actually serves.
@dataclass
class PerformanceCase:
    label: str
    question: str
    context_factory: Callable[[], AgentContext] | None = None


def measure_suite(
    agent_factory: Callable[[], CoachingAgent],
    cases: Iterable[PerformanceCase],
    *,
    label: str = "agent",
    repeat: int = 1,
) -> PerformanceReport:
    """Measure one turn per case, ``repeat`` times, and aggregate.

    A fresh agent is built per turn so no measurement inherits state (a cached tool
    result) from the previous one — which is what makes these numbers comparable.
    """
    report = PerformanceReport(label=label)
    started = time.perf_counter()
    for case in cases:
        for _ in range(max(1, repeat)):
            context = case.context_factory() if case.context_factory else None
            measurement = measure_turn(agent_factory(), case.question, context=context)
            report.measurements.append(measurement)
    report.wall_clock_ms = (time.perf_counter() - started) * 1000
    return report


__all__ = [
    "PerformanceCase",
    "PerformanceReport",
    "TurnMeasurement",
    "measure_suite",
    "measure_turn",
    "percentile",
]
