# Caissa — Architecture

Caissa is an AI-powered chess game intelligence, analysis, and coaching
platform. It is **not** a thin Stockfish wrapper: deterministic chess analysis
(engine + features) is kept strictly separate from probabilistic AI
functionality (LLM/ML).

## Conceptual pipeline

```
PGN / Uploaded file / Chess.com · Lichess  (public read APIs)
      ↓
Game Importer          (argus.importing — GameImporter interface + registry)
      ↓
Structured Validation  (argus.importing.validation — typed, user-facing issues)
      ↓
Game Parser            (argus.chess_core.pgn — internal Game representation)
      ↓
Position Generation    (argus.chess_core.positions — canonical GamePositionState)
      ↓
Stockfish Analysis     (argus.analysis.engine — ChessEngine abstraction)
      ↓
Feature Extraction     (argus.analysis.features — raw board features)
      ↓
Game Intelligence      (argus.intelligence — Phase 4: phases, opening, material,
                       tactics, positional, king safety, activity, structure,
                       turning points, advantage states, accuracy, conversion)
      ↓
GameReport             (structured, evidenced — argus.intelligence.report)
      ↓
Player Intelligence    (Phase 5 — multi-game profiles / Chess DNA)
      ↓
Dataset Engineering    (Phase 6 — argus.datasets: validation, dedupe,
                       identity, labels, features, sampling, manifests,
                       quality/bias, leakage checks, split strategies)
      ↓
ML Evaluation          (Phase 6 — argus.ml: baselines, metrics, calibration,
                       error analysis, gating, registry, experiments;
                       trains nothing that has not passed the gate)
      ↓
AI Coaching Agent      (argus.ai_agent — tool-calling over structured outputs)
      ↓
Personalized Training  (Phase 8 — argus.training: exercises built from stored
                       analysis; its agent tools register unavailable only when
                       the training provider is not wired)
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
│           ├── routes/      health, games, analysis, intelligence (modular routers)
│           ├── services/    engine_service (engine lifecycle), analysis_jobs,
│           │                redis_service
│           └── db/          SQLAlchemy models (incl. GameReportRecord), session,
│                            repository
├── packages/argus/          Core library (installable: `pip install -e packages/argus`)
│   └── argus/
│       ├── shared/          errors (typed hierarchy), logging (structured)
│       ├── chess_core/      FEN/PGN/moves/positions — pure chess data
│       ├── analysis/        engine (base + stockfish), features, phase,
│       │                    classification, game_analyzer, reports
│       ├── intelligence/    Phase 4 game intelligence: phases, openings,
│       │                    material, tactics, positional, king_safety,
│       │                    activity, structure, trajectory, advantage,
│       │                    turning_points, accuracy, conversion,
│       │                    categories, performance, report, service
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
| `intelligence` | analysis + chess_core (read-only), stored analysis | engine calls, LLM, DB, UI |
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

## Phase 2 — ingestion, positions, and lifecycle

### Import subsystem (`argus.importing`)

Every game source implements the same `GameImporter` contract (`validate` +
`import_games`), so adding a platform source (Chess.com and Lichess are both
implemented this way) never changes the core game
model or the analysis pipeline. `PgnImporter` serves pasted text, uploaded files
and both platform sources — a platform client *fetches* PGN and the importer
*parses* it, so parsing exists in exactly one place.

### Platform sources (`argus.importing.chesscom`, `argus.importing.lichess`)

Both clients are read-only and key-less: they hold no credentials and return the
PGN the platform already publishes. Failures are typed (`SourcePlayerNotFoundError`,
`SourceRateLimitedError`, `SourceUnavailableError`, `SourceResponseError`) and map
to distinct HTTP statuses, so a missing player is a 404, not a 500.

The platforms genuinely differ, and the API states the difference instead of
hiding it:

- **Chess.com** publishes an archive index, so its months are the months that
  contain games.
- **Lichess** publishes no archive index, so its months are calendar months
  between the account's creation month and the current month
  (`months_for_profile`). A month with no games comes back empty; Caissa never
  invents a game count for a month it has not fetched.
- Both feeds mix variants in, so variants are counted and reported
  (`variant_count`) and never imported as standard chess.

Imports are idempotent: each game keeps its upstream URL as `source_game_id`, so
re-importing the same selection reports *skipped* rather than duplicating the
library. A site with no client registered is refused with the real list of
supported sources (`UnsupportedSourceError`, HTTP 422) — there is no silent
fallback, and any other site's games can still be imported as PGN.

Validation returns a `ValidationReport` of typed `ValidationIssue`s
(`EMPTY_PGN`, `NO_GAMES`, `MALFORMED_PGN`, `ILLEGAL_MOVE`, `INCOMPLETE_GAME`,
`RESULT_MISMATCH`, `GAME_TOO_LONG`) with game index, move number, and ply where
determinable. Raw parser exceptions are never exposed to the client.

### Positions

`argus.chess_core.positions.generate_game_positions` produces the linear
sequence `initial → move 1 → … → final` as `GamePositionState` objects
(ply, move number, side to move, FEN, SAN/UCI, previous/resulting FEN, and
transient board facts). Persisted as `game_positions`, this is the canonical
board representation: the frontend renders it directly and the Stockfish phase
reads `fen` directly — no board-state logic is duplicated.

### Analysis lifecycle

`AnalysisStatus` (`imported → validating → ready → analyzing → analyzed`, or
`failed`) is stored on `games.analysis_status` and is the single source of
truth. The frontend never infers whether analysis exists; it reads
`GET /api/games/{id}/status`.

### Asynchronous analysis seam

`argus_api.services.analysis_jobs.AnalysisJobRunner` runs a background analysis
with its own DB session, updating status around the engine call. It is a thin,
replaceable seam: a real queue (Celery/RQ/Arq) can replace it without changing
routes or the state machine. A process-wide lock serializes access to the
single Stockfish subprocess.

### Database (Phase 2 additions)

```
Player ──< PlayerGame >── Game ──< GameMove
                            │
                            ├──< GamePosition    (unique game_id+ply; ply 0 = initial)
                            ├──< PositionAnalysis (unique game_id+ply+depth)
                            └──< AnalysisSession
