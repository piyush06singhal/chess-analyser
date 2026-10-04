# Configuration

Every setting is an environment variable prefixed `ARGUS_` (see
`apps/api/argus_api/config.py`). None are hardcoded, and secrets are env-only.
`.env.example` lists the full set; the per-environment templates narrow it.

## Validating configuration

The configuration check a deploy pipeline runs before shipping:

```bash
python scripts/validate_config.py --environment production
# or, once the package is installed:
argus config validate --environment production
```

Exit code 0 means valid. The command applies the environment's rules (see
[environments.md](environments.md)) and prints every problem at once, so a
misconfigured deployment fails with the whole list rather than one item at a
time.

## Key settings

| Variable | Purpose | Default |
| --- | --- | --- |
| `ARGUS_ENV` | environment name | `development` |
| `ARGUS_LOG_LEVEL` / `ARGUS_LOG_FORMAT` | logging | `INFO` / `text` |
| `ARGUS_DATABASE_URL` | SQLAlchemy URL | empty (SQLite/dev) |
| `ARGUS_REDIS_URL` | cache; optional | empty |
| `ARGUS_API_KEYS` | `key:caller:role` list; empty = open | empty |
| `ARGUS_FEATURE_FLAGS` | server-controlled switches | empty |
| `ARGUS_STOCKFISH_PATH` | engine binary | empty (auto-detect) |
| `ARGUS_ENGINE_MAX_CONCURRENCY` | concurrent analyses per process | 2 |
| `ARGUS_ANALYSIS_QUEUE_LIMIT` | analyses queued before 503 | 64 |
| `ARGUS_MAX_REQUEST_BYTES` | request body cap | 4000000 |
| `ARGUS_RATE_LIMIT_*_PER_MINUTE` | per-caller limits | 30/30/20/10/60 |
| `ARGUS_LLM_PROVIDER` | coach provider: `openai`/`anthropic`/`groq`/`echo`; empty = 501 | empty |
| `ARGUS_MODELS_DIR` | ML model store | `data/models` |
| `ARGUS_CORS_ORIGINS` | allowed browser origins | localhost dev origins |

## Feature flags

Server-controlled switches, format `name=true,name2=false`. A flag that is not
listed takes its default, and an **unknown** flag name is never enabled by a
typo: it is simply not in the catalogue. Known flags (all default on):

`coach`, `live_chess`, `graph`, `predictions`, `scenarios`, `training`.

A disabled flag makes the surface a **404** (not advertised) and names the flag
in the error details. The full list is in `argus_api.feature_flags.KNOWN_FLAGS`.

## Startup validation

`main.lifespan` calls `validate_settings` at startup. In **production** a
misconfigured deployment refuses to start — an open production API is exactly
the failure this catches. In every other environment the problems are logged so
they are visible without blocking local work.

## Secrets

Configuration is environment-only. Keys never reach the browser: the frontend
sees only whether a provider is configured. `GET /ready` reports provider
*configuration*, never material. Never commit a filled-in `.env`.
