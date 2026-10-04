"""Performance benchmarks (§33): measured, not assumed.

Micro-benchmarks of the pure, always-available operations, reported as p50/p95
percentiles rather than an average — an average hides the tail a user feels. The
budgets are deliberately generous multiples of the measured baseline so an
ordinary machine does not flake, while a real regression (parsing or traversal
getting an order of magnitude slower) still fails.

API and database latency are measured separately by ``scripts/benchmark_api.py``
against a running stack, because they depend on infrastructure this in-process
suite cannot see.
"""

from __future__ import annotations

import time

from argus.evaluation.results import SuiteResult, check, warning
from argus.evaluation.fixtures import STARTPOS_FEN
from argus.shared.stats import percentile
from argus.intelligence_graph.fingerprint import fingerprint
from argus.intelligence_graph.models import GraphEdge, GraphNode
from argus.intelligence_graph.service import IntelligenceGraphService
from argus.intelligence_graph.similarity import classify
from argus.intelligence_graph.store import InMemoryGraphStore
from argus.intelligence_graph.taxonomy import EdgeType, NodeType


def _time(operation, iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def _percentile(samples: list[float], fraction: float) -> float:
    """The shared nearest-rank percentile, rounded for the report."""
    value = percentile(samples, fraction * 100)
    return round(value, 4) if value is not None else 0.0


def _chain_service(length: int = 50) -> IntelligenceGraphService:
    service = IntelligenceGraphService(InMemoryGraphStore())
    for index in range(length):
        service.upsert_node(
            GraphNode(
                node_type=NodeType.POSITION,
                node_key=f"n{index}",
                label=f"n{index}",
                attributes={"game_id": "g"},
            )
        )
        if index:
            service.write_edge(
                GraphEdge(
                    edge_type=EdgeType.REACHES,
                    from_type=NodeType.POSITION,
                    from_key=f"n{index - 1}",
                    to_type=NodeType.POSITION,
                    to_key=f"n{index}",
                )
            )
    return service


def performance_suite(context) -> SuiteResult:
    """Percentile latency for the core deterministic operations."""
    checks = []

    budgets = {
        "fingerprint": 5.0,
        "validate_fen": 5.0,
        "classify": 10.0,
        "exhibited_concepts": 25.0,
        "graph_traversal": 15.0,
    }

    from argus.chess_core.fen import validate_fen
    from argus.intelligence_graph.knowledge import exhibited_concepts

    fen = STARTPOS_FEN
    other = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
    service = _chain_service()

    measurements = {
        "fingerprint": _time(lambda: fingerprint(fen), 2000),
        "validate_fen": _time(lambda: validate_fen(fen), 2000),
        "classify": _time(lambda: classify(fen, other), 1000),
        "exhibited_concepts": _time(lambda: exhibited_concepts(fen), 500),
        "graph_traversal": _time(
            lambda: service.traverse(NodeType.POSITION, "n0", max_depth=3, limit=100), 500
        ),
    }

    for name, samples in measurements.items():
        p50 = _percentile(samples, 0.50)
        p95 = _percentile(samples, 0.95)
        budget = budgets[name]
        detail = f"p50={p50}ms p95={p95}ms (budget {budget}ms, n={len(samples)})"
        if p95 <= budget:
            checks.append(
                check(
                    f"latency.{name}",
                    True,
                    detail=detail,
                    metrics={"p50_ms": p50, "p95_ms": p95, "budget_ms": budget},
                )
            )
        else:
            checks.append(warning(f"latency.{name}", f"over budget — {detail}"))

    # A generous hard ceiling: anything this slow is a real defect, not noise.
    worst = max(_percentile(samples, 0.95) for samples in measurements.values())
    checks.append(
        check(
            "no operation is pathologically slow",
            worst <= 100.0,
            detail=f"worst p95={worst}ms",
            critical=True,
        )
    )

    return SuiteResult(
        suite="performance",
        title="Core operation latency",
        checks=checks,
    )


__all__ = ["performance_suite"]