```

Integrity is enforced in the schema with `CHECK` constraints (valid result,
color, status, ply/move-number ranges) and unique constraints (`game_moves`
`(game_id, ply)`, `game_positions` `(game_id, ply)`, `player_games`
`(player_id, game_id, color)`). Deletes cascade from the game.

### Security

Uploads are treated strictly as data: extension, MIME type, size (2 MB) and
UTF-8 decoding are validated, filenames are sanitized to a display-only form,
and nothing is ever written to a user-controlled path or executed.

## Phase 3 — the Stockfish analysis engine

### Layers (raw output → explanation never mix)

```
ENGINE (UCI, Stockfish)          argus.analysis.engine
   ↓ typed, perspective-normalized
STRUCTURED ANALYSIS              argus.analysis.{pipeline,game_analyzer,
                                 classification,critical_positions,perspective}
   ↓ persisted
INTELLIGENCE LAYER                argus.intelligence (Phase 4)
   ↓ structured, evidenced GameReport
NATURAL LANGUAGE                 (LLM — explains tool output, never invents)
```

Raw engine output, structured analysis, and human/LLM interpretation are kept
strictly separate. The intelligence layer follows the same rule internally:
every insight is tagged `ENGINE FACT`, `CAISSA-DERIVED FEATURE`, or
`CAISSA INTERPRETATION` (see `docs/game-intelligence.md`).

### New modules

| Module | Responsibility |
| ------ | -------------- |
| `analysis/perspective.py` | One convention (positive = White) + conversions |
| `analysis/pipeline.py` | Profiles (`fast`/`standard`/`deep`), config, `ANALYSIS_VERSION` |
| `analysis/critical_positions.py` | Candidate critical moments with reasons/severity |
| `analysis/cache.py` | Configuration-aware position-analysis cache |
| `engine/base.py` | Added time-based search, node metadata, named ops, `calculate_centipawn_loss` |

### Lifecycle & async execution

`AnalysisJobRunner` (`argus_api.services.analysis_jobs`) executes the pipeline
with progress, incremental persistence, resume, cancellation, and versioning.
It owns its database sessions and is a thin, replaceable seam for a real queue.
A process-wide `_ENGINE_LOCK` serializes the single Stockfish subprocess;
a future queue gives each worker its own engine. `CancellationRegistry` tracks
in-flight runs by game id.

### Persistence (Phase 3 additions)

```
Game ──< MoveAnalysis      (unique game_id + ply + analysis_version)
     ──< CriticalPosition  (unique game_id + ply + reason + analysis_version)
     ──< AnalysisSession   (progress, engine config, timings, status)
