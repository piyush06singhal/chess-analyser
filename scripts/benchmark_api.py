#!/usr/bin/env python
"""API latency benchmark against a running Caissa stack (Phase 14 §33/§34).

Records p50/p95/p99 latency and error rate per endpoint — percentiles, not an
average, because the tail is what a user feels. In-process micro-benchmarks
(`argus.evaluation.suites.performance`) cannot see the database or the network, so
end-to-end latency is measured here, against the real server.

Usage:

    python scripts/benchmark_api.py                       # default 127.0.0.1:8002
    python scripts/benchmark_api.py --base-url http://localhost:8002 --rounds 30
    python scripts/benchmark_api.py --json evaluation/reports/api-benchmark.json

The endpoints are read-only GETs so the benchmark never mutates state.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "argus"))

from argus.shared.stats import percentile  # noqa: E402  (needs the path above)

#: (label, path). Read-only. A 404 is a result, not an error, unless the endpoint
#: is expected to exist; the script reports status codes honestly.
ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("health", "/health"),
    ("games.list", "/api/games"),
    ("players.list", "/api/players"),
    ("graph.method", "/api/graph/method"),
    ("graph.health", "/api/graph/health"),
    ("graph.nodes", "/api/graph/nodes/game?limit=25"),
    ("graph.search", "/api/graph/search?kind=games&limit=25"),
)


def _call(url: str, timeout: float) -> tuple[int, float]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        exc.read()
        status = exc.code
    except Exception:  # noqa: BLE001 — a connection failure is a failed sample
        status = 0
    return status, (time.perf_counter() - started) * 1000.0


def _percentile(samples: list[float], fraction: float) -> float:
    """The shared nearest-rank percentile, rounded for the report."""
    value = percentile(samples, fraction * 100)
    return round(value, 2) if value is not None else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Caissa API latency.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", default=str(REPO_ROOT / "evaluation" / "reports" / "api-benchmark.json"))
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    results: dict[str, dict] = {}
    for label, path in ENDPOINTS:
        url = f"{base}{path}"
        durations: list[float] = []
        statuses: list[int] = []
        for _ in range(args.rounds):
            status, elapsed = _call(url, args.timeout)
            statuses.append(status)
            if status < 400:
                durations.append(elapsed)
        if not durations:
            results[label] = {
                "path": path,
                "samples": 0,
                "error_rate": 1.0,
                "statuses": sorted(set(statuses)),
            }
            continue
        errors = sum(1 for status in statuses if status >= 400 or status == 0)
        results[label] = {
            "path": path,
            "samples": len(statuses),
            "ok_samples": len(durations),
            "p50_ms": _percentile(durations, 0.50),
            "p95_ms": _percentile(durations, 0.95),
            "p99_ms": _percentile(durations, 0.99),
            "mean_ms": round(statistics.fmean(durations), 2),
            "error_rate": round(errors / len(statuses), 4),
            "statuses": sorted(set(statuses)),
        }

    print(f"API benchmark — {base} ({args.rounds} rounds/endpoint)\n")
    print(f"{'endpoint':<16}{'p50':>9}{'p95':>9}{'p99':>9}{'err':>8}  status")
    for label, row in results.items():
        if row.get("samples") == 0 or row.get("ok_samples", 0) == 0:
            print(f"{label:<16}{'--':>9}{'--':>9}{'--':>9}{row['error_rate']:>8.2f}  {row['statuses']}")
            continue
        print(
            f"{label:<16}{row['p50_ms']:>9}{row['p95_ms']:>9}{row['p99_ms']:>9}"
            f"{row['error_rate']:>8.2f}  {row['statuses']}"
        )

    payload = {"base_url": base, "rounds": args.rounds, "endpoints": results}
    target = Path(args.json)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2))
    print(f"\nwritten: {target}")

    # Exit non-zero if every endpoint failed — the stack is not reachable.
    reachable = any(row.get("ok_samples", 0) > 0 for row in results.values())
    return 0 if reachable else 1


if __name__ == "__main__":
    raise SystemExit(main())
