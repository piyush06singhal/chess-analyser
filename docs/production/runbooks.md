# Runbooks

Procedure for the incidents that actually happen. Each one starts from a
measurement, not a guess, and every diagnostic command is real.

The first tool for anything is:

```bash
python scripts/system_check.py
```

It reports the deployment's real state, dependency by dependency.

## Service is not ready (`/ready` is `degraded`)

`/ready` names the blocking checks. Fix the named one:

| `blocking` contains | Likely cause | Action |
| --- | --- | --- |
| `database` | Postgres down, wrong URL, pool exhausted | Check `docker compose ps postgres`; check `ARGUS_DATABASE_URL`; check the database's connection limit |
| `stockfish` | engine binary missing or not executable | Check `ARGUS_STOCKFISH_PATH`; the API container installs it at `/usr/games/stockfish` |

If `blocking` is empty but the status is not `ready`, that is a bug — capture the
full `/ready` body and the request id.

## Analysis is slow or queued

1. `GET /ready` → `checks.engine_capacity`. If `queued` is high and `running` is
   at the limit, the machine is simply at capacity.
2. Confirm the bound is sane: `ARGUS_ENGINE_MAX_CONCURRENCY` and
   `ARGUS_ANALYSIS_QUEUE_LIMIT`. Raising them lets more work in; it does not make
   the CPU faster, so raise them only with headroom.
3. A single caller flooding the queue is limited by the `analysis` rate-limit
   bucket; a 429 with `Retry-After` is the correct response, not an outage.

## Clients see 429

A rate limit is doing its job. Decide which it is:

* **A misbehaving client** — the 429 body names the bucket; the caller should
  honour `Retry-After`.
* **A limit too low for legitimate traffic** — raise the bucket
  (`ARGUS_RATE_LIMIT_<BUCKET>_PER_MINUTE`) and redeploy, or set it to 0 to disable
  that bucket while you investigate.

Never disable rate limiting wholesale to "fix" a 429.

## Clients see 503 (`service_busy`)

The engine queue is full. This is the intended back-pressure. Actions:

1. Confirm with `/ready` → `engine_capacity`.
2. If legitimate load, scale up (more instances) or raise the queue — with CPU
   headroom.
3. If a single caller, the `analysis` rate limit should bound it; if it is not,
   that is the bug to fix.

## A game is stuck in `analyzing`

1. `GET /api/analysis/games/{id}/progress` — is a run actually advancing?
2. If the process restarted mid-run, the run is resumable: re-request analysis
   (`POST /api/analysis/games/{id}`). Already-analysed plies are skipped.
3. If the game shows `analyzing` with no active run, the status is stale. The
   analysis endpoints resolve against the run record, so re-requesting analysis
   corrects it. If it persists, capture the game id and the `/progress` body.

## Data looks inconsistent

```bash
python scripts/check_data_consistency.py --json
```

It reports each class of problem without modifying anything. Serious classes
(analysis that contradicts its game, analysed games without a complete
generation) mean a real defect; capture the output before doing anything else.
Never "repair" a database by hand without a verified backup.

## Restore from backup

1. `python scripts/backup_database.py` — take a backup of the *current* state
   before restoring anything, so the restore itself is reversible.
2. `python scripts/restore_database.py --dump <backup> --target-url <fresh-db>
   --source-url <old-db>` — restore into a **fresh** database and verify it.
3. Cut over to the restored database (point `ARGUS_DATABASE_URL` at it, restart
   the API). Keep the old database until the new one is confirmed good.

## Disable a surface quickly

```bash
ARGUS_FEATURE_FLAGS=coach=false   # or live_chess, graph, predictions, scenarios, training
```

Restart the API. The surface returns 404 and everything else stays up. This is
the fastest mitigation for a misbehaving surface and needs no code change.

## Metrics look wrong after a restart

They reset — metrics are per process (see
[observability.md](observability.md)). That is expected, not data loss. If a
multi-instance deployment needs fleet-wide totals, scrape each process or add a
shared store.

## Escalation

If the diagnosis is not in this list, gather `/ready`, `/health`, the request id
from the failing response, and the recent structured logs, then escalate. The
request id ties the client's report to the server's log line.