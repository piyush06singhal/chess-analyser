<div align="center">

# Caissa

**Chess intelligence you can verify.**

Engine-grade analysis, structured reports and evidence-based coaching.
Self-hosted, and every claim traceable to the data behind it.

[![quality](https://github.com/piyush06singhal/chess-analyser/actions/workflows/quality.yml/badge.svg)](https://github.com/piyush06singhal/chess-analyser/actions/workflows/quality.yml)
[![release](https://img.shields.io/badge/release-1.0.0-blue)](docs/release/RELEASE_STATUS.md)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![node](https://img.shields.io/badge/node-20%2B-brightgreen)](https://nodejs.org/)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

---

Caissa turns imported games into reports, player profiles, training exercises and
opponent preparation. The engine measures; the coach explains those measurements
and refuses to invent anything beyond them.

> **A capability that is not built is reported as not built.**
> **A number that was not measured is not shown.**
> **A claim without evidence is refused.**

That rule is enforced in code, not only written down. Lifetimes, gate levels and
sample thresholds are values in the source, and the evaluation gate fails the
release when any of them is broken.

## At a glance

| Area | What it does |
| --- | --- |
| **Import** | Pasted PGN, `.pgn`/`.txt` upload, or a public Chess.com / Lichess account |
| **Analysis** | Stockfish over UCI — depth or time, MultiPV, resumable lifecycle |
| **Classification** | `best · excellent · good · inaccurate · mistake · blunder`, plus an evidence-gated `brilliant` |
| **Game report** | Phases, opening and deviation, material, tactics, king safety, turning points, accuracy with its methodology |
| **Player profile** | Versioned, built from stored reports with no engine calls — claims carry a level, a coverage band and a sample size |
| **Training** | Exercises from your own mistakes, each traceable to `game → ply → classification`, with spaced repetition |
| **Opponent prep** | Repertoire, recurring positions and measured tendencies from the opponent's stored games only |
| **AI coach** | A tool-using agent that answers from retrieved evidence; unsupported numbers are refuted by a validator |
| **Live chess** | Server-authoritative play, clocks from stored timestamps, fair play enforced in the backend |
| **Intelligence graph** | One evidence layer over players, games, positions, openings, patterns and training, with a "Why?" trace |

Two surfaces exist for every insight: the number, and the level of certainty
attached to it. Claim types — `fact`, `observation`, `interpretation`, `coaching`
— never blur into each other.

## Quickstart

**Requires** Python ≥ 3.11, Node.js ≥ 20 and Stockfish.

```bash
# 1 · Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e "packages/argus[ml]" -e apps/api

# 2 · Engine (or set ARGUS_STOCKFISH_PATH)
brew install stockfish
python scripts/verify_engine.py

# 3 · API — SQLite locally; set ARGUS_DATABASE_URL for PostgreSQL
ARGUS_DATABASE_URL=sqlite:///data/argus-dev.db \
  uvicorn argus_api.main:app --reload --port 8002

# 4 · Web
cd apps/web && npm install && npm run dev        # http://localhost:3000
```

**Docker Compose**

```bash
cp .env.example .env
docker compose up --build
```

Host ports come from `.env` — by default API `8002`, web `3100`, PostgreSQL
`5434`, Redis `6380`.

## Architecture

| Stage | Responsibility |
| --- | --- |
| `importing` | PGN, upload, Chess.com / Lichess — behind one importer interface |
| `validation` | Typed, user-facing issues carrying a move or ply, never a stack trace |
| `chess_core` | Parsing, normalisation, positions |
| `analysis` | Stockfish search, move classification, critical moments |
| `intelligence` | Phases, openings, tactics, turning points → `GameReport` |
| `player_intelligence` | Chess DNA, claims, evidence |
| `training` · `opponents` · `scenarios` · `live` · `intelligence_graph` · `coaching` | Feature layers over the same evidence |
| `ai_agent` | Intent → tools → evidence → answer → validation |
| **API** (`apps/api`) | FastAPI — routes, services, auth, observability |
| **Web** (`apps/web`) | Next.js — landing page and workspaces |

Each layer is independent: the engine code contains no LLM logic, the agent never
computes chess itself, and the frontend contains no business rules. Deterministic
analysis is kept strictly apart from probabilistic AI.

```
chess-analyser/
├── apps/
│   ├── web/       Next.js landing and workspace
│   └── api/       FastAPI service
├── packages/argus/  Core library
├── data/          Datasets, models, experiments
├── docker/        Dockerfile.api · Dockerfile.web
├── docs/          Documentation, indexed
├── evaluation/    Benchmark suites and baselines
├── scripts/       Verification and operations
└── tests/         Pytest suite
```

## Verification

Every number below comes from a command in this repository, runnable as shown.

| Gate | Command | Result |
| --- | --- | --- |
| Backend suite | `python -m pytest -o addopts= -q` | **1599 passed**, 1 skipped |
| Static correctness | `python -m ruff check packages apps scripts tests` | Clean |
| Release gate | `python scripts/run_evaluation.py --quiet` | **165 passed**, 0 blockers |
| User journeys | `python scripts/verify_journeys.py` | 5 of 5, end to end |
| Deployment | `python scripts/system_check.py` | All checks passed |
| Frontend | `npx tsc --noEmit && npx eslint . && npx next build` | Clean |
| Browser matrix | `npx playwright test` | **210 passed** · Chromium, Firefox, WebKit |
| Accessibility | axe, in the browser suite | 0 serious/critical on 17 pages |
| Dependencies | `python scripts/audit_dependencies.py` | pip-audit 0 · shipped npm tree 0 |

A suite that cannot run is reported as **skipped with its reason**, and the gate
fails. *Could not measure* is never reported as *fine*.

Continuous integration runs all of the above, plus a secret, misconfiguration and
container scan, on every push and pull request —
[`.github/workflows/quality.yml`](.github/workflows/quality.yml).

## Configuration

Environment variables with the `ARGUS_` prefix. `.env.example` lists the full set;
`.env.<environment>.example` holds the per-environment templates.

| Variable | Purpose | Default |
| --- | --- | --- |
| `ARGUS_DATABASE_URL` | Database connection | SQLite locally |
| `ARGUS_STOCKFISH_PATH` | Engine binary | Auto-detected |
| `ARGUS_ENGINE_DEPTH` · `ARGUS_ENGINE_MULTIPV` | Search defaults | `12` · `3` |
| `ARGUS_ANALYSIS_PROFILE` | `fast` / `standard` / `deep` | `standard` |
| `ARGUS_LLM_PROVIDER` | `openai` / `anthropic` / `groq` / `echo` | Empty — coach runs offline |
| `ARGUS_LLM_API_KEY` | Provider key | — |
| `ARGUS_API_KEYS` | `key:caller:role` entries | Empty — open deployment |
| `ARGUS_ENV` | `development` / `test` / `staging` / `production` | `development` |
| `ARGUS_REDIS_URL` | Redis, for health and cache | Empty |

```bash
python scripts/validate_config.py --environment production
```

A production deployment refuses to start when misconfigured — open mode, SQLite,
text logs or the `echo` provider. Keys never reach the browser; the LLM key is read
from the environment only.

## Deployment

| Concern | Approach |
| --- | --- |
| **Environments** | `development`, `test`, `staging`, `production`, each with its own template and rules |
| **Containers** | Pinned base images, non-root, health checks, resource limits; no bind-mounts and no published database port in production |
| **Database** | Additive, idempotent upgrades; backup, restore and a verified round-trip are scripted |
| **Reliability** | Bounded engine gate, per-caller rate limits, feature flags, real `/health` `/ready` `/metrics` probes |
| **Topology** | Web on Vercel beside the API on a container host — see [`docs/deployment.md`](docs/deployment.md) |

The readiness assessment behind the release decision is
[`PRODUCTION_READINESS_REPORT.md`](PRODUCTION_READINESS_REPORT.md).

## Documentation

The index is [`docs/README.md`](docs/README.md), organised by what you are trying
to do.

| Area | Documents |
| --- | --- |
| System and architecture | [`system-overview.md`](docs/system-overview.md) · [`architecture.md`](docs/architecture.md) |
| Chess analysis | [`chess-analysis.md`](docs/chess-analysis.md) |
| Game · player · opponent | [`game-intelligence.md`](docs/game-intelligence.md) · [`player-intelligence.md`](docs/player-intelligence.md) · [`opponent-intelligence.md`](docs/opponent-intelligence.md) |
| Agent and coaching | [`ai-agent.md`](docs/ai-agent.md) · [`coaching.md`](docs/coaching.md) |
| Training and scenarios | [`training-engine.md`](docs/training-engine.md) · [`scenarios.md`](docs/scenarios.md) |
| Live chess | [`live-chess.md`](docs/live-chess.md) · [`realtime-protocol.md`](docs/realtime-protocol.md) |
| Intelligence graph | [`intelligence-graph/`](docs/intelligence-graph/README.md) |
| Data and ML | [`ml-and-data.md`](docs/ml-and-data.md) |
| Evaluation · operations · product | [`evaluation/`](docs/evaluation/README.md) · [`production/`](docs/production/README.md) · [`product/`](docs/product/design-system.md) |
| Security and release | [`security.md`](docs/security.md) · [`release/`](docs/release/RELEASE_NOTES.md) |

## Not in this release

Stated rather than hidden: each row is a planned extension with a clear trigger.
This release is scoped to a single-operator, self-hosted deployment and is
complete for that scope.

| Area | Today | Trigger to build |
| --- | --- | --- |
| **User accounts** | API key mapped to a caller id; cross-account isolation already enforced | The first public sign-up |
| **Multi-instance** | Rate limiting, metrics and the live hub are per process | More than one API instance |
| **Schema migrations** | Additive, idempotent upgrades | The first destructive change |
| **Distributed tracing** | Request and correlation ids only | More than one service |
| **Legal and privacy** | Not written | Storing the public's personal data |
| **ML predictions** | Surfaces disabled until a model clears its gate | A model that passes its pre-registered gate |
| **Larger corpora** | The bundled corpus verifies the pipeline, not model quality | A real dataset |
| **Streaming at scale** | Designed, not exercised | A million-game corpus |
| **Screen-reader pass** | axe clean, boards keyboard-operable | Human certification |
| **Field Web Vitals** | Lab baseline recorded | Real traffic |

Full rationale: [`docs/release/DEFERRED_WORK.md`](docs/release/DEFERRED_WORK.md).
Per-item status with evidence:
[`docs/product/FINAL_PRODUCT_QUALITY_REPORT.md`](docs/product/FINAL_PRODUCT_QUALITY_REPORT.md).

## Stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 16 · React 19 · TypeScript 5 · Tailwind CSS 4 · chess.js · react-chessboard |
| Backend | Python 3.11+ · FastAPI · python-chess · SQLAlchemy 2.0 · Pydantic 2 |
| Engine | Stockfish over UCI |
| Data | PostgreSQL in production; SQLite for local dev and tests; Redis optional |
| AI | `argus.llm` — OpenAI, Anthropic and Groq over httpx, plus an `echo` dev provider |
| ML | scikit-learn behind framework-neutral interfaces, with guarded training |
| Infra | Docker · Docker Compose · GitHub Actions |

## License

[MIT](LICENSE) © 2026 Caissa contributors