EngineConfiguration        (hash-keyed config for audit)
```

### Caching

`CachingEngine` wraps the shared engine. The cache key is
`FEN + engine version + depth/movetime + MultiPV`, so a stale result from a
different configuration is never returned.

## Phase 4 — the game-intelligence layer

`argus.intelligence` consumes the analysis Phase 3 **already stored** and
produces a structured, fully-evidenced `GameReport`. It contains no engine
calls, no ML and no natural language. Full methodology:
`docs/game-intelligence.md`.

### Evidence model (enforced in every insight)

```
ENGINE FACT              read from Stockfish output (evals, PVs, CPL, labels)
CAISSA-DERIVED FEATURE    measured from the board (material, structure, activity,
                         king zone, tactics, phase indicators)
CAISSA INTERPRETATION     Caissa's own framework (advantage bands, turning points,
                         error categories, conversion, phase performance, accuracy)
FUTURE AI INTERPRETATION (not built — the explanation/LLM layer, later phase)
```

Each `Insight`/`Finding` carries `source`, `certainty` (`confirmed` /
`candidate`), `evidence`, and the ply it refers to; `REPORT_VERSION = "4.2"` and
Phase 3's `analysis_version` (currently `3.1`) are both stored.

### Module map

| Module | Responsibility |
| ------ | -------------- |
| `base.py` | Evidence/certainty enums, policies, `MoveFact`, `GameContext`, `Insight` |
| `phases.py` | Board-state `GamePhaseDetector` + transitions |
| `openings.py`, `opening_book.py` | Opening identification, ECO, deviation |
| `material.py` | Engine-free material timeline and events |
| `tactics.py` | Tactical events and candidates |
| `positional.py`, `structure.py`, `activity.py` | Positional features, pawn structure, piece activity |
| `king_safety.py` | Weighted king-zone events |
| `trajectory.py`, `advantage.py` | Evaluation trajectory, analytical states, advantage bands |
| `turning_points.py`, `conversion.py` | Evidenced turning points, conversion assessment |
| `accuracy.py`, `categories.py`, `performance.py` | Accuracy methodology, error categories, per-phase performance |
| `report.py`, `service.py` | `GameReport` and the AI-ready accessor surface |

### Persistence & API

```
Game ──< GameReportRecord   (unique game_id + report_version + analysis_version;
                             full report in a JSON payload column)
```

Routes live in `apps/api/argus_api/routes/intelligence.py` under
`/api/intelligence` — report generation/retrieval, and one accessor endpoint per
question (summary, trajectory, critical moments, tactical/positional events,
phase analysis, material timeline, accuracy, player statistics, tools). When a
game has no stored analysis the API answers `409 analysis_required` instead of
emitting unsupportable sections. The report is persisted so the UI can serve the
stored copy without recomputation.

## Phase 5 — player intelligence (Chess DNA)

`argus.player_intelligence` moves from one game to many. It consumes the
structured reports Phase 4 **already stored** and produces a versioned player
profile. Like Phase 4 it contains no engine calls, no ML and no natural
language. Full methodology: `docs/player-intelligence.md`.

### Pipeline (separate from game analysis)

```
ANALYZED GAMES → Player Aggregation → Feature Extraction → Pattern Detection
      → Evidence Generation → PlayerProfile → Chess DNA → Dashboard / AI tools
