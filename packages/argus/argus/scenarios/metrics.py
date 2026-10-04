"""Phase 10 observability: counters and timings for decision intelligence.

The spec asks for specific things to be tracked, and the point of tracking them is
to be able to answer operational questions honestly:

``counterfactual_requests`` / ``comparison_requests``
    how much engine work the counterfactual surface is actually being asked for;
``engine_analysis_time_ms`` / ``scenario_generation_time_ms``
    where the time goes — engine search or our own assembly;
``cache_hit_rate``
    whether the cache is earning its memory;
``prediction_requests`` / ``prediction_rejections``
    how often a prediction was asked for and how often the honest answer was "no
    validated model";
``model_version_usage``
    which model actually answered, so a served prediction is attributable;
``training_from_scenario_count``
    whether counterfactual study is turning into practice;
``agent_tool_usage``
    which tools the agent reaches for, per tool.

Nothing here logs game content. The counters are aggregates: a count is not a
position, and a duration is not a move. Keeping that boundary is what makes the
metrics safe to expose on an endpoint.

The registry is in-process. That is a deliberate, documented limitation rather
than an oversight: it reports what *this* process has seen, and each response says
so. A shared store (Redis is already configured) is the natural next step and
would not change this interface.
"""

from __future__ import annotations

import threading

#: Every counter this module can record. Declared so an empty registry still
#: reports the full set with zeros, instead of omitting what has not happened yet.
COUNTERS: tuple[str, ...] = (
    "counterfactual_requests",
    "comparison_requests",
    "position_comparison_requests",
    "explorer_requests",
    "refusals_illegal_move",
    "refusals_unavailable",
    "prediction_requests",
    "prediction_rejections",
    "training_from_scenario_count",
    "scenario_persist_count",
)

#: Every timing series this module can record (milliseconds).
TIMINGS: tuple[str, ...] = (
    "engine_analysis_time_ms",
    "scenario_generation_time_ms",
)


class MetricsRegistry:
    """Thread-safe counters and timing series for one process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {name: 0 for name in COUNTERS}
        self._timings: dict[str, dict[str, float]] = {
            name: {"count": 0, "total_ms": 0.0, "max_ms": 0.0} for name in TIMINGS
        }
        self._tool_usage: dict[str, int] = {}
        self._model_usage: dict[str, int] = {}

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def observe_ms(self, name: str, duration_ms: float) -> None:
        with self._lock:
            series = self._timings.setdefault(
                name, {"count": 0, "total_ms": 0.0, "max_ms": 0.0}
            )
            series["count"] += 1
            series["total_ms"] += float(duration_ms)
            series["max_ms"] = max(series["max_ms"], float(duration_ms))

    def record_tool(self, tool_name: str) -> None:
        with self._lock:
            self._tool_usage[tool_name] = self._tool_usage.get(tool_name, 0) + 1

    def record_model(self, model_id: str) -> None:
        with self._lock:
            self._model_usage[model_id] = self._model_usage.get(model_id, 0) + 1

    def snapshot(self) -> dict:
        with self._lock:
            timings = {
                name: {
                    "count": series["count"],
                    "total_ms": round(series["total_ms"], 3),
                    "mean_ms": (
                        round(series["total_ms"] / series["count"], 3)
                        if series["count"]
                        else None
                    ),
                    "max_ms": round(series["max_ms"], 3),
                }
                for name, series in self._timings.items()
            }
            return {
                "counters": dict(self._counters),
                "timings": timings,
                "agent_tool_usage": dict(sorted(self._tool_usage.items())),
                "model_version_usage": dict(sorted(self._model_usage.items())),
                "scope": (
                    "This process only. Caissa does not log game content; a counter is a "
                    "count and a timing is a duration."
                ),
            }


#: The process-wide registry the API and the agent share.
REGISTRY = MetricsRegistry()


def snapshot() -> dict:
    """The current metrics, with cache statistics merged in by the caller."""
    return REGISTRY.snapshot()


__all__ = ["COUNTERS", "REGISTRY", "TIMINGS", "MetricsRegistry", "snapshot"]
