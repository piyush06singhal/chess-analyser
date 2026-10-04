# Reliability model

What keeps Caissa up, and what each bound is. Every limit below is enforced in
code and reported by an endpoint, so it can be checked rather than assumed.

## The honesty contract, operationally

A reliability failure in Caissa must never look like success. That shapes every
decision here:

* a full queue is a **503 with a reason** — never a dropped task;
* a duplicate analysis is a **409** — never a second silent run over the same rows;
* a rate limit is a **429 with `Retry-After`** — never a silently truncated batch;
* a disabled feature is a **404** — never a route that half-works.

## Background analysis jobs

Analysis runs after the response, on a worker thread (`AnalysisJobRunner`), not
on a distributed queue. Its lifecycle states:

| State | Meaning |
| --- | --- |
| `running` | holding an engine slot, writing per-move rows incrementally |
| `completed` | every ply analysed; derived data and the run record are rebuilt |
| `cancelled` | stopped cooperatively; completed plies are kept; the game returns to `paused` |
| `failed` | the run recorded its error and the game is marked `failed` |

Properties the runner guarantees:

* **Progress is observable immediately.** The route marks the game `analyzing`
  before returning, so a client's first progress poll cannot race the worker.
* **Incremental persistence.** Each move is committed as it finishes, so an
  interrupted run keeps its completed plies.
* **Resume.** Already-analysed plies (same analysis version) are skipped.
* **Cancellation is cooperative.** The engine stops after the current position.
* **Idempotency.** The engine gate refuses a second admitted run for the same
  game (409), so re-clicking "analyze" cannot spawn a duplicate.

### The engine resource gate (§8)

Stockfish is one process shared by the whole API process. Unbounded analysis
requests would be a denial-of-service against Caissa itself, so admission is
bounded by `argus_api.services.resource_gate`:

* **Concurrency** (`ARGUS_ENGINE_MAX_CONCURRENCY`, default 2) — how many
  analyses may hold a slot. The engine's own lock still serialises the actual
  searches, so this is a *work-admission* bound, not a claim of parallel search.
* **Queue** (`ARGUS_ANALYSIS_QUEUE_LIMIT`, default 64) — how many further
  analyses may wait for a slot before new requests are refused with a 503 and a
  `Retry-After`.
* **Idempotency** — a game that already has an admitted run is a 409.

The gate's real state (`running`, `queued`, limits) is reported by `GET /ready`
under `checks.engine_capacity`, so a busy engine is visible rather than guessed.

## Rate limiting

Per-caller, fixed-window limits for expensive operations, in
`argus_api.rate_limit`:

| Bucket | Routes |
| --- | --- |
| `analysis` | position analysis, game analysis, per-game analyze |
| `import` | PGN paste, file upload (import), Chess.com/Lichess import |
| `coach` | `/api/coach/ask`, `/api/coach/chat` |
| `upload` | the PGN file upload route |
| `search` | graph search |

The caller id comes from the authenticated key (or `local` in open mode), never
from a client field — so one user cannot spend another's budget. Limits are
configuration; a limit of 0 disables a bucket. Exceeding a limit is a 429 with
`Retry-After`. The limiter is per process and bounded (expired windows are
evicted); the multi-process plan is below.

## Redis

Redis is **optional** and never a source of truth. When configured, `/health` and
`/ready` report its real connection state; when not, they say so. In production
compose it runs with a password, a bounded memory (`maxmemory`) and LRU
eviction, and with `FLUSHALL`/`CONFIG` renamed away — a cache needs none of them,
and an accidental flush should not be possible.

## Health and readiness

| Endpoint | Question |
| --- | --- |
| `GET /health` | is the process up, and what do its dependencies look like? |
| `GET /ready` | can this instance serve traffic right now? |
| `GET /health/ready` | alias of `/ready` |
| `GET /metrics` | Prometheus text exposition of this process |

`/ready` performs real probes: `database` and `stockfish` are **blocking**;
`redis`, `auth`, `engine_capacity`, `migrations`, `ml_registry` and
`ai_provider` are reported but do not block serving.

## Graceful shutdown

On shutdown the lifespan cancels the live-clock sweeper task, disposes the
engine and the database engine, and logs the stop. In-flight background analyses
finish their current commit; an interrupted run keeps its completed plies and is
resumable, so a restart loses no completed work.

## Caching

* **Position analyses** are cached in-process (`PositionAnalysisCache`), keyed by
  FEN + engine version + search limit + MultiPV — a result from a different depth
  or engine version is never served, because it would be misleading.
* The cache is per process and bounded (`ARGUS_ANALYSIS_CACHE_ENTRIES`).

## Multi-process / multi-instance (stated, not hidden)

The rate limiter, the engine gate, the metrics registry and the live event hub
are **per process**. A single-instance deployment is fully correct. To run
several API processes:

* **Metrics** — scrape each process (Prometheus handles this) or add a shared
  store.
* **Rate limiting** — back the limiter with Redis (the two methods in
  `RateLimiter` are the interface).
* **Live event fan-out** — add a Redis pub/sub fan-out for `HUB.publish`; the
  database event log and `GET /api/live/{id}/sync` are already the recovery path,
  so correctness does not depend on the hub.
* **The clock sweeper** — is idempotent, so multiple workers are safe; a single
  scheduled owner is the cleaner production shape.

None of these are needed for a single-instance deployment, which is what the
compose files describe. This is the honest boundary.