```

### Claim discipline

```
ClaimLevel   insufficient → observation → pattern → tendency
Coverage     insufficient → limited → moderate → robust   (describes the sample)
```

Every aggregate carries a `sample` object (games, events, claim level, coverage,
optional note). Thresholds live in one `PlayerInsightPolicy`, are configurable,
and travel with every stored profile so a reading stays reproducible. Nothing
above `observation` is emitted unless its thresholds are met; the UI shows the
coverage band instead of inventing a conclusion.

### Module map

| Module | Responsibility |
| ------ | -------------- |
| `policy.py` | `ClaimLevel`, `Coverage`, coverage bands, `PlayerInsightPolicy` |
| `models.py` | `PlayerGameInput` + event types, and the profile contract |
| `aggregate.py` | Statistics: games, colour, openings, phases, tactics, positional, king safety, material, conversion, recovery, time control, opponents, trends |
| `patterns.py` | Recurring-pattern detection (occurrences × games × coverage × consistency) |
| `dna.py` | Chess DNA dimensions — interpretable metrics, no composite score |
| `features.py` | The ML-ready feature contract (`FEATURE_VERSION = "5.0"`) |
| `sanity.py` | `validate_profile` invariants (counts, ranges, evidence existence) |
| `insights.py`, `profile.py`, `service.py` | Insight assembly, `build_profile`, AI-ready accessors |

### Identity model

```
Player ──< PlayerGame >── Game
   │        (color)         │ white_player_id / black_player_id (effective colour)
   └──< PlayerProfileRecord (one per profile_version)
```

`identity_key` is the normalized matching key; `platform` /
`platform_username` hold upstream identity for a future linking step. Startup
runs `backfill_player_identities` → `merge_duplicate_players` →
`prune_orphan_players` (in that order): keys are filled, rows that normalize to
the same player are merged with every game, link and profile re-pointed, and
players left with no history are removed together with their stale snapshots.
Merging is explicit and testable — never a silent guess at request time.

### Persistence & API

```
games ── GameReportRecord ──> PlayerProfileRecord
        (Phase 4 snapshot)     (Phase 5 derived snapshot, per profile_version,
                                with source_signature for staleness)
```

Routes live in `apps/api/argus_api/routes/players.py` under `/api/players`:
the list, one profile, an explicit rebuild, plus insights / evidence / features.
The profile service maps stored reports into aggregation inputs
(`apps/api/argus_api/services/player_profile_service.py`), materialises any
missing report for an **analyzed** game (deterministic, engine-free — see
`services/report_service.py`, shared with the intelligence routes), and caches
the result until its input signature changes.

### Privacy boundary

Player features are user-specific data: every `PlayerFeature` carries
`user_specific=True` and `training_eligible=False`. A future global dataset
builder must make an explicit, documented decision before any user data could
enter a shared training set.

## Phase 6 — dataset engineering and the statistical foundation

Phase 6 adds two packages that sit *beside* the analysis pipeline rather than
inside it. Neither one calls the engine, and neither one changes any Phase 1–5
behaviour: they consume what earlier phases already stored.

```
stored games / PGN corpora
      ↓
argus.datasets   ingestion → validation → dedupe → identity → labels
                 → features → sampling → positions → quality/bias
                 → manifests → split strategies → leakage validation
      ↓
argus.ml         tasks → baselines → metrics → calibration → error analysis
                 → production gating → registry → experiments
                 → prediction service (PRODUCTION models only)
