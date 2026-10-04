# Deployment

How to run Caissa, what it needs, and how to tell whether a deployment is healthy.
Everything here is verifiable with the scripts in `scripts/`.

## Components

| Component | Default host port | Notes |
| --- | --- | --- |
| API (FastAPI) | 8002 | `apps/api`, served by uvicorn |
| Web (Next.js) | 3100 | `apps/web` (port 3100 to avoid common conflicts) |
| PostgreSQL | 5434 | primary store in Docker Compose |
| Redis | 6380 | optional cache; reported by `/health` and `/ready` |
| Stockfish | — | must be reachable by the API process |

## Local (no Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "packages/argus[ml]" -e apps/api
brew install stockfish                     # or set ARGUS_STOCKFISH_PATH

# API against SQLite for local development
ARGUS_DATABASE_URL=sqlite:///data/argus-dev.db \
  uvicorn argus_api.main:app --reload --port 8002

# Web
cd apps/web && npm install && npm run dev  # http://localhost:3000
```

## Docker Compose

```bash
cp .env.example .env      # adjust values
docker compose up --build
```

Host ports come from `.env` (defaults: api 8002, web 3100, postgres 5434,
redis 6380). The API source is bind-mounted read-only, so a backend source change
needs `docker compose restart api`.

## Configuration

All configuration is environment variables prefixed with `ARGUS_`; none are
hardcoded, and secrets are env-only (see `security.md`). `.env.example` lists the
full set. The ones that matter most:

| Variable | Purpose | Default |
| --- | --- | --- |
| `ARGUS_ENV` | environment name (`development` / `production`) | `development` |
| `ARGUS_LOG_LEVEL` | log verbosity | `INFO` |
| `ARGUS_LOG_FORMAT` | `text` or `json` (JSON for log aggregation) | `text` |
| `ARGUS_DATABASE_URL` | SQLAlchemy URL (PostgreSQL in production) | empty (SQLite/dev) |
| `ARGUS_REDIS_URL` | cache; optional | empty |
| `ARGUS_STOCKFISH_PATH` | engine binary; empty auto-detects | empty |
| `ARGUS_ENGINE_DEPTH` / `ARGUS_ENGINE_MULTIPV` | search defaults | 12 / 3 |
| `ARGUS_ANALYSIS_PROFILE` | `fast` / `standard` / `deep` | `standard` |
| `ARGUS_LLM_PROVIDER` / `ARGUS_LLM_MODEL` / `ARGUS_LLM_API_KEY` | explanation layer; empty ⇒ honest 501 | empty |
| `ARGUS_MODELS_DIR` | ML model store | `data/models` |
| `ARGUS_CORS_ORIGINS` | allowed browser origins | localhost dev origins |
| `ARGUS_PLAYER_MIN_GAMES_*` | sample thresholds (product contract) | 2 / 20 / 50 |

### Production notes

* **Database.** Use PostgreSQL and let `Base.metadata.create_all` plus
  `ensure_schema_upgrades` run at startup. Alembic is a known follow-up; until
  then, recreate a dev database when a non-additive schema change is required.
* **Engine.** Point `ARGUS_STOCKFISH_PATH` at a pinned Stockfish build and verify
  it at startup; `/ready` fails the `stockfish` check if it is missing.
* **Redis.** Optional. When configured, `/health` and `/ready` report its real
  connection state; when not, they say so rather than pretending.
* **LLM.** Optional. With no provider configured, every deterministic path works
  and the coach returns an explicit 501 — never a fabricated answer.
* **Predictions.** No model has passed its production gate, so `/api/predictions/*`
  returns `available: false` by design. That is a product decision, not a
  misconfiguration.

## Health and readiness

| Endpoint | Question | Semantics |
| --- | --- | --- |
| `GET /health` | is the process up, and what do its dependencies look like? | always `status: ok` when the service runs; each dependency reported separately |
| `GET /ready` | can this instance serve traffic right now? | `ready` when the database and engine are usable, else `degraded` with `blocking` list |
| `GET /health/ready` | alias of `/ready` | for probes that expect it under `/health` |

`/ready` performs real probes and reports each dependency honestly:

* `database` — an actual connection check (blocking);
* `stockfish` — engine availability and version (blocking);
* `redis` — connection state (non-blocking);
* `migrations` — presence of the expected tables, by name (non-blocking);
* `ml_registry` — how many models are production-approved (non-blocking);
* `ai_provider` — whether an LLM provider is configured; keys are never exposed
  (non-blocking).

Nothing is stubbed to return "ok". If the engine is missing, `/ready` says so and
the status is `degraded`.

## Verifying a deployment

| Command | Proves |
| --- | --- |
| `.venv/bin/python scripts/system_check.py` | the deployment's real state, dependency by dependency |
| `.venv/bin/python scripts/verify_coaching.py` | the whole coaching loop, end to end, on real data |
| `.venv/bin/python scripts/verify_scenarios.py` | the what-if gate |
| `.venv/bin/python scripts/check_data_consistency.py` | stored analysis still describes its game |
| `.venv/bin/python -m pytest -o addopts="" -q` | the unit/integration suite |

## Live chess (Phase 12)

* **One API process owns the event hub.** The hub (`services/live_hub.py`) is
  per-process; correctness does not depend on it (the database event log and
  `GET /api/live/{id}/sync` are the recovery path), but to run several workers add
  a Redis fan-out for `HUB.publish`.
* **The clock sweeper runs inside the API process**, started with the application.
  It holds no state between passes and applies a flag fall through the same
  validated transition a move uses, so a restart loses nothing. With multiple API
  workers the sweep is idempotent; a single scheduled owner is the production
  shape.
* **Live limits are constants**, not settings: `max_open_games_per_player = 8`,
  `invite_ttl_days = 7`, `sync_page_size = 500`, and a 250 ms clock latency grace.
  They are reported by `GET /api/live/method`.
* **Real-time analysis is background and cancellable**; the engine's own reply is
  made inside the request because the client's board is wrong until it arrives.
* **`GET /health` and `GET /ready`** report database, Redis, engine, migrations, the
  ML registry and the AI provider; the live surface is checked by `system_check.py`
  and the whole lifecycle by `scripts/verify_live.py`.

## Operational limits (stated, not hidden)

* **Metrics are per process.** `/metrics` counters reset on restart; a
  multi-worker deployment will need a shared store.
* **The job runner is in-process.** Analysis runs after the response, not on a
  distributed queue; cancellation takes effect after the current engine search.
* **No Alembic.** Schema changes are additive upgrades plus `create_all`.
* **Rate limiting is per process and accounts are not built.** Per-caller limits
  are enforced (`rate_limit.py`) but their window is in process memory; a public
  multi-user deployment needs a shared limiter store and a real account system
  (see `security.md`).

## Split deployment: Vercel (web) + Fly.io (API) + managed data

The recommended host for a **web-accessible** deployment separates the two parts
by what they actually need. The Next.js frontend is a static/SSR artifact and
belongs on Vercel; the API is a long-lived process that owns a Stockfish binary,
a background analysis runner, an in-process clock sweeper and a WebSocket hub, so
it needs a host that runs a container continuously — not a serverless function.

| Component | Host | Why it must be there |
| --- | --- | --- |
| Web (`apps/web`) | **Vercel** | Zero-config for Next.js; preview deploys per branch |
| API (`apps/api`) | **Fly.io** (or Render/Railway) | Stockfish binary + a persistent process (background analysis, clock sweeper, WebSockets) |
| PostgreSQL | **Neon** / Supabase | Managed; use the pooled connection string |
| Redis | **Upstash** (TCP) / Fly Redis | The live hub uses pub/sub, so a TCP Redis, not a REST endpoint |

### Web on Vercel

Set the project's **Root Directory** to `apps/web`; Vercel detects Next.js and
needs no `vercel.json`. Set the environment variables:

| Variable | Value |
| --- | --- |
| `NEXT_PUBLIC_API_URL` | the public API URL, e.g. `https://caissa-api.fly.dev` |
| `API_INTERNAL_URL` | leave unset — server rendering now runs on Vercel, not beside the API, so it correctly falls back to the public URL |

### API on Fly.io

`fly.toml` at the repository root builds `docker/Dockerfile.api` and keeps one
machine always running (`auto_stop_machines = false`), because background analysis
and the clock sweeper need a live process. Create the volume named in `fly.toml`
(`fly volumes create caissa_data`) and set the secrets — never in the image:

```bash
fly secrets set \
  ARGUS_DATABASE_URL='postgresql+psycopg2://...@...neon.tech/caissa?sslmode=require' \
  ARGUS_REDIS_URL='redis://default:...@...upstash.io:6379/0' \
  ARGUS_CORS_ORIGINS='https://caissa.vercel.app,https://your-domain.example' \
  ARGUS_API_KEYS='...' \
  ARGUS_LLM_PROVIDER='groq' ARGUS_LLM_MODEL='...' ARGUS_LLM_API_KEY='...'
```

`ARGUS_CORS_ORIGINS` must list every browser origin that calls the API, including
Vercel **preview** URLs, or the browser blocks the request. The API runs its
bootstrap and additive schema upgrades at startup, so keep it to a **single
instance** until Alembic and a shared rate-limit store land (see
`DEFERRED_WORK.md`).

### What not to put on Vercel

Vercel functions cannot install Stockfish, are frozen between requests (so the
background analysis runner and clock sweeper stop), do not host long-lived
WebSockets, and cap execution at 60 s (300 s on Pro) — all of which the API
depends on. Moving the API onto Vercel would mean re-architecting analysis, live
play and real-time updates; the split above needs no code change.

## Access control, probes and the browser

Two facts decide how a deployment can be reached, and they pull in opposite
directions:

* **Production requires `ARGUS_API_KEYS`.** `scripts/validate_config.py` refuses
  an open deployment, and when keys are set the API requires `X-API-Key` (or
  `Authorization: Bearer <key>`) on every request.
* **The browser client sends no credential.** There is no `NEXT_PUBLIC_API_KEY`,
  because a key shipped in a browser bundle is not a secret — anyone who loads
  the page has it.

So a web-accessible deployment has exactly two honest shapes:

| Shape | How it works | Who can reach it |
| --- | --- | --- |
| **Edge-gated** (recommended) | One origin: a reverse proxy terminates TLS, injects `X-API-Key`, and gates access (Cloudflare Access, basic auth, a VPN) | Only the people the gate admits |
| **Public, open** | No gate; run with `ARGUS_ENV=staging` and no `ARGUS_API_KEYS` | Everyone with the URL |

The first is the recommended shape until **accounts** land (see
[`release/DEFERRED_WORK.md`](release/DEFERRED_WORK.md)): the proxy holds the key,
the browser never sees it, and the deployment stays a valid `production`
configuration. Be accurate about its strength — the key becomes a *shared*
credential, so access control is exactly as strong as the gate in front of it.
It is not per-user authentication.

### Liveness and readiness are exempt from the key

A healthcheck cannot present a credential: the Docker `HEALTHCHECK`, the
`http_service.checks` in `fly.toml`, a compose `condition: service_healthy` and an
orchestrator's readiness probe all issue a bare request. `/health`, `/ready` and
`/health/ready` therefore answer without one (`UNAUTHENTICATED_PATHS` in
`apps/api/argus_api/middleware.py`). They report dependency *state* and never user
data. `/metrics` stays keyed — a scraper can send a header — and every data route
still requires the key.

### One-origin reverse proxy (Caddy)

This serves the web app and the API on one origin, so the browser needs no CORS
handling and never holds the key. `NEXT_PUBLIC_API_URL` is then the site's own
`/api` prefix.

```caddyfile
app.example.com {
    # Everything else is the Next.js app.
    handle {
        reverse_proxy web:3000
    }

    # The API, with the key injected server-side. A client-supplied key is
    # replaced, never passed through.
    handle /api/* {
        reverse_proxy api:8000 {
            header_up X-API-Key "{$CAISSA_API_KEY}"
        }
    }

    # Probes need no key, and are useful to a load balancer in front of this.
    handle /health*   { reverse_proxy api:8000 }
    handle /ready*    { reverse_proxy api:8000 }

    # Optional edge gate for the whole site:
    # basic_auth { admin $2a$14$<bcrypt-hash> }
}
```

With this shape, set `ARGUS_CORS_ORIGINS=https://app.example.com` and
`NEXT_PUBLIC_API_URL=https://app.example.com/api`. Because the browser calls its
own origin, CORS is not exercised at all — which removes an entire class of
deployment failure (a preview URL missing from the allow-list).
