# Caissa system overview

One page that answers: *what are the systems, what does each one own, and how does
work flow between them?* Every arrow below carries real persisted data. There is
no stage in this document that exists only in the UI, and no number that is
computed anywhere except the system that owns it.

## The loop

```
                   ┌──────────────────────────────────────────────┐
                   │                                              │
  PGN / platform ──┴─► IMPORT ──► GAME STORE ──► ANALYSIS ─────────┴──► GAME INTELLIGENCE
                       (games,     (moves,        (Stockfish,              (report: critical
                        players)    positions)     move rows,               moments, phases,
                                                    MultiPV)                 accuracy, lessons)
                                                          │
                                                          ▼
                                            PLAYER INTELLIGENCE (Chess DNA)
                                            profile · insights · trends
                                                          │
                              ┌───────────────────────────┼───────────────────────────┐
                              ▼                           ▼                           ▼
                       TRAINING ENGINE            OPPONENT INTELLIGENCE        COACHING WORKSPACE
                       exercise library,          profile · repertoire ·       context · debrief ·
                       attempts, retention,       tendencies · preparation      feed · focus · plan
                       review queue                      │
                              │                          │
                              └────────────► SCENARIOS / WHAT-IF ◄──────────┘
                                             comparison · counterfactual ·
                                             turning points · training bridge
                                                          │
                                                          ▼
                                                    AI AGENT (tools)
                                                          │
                                                          ▼
                                              EXPLANATION (LLM, or an honest refusal)
                                                          │
                                                          ▼
                                                     NEXT GAME ──► measured change
```

## Who owns what

| System | Package / service | Owns | Never does |
| --- | --- | --- | --- |
| Import | `argus.importing`, `routes/games.py` | PGN/platform ingestion, validation, dedupe, players | evaluate a position |
| Analysis | `argus.analysis`, `analysis_jobs.py` | Stockfish runs, per-move evaluations, MultiPV, critical positions | interpret, advise |
| Game Intelligence | `argus.intelligence`, `report_service.py` | the structured game report (phases, material, tactics, accuracy, forecast, lessons) | call an LLM |
| Player Intelligence | `argus.player_intelligence` | the profile: coverage, per-phase and per-category metrics, trends, Chess DNA, insights | invent a statistic |
| Training | `argus.training`, `training_service.py` | positions from real mistakes, attempts, adaptive difficulty, retention, review queue, opponent prep exercises | grade without the stored solution |
| Opponent Intelligence | `argus.opponent_intelligence` | repertoire, tendencies, position responses, historical stats, preparation brief | predict the opponent's move |
| Scenarios / What-if | `argus.scenarios`, `scenario_service.py` | move comparison, counterfactuals, why-not/what-if facts, turning-point explorer, scenario→training bridge | present a search as history |
| Coaching | `argus.coaching`, `coaching_service.py` | situation/mode resolution, prioritisation, feed, debrief, focus, plan | produce a chess fact |
| Intelligence Graph | `argus.intelligence_graph`, `graph_*` services | identity and evidenced relationships between stored objects; bounded, authorized traversal | be a source of chess facts, or duplicate a model |
| Agent | `argus.ai_agent` | tool selection, evidence collection, claim validation | compute chess, or reach outside Caissa |
| Predictions | `argus.ml`, `prediction_service.py` | the gated route to a validated model | serve an unvalidated model |
| Explanation | `argus.llm`, `routes/coach.py` | prose over evidence | be a source of truth |

## The hand-offs that matter

**Import → analysis.** Import stores the game and its moves. Analysis is a
background job (`analysis_jobs.py`) that writes one row per ply (`move_analyses`),
then materialises critical positions. A game can be *partially* analysed; the
pipeline's `analysis_status` says what the pipeline did, and
`check_data_consistency.py` says whether the stored rows agree with the stored
moves.

**Analysis → game intelligence.** The report is deterministic and stored. It is
the only thing the coach, the debrief and the UI read about a finished game — no
screen recomputes an evaluation.

**Game intelligence → player intelligence.** The profile aggregates stored report
metrics across a player's analysed games, carrying coverage and sample size with
every number. "Insufficient data" is a first-class answer.

**Player intelligence → training.** Exercises are derived from *stored* analysis:
the position before a mistake, the engine's own solution, the source game and ply.
Difficulty adapts on more than correctness (see `training-engine.md`).

**Analysis + player → opponent intelligence.** Tendencies are counts over the
opponent's stored games. Repertoire is what was played, not what is expected.

**Everything stored → scenarios.** A comparison, counterfactual, why-not or what-if
is a *new engine search*, clearly labelled as such, and always shown beside the
move that was actually played.

**Coaching → the workspace.** `CoachContext` resolves where the user is (live game,
review, training, preparation, position study) and what Caissa knows about it;
`gaps` lists what it does not. The feed turns verified findings into cards, each
linking to its evidence and each dismissible. The debrief is the eight-step review
read from the stored report. `focus` answers "what should I work on?" or explains
why it cannot.

**Agent → explanation.** Tools return facts; the evidence packet holds them; the
validator checks the prose against the packet; the answer shows both. With no LLM
configured the agent still answers from tools and says it cannot write prose.

## Product surfaces (frontend)

