# ARGUS Chess

**AI-powered chess game intelligence, analysis, and personalized coaching.**

ARGUS Chess combines a chess engine (Stockfish), deterministic game/position analysis, player analytics, and an AI coaching layer — built as a production platform, not a Stockfish wrapper. Deterministic chess analysis (engine + board features) is kept strictly separate from probabilistic AI (LLM/ML), and no analysis results are ever fabricated: every number in a report comes from the engine or the board.

## Architecture

```
PGN / Imported Game
   ↓
Game Parser (chess_core)          — parsing, validation, metadata
   ↓
Position Extraction               — per-move positions (FEN)
   ↓
Stockfish Analysis (analysis)     — evals, best moves, PV, MultiPV
   ↓
Feature Extraction                — raw board features per position
   ↓
Game Intelligence                 — phases, classification, CPL, summary
   ↓
Report Generation                 — explainable, structured report
   ↓
AI Coaching Agent (ai_agent)      — tool-calling over structured outputs
   ↓
Personalized Training             — planned (Phase 2+)
```

Each stage is independent: engine code contains no LLM logic, the agent never calculates chess positions itself, the frontend contains no business logic, and ML code is not coupled to the UI.


## Quickstart (local)

```bash
# 1. Python environment + dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/argus -e apps/api

# 2. Stockfish (macOS)
brew install stockfish          # or set ARGUS_STOCKFISH_PATH

# 3. Verify the engine
python scripts/verify_engine.py

# 4. Start the API (SQLite for local dev; set ARGUS_DATABASE_URL for Postgres)
uvicorn argus_api.main:app --reload --port 8002

# 5. Frontend
cd apps/web && npm install && npm run dev
# open http://localhost:3000
```

## Docker Compose

```bash
cp .env.example .env            # adjust values as needed
docker compose up --build
# Host ports come from .env (defaults: api 8002, web 3001, postgres 5434, redis 6380)
```

## Testing

```bash
.venv/bin/python -m pytest                            # full suite
.venv/bin/python scripts/verify_engine.py             # live engine smoke test
.venv/bin/python scripts/verify_game_analysis.py      # full PGN → report pipeline
.venv/bin/python scripts/verify_live_api.py           # live API end-to-end (needs API running)
```

Tests use real games (e.g. Morphy's Opera Game) and verified positions — never fabricated engine outputs.

## Technology stack

| Layer      | Technology |
|------------|------------|
| Frontend   | Next.js 16, React 19, TypeScript 5, Tailwind CSS 4, chess.js, react-chessboard 5 |
| Backend    | Python 3.11+, FastAPI, python-chess, SQLAlchemy 2.0, Pydantic 2 |
| Engine     | Stockfish (UCI), auto-detected or via `ARGUS_STOCKFISH_PATH` |
| Database   | PostgreSQL (psycopg2/asyncpg); SQLite supported for local dev/tests |
| Caching    | Redis (prepared, optional) |
| AI         | LLM abstraction layer (`LLMClient`), tool-calling registry |
| ML         | scikit-learn-ready interfaces (no model trained yet — by design) |

## Known limitations (Phase 1)

- **Opening detection** relies on PGN headers when present; detection from the move sequence itself is planned.
- **No predictive ML**: interfaces exist, but no model is trained — datasets are deliberately selected in a later phase (see `docs/ml-strategy.md`).
- **AI coaching agent**: tool-calling foundation only; no LLM provider is wired yet, and unavailable tools report honest errors instead of fake answers.
- **Report narratives** (key lessons, player tendencies, training plans) are listed as pending sections — no invented insights.
- **Authentication** is architectural only (anonymous/local analysis; user accounts arrive with Supabase in Phase 2).
- **Migrations** use `Base.metadata.create_all` at startup; Alembic arrives in Phase 2.
- **Engine analysis is synchronous per request** (off the event loop); background job queue arrives with Phase 2.

## Phase 2 objective (recommended)

Player intelligence on persisted games: Alembic migrations, user accounts via Supabase auth, multi-game import (database/PGN upload), player profiles with longitudinal statistics from stored analyses, opening detection from move sequences (ECO book), and background analysis jobs — the foundation for dataset-driven ML in Phase 3.

## Final structure

```
argus-chess/
├── apps/
│   ├── web/          # Next.js 16 dashboard (src/app, src/components, src/lib)
│   └── api/          # FastAPI service (argus_api: routes, services, db, schemas)
├── packages/
│   └── argus/        # core library: chess_core, analysis, ai_agent, ml, shared
├── data/             # raw/ processed/ features/ (see data/README.md)
├── docker/           # Dockerfile.api, Dockerfile.web
├── docs/             # architecture, chess-analysis, data-strategy, ai-agent, ml-strategy, development
├── scripts/          # verify_engine, verify_game_analysis, verify_live_api
├── tests/            # pytest suite (112 tests)
├── .env.example
├── docker-compose.yml
├── README.md
└── LICENSE
```

| Infra      | Docker, Docker Compose |
