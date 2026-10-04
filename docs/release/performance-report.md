# Caissa — Performance & Load Report

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)
**Date:** 2026-10-04

All figures are measured on the running release candidate (a developer machine:
API container at `:8002`, PostgreSQL 17, Redis 7). They are local numbers, not a
production SLA; the point is that the budgets hold and the shapes are known.

## API latency — `scripts/benchmark_api.py`

20 rounds per endpoint:

| Endpoint | p50 (ms) | p95 (ms) | p99 (ms) | errors |
| --- | --- | --- | --- | --- |
| `GET /health` | 2.26 | 6.36 | 33.33 | 0 |
| `GET /api/games` | 3.89 | 5.47 | 7.21 | 0 |
| `GET /api/players` | 1.61 | 3.16 | 3.54 | 0 |
| `GET /api/graph/methodology` | 1.04 | 1.65 | 1.80 | 0 |
| `GET /api/graph/health` | 6.66 | 8.38 | 85.68 | 0 |
| `GET /api/graph/nodes` | 1.87 | 2.06 | 2.28 | 0 |
| `GET /api/graph/search` | 1.97 | 2.24 | 2.95 | 0 |

Written to `evaluation/reports/api-benchmark.json`.

## Database latency — `scripts/benchmark_db.py`

30 rounds per query against PostgreSQL 17:

| Query | p50 (ms) | p95 (ms) | max (ms) | plan |
| --- | --- | --- | --- | --- |
| `games.list_recent` | 0.54 | 2.16 | 20.75 | small tables only |
| `games.positions_by_game` | 1.01 | 3.77 | 5.28 | small tables only |
| `games.move_analyses_by_game` | 2.61 | 4.99 | 5.36 | small tables only |
| `players.profile_by_player` | 1.61 | 3.45 | 3.88 | small tables only |
| `training.positions_by_player` | 1.09 | 2.89 | 2.91 | small tables only |
| `training.attempts_by_player` | 0.28 | 0.47 | 2.39 | small tables only |
| `graph.nodes_by_type` | 0.46 | 1.80 | 3.64 | small tables only |
| `graph.edges_by_source` | 0.56 | 1.22 | 1.78 | small tables only |

The query plans are sequential scans, which is correct and fast at this data size
(hundreds of rows); the benchmarks name the query and record the plan so a growth
in size that changes the plan is visible later. Written to
`evaluation/reports/db-benchmark.json`.

## Live-chess load probe — `scripts/load_live.py`

6 concurrent games × 20 plies, 4 spectators:

| Measure | Value |
| --- | --- |
| Game creation p50 | 10 ms |
| Moves played | 120 in 0.69 s |
| Move latency | p50 26 ms · p95 41 ms · max 99 ms |
| State/sync read | p50 3 ms · p95 6 ms |
| WebSocket event latency | p50 26 ms · p95 41 ms (n=120) |
| Events delivered | 510 |
| Spectators attached | 4/4 |

**Result: PROBE PASSED — latencies within bound, every move accepted.**

## In-process operation budgets

The evaluation suite measures core operations directly (see the release gate
output): fingerprint p95 0.04 ms, `validate_fen` p95 0.02 ms, `classify` p95
0.14 ms, `exhibited_concepts` p95 0.04 ms, graph traversal p95 0.03 ms — every
one far inside its budget.

## Agent latency

`scripts/verify_agent.py` (5 turns, one with a real engine search): mean 537 ms,
p50 697 ms, p95 925 ms, max 925 ms. The engine search dominates; the agent's own
overhead is milliseconds.

## Frontend

`next build`: 24 routes, largest chunk 224 KB,
`.next/static` 1.9 MB.

## Web Vitals

**Field: UNKNOWN.** There is no production traffic, so no field LCP/CLS/INP
exists. The app is instrumented (`src/components/vitals.tsx`) but reports nothing
without an endpoint configured. Field numbers are reported as unknown rather than
invented.

**Lab: MEASURED.** `node scripts/measure-lab.mjs` performs a cold load of eight
routes against the local production build (Chromium, 1280×900): TTFB 7–29 ms, FCP
and LCP 68–212 ms, CLS 0.000–0.068, gzip document transfer 4.8–6.5 KB. Every route
is inside the "Good" thresholds. These are lab numbers and are labelled as such —
they are not a substitute for field data.

## Verdict

Budgets hold; the latency profile is dominated by the engine search, which is
expected. No pathological case was observed. Field performance is unmeasured and
stated as such.