| Route | Purpose |
| --- | --- |
| `/` | Dashboard: the library and how to add to it |
| `/games`, `/game/[id]`, `/game/[id]/report` | Library, board + move analysis, game intelligence report |
| `/coach?tab=coach` | AI Coach conversation |
| `/coach?tab=today` | Coaching workspace: context, focus, feed, debrief |
| `/players`, `/players/[id]` | Player list and Chess DNA |
| `/opponents` | Opponent intelligence and preparation |
| `/collections` | Study Collections: curated pointers to games, positions, exercises, insights |
| `/progress` | Progress: measured change between earlier and recent games, with the causality warning |
| `/search` | Unified search across every stored kind |
| `/intelligence` | Intelligence Explorer: the evidence graph by position, game or player |
| `/training`, `/training/solve/[id]` | Exercise library and solver |
| `/lab` | Position lab (send any FEN to the engine) |
| `/scenarios` | What-If Lab |
| `/import` | Import from PGN or a platform account |

Navigation is intentionally one entry per *job*, not per system: several systems
combine behind "Coach", "Opponents" and "Training".

## Verification

| Command | What it proves |
| --- | --- |
| `pytest` | the unit/integration suite (engine tests marked `engine`) |
| `scripts/system_check.py` | the deployment's real state, dependency by dependency |
| `scripts/verify_coaching.py` | the whole coaching loop, end to end, on real data |
| `scripts/verify_scenarios.py` | the what-if gate |
| `scripts/verify_intelligence_graph.py` | the Phase 13 graph gate, end to end |
| `scripts/check_data_consistency.py` | that stored analysis still describes its game |

## The workspace surfaces (Phase 11)

Beyond the loop above, the coaching workspace adds four reusable answers, all of
them read-only and engine-free:

* **Show me why** (`GET /api/coaching/evidence`) — resolves any claim to its
  stored evidence, classifying each reference as an engine fact, a Caissa
  feature, an interpretation or a prediction, and reporting every reference it
  cannot follow as a gap. Rendered by the shared `EvidencePanel`.
* **Study Collections** (`/api/coaching/collections`) — named, typed pointers to
  stored things. A collection references, never copies, and enforces which item
  kinds it permits.
* **Unified search** (`GET /api/coaching/search`) — one ranked query across games,
  players, training, scenarios, insights, openings and collections; distinguishes
  "nothing stored" from "nothing matched", each with a reason.
* **Match preparation** (`POST /api/coaching/match-preparation`) — a stored
  snapshot plus the `Caissa MATCH BRIEF`, every section gated on its sample, none
  of it a prediction.
* **Progress comparison** (`GET /api/coaching/progress/compare`) — the measured
  difference between two periods of a player's games, carrying its sample and the
  causality note wherever it goes.

## Live chess and real time (Phase 12)

Phase 12 adds a live game alongside the stored-game pipeline, and reuses it rather
than duplicating it:

* **A live game is server-authoritative.** The board is rebuilt from the stored
  position; a client sends an intent and receives the resulting state. There is no
  client-supplied FEN, clock, result or version that is trusted.
* **The clock is derived from timestamps**, not a counter, and a sweeper applies a
  flag fall through the same validated transition a move uses.
* **Events are sequenced.** Each carries a `sequence_number`; a client that missed
  events receives exactly the difference, or a full state when the gap cannot be
  filled. The database log is the source of truth; the in-process hub is only a
  latency optimization.
* **Fair play is enforced in the backend.** Competitive games are clamped to
  `no_analysis`; the coach refuses engine moves and every engine-backed agent tool
  is unavailable while a competitive live game is active.
* **The post-game hand-off is the existing pipeline.** A finished live game becomes
  a library game, and Phases 3–11 run on it unchanged.

## The intelligence graph (Phase 13)

Phase 13 connects everything above into one evidence-driven graph, without
duplicating any of it:

* **A relationship is only stored with its evidence.** Structural edges (a game
  contains its positions) need none; derived edges (a player has a pattern, a
  position is similar to another) carry an `EvidenceReference` and are refused
  without one. "Related" is the weakest edge kind and still requires evidence.
* **Similarity is a category, not a score.** `exact`, `equivalent`,
  `structurally_similar`, `opening_similar`, `tactically_similar` — the name is the
  claim, and only `exact` is ever labelled an exact match.
* **Every traversal is bounded and authorized**, and the count of nodes withheld by
  authorization is reported rather than the nodes themselves.
* **The what/why surfaces** are the Position, Game and Player Explorers, the
  "Why?" trace (relationship → evidence → sample → methodology → gaps), the graph
  health check and the sourced chess knowledge concepts.
* **The agent gains nine graph tools**, so a multi-hop answer can name a pattern,
  the positions behind it and the games behind those — with a stored reference at
  every step, validated into the evidence packet.

The storage decision is published at `GET /api/graph/method`: **PostgreSQL
relational tables with a JSON evidence column**. A graph database was evaluated and
rejected because the access patterns are bounded, indexable traversals, not
arbitrary paths. Full methodology: `docs/intelligence-graph/`.

## Related documents

- `architecture.md` — layering, request flow, and the storage schema
- `intelligence-graph/` — the Phase 13 graph: architecture, nodes, relationships,
  evidence, versioning, authorization, queries, performance
- `coaching.md` — modes, context, prioritisation, feed, debrief, focus, plan
- `game-intelligence.md`, `player-intelligence.md` — the report and the profile
- `training-engine.md` — generation, grading, difficulty, retention
- `opponent-intelligence.md` — repertoire, tendencies, preparation
- `scenarios.md` — comparison, counterfactuals, refusals, the training bridge
- `ai-agent.md` — tools, permissions, evidence, validation
- `live-chess.md` — the live domain, state machine, clock, fair play, engine limits
- `realtime-protocol.md` — the transport, event schemas, synchronization, error codes
- `security.md` — the boundaries and the tests that hold them
- `deployment.md` — configuration, health checks, production notes
