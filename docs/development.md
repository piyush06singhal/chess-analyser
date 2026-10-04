# Development Guide

## Prerequisites

- Python 3.11+ (3.14 used in development)
- Node.js 20+ / npm 11
- Stockfish (`brew install stockfish` on macOS, `apt install stockfish` on Debian/Ubuntu)
- Docker + Docker Compose (for the full stack; optional for local dev)

## Local setup (backend)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e "packages/argus[ml]" -e apps/api

# Verify the engine
python scripts/verify_engine.py

# Start the API (SQLite dev DB; set ARGUS_DATABASE_URL for PostgreSQL)
ARGUS_DATABASE_URL=sqlite:///data/argus-dev.db uvicorn argus_api.main:app --reload --port 8002
```

Interactive docs: http://localhost:8002/docs

## Local setup (frontend)

```bash
cd apps/web
npm install
NEXT_PUBLIC_API_URL=http://localhost:8002 npm run dev
# open http://localhost:3000
```

## Environment configuration

Copy `.env.example` → `.env` and adjust. Highlights:

| Variable | Purpose |
| -------- | ------- |
| `ARGUS_DATABASE_URL` | PostgreSQL (prod) or `sqlite:///...` (local dev/tests). Empty disables persistence with honest 503s. |
| `ARGUS_REDIS_URL` | Optional cache; status-only in Phase 1. |
| `ARGUS_STOCKFISH_PATH` | Empty = auto-detect on PATH / common locations. |
| `ARGUS_LLM_PROVIDER` | `openai` \| `anthropic` \| `echo` \| empty (coach disabled, 501). |
| `ARGUS_LLM_API_KEY` | Server-side only; never exposed to the browser or logs. |
| `ARGUS_MODELS_DIR` | Model store for trained ML artifacts. |
| `ARGUS_CORS_ORIGINS` | Comma-separated (or JSON list) browser origins; include both `localhost` and `127.0.0.1` for the web port. |
| `NEXT_PUBLIC_API_URL` | Browser-facing API base URL (baked into the client bundle). |
| `API_INTERNAL_URL` | Server-side API base URL used by Next server components (e.g. `http://api:8000` in Docker). |

No secrets are ever hardcoded; the API key the frontend displays as
"configured" is only a boolean flag from `/api/coach/status`.

## AI coach setup

```bash
# OpenAI
ARGUS_LLM_PROVIDER=openai
ARGUS_LLM_API_KEY=sk-...

# or Anthropic
ARGUS_LLM_PROVIDER=anthropic
ARGUS_LLM_API_KEY=sk-ant-...

# or keyless development provider (self-identifying, no external calls)
ARGUS_LLM_PROVIDER=echo
```

Without a provider, `/api/coach/chat` answers HTTP 501 with
`llm_not_configured` — the UI shows setup guidance instead of a fake chat.
The coach only explains tool outputs (engine analyses); it never calculates
chess facts itself, and every reply lists the tools it used.

### Phase 7 agent endpoints

`POST /api/coach/ask` is the Phase 7 agent and, unlike `/chat`, **always answers** —
no provider is a state, not an error. It returns the answer, the evidence packet, the
validated claims, the data-backed actions and the turn's trace.

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/api/coach/tools` | Declared tool catalogue: permissions, availability, reasons, outputs |
| POST | `/api/coach/ask` | One agent turn (see the request shape below) |
| GET | `/api/coach/status` | Provider/model/configured; never key material |

Request body (all context fields optional — but board awareness is what makes
"why is this bad?" resolve without a pasted FEN):

```jsonc
{
  "question": "Why was this move bad?",
  "game_id": "71b2e6c4-…",     // the game the client has open
  "ply": 17,                     // the selected ply
  "move_san": "gxf3",
  "fen": null,                   // or a position the user pasted
  "player_id": null,
  "mode": "coach",               // coach|beginner|analyst|advanced|game_review|player_coach
  "history": [                    // prior turns, in the response's own message shape
    {"role": "user", "content": "Why was this move bad?"},
    {"role": "assistant", "content": "…"}
  ],
  "include_evidence": true
}
```

Response highlights: `message`, `deterministic` (true when no model was called),
`claims[]` (typed, with `verified`), `actions[]` (with `available` and an
`unavailable_reason` when declared but unbuilt), `validation` (checked claims and any
failures), `limitations[]`, `evidence` (`items[]` and `missing[]`), and `trace`
(tool calls with durations, stage timings, status).

Verification, with the API running:

```bash
python scripts/verify_agent.py
# → catalogue and reasons, prediction refusal, engine evidence, player gap,
#   cross-caller game refused, stored-game move question, no credential in any
#   payload, and measured p50/p95 latency over the turns it ran
```

Streaming preparation: `argus.ai_agent.streaming.turn_event_stream(agent, question, …)`
yields the turn's lifecycle frames (`plan`, `tool_call`, `missing`, `evidence`,
`answer`, `validation`, `done`, then a terminal `result` or `error`) as they happen,
so a transport can forward progress before the answer exists. Token-level deltas are
not streamed — the provider clients expose no streaming completion API.

## ML dataset + training workflow

```bash
# 1. Put real PGN corpora in data/raw/ (see data/README.md for sources)
# 2. Generate engine-evaluated feature rows (real Stockfish, versioned provenance)
python -m argus.ml.build_dataset data/raw/your_corpus.pgn \
    --output data/processed/positions_dataset.csv --depth 12