```

### Why the two packages are separate

`argus.datasets` is about *data*: what a row is, where it came from, whether it is
valid, and whether it is allowed to be used. `argus.ml` is about *claims*: what a
model scores, whether that score means anything, and whether it may be shown.
The separation is what makes the leakage tests meaningful — a dataset can be
validated without any model existing, and a model can be evaluated only on a
dataset that already passed its checks.

### Key structures

| Structure | Module | Role |
| --- | --- | --- |
| `IngestedGame` | `datasets.records` | the single normalized output of every importer |
| `PlayerIdentity` | `datasets.records` | original spelling **and** normalized identity |
| `DatasetManifest` | `datasets.manifest` | machine-readable provenance, incl. scope and rejection counts |
| `ValidationReport` | `datasets.validation` | per-record issues and reasons — nothing is silently dropped |
| `DuplicateReport` | `datasets.dedupe` | exact vs. metadata-variant duplicates, with the policy |
| `FeatureDefinition` | `datasets.features` | name, definition, source, availability time, type, version |
| `LeakageReport` | `datasets.leakage` | the checks and their findings |
| `SplitPlan` | `datasets.splits` | the actual assignment, re-derivable |
| `PredictionTaskDefinition` | `ml.tasks` | target, labels, requirements, risks, metrics |
| `RegisteredModel` | `ml.registry` | status, metrics, calibration, versions |
| `GateDecision` | `ml.gating` | the production decision and every individual check |
| `PredictionResult` | `ml.service` | probabilities **beside** data coverage, never merged |

### Ingestion sources

Both source kinds converge on `build_dataset_from_ingestion`, so there is exactly
one path through validation, deduplication and manifests:

* `DatasetImporter` — PGN files, split into games before parsing.
* `DatabaseGameSource` — the Caissa database, streamed under a server-side cursor
  and re-serialised from normalised columns.

### Privacy boundary, enforced

Datasets carry a `DatasetScope`. A `USER` source raises unless the caller passes
`allow_user_scope=True`, and the scope reaches the manifest, so "private games
stay out of global training" is a checkable property rather than a promise. The
`global_model` / `user_specific_statistics` distinction from Phase 5 is preserved:
Phase 6 never pools user games into a shared dataset.

### Serving boundary

`PredictionService` reads **only** `PRODUCTION` registry entries. With no such
entry it returns `PredictionUnavailable` carrying the reason and the missing
requirements, and `/api/predictions/*` returns that as a normal response. There is
no fallback path to a number, by construction.

See `docs/ml-and-data.md`, `docs/ml-and-data.md` and
`docs/ml-and-data.md`.

## Phase 7 — the AI chess agent

The agent is a *tool-using* layer over services Caissa already trusts, not a chatbot
with chess opinions. It resolves what the user is talking about, gathers evidence
through a permissioned tool surface, and writes its answer only from that evidence.

```
question
  → resolve context (which game, which ply)
  → plan (intent, shortlisted tools)      ← deterministic, no model
  → tools (validated, authorized, budgeted)
  → EvidencePacket (typed, with provenance and recorded absences)
  → generate (from the packet, under an explicit prohibition)
  → validate (high-value claims checked against the packet)
  → answer + evidence + actions + trace
```

### Module map

| Path | Responsibility |
| ---- | -------------- |
| `ai_agent/core/loop.py` | `CoachingAgent.ask` — the whole turn, in order |
| `ai_agent/core/planner.py` | intent detection, tool shortlisting, deterministic fast paths |
| `ai_agent/core/context.py` | board awareness and the caller's authorized game set |
| `ai_agent/core/evidence.py` | `EvidenceItem` / `EvidencePacket` — the only basis for an answer |
| `ai_agent/core/response.py` | claims (typed), actions (data-backed), validation, the answer |
| `ai_agent/core/collection.py` | tool payload → evidence, with provenance assigned once |
| `ai_agent/tools/` | the tool families + the tool contract and provider seam |
| `ai_agent/memory/` | bounded conversation memory and context resolution |
| `ai_agent/safety/` | resource ceilings and the hallucination validator |
| `ai_agent/streaming.py` | the turn's lifecycle event stream |
| `ai_agent/performance.py` | measured latency harness |
| `ai_agent/evaluation.py` | runnable tool-selection + adversarial suite |
| `ai_agent/observability.py` | `AgentTrace` (credential-scrubbed) |

### Boundaries

* **The agent never computes a chess fact.** Evaluations come from the engine tools or
  from stored analysis; openings from a versioned curated base; player claims from
  Phase 5; probabilities only from a production-gated Phase 6 model.
* **The API wires, the core decides.** `argus_api.services.agent_service` builds the
  narrow provider callables and the authorized context; `argus.ai_agent` never imports
  the API, so the whole agent is testable without a database or a web server.
* **Reports are materialised engine-free.** Asking the coach a question can never
  silently start a 20-second Stockfish run; a tool that needs analysis the store does
  not have reports the gap instead.
* **Absence is data.** Unavailable tools, failed calls and empty results all become
  recorded `missing` entries that reach both the answer and the trace.
* **Authorization has one home.** `authorized_game_ids()` builds the context's
  allow-list; every game tool refuses an id outside it, in the backend.

See `docs/ai-agent.md` for the full design, and `scripts/verify_agent.py` for live
verification.

## Phase 8 — the personalized training engine

The training engine closes the product loop. The *same* stored analysis that powers
Game Intelligence and Player Intelligence becomes practice material, so an exercise
is never detached from the game it came from:

```
game → ply → classification → eligibility gate → TrainingPosition (verified solution)
                                                         ↓
                        attempt → MoveAcceptancePolicy → TrainingAttempt (stored forever)
                                                         ↓
                                        scheduler (new→learning→review→mastered)
```

### Layers (engine-free at read time)

| Layer | Responsibility |
| ----- | -------------- |
| `argus.training.models` | vocabulary: categories, difficulties, position types, states, transitions |
| `argus.training.eligibility` | the gate: which analysed moves may become exercises (documented thresholds) |
| `argus.training.generator` | the deterministic game → exercise pipeline; refuses what it cannot justify |
| `argus.training.difficulty` | measurable difficulty factors and bands |
| `argus.training.acceptance` | correct / near-best / incorrect, with mate arithmetic |
| `argus.training.scheduler` | deterministic spaced repetition and the state machine |
| `argus.training.sessions` | the seven resumable session kinds and their plans |
| `argus.training.recommendations` | evidenced, sample-gated training priorities |
| `argus.training.progress` | measured statistics, each with its sample size |
| `argus.training.hints` | progressive hints derived only from stored evidence |

### Persistence

Three additive tables: `training_positions` (the exercise, its origin chain and its
engine-verified solution), `training_attempts` (the full history, stored forever) and
`training_sessions` (resumable plans). The origin uses `ON DELETE SET NULL`: deleting
a game preserves the exercise and its history and only marks the source unavailable,
so a deleted game cannot silently break training history. Attempts and positions
carry `player_id`; the backend enforces ownership, never the client (§36).

### API & UI

`argus_api.services.training_service` is the seam between stored analyses and the
engine; `routes/training.py` exposes generation, the library, hints, reveal, attempt
grading, the review queue, progress, recommendations and sessions. The web app adds
`/training` (dashboard) and `/training/solve/[id]` (interactive board), with the game
page linking forward (`Practice this game`) and each exercise linking back
(`/game/{id}?ply={n}`).

### Boundaries

* **Refusal is a result.** An ineligible position is rejected with its reason, and a
  category is assigned only when the stored evidence proves it.
* **No engine at read time.** Solutions are verified at generation; serving,
  reviewing and measuring are pure database reads (§44).
* **Privacy is structural.** Personalized exercises carry their owner and are private;
  general exercises are the only shareable ones.

See `docs/training-engine.md` for the full methodology and `scripts/verify_training.py`
for live verification.

## Phase 9 — opponent intelligence + advanced analytics

Phase 9 answers, from stored games only: *what does the data say about this
opponent's repertoire, recurring positions, tendencies and preparation
opportunities?* It is analytics, not psychology, and never a prediction.

```
stored games + move analysis → OpponentGameInput
   → repertoire / responses / statistics / phases
   → evidence-gated insights → OpponentProfile → OpponentPreparationReport
   → opponent_profiles (cached snapshot) → API · agent tools · /opponents UI
```

### Layers (engine-free)

| Layer | Responsibility |
| ----- | -------------- |
| `argus.opponent_intelligence.policy` | the four named sample-size gates and claim levels |
| `argus.opponent_intelligence.repertoire` | the position-keyed opening tree per colour |
| `argus.opponent_intelligence.responses` | exact/loose position answers and recurring positions |
| `argus.opponent_intelligence.statistics` | phase statistics and measured tendencies |
| `argus.opponent_intelligence.preparation` | profile, evidence-gated insights, preparation report |
| `argus.opponent_intelligence.service` | the deterministic orchestrator |

### Reuse, not duplication

There is **no second player identity system** and no duplicated game data: the API
layer reads `Game` / `PlayerGame` / `MoveAnalysis` through
`repository.opponent_game_rows`, and only the fingerprint-guarded snapshot table
`opponent_profiles` is new — the same pattern as `PlayerProfileRecord`.

### Views

`argus_api.services.opponent_service` maps rows to the engine-free vocabulary and
caches the profile; `routes/opponents.py` exposes the profile, games, repertoire
(all-time and recent), position response, recurring positions, tendencies, phase
statistics and the preparation report under `/api/players/{id}/…`, plus nine agent
tools. The web app adds `/opponents` (the preparation workbench).

### Boundaries

* **Claim levels everywhere.** `insufficient` < `observation` < `pattern` <
  `tendency`; below a gate the count is shown, never a finding.
* **No psychology, no prediction.** Tendencies are measured behaviours with their
  sample sizes; the tactical/positional split and accuracy proxy are labelled
  proxies.
* **Authorization in the backend.** An unknown player answers 404; the frontend is
  never trusted to scope a request.

See `docs/opponent-intelligence.md` for the full methodology and
`scripts/verify_opponent.py` for live verification.

## Phase 10 — decision intelligence (counterfactuals)

Phase 10 answers the counterfactual question — *what could have happened if I had
played differently?* — without ever inventing an answer. Stockfish remains the only
source of chess calculation; the phase adds branching, comparison and framing on
top of it.

```
FEN or game_id+ply
   → root MultiPV search (engine)
   → position comparison: engine axis (a search) vs structural axis (board facts)
   → counterfactual branch: actual line vs alternative line, ply by ply (engine)
   → explanation bundle: atomic, sourced facts (no prose generation)
   → scenarios table (immutable record) · training position · agent tools · API · UI
```

### Layers

| Layer | Responsibility |
| ----- | -------------- |
| `argus.scenarios.policy` | versions, the closed scenario-type set, resource limits |
| `argus.scenarios.positions` | board facts + `PositionComparisonService` (two axes kept apart) |
| `argus.scenarios.candidates` | `CandidateMoveComparison`: one search, several moves, measured consequences |
| `argus.scenarios.counterfactual` | `CounterfactualAnalyzer`: actual vs alternative line, ply by ply |
| `argus.scenarios.whatif` | "why not this move?" / "what if I had …?" as structured evidence |
| `argus.scenarios.explorer` | turning-point explorer over stored analysis (engine-free) |
| `argus.scenarios.service` | caching, limits, refusals, prediction gate |
| `argus.scenarios.metrics` | counters and timings (§40) |
| `argus.training.from_scenario` | the Phase 8 bridge: a counterfactual becomes an exercise |

### Reuse, not duplication

Board reading comes from `analysis.features.extractor`, phase detection from
`analysis.phase`, the engine interface from `analysis.engine.base`, per-move scores
and MultiPV candidates from the Phase 3 `MoveAnalysis` rows, opponent responses from
Phase 9, and the eligibility/acceptance rules from Phase 8. The only new table is
`scenarios` (immutable, `ON DELETE SET NULL` on the game).

### Views

`argus_api.services.scenario_service` is the seam: it resolves a `(game_id, ply)` to
the real FEN and the move played there, applies authorization once
(`services/authorization.py`), persists scenarios, and builds the Phase 8 exercise.
`routes/scenarios.py` exposes 15 endpoints under `/api/scenarios`; eight agent tools
join the catalogue. The web app adds `/scenarios` (the What-If Lab).

### Boundaries

* **Refusal is a result.** `illegal_move`, `unavailable` and `insufficient_evidence`
  are statuses with reasons, never replaced by a plausible number.
* **Two axes, never merged.** An engine difference is a search; a structural
  difference is a board fact. Scores from a separate search are labelled
  `resulting_position` and are not comparable to same-search scores.
* **A branch never modifies a game.** It is a value object built from a FEN.
* **Predictions are gated or absent.** Only a `PRODUCTION` model may answer;
  otherwise the response says unavailable, with the reason.

See `docs/scenarios.md` for the full methodology and `scripts/verify_scenarios.py`
for live verification.

## Phase 13 — the intelligence graph + chess knowledge system

Phase 13 adds one layer *above* the domain and introduces no new chess concepts. It
records **identity and relationships** between the objects the earlier phases
already store, and refuses to store a derived relationship without evidence.

```
domain objects (players · games · positions · openings · patterns · insights
                · training · scenarios · opponents · knowledge)
      ↓
intelligence-graph materialization          (incremental or rebuild)
      ↓
graph_nodes / graph_edges  (JSON evidence) / graph_snapshots
      ↓
intelligence_graph service  → explorers · why-trace · health · snapshot
      ↓
agent graph tools → CoachEvidencePacket → validated, grounded answer
```

### Layers

| Layer | Responsibility |
| ----- | -------------- |
| `intelligence_graph.taxonomy` | the controlled node/edge vocabulary and versions |
| `intelligence_graph.evidence` | `EvidenceReference`; derived edges require one |
| `intelligence_graph.fingerprint` | position identity (placement, side, castling, en passant) |
| `intelligence_graph.similarity` | ordered, non-interchangeable similarity levels |
| `intelligence_graph.service` | validated writes, bounded + authorized traversal, evidence tracing, versioning, metrics |
| `intelligence_graph.knowledge` | sourced concepts + which a real position exhibits |
| `intelligence_graph.health` | orphans, invalid shapes, missing evidence, dangling edges, staleness |
| `intelligence_graph.packet` | `CoachEvidencePacket` and the answer validator |
| `argus_api.services.graph_store` | `GraphStore` over the ORM |
| `argus_api.services.graph_service` / `graph_intelligence` | materialization and incremental updates |
| `argus_api.services.graph_explorers` | Position/Game/Player Explorer, Why, health, snapshot, search |
| `argus_api.routes.graph` | the `/api/graph` surface |
| `argus.ai_agent.tools.graph` | nine schema-validated graph tools |

### Key design decisions

* **Storage (§3):** relational tables with a JSON evidence column. A graph database
  was evaluated and rejected — the access patterns are bounded, indexable
  traversals, not arbitrary paths. Published at `GET /api/graph/method`.
* **Evidence or nothing (§6/§52).** A derived edge without an `EvidenceReference` is
  refused with a reason; `related_to` is the weakest kind and still needs evidence.
* **Similarity is a category.** `exact` / `equivalent` / `structurally_similar` /
  `opening_similar` / `tactically_similar`; only `exact` is called a match.
* **Bounds (§47).** Depth ≤ 3 and nodes ≤ 500 by default; a truncated traversal
  says so. The similarity scan is bounded and states its size.
* **Authorization in one place.** Every node/edge is filtered through a
  `GraphAccessPolicy` before it is returned; denials are counted, not leaked.
* **Versioning (§8/§37).** Schema, methodology and knowledge versions are stamped
  on writes; snapshots are reproducible; derived edges are invalidated on change and
  re-derived, never silently left stale.

### Persistence

```
graph_nodes      unique(node_type, node_key); edge/type/methodology indexes
graph_edges      unique(edge_type, from, to); from/to/type/methodology indexes
graph_snapshots  reproducible counts and versions
knowledge_sources / knowledge_documents / knowledge_chunks / knowledge_concepts
```

Full methodology: `docs/intelligence-graph/`. Live verification:
`scripts/verify_intelligence_graph.py`.
