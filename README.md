# Caissa

**Chess intelligence you can verify — engine-grade analysis, structured reports, and evidence-based coaching.**

Caissa is a self-hosted chess analysis platform built on Stockfish. It turns
imported games into structured reports, player profiles, training exercises and
opponent preparation, with an AI coach that explains the numbers rather than
inventing them.

The product rule is enforced in code, not only documented:

> A capability that is not built is reported as not built. A number that was not
> measured is not shown. A claim without evidence is refused.

**Release:** 1.0.0 (candidate `v1.0.0-rc.1`). The sign-off decision, scope and
blockers are in [`docs/release/RELEASE_STATUS.md`](docs/release/RELEASE_STATUS.md);
what is deliberately deferred is in
[`docs/release/DEFERRED_WORK.md`](docs/release/DEFERRED_WORK.md). The full index is
[`docs/README.md`](docs/README.md).

---

## Contents

- [Capabilities](#capabilities)
- [Architecture](#architecture)
- [Quickstart](#quickstart)
- [Configuration](#configuration)
- [Testing and quality](#testing-and-quality)
- [Deployment](#deployment)
- [Documentation](#documentation)
- [Future upgrades](#future-upgrades)
- [Technology](#technology)

---

## Capabilities

### Import and analysis

- **Import** from pasted PGN, a `.pgn`/`.txt` upload, or a public Chess.com /
  Lichess account.
- **Validate** with typed, user-facing issues (`ILLEGAL_MOVE`, `INCOMPLETE_GAME`,
  `RESULT_MISMATCH`, …) that carry a move/ply — never a stack trace.
- **Analyse** with Stockfish over UCI: depth- or time-based search, MultiPV, node
  metadata. One evaluation convention throughout: White-perspective centipawns,
  `#n` for mate, `None` when unknown.
- **Classify** moves as `best / excellent / good / inaccurate / mistake /
  blunder`, plus an evidence-gated `brilliant`. The lifecycle
  (`imported → ready → analyzing → analyzed / failed`) is resumable and
  cancellable.

### Game intelligence

Board-state phases (never move numbers), opening identification with its deviation
point, a material timeline, tactical and positional detection, king safety, and
evidenced turning points. Every insight records its source (`ENGINE FACT` /
`CAISSA-DERIVED FEATURE` / `CAISSA INTERPRETATION`), its certainty
(`confirmed` / `candidate`) and its ply. Accuracy is Caissa-defined and travels
with its methodology (it is **not** Chess.com or Lichess accuracy).

### Player intelligence

A versioned profile aggregated from stored reports, with no engine calls: record,
colour split, repertoire, phase performance, tactical and positional themes, king
safety, material tendencies, conversion, recovery and trends. Every statement
carries a claim level (`observation` → `pattern` → `tendency`), a coverage band
and a sample size; below threshold it reports *not enough data*. Chess DNA is a
set of measurable dimensions — no composite score, no personality labels.

### Training

Exercises generated only from your own analysed mistakes, each traceable to
`game → ply → classification → exercise` with an engine-verified solution. A
documented eligibility gate rejects ambiguous, trivial, forced and duplicate
positions; spaced repetition uses a real state machine (`new → learning → review →
mastered`, where mastery requires a streak).

### Opponent preparation

Repertoire, recurring positions, measured tendencies and phase performance built
from the opponent's stored games only. Named sample gates decide whether a count
may be called a `pattern` or a `tendency`; below a gate the count is shown and
never smoothed into a finding.

### AI coach

A tool-using agent that resolves context, retrieves structured data, runs the
engine only when needed, and answers strictly from the evidence it gathered. A
hallucination validator checks evaluations, mate distances, percentages, counts,
opening names and FENs against the evidence packet and refutes anything
unsupported. Claim types (fact, observation, interpretation, coaching) stay
visibly apart. With no provider configured, or during a provider outage, the
coach degrades to a stored-fact answer and names the real reason.

### Live chess

Server-authoritative play: the client sends an intent, the board is rebuilt from
stored positions, every move is validated server-side, and remaining time is
derived from stored timestamps. An explicit state machine, optimistic concurrency
and a sequenced event stream with gap recovery. Fair play is enforced in the
backend — competitive games run without engine assistance, and completed games
hand off to the full analysis pipeline.

### Intelligence graph

One evidence-driven layer connecting players, games, positions, openings,
patterns, insights, training, opponents and scenarios. A node is a pointer; no
data is copied. Derived edges require an `EvidenceReference` or are refused, and a
"Why?" surface resolves any node to its relationship, evidence, sample and
methodology version.

### Evaluation framework

18 benchmark suites grouped into 12 release gates, blocking on failures that must
never pass (an illegal chess state, a cross-user access, a fabricated number, an
invalid engine perspective, a broken training solution, prediction leakage, a
live-sync corruption, a critical security issue). A suite that cannot run is
skipped with its reason and its gate fails — *could not measure* is never
reported as *fine*.

---

## Architecture

```
PGN / upload / Chess.com / Lichess
   ↓  importing            — GameImporter interface + registry
   ↓  validation           — typed, user-facing issues
   ↓  chess_core           — parsing, normalization, positions
   ↓  analysis             — Stockfish, classification, critical moments
   ↓  intelligence         — phases, openings, tactics, turning points, GameReport
   ↓  player_intelligence  — Chess DNA, insights, evidence
   ↓  training / opponents / scenarios / live / intelligence_graph / coaching
   ↓  ai_agent             — intent → tools → evidence → answer → validate
   ↓  FastAPI (argus_api)  — routes, services, auth, observability
   ↓  Next.js (apps/web)   — landing page and workspaces
```

Each layer is independent: engine code contains no LLM logic, the agent never
calculates chess itself, the frontend contains no business logic. Deterministic
analysis (engine and board features) is kept strictly separate from probabilistic
AI (LLM/ML).

**Repository layout**

```
argus-chess/
├── apps/
│   ├── web/       # Next.js landing + workspace (src/app, src/components, src/lib)
│   └── api/       # FastAPI service (routes, services, db, schemas)
├── packages/
│   └── argus/     # core library (chess_core, analysis, intelligence, …)
├── data/          # datasets, models, experiments (see data/README.md)
├── docker/        # Dockerfile.api, Dockerfile.web
├── docs/          # index and per-subsystem documentation
├── scripts/       # verification, benchmarking, operations
├── tests/         # pytest suite
└── evaluation/    # benchmark suites and baselines
```

---

## Quickstart

**Prerequisites:** Python ≥ 3.11, Node.js ≥ 20, Stockfish, and (optionally)
Docker.

```bash
# 1. Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -e "packages/argus[ml]" -e apps/api

# 2. Stockfish
brew install stockfish          # or set ARGUS_STOCKFISH_PATH

# 3. Verify the engine
python scripts/verify_engine.py

# 4. API (SQLite for local dev; set ARGUS_DATABASE_URL for PostgreSQL)
ARGUS_DATABASE_URL=sqlite:///data/argus-dev.db \
  uvicorn argus_api.main:app --reload --port 8002

# 5. Web
cd apps/web && npm install && npm run dev    # http://localhost:3000
```

**Docker Compose**

```bash
cp .env.example .env
docker compose up --build
# Host ports come from .env (defaults: api 8002, web 3100, postgres 5434, redis 6380).
```

---

## Configuration

Configuration is environment variables with the `ARGUS_` prefix. `.env.example`
lists the full set; `.env.<environment>.example` holds the per-environment
templates.

| Variable | Purpose | Default |
| --- | --- | --- |
| `ARGUS_DATABASE_URL` | Database connection | SQLite (local) |
| `ARGUS_STOCKFISH_PATH` | Engine binary | auto-detected |
| `ARGUS_ENGINE_DEPTH` / `ARGUS_ENGINE_MULTIPV` | Search defaults | `12` / `3` |
| `ARGUS_ANALYSIS_PROFILE` | `fast` / `standard` / `deep` | `standard` |
| `ARGUS_LLM_PROVIDER` | `openai` / `anthropic` / `groq` / `echo` / empty | empty (coach offline) |
| `ARGUS_LLM_API_KEY` | Provider key | — |
| `ARGUS_API_KEYS` | `key:caller:role` entries; empty = open deployment | empty |
| `ARGUS_ENV` | `development` / `test` / `staging` / `production` | `development` |
| `ARGUS_REDIS_URL` | Redis (health and cache) | empty |

Validate a configuration before starting:

```bash
python scripts/validate_config.py --environment production
```

A production deployment refuses to start when misconfigured (open mode, SQLite,
text logs, the `echo` provider). API keys never reach the browser; the LLM key is
read from the environment only.

---

## Testing and quality

```bash
# Full test suite
python -m pytest -o addopts= -q

# Static correctness
python -m ruff check packages apps scripts tests

# Release gate (evaluation suites; exits non-zero on a blocker)
python scripts/run_evaluation.py --quiet

# The five user journeys, end to end against a live API
python scripts/verify_journeys.py

# Live deployment check
python scripts/system_check.py

# Frontend
cd apps/web
npx tsc --noEmit && npx eslint . && npx next build
npx playwright test
```

Current measured state: **1584 tests passing, 1 skipped**; the release gate
reports **165 passed, 0 blockers**; the browser suite passes on **Chromium,
Firefox and WebKit** with **no serious axe violations** on 17 pages and no
horizontal overflow on 15 pages at 390 / 768 / 1280 px. Continuous integration
runs these gates, the dependency audits and a secret/vulnerability scan on every
push and pull request (`.github/workflows/quality.yml`).

---

## Deployment

- **Environments:** `development`, `test`, `staging`, `production`, each with its
  own template and rules.
- **Containers:** pinned base images, non-root users, health checks, resource
  limits, no bind-mounts and no published database port in production.
- **Database:** additive, idempotent schema upgrades plus a guarded legacy-table
  drop script; backup, restore and a verified round-trip are scripted.
- **Reliability:** a bounded engine gate (concurrency and queue), per-caller rate
  limits, feature flags, and real `/health` / `/ready` / `/metrics` probes.
- **Web-accessible topology:** the frontend can run on Vercel while the API runs
  on a container host beside managed Postgres/Redis — see
  [`docs/deployment.md`](docs/deployment.md) for the split and the exact wiring.

Readiness evidence: [`PRODUCTION_READINESS_REPORT.md`](PRODUCTION_READINESS_REPORT.md).

---

## Documentation

The full index is [`docs/README.md`](docs/README.md). Main entry points:

| Area | Location |
| --- | --- |
| System overview | `docs/system-overview.md`, `docs/architecture.md` |
| Chess analysis | `docs/chess-analysis.md` |
| Game / player / opponent intelligence | `docs/game-intelligence.md`, `docs/player-intelligence.md`, `docs/opponent-intelligence.md` |
| AI agent and coaching | `docs/ai-agent.md`, `docs/coaching.md` |
| Training and scenarios | `docs/training-engine.md`, `docs/scenarios.md` |
| Live chess | `docs/live-chess.md`, `docs/realtime-protocol.md` |
| Intelligence graph | `docs/intelligence-graph/README.md` |
| Data and ML design | `docs/ml-and-data.md` |
| Evaluation, operations, product | `docs/evaluation/`, `docs/production/`, `docs/product/` |
| Security and release | `docs/security.md`, `docs/release/` |

---

## Future upgrades

The boundaries below are stated rather than hidden, because each one is a planned
extension with a clear trigger. The current release is scoped to a single-operator,
self-hosted deployment and is complete for that scope.

**Platform**

- **User accounts and multi-tenant isolation** — identity is currently an API key
  mapped to a caller id. Sign-up, login, sessions and account deletion are the
  next platform milestone; cross-account privacy is already enforced.
- **Horizontal scaling** — rate limiting, metrics and the live hub are per
  process. Sharing that state (a store plus a Redis fan-out) is what unlocks more
  than one instance.
- **Database migrations** — the schema uses additive, idempotent upgrades; an
  Alembic baselining pass is the planned replacement before long-lived multi-
  instance operations or the first destructive schema change.
- **Distributed tracing** — request and correlation IDs are in place today; a
  tracing backend becomes worthwhile once the architecture is more than one
  service.
- **Legal, privacy and support** — terms, a privacy policy, a support channel and
  a "export everything" bundle are required before storing the public's personal
  data.

**Intelligence**

- **Calibrated ML predictions** — the prediction surfaces are intentionally
  unavailable until a model clears its pre-registered production gate; the
  training, evaluation and rollback path already exist. Shipping an ungated model
  would break the product's central promise.
- **Larger, validated corpora** — the bundled corpus verifies the pipeline, not
  model quality; a real dataset is what turns the training path into a shipped
  model.
- **Streaming at scale** — the streaming design is complete but has not been
  exercised on a million-game corpus.

**Frontend and quality**

- **Manual screen-reader walkthrough** — automated axe scans pass on 17 pages and
  the boards are keyboard-operable; a human-certified pass is still to be done.
- **Field performance measurement** — the app is instrumented for Web Vitals; a
  lab baseline is recorded and a field figure follows real traffic.

Per-item status with evidence is in
[`docs/product/FINAL_PRODUCT_QUALITY_REPORT.md`](docs/product/FINAL_PRODUCT_QUALITY_REPORT.md),
and the deferral rationale is in
[`docs/release/DEFERRED_WORK.md`](docs/release/DEFERRED_WORK.md).

---

## Technology

| Layer | Stack |
| --- | --- |
| Frontend | Next.js 16, React 19, TypeScript 5, Tailwind CSS 4, chess.js, react-chessboard |
| Backend | Python 3.11+, FastAPI, python-chess, SQLAlchemy 2.0, Pydantic 2 |
| Engine | Stockfish (UCI), auto-detected or via `ARGUS_STOCKFISH_PATH` |
| Database | PostgreSQL (production); SQLite for local dev and tests |
| Cache / coordination | Redis (health and caching; optional) |
| AI | `argus.llm` — OpenAI, Anthropic and Groq providers over httpx, plus an `echo` dev provider |
| ML | scikit-learn behind framework-neutral interfaces; guarded training |
| Infra | Docker, Docker Compose, GitHub Actions |

---

## License

See [`LICENSE`](LICENSE).
