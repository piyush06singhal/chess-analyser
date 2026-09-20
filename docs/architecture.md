# ARGUS Chess — Architecture

ARGUS Chess is an AI-powered chess game intelligence, analysis, and coaching
platform. It is **not** a thin Stockfish wrapper: deterministic chess analysis
(engine + features) is kept strictly separate from probabilistic AI
functionality (LLM/ML).

## Conceptual pipeline

```
PGN / Imported Game
      ↓
Game Parser            (argus.chess_core.pgn)
      ↓
Game Validation        (argus.chess_core.pgn — rejects illegal/unreadable games)
      ↓
Position Extraction    (argus.chess_core — FEN per ply)
      ↓
Stockfish Analysis     (argus.analysis.engine — ChessEngine abstraction)
      ↓
Feature Extraction     (argus.analysis.features — raw board features)
      ↓
Game Intelligence      (argus.analysis.game_analyzer — phases, classification)
      ↓
Player Intelligence    (per-side aggregates in GameSummary; profiles in Phase 2)
      ↓
Report Generation      (argus.analysis.reports — deterministic report builder)
      ↓
AI Coaching Agent      (argus.ai_agent — tool-calling over structured outputs)
      ↓
Personalized Training  (Phase 2+; tool registered as unavailable until built)
```

Each stage is independent. Chess engine code contains no LLM logic; the AI
agent never manipulates raw Stockfish internals; the frontend contains no
business logic that belongs in the backend; ML models are not coupled to the UI.

## Repository layout

```
argus-chess/
├── apps/
│   ├── web/                 Next.js 16 frontend (TypeScript, React 19, Tailwind 4)
│   └── api/                 FastAPI backend (argus_api package)
│       └── argus_api/
│           ├── config.py    Pydantic Settings (env-driven, ARGUS_ prefix)
│           ├── main.py      App factory, middleware, central error mapping
│           ├── schemas.py   Request/response Pydantic models
│           ├── deps.py      FastAPI dependency providers
│           ├── routes/      health, games, analysis (modular routers)
│           ├── services/    engine_service (engine lifecycle), redis_service
│           └── db/          SQLAlchemy models, session, repository
├── packages/argus/          Core library (installable: `pip install -e packages/argus`)
│   └── argus/
│       ├── shared/          errors (typed hierarchy), logging (structured)
│       ├── chess_core/      FEN/PGN/moves/positions — pure chess data
│       ├── analysis/        engine (base + stockfish), features, phase,
│       │                    classification, game_analyzer, reports
│       ├── ai_agent/        tool registry, agent shell (LLMClient abstraction)
│       └── ml/              dataset, pipelines, features, model_store
├── data/                    raw | processed | features | models (see data/README.md)
├── engine/stockfish/        Engine binaries (gitignored; brew/apt installs supported)
├── scripts/                 Verification utilities (engine, game analysis, live API)
├── tests/                   Pytest suite (chess core, engine, analysis, API, agent/ML)
├── docs/                    This documentation
├── docker/                  Dockerfile.api, Dockerfile.web
├── docker-compose.yml       postgres, redis, api, web
├── .env.example             Environment template (no real secrets)
└── pytest.ini               Test configuration
```

## Separation of concerns

| Layer | May use | Must not use |
| ----- | ------- | ------------ |
| `chess_core` | python-chess, pydantic | engine, LLM, DB, UI |
| `analysis` | chess_core, Stockfish (UCI) | LLM, DB, UI |
| `ai_agent` | analysis/chess_core services via tools, LLMClient | raw engine internals |
| `ml` | datasets, scikit-learn (later) | UI, live engine paths |
| `apps/api` | all packages, SQLAlchemy | direct UCI traffic |
| `apps/web` | REST API only | business logic |

## Key design decisions

1. **Stockfish is the authoritative calculation engine.** All evaluations,
   best moves, and PVs come from the engine; mate scores map onto a documented
   centipawn-equivalent scale (10_000 ceiling) for mechanical comparability.
2. **Everything engine-derived is side-perspective-explicit.** Scores are
   reported from the mover's perspective; flipping is done in one place.
3. **Terminal positions are handled explicitly.** Checkmate/stalemate positions
   return `is_terminal` results instead of engine calls (`bestmove (none)` is
   not an analysis).
4. **Classification thresholds are configurable** (`PhaseThresholds`,
   `ClassificationThresholds`) — defaults are documented heuristics, not
   invented facts, and live in one place.
5. **Raw features vs ML features.** `RawPositionFeatures` are pure board-state
   facts; derived features belong to `argus.ml.features`.
6. **Honest unavailability.** Unbuilt tools (player history, knowledge search,
   training generation) are registered with `available=False` and a reason;
   the API reports `database: connected: false` with a reason when not
   configured; the frontend shows proper empty states. No fake data anywhere.
7. **Sync SQLAlchemy + threadpool.** Route handlers defined with `def` run in
   FastAPI's threadpool, which also suits the blocking engine subprocess I/O
   (the engine itself is thread-safe behind a lock).
8. **Central error mapping.** Domain errors carry stable `code`s; `main.py`
   maps them to HTTP responses in one place.
