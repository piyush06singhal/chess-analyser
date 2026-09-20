# ARGUS Chess — Development Guide

## Prerequisites

- Python ≥ 3.11 (3.14 used in development)
- Node.js ≥ 20 (24 used in development)
- Stockfish (macOS: `brew install stockfish`; Debian/Ubuntu: `apt install stockfish`)
- Docker + Docker Compose (optional, for the full environment)
- PostgreSQL + Redis (Docker Compose provides both; local runs can use SQLite)

## Setup

### 1. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/argus
pip install -e apps/api
```

### 2. Configuration

```bash
cp .env.example .env   # edit as needed; never commit real secrets
```

Without `ARGUS_DATABASE_URL` the API degrades gracefully (persistence
unavailable, honest health report). For quick local runs:

```bash
ARGUS_DATABASE_URL=sqlite:///data/argus-dev.db
```

### 3. Frontend

```bash
cd apps/web
npm install
```

## Running

### Backend (from the repository root)

```bash
.venv/bin/python -m uvicorn argus_api.main:app --reload --port 8000
```

Interactive docs: http://127.0.0.1:8000/docs — health: `/health`.

### Frontend

```bash
cd apps/web && npm run dev   # http://localhost:3000
```

The frontend talks to `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`).

### Full environment (Docker Compose)

```bash
docker compose up --build
```

Starts postgres (5432), redis (6379), api (8000), web (3000). The API container
installs Stockfish and receives its path via `ARGUS_STOCKFISH_PATH`.

## Verification scripts

| Script | Verifies |
| ------ | -------- |
| `python scripts/verify_engine.py` | Stockfish detection, start, structured analysis |
| `python scripts/verify_game_analysis.py` | PGN → engine → features → report end-to-end (Opera Game) |
| `python scripts/verify_live_api.py` | Live API: health → position → validation → import+analyze → fetch |

## Testing

```bash
python -m pytest            # full suite
python -m pytest tests/test_engine.py -q   # engine only
```

The suite covers PGN parsing/validation, FEN validation, move validation,
game results, engine integration (real Stockfish), feature extraction,
phase/classification, agent tools, ML dataset guards, and API behavior.
Engine-backed tests are skipped honestly when Stockfish is absent.

## Database

Phase 1 establishes schema via SQLAlchemy models with
`Base.metadata.create_all` on startup (dev-friendly). Alembic migrations are
planned for Phase 2 — the models are already declarative (Mapped[...]) so the
migration autogeneration will work against the same metadata.

Schema: `games` → `game_moves`, `games` → `position_analyses`,
`games` → `analysis_sessions`, `players` → `player_games`.

## Environment variables

See `.env.example` for the full annotated list (`ARGUS_` prefix). Highlights:
`ARGUS_DATABASE_URL`, `ARGUS_REDIS_URL`, `ARGUS_STOCKFISH_PATH`,
`ARGUS_STOCKFISH_DEPTH`, `ARGUS_CORS_ORIGINS`, `ARGUS_MAX_PGN_KB`.

## Code quality

- Type-safe frontend; Pydantic models for API schemas; typed Python.
- Structured logging via `argus.shared.logging` (text or JSON).
- No secrets in source; env vars for credentials/configuration.
- Modular services; no giant files; comments only where they add value.

## Troubleshooting

- **"Stockfish binary not found"** — install Stockfish or set
  `ARGUS_STOCKFISH_PATH` (verify with `python scripts/verify_engine.py`).
- **`database connected: false`** — check `ARGUS_DATABASE_URL`; for Docker
  Compose the host is `postgres`, not `localhost`.
- **Port already in use** — change `--port` / compose ports.