# 3. Train (refuses datasets below the declared spec — by design)
python -m argus.ml.train --dataset data/processed/positions_dataset.csv
```

The current smoke-test corpus (~46 rows) is correctly refused: the spec for
`move_evaluation_regression` requires ≥ 2,000 training rows. No toy model is
ever presented as accurate. Metrics are only produced from measured held-out
validation/test splits.

## Testing

```bash
python -m pytest                                # full suite (1584 tests)
python -m pytest -m "not engine"                # fast suite (no Stockfish)
python -m ruff check packages apps scripts tests # static correctness (F, E9)
python -m pytest tests/test_importing.py        # importer + validation
python -m pytest tests/test_positions.py        # position generation rules
python -m pytest tests/test_games_api.py        # game API endpoints
python -m pytest tests/test_e2e_import.py       # PGN → board end-to-end
python -m pytest tests/test_perspective.py      # perspective + centipawn loss
python -m pytest tests/test_phase3_core.py      # criticals, cache, config
python -m pytest tests/test_phase3_engine.py    # engine: multipv, movetime, cancel
python -m pytest tests/test_phase3_api.py       # analysis API end-to-end
python -m pytest tests/test_llm_coach.py -q     # coach layer only
python -m pytest tests/test_ml_pipelines.py -q  # ML guardrails only
python scripts/verify_engine.py                 # live engine smoke test
python scripts/verify_game_analysis.py          # PGN → report pipeline
python scripts/verify_games_api.py              # Phase 2 import pipeline (API must run)
python scripts/verify_analysis.py               # Phase 3 full-game analysis + timings
```

Phase 7 agent (218 tests; the six files below are the agent's own suite):

```bash
python -m pytest tests/test_agent_tools.py       # schemas, permissions, authorization, clamps
python -m pytest tests/test_agent_behaviour.py   # planner, memory, evidence, validation, actions
python -m pytest tests/test_agent_e2e.py         # whole turns + adversarial (scripted) providers
python -m pytest tests/test_agent_streaming.py   # lifecycle event order + terminal frame
python -m pytest tests/test_agent_performance.py # the measurement harness itself
python -m pytest tests/test_agent_evaluation.py  # the evaluation suite, run for real
python scripts/verify_agent.py                   # live agent verification + latency (API must run)
```

The adversarial half of the e2e suite matters most: it runs scripted providers that
invent an evaluation, a probability, a count and an opening name, and asserts the
*validator* catches each one. If those tests pass without the validator, the validator
is decoration.

Frontend:

```bash
cd apps/web
npm run lint    # eslint (0 warnings expected)
npm run build   # type-check + production build
# Browser suites (need a running web app; set ARGUS_WEB_URL to point at it)
npx playwright test tests/e2e/accessibility.spec.ts   # axe, serious/critical = 0
npx playwright test tests/e2e/responsive.spec.ts      # no overflow at 390/768/1280 px
```

## Code conventions

- Backend: strict typing, Pydantic schemas at the API edge, central error
  mapping (`argus.shared.errors` → HTTP), structured logging.
- Frontend: strict TypeScript, no business logic in components — data comes
  from the typed API client (`src/lib/api.ts`).
- Unimplemented functionality shows honest empty states; nothing is simulated.
- Engine/analysis/agent/ML boundaries are documented in `docs/architecture.md`.

## Game API (Phase 2)

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/api/games/validate` | Structured PGN validation (no persistence) |
| POST | `/api/games/import` | Import pasted PGN (optional analysis) |
| POST | `/api/games/import/file` | Import an uploaded `.pgn`/`.txt` file (multipart) |
| GET | `/api/games` | Game library with analysis status |
| GET | `/api/games/{id}` | Game metadata + moves |
| GET | `/api/games/{id}/positions` | Canonical position sequence |
| GET | `/api/games/{id}/status` | Analysis lifecycle state |
| POST | `/api/games/{id}/analyze` | Queue background analysis (202) |
| DELETE | `/api/games/{id}` | Delete game + cascaded data |

### Analysis API (Phase 3)

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/api/analysis/games/{id}` | Start/resume analysis (profile, depth, multipv, movetime) |
| GET | `/api/analysis/games/{id}` | Full result: session, counts, criticals |
| GET | `/api/analysis/games/{id}/progress` | Progress + configuration |
| GET | `/api/analysis/games/{id}/moves` | Per-move engine analysis |
| GET | `/api/analysis/games/{id}/critical-moments` | Critical candidates |
| POST | `/api/analysis/games/{id}/cancel` | Cooperative cancellation |
| POST | `/api/analysis/position` | Single-position analysis |
| POST | `/api/analysis/position/multipv` | MultiPV analysis |

Uploads require `python-multipart` (installed via `apps/api` dependencies).
Uploads are validated for extension, MIME type, size (2 MB), and UTF-8; content
is treated as data only.

## Database

Phase 1 used `create_all` bootstrap. Phase 2 keeps it but hardens the schema
with `CHECK` constraints (valid result/color/status/ranges) and unique
constraints (`game_moves (game_id, ply)`, `game_positions (game_id, ply)`,
`player_games (player_id, game_id, color)`). Alembic migrations remain a
follow-up; when the schema changes, recreate a dev database or add a manual
migration. SQLite (dev) and PostgreSQL (prod) are both supported by the
portable column types.
