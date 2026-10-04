# Load, chaos, consistency and smoke testing

The tests that answer "does it hold up under load, failure and real data?" They
are separate from the unit suite because they need a running stack and real
concurrency.

## Load testing

| Script | Measures |
| --- | --- |
| `scripts/benchmark_api.py` | API throughput and latency for read routes |
| `scripts/benchmark_db.py` | database performance, with real query plans |
| `scripts/load_live.py` | live-game load (moves, sync, event fan-out) |

Run them against **staging**, not production. They report real numbers; a number
that looks bad is a finding, not a failure of the tool. The results feed the SLI
objectives in [observability.md](observability.md).

What they deliberately do **not** do: they do not claim a capacity Caissa has not
demonstrated. A benchmark on a laptop is not a production capacity plan.

## Chaos testing

The failure modes that matter, and how to exercise them safely:

* **Engine unavailable** — stop the engine (or point `ARGUS_STOCKFISH_PATH` at a
  missing binary). `/ready` must report `stockfish` blocking and the status must
  be `degraded`; analysis requests must fail with a real reason, never a
  fabricated evaluation.
* **Database unavailable** — stop Postgres. `/ready` must report `database`
  blocking; read routes must fail with a real reason, not an empty success.
* **Redis unavailable** — stop Redis. The API must degrade gracefully (Redis is
  optional); `/health` must report the real state.
* **Queue saturation** — flood the analysis queue past
  `ARGUS_ANALYSIS_QUEUE_LIMIT`. New requests must get a 503 with `Retry-After`,
  and the accepted work must still complete.
* **Process restart mid-analysis** — restart the API while a game is analysing.
  The run must be resumable and must keep its completed plies.

Each of these is a property with a test or a script; none is a claim that the
system is unbreakable. The point is that a failure is **honest** — it refuses
with a reason and does not corrupt data.

## Data consistency

```bash
python scripts/check_data_consistency.py
```

Checks that stored analysis still describes its game: analysis vs moves, ply
coverage, stale generations, stale derived profiles, orphans and training
provenance. It reports, never repairs — a database is not something a script
should silently "fix".

## Smoke testing

A smoke test is the shortest path that proves the deployment works end to end:

```bash
python scripts/verify_journeys.py
```

It walks the five user journeys — import a game, read the report, train the
weakness, prepare for the opponent, ask the coach — start to finish against a
live API and engine. If this passes on a deployment, the primary path works.

`python scripts/system_check.py` is the lighter companion: it checks each
dependency without exercising a journey.

## When to run these

| Test | Every push (CI) | Before a release | After a deploy |
| --- | --- | --- | --- |
| Unit suite | ✅ | ✅ | — |
| Evaluation framework | ✅ (engine skipped) | ✅ (full) | — |
| Load | — | ✅ on staging | — |
| Chaos | — | ✅ on staging | — |
| Consistency | — | ✅ on staging | ✅ on production |
| Smoke (journeys) | ✅ (browser job) | ✅ | ✅ |

CI runs the fast ones. The load and chaos runs are pre-release steps on staging,
because they need a real stack and real concurrency, and because a CI run that
takes too long is a CI run nobody reads.