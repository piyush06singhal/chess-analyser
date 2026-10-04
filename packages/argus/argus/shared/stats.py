"""Percentile helpers shared by every Caissa latency report.

There is exactly one definition of a percentile in Caissa, so two benchmarks can
never disagree about what "p95" means — before this existed, the agent
performance report, the evaluation performance suite, the API benchmark and the
live load probe each carried their own copy, and they did not all pick the same
sample.

The method is **nearest-rank**: the returned value is always one of the samples.
With the handful of measurements these tools take, an interpolated percentile
would report a latency that never occurred, which is exactly the kind of invented
number Caissa refuses elsewhere.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


def percentile(values: Iterable[float], rank: float) -> float | None:
    """The ``rank``-th percentile (0–100) by nearest-rank, or ``None`` when empty.

    ``None`` for an empty sample is deliberate: a percentile of nothing is not
    zero, it is unknown, and callers must say so rather than print ``0``.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    if rank <= 0:
        return ordered[0]
    if rank >= 100:
        return ordered[-1]
    index = max(0, math.ceil(rank / 100 * len(ordered)) - 1)
    return ordered[index]


def latency_summary(
    values: Iterable[float], *, digits: int = 2
) -> dict[str, float | int | None]:
    """p50/p95/p99, mean and max for a set of millisecond samples.

    Every field is ``None`` when there is nothing to measure, so a caller cannot
    accidentally render an empty sample as a fast one.
    """
    samples = list(values)
    if not samples:
        return {"count": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}
    p50 = percentile(samples, 50)
    p95 = percentile(samples, 95)
    p99 = percentile(samples, 99)
    return {
        "count": len(samples),
        "p50_ms": round(p50, digits) if p50 is not None else None,
        "p95_ms": round(p95, digits) if p95 is not None else None,
        "p99_ms": round(p99, digits) if p99 is not None else None,
        "max_ms": round(max(samples), digits),
    }


__all__ = ["latency_summary", "percentile"]
