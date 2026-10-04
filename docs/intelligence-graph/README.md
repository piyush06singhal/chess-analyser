# The Caissa Intelligence Graph (Phase 13)

Phase 13 adds an evidence-driven intelligence layer *above* the domain. It does not
duplicate a single model: it records **identity and relationships** between the
objects Caissa already stores — players, games, positions, openings, patterns,
insights, training, opponents, scenarios and knowledge — and it refuses to store a
derived relationship without the evidence that produced it.

The guiding rule is the same one the whole project uses: **no fake data**. A query
with nothing behind it answers "no verified relationship found", not a plausible
default.

## Read in this order

| Document | Answers |
| --- | --- |
| Architecture | what the layer is, how it is layered, why it is stored in relational tables |
| Nodes | the controlled node vocabulary, identity keys and position fingerprints |
| Relationships | the edge vocabulary, declared shapes, and which edges require evidence |
| Evidence | `EvidenceReference`, tracing, similarity levels, ranking, and the coach packet |
| Versioning | schema/methodology/knowledge versions, snapshots and invalidation |
| Authorization | how privacy is enforced before anything is returned |
| Queries | the API surface, the explorers, the why-trace and the agent tools |
| The map | the Intelligence Map (§39): the force-directed neighbourhood view |
| Performance | indexes, bounds, caching and the observability counters |

## The one-line version

```
domain objects → graph nodes/edges (with JSON evidence) → authorized traversal
              → evidence packet → grounded answer, every step traceable
```

`GET /api/graph/method` publishes the vocabulary, versions and storage decision;
`GET /api/graph/health` publishes the graph's integrity.

Verification: `tests/test_intelligence_graph_{core,api,agent,e2e}.py` and
`scripts/verify_intelligence_graph.py`.

## The Caissa Intelligence Graph — architecture

Phase 13 adds one layer *above* the domain Caissa already has. It introduces no new
chess concepts and no duplicate game data. It records **identity and relationships**
between the objects the earlier phases already store — players, games, positions,
openings, patterns, insights, training positions and attempts, scenarios, opponents
and knowledge concepts — and it refuses to store a derived relationship without
evidence.

```
players · games · positions · openings · patterns · insights
        · training · scenarios · opponents · knowledge
                          │
                          ▼
              intelligent-graph materialization        apps/api/services
        (graph_service, graph_intelligence)
                          │
                          ▼
       ┌──────────────────────────────────────────────────┐
       │  graph_nodes / graph_edges (JSON evidence)         │  ← stored in the
       │  graph_snapshots                                   │    existing database
       └──────────────────────────────────────────────────┘
                          │
              ┌───────────┼────────────┐
              ▼           ▼            ▼
      IntelligenceGraph  Explorers   Agent graph tools
      Service (§30)      (§40–§43)   (§31)
              │
              ▼
       CoachEvidencePacket → grounded answer (validated against the packet)
```

## Why a relational store, not a graph database (§3)

The storage decision is published at `GET /api/graph/method` as
`"postgresql-relational-tables-with-json-evidence"`.

The graph is stored in **ordinary relational tables** (`graph_nodes`,
`graph_edges`, `graph_snapshots`) with the evidence for an edge held in a **JSON
column**. A dedicated graph database was evaluated and **rejected**:

* The access patterns are **bounded, indexable traversals**, not arbitrary path
  queries. The explorers ask "everything directly connected to this position /
  game / player", "which games share this opening", "which pattern explains this
  position". Each of those is a two-index lookup, not a variable-length path search.
* The graph is a **derived view over existing rows**. The authoritative data stays in
  the tables that already own it; nodes reference them by `(node_type, node_key)`.
  Keeping the graph beside that data means a rebuild is a local transaction, not a
  second system to keep consistent.
* Evidence is a **JSON document** (a list of `EvidenceReference` records). A graph
  database would either lose it or require a parallel store, and evidence is the
  entire point of the layer.

If a future requirement genuinely needs arbitrary path search at scale, the
`GraphStore` abstraction (`apps/api/argus_api/services/graph_store.py`) is the single
seam to replace; nothing above it knows how the graph is persisted.

## Layering

| Layer | Path | Responsibility |
| --- | --- | --- |
| Core vocabulary | `argus.intelligence_graph.taxonomy` | the only place node/edge kinds are declared |
| Evidence | `argus.intelligence_graph.evidence` | `EvidenceReference`, and the check that a derived edge carries some |
| Identity | `argus.intelligence_graph.fingerprint` | position identity: placement, side, castling, en passant |
| Similarity | `argus.intelligence_graph.similarity` | ordered, non-interchangeable similarity levels |
| Models | `argus.intelligence_graph.models` | node/edge/snapshot/traversal value objects |
| Store seam | `argus.intelligence_graph.store` | `GraphStore` protocol + in-memory implementation |
| Authorization | `argus.intelligence_graph.access` | one access policy, applied before anything is returned |
| Service | `argus.intelligence_graph.service` | the only way to read or write the graph |
| Health | `argus.intelligence_graph.health` | orphans, invalid shapes, missing evidence, dangling edges, stale versions |
| Ranking | `argus.intelligence_graph.ranking` | documented evidence ranking |
| Packet | `argus.intelligence_graph.packet` | what the coach model is given, and the answer validator |
| Knowledge | `argus.intelligence_graph.knowledge` | sourced concepts, and which concepts a position exhibits |
| SQL store | `argus_api.services.graph_store` | `GraphStore` over the ORM |
| Materialization | `argus_api.services.graph_service`, `graph_intelligence` | incremental updates, rebuilds, pattern/training/opponent/scenario links |
| Explorers | `argus_api.services.graph_explorers` | Position/Game/Player Explorer, Why, health, snapshot, search |
| Routes | `argus_api.routes.graph` | the `/api/graph` surface |
| Agent tools | `argus.ai_agent.tools.graph` | nine schema-validated graph tools |

The LLM never calls `IntelligenceGraphService` directly. It calls the **graph
tools**, which validate the request and call the service with an authorized scope.

## Data flow

**Incremental (§34).** Finishing a game calls `update_game(db, game_id)`, which
materializes the player → game → position → opening nodes and edges for that game
only. `POST /api/graph/players/{id}/update`, `/opponents/update` and
`/scenarios/update` do the same for their families. Every write is idempotent
(the edge identity is unique), so a re-run is safe.

**Rebuild.** `POST /api/graph/rebuild` re-derives the graph from the domain. It is
bounded by `limit_games`, so it can be run against a subset without touching the rest.

**Invalidation (§35).** When source data changes, `IntelligenceGraphService.invalidate`
drops the *derived* edges affected by the change. Structural edges (a game contains
its positions) are re-materialized by the next incremental update; derived edges
(patterns, similarity) must be recomputed from the new data rather than left stale.

**Reads.** The explorers compose authorized neighbours; the why-trace follows a
node's justification to the stored references behind it; the health check reports
the graph's consistency; the snapshot records its versions and counts.

## What the graph is not

* **Not a second game store.** A node carries a label and a few small display
  attributes — never a copy of the game, the analysis or the report.
* **Not a source of chess facts.** Every relationship is either a structural fact
  read from the domain or a derived claim carrying the evidence that produced it.
* **Not an inference engine.** There is no path-length reasoning that invents a
  connection; a `RELATED_TO` edge still requires explicit evidence (see
  the relationships section).

## Verification

* `.venv/bin/python -m pytest tests/test_intelligence_graph_core.py tests/test_intelligence_graph_api.py tests/test_intelligence_graph_agent.py tests/test_intelligence_graph_e2e.py -o addopts=`
* `.venv/bin/python scripts/verify_intelligence_graph.py` — walks the phase gate
  against a **running** API with a real engine; reports SKIP (never PASS) when the
  engine is unavailable.

See the nodes section, the relationships section, the evidence section, the versioning section,
the authorization section, the queries section and the performance section for the details.

## Nodes — what the graph knows about

A **node** is a pointer to something that already exists. It has a `node_type` from
the controlled `NodeType` enum and a `node_key` that identifies the object in its
own domain (a game id, a player id, a position hash, a pattern id). It carries a
human label and a small `attributes` map for display — never a copy of the object.

The vocabulary lives in one place — `argus/intelligence_graph/taxonomy.py` — so no
code path can invent a node kind. `GET /api/graph/method` publishes the list.

## Identity keys

| Node type | `node_key` | Backed by |
| --- | --- | --- |
| `player` | player id | `players` |
| `game` | game id | `games` |
| `move` | `game_id:ply` | `game_moves` / `move_analyses` |
| `position` | 32-hex position hash | `game_positions` (via the fingerprint) |
| `opening` | ECO code (or the opening key) | `Game.eco_code` / opening identification |
| `opening_node` | opening-line node key | the identified opening line |
| `game_phase` | phase key | Phase 4 phase detection |
| `pattern` | pattern id | derived, evidenced |
| `tactical_pattern` | pattern id | Phase 4 tactical events |
| `positional_pattern` | pattern id | Phase 4 positional features |
| `king_safety_pattern` | pattern id | Phase 4 king-safety events |
| `material_pattern` | pattern id | Phase 4 material events |
| `insight` | insight id | Phase 4/5/9 insights |
| `training_position` | training position id | `training_positions` |
| `training_attempt` | training attempt id | `training_attempts` |
| `training_session` | session id | `training_sessions` |
| `scenario` | scenario id | `scenarios` |
| `prediction` | prediction id | gated Phase 6 model output |
| `opponent_profile` | player id | Phase 9 profile (snapshot) |
| `preparation_report` | report key | Phase 9/11 preparation |
| `knowledge_document` | document id | `knowledge_documents` |
| `knowledge_concept` | concept slug | `knowledge_concepts` |
| `study_item` | item key | Phase 11 Study Collections |

## Patterns (§16)

Patterns are classified by *what kind of chess problem they are*, independently of
how they are stored. The categories are the controlled `PatternType` enum:
`tactical`, `positional`, `king_safety`, `material`, `opening`, `calculation`,
`conversion`, `recovery`, `endgame`, `time_management`. Only categories Caissa can
actually detect exist.

Four categories map to a **dedicated** node kind — tactical, positional, king
safety and material — because Caissa has a real detector for each. Everything else
is a generic `pattern` node carrying its `pattern_type` attribute. `pattern_node_type`
is the one function that performs the mapping.

## Position identity (§11)

Two positions are the same only when the same player to move has the same legal move
set. The **position node key is a hash of the position identity**, not of the FEN
string:

* piece placement,
* side to move,
* castling rights,
* en-passant target.

The halfmove clock and fullmove number are deliberately excluded — they are
bookkeeping, so a transposition reached by two move orders compares equal. The
hash is `sha256` of the normalized four-field FEN, truncated to 32 hex characters,
so it is deterministic across processes and runs. `fingerprint(fen)` returns the
decomposed identity and `valid`; an invalid FEN produces an invalid fingerprint and
never a node.

## Relationships — typed, directional, evidenced

An **edge** connects two nodes with an `EdgeType` from the controlled enum. Every
edge kind declares its legal `(from_type → to_type)` **shape**, and a write whose
shape is not declared is refused with an explanation — a writer cannot silently
reverse an edge. Shapes live beside the vocabulary in `taxonomy.py` and are exposed
by `edge_shape`, `edge_shape_valid` and `edge_shape_error`.

`GET /api/graph/method` publishes the full list of edge types.

## The edge kinds

| Edge | Shape | Meaning |
| --- | --- | --- |
| `played` | player → game | the player took part in the game |
| `participated_in` | game → player | inverse of `played` |
| `contains_move` | game → move | the game contains the move |
| `contains_position` | game → position | the game contains the position (structural) |
| `occurs_in` | move/position → game | inverse of the two above |
| `reaches` | position → position | one ply on in the game |
| `deviates_from` | position/game → opening_node/opening | left the identified line |
| `belongs_to_opening` | game/position/opening_node → opening | opening classification |
| `plays_opening` | player → opening | the player has played this opening |
| `follows` | opening_node → opening_node | one step along an opening line |
| `has_pattern` | player → pattern | the player exhibits the pattern |
| `pattern_evidence` | pattern → position/game/move | the position/game that shows it |
| `has_insight` | player → insight | the insight belongs to the player |
| `generated_training` | pattern/game/scenario/insight → training_position | where the exercise came from |
| `trained_with` | player/training_position/pattern → training_position | training linkage |
| `attempted` | player → training_attempt | the attempt belongs to the player |
| `attempt_of` | training_attempt → training_position | the attempt answered the position |
| `in_session` | training_attempt → training_session | the attempt is part of a session |
| `improved_after` | pattern → training_position | a measured improvement link (evidenced) |
| `similar_to` | position → position | a similarity level is attached (evidenced, never exact) |
| `responded_with` | position/player → move | the opponent's observed answer (evidenced) |
| `prepared_for` | preparation_report → opponent_profile/player | the report targets the opponent |
| `derived_from` | scenario/prediction/training_position → game/position/move | the source |
| `predicted` | position → prediction | a gated model output |
| `exhibits` | position/pattern → knowledge_concept | the board feature a concept names (evidenced) |
| `explains` | knowledge_concept/document → knowledge_concept | concept-to-concept relation |
| `supported_by` | any → knowledge_document/concept | the source behind a concept |
| `derived_from_knowledge` | knowledge_concept → knowledge_document | chunk/source linkage |
| `collected` | study_item → entity | a Study Collection references an item |
| `related_to` | any → any | **the weakest kind**, permitted only with explicit evidence |

## Evidence-bearing edges (§6/§52)

Some edges are *structural facts* — a game contains its positions, a player played a
game — and need no evidence beyond existing. Others are **derived claims**, and the
taxonomy marks them so they cannot be stored without evidence:

`has_pattern`, `pattern_evidence`, `has_insight`, `similar_to`, `improved_after`,
`responded_with`, `derived_from`, `predicted`, `exhibits`, `related_to`,
`generated_training`, `deviates_from`.

Writing one of these with no usable reference raises `GraphEvidenceError` with the
edge named and the reason stated. `RELATED_TO` exists precisely so that "related"
is expressible — but it is the *weakest* kind and still requires evidence, because
"related" without evidence is the fabricated connection the phase forbids.

## Sample size

When an evidenced edge is written without an explicit `sample_size`, it defaults to
the **number of distinct evidence references** — the honest lower bound. It is never
an invented number.

## Reading edges

Neighbour queries are bounded and authorized:

* `service.neighbors(node_type, node_key, edge_types=…, direction=…)` — immediate
  neighbours, with dangling endpoints retained so health can report them.
* `service.traverse(…)` — breadth-first, depth- and node-limited, returning the hops
  taken so an answer can be explained, plus a count of nodes withheld by
  authorization.

A query that finds no edge answers with an honest empty result and a note — "no
verified relationship found" — never a plausible default.

## Evidence — the reason a relationship may exist

Every relationship in the graph that is not a plain structural fact is a **derived
claim**: a player *has* a pattern, a position is *similar* to another, training
*improved* a result. The rule is absolute — a derived relationship without evidence
must not be presented as an established fact.

Evidence takes one shape, whatever the claim. `EvidenceReference` can point at a
game, move, position, analysis, training position, training attempt, session,
scenario, insight, player, opponent, prediction, dataset version, model version,
knowledge source or engine. Exactly the fields that identify the object are filled;
`has_anchor()` enforces that at least one is present, and a reference that names no
anchor is dropped because it proves nothing.

```
EvidenceReference(kind=…, game_id=…, ply=…, value=…, unit=…, statement=…)
```

A reference keeps the *kind* of evidence, a human label, and — when the claim is
numeric — the stored `value` and `unit` beside the pointer, so the evidence is
checkable rather than merely asserted.

## Three rules

1. **A derived edge carries evidence.** `require_evidence` refuses any edge in the
   evidence-bearing set (the relationships section) with no usable reference, raising
   `MissingEvidenceError` inside the domain and `GraphEvidenceError` at the API.
2. **A source that disappears is reported, not erased.** Deleting the object a
   reference points at does not delete the reference; the why-trace reports it as a
   **gap**. The history that an analysis once ran survives the row it analysed.
3. **A reference is never invented.** The graph stores only references the domain
   actually produced.

## Tracing (§43)

`IntelligenceGraphService.trace_evidence(node_type, node_key)` follows a node's
justification to the stored objects behind it. It returns:

* the resolved references, grouped by `EvidenceKind`,
* the hops taken (edge type, endpoints, evidence count, sample size),
* the **gaps**: edges that are derived but whose evidence is missing or whose
  referenced object can no longer be found.

`GET /api/graph/why/{node_type}/{node_key}` presents that as relationship,
evidence, sample, methodology version, date range, ranking methodology and gaps.
The `EvidencePanel` in the UI renders the same classification: engine fact,
Caissa-derived feature, interpretation or prediction.

## Similarity is a category, not a score (§12)

The most damaging thing a knowledge graph can do is present a *similar* position as
an *exact historical match*. So similarity is a small, ordered set of named levels,
and **the name is the claim**:

| Level | Definition | Board claim? |
| --- | --- | --- |
| `exact` | identical placement, side to move, castling rights and en-passant target | yes |
| `equivalent` | a different FEN with an identical legal move set (a bookkeeping-only transposition) | yes |
| `structurally_similar` | same side to move, same material, same pawn structure | yes |
| `opening_similar` | the same stored opening line | no (verified against stored opening data) |
| `tactically_similar` | the same stored tactical motif | no (verified against stored tactical events) |

`classify` returns the **strongest** level that holds, so a pair that is `exact` is
never also reported `structurally_similar`. A caller needing a weaker relationship
asks for exactly that level with `qualifies`; a stronger one does not imply it.
`None` is the honest "no verified similarity". The definitions are published
verbatim so the UI can never overstate.

## Ranking (§29)

When several pieces of evidence could support a claim, `ranking.py` orders them by
documented factors — relevance, recency, source reliability, sample size, exactness,
similarity and ownership — with weights declared in one `WEIGHTS` table.
`why` returns `ranking_methodology()` alongside the evidence so the ordering is
inspectable. A factor with no measurement is not filled with a guess.

## Evidence reaches the answer, not just the database

The coach reads a `CoachEvidencePacket` (§44): the typed facts retrieved for the
turn, each with its provenance and its recorded absences. The generated prose is
then validated against the packet — an evaluation, a count, a probability or an
opening name with no matching evidence is **refuted** and reported. The graph is
what makes multi-hop answers checkable: an answer can name the pattern, then the
positions behind it, then the games behind those, and every step is a stored edge
with a reference at the end of it.

## Versioning and snapshots (§8/§37)

A relationship that cannot be dated or attributed cannot be trusted. The graph
therefore versions its *vocabulary*, its *derivation* and its *content* separately,
and records the versions with every write.

## The three versions

| Version | Constant | Bumped when |
| --- | --- | --- |
| **Schema** | `GRAPH_SCHEMA_VERSION` (`13.0`) | node/edge semantics change in a way that invalidates a stored edge |
| **Methodology** | `GRAPH_METHODOLOGY_VERSION` (`13.0`) | the *derivation* changes (how a pattern is detected, how similarity is computed) |
| **Knowledge** | `KNOWLEDGE_GRAPH_VERSION` (`13.0`) | the sourced concept base changes |

A stored edge whose `schema_version` is older is reported **stale** by the health
check rather than silently read. Evidence records the methodology that produced it,
so an insight can always name the method responsible for it.

`GET /api/graph/method` returns all three versions plus the node and edge lists and
the storage description, so the whole vocabulary is inspectable from one endpoint.

## Stamping

* `upsert_node` stamps the current methodology version when a node does not already
  have one.
* `write_edge` stamps the methodology version, sets `sample_size` from the evidence
  when absent, and timestamps the write.
* Rows carry `methodology_version` and `data_cutoff` columns, both indexed.

## Snapshots

`IntelligenceGraphService.snapshot(data_cutoff=…)` produces a **reproducible
description** of the graph at a point in time: a `graph_version` combining the
schema version with the cutoff (`13.0+YYYYMMDDTHHMMSSZ`), the schema and methodology
versions, the cutoff, per-type node and edge counts, and totals.

`POST /api/graph/snapshot` persists it. Persisted snapshots let a reading be
reproduced: "the graph held N position nodes and M pattern edges as of this cutoff,
under this methodology". `graph_snapshots` is indexed on `data_cutoff` so snapshot
history is queryable.

## Invalidation vs. staleness

Changing source data does not silently rewrite the graph:

* `IntelligenceGraphService.invalidate(game_id=…)` drops the **derived** edges
  affected by a change (by game, by node, or by edge type) and returns the count
  removed. Structural edges are re-materialized by the next incremental update.
* The **health check** (`/api/graph/health`) reports stale versions, orphans,
  invalid shapes, missing evidence and dangling endpoints — it never repairs them
  silently. A rebuild recomputes derived edges from the new data.

Versioning is also what makes a rebuild safe: a re-derived edge replaces the stored
one under the current methodology, and an edge left from an older methodology is
visible as stale until it is.

## Authorization — enforced in one place

Privacy is a property of the graph, not a convention. Every node and every edge is
filtered through a `GraphAccessPolicy` **before** it is returned, so no caller — the
API, the agent, the UI — can read something it is not allowed to read.

## One decision point

`IntelligenceGraphService` holds a `policy`. Reads call `policy.can_read_node(node)`
and, when the answer is no, return nothing *and* increment an `authorization_denials`
counter. The refusal is counted, never returned as content.

The policy is applied in three places inside the service, once each:

* `get_node` / `nodes_of_type` — a node the caller cannot read is absent.
* `neighbors` — a neighbour the caller cannot read is skipped, not replaced.
* `traverse` — the count of nodes withheld travels on the result
  (`denied_count`), so a caller learns a traversal was bounded by authorization
  without learning what was behind the boundary.

## What the policy scopes

`GraphAccessPolicy` carries the caller's `user_id` and, today, an optional set of
`allowed_game_ids`. A node is readable when:

* it is not user-scoped (shared knowledge, an opening, a pattern definition), or
* it belongs to the caller, or
* it is reachable through a game the caller is allowed to read.

`unrestricted()` is the explicit "single-user, whole library" policy used when there
is no tenancy yet. It is a deliberate, named choice — not an implicit bypass — and
the moment accounts arrive it is replaced in one place.

## Why this ordering matters

The alternative — filter in the route, or in the query, or in the UI — creates one
place per surface where a check can be forgotten. Here the graph has exactly one
gate, the same one the rest of Caissa uses: an unknown or forbidden object answers
"not found", and the number of denials is observable rather than silent.

## Relation to the rest of Caissa

* Game-level authorization already lives in `argus_api.services.authorization`; a
  graph policy that scopes by game composes with it rather than re-implementing it.
* The agent's `AgentContext.available_game_ids` is the allow-list its game tools
  enforce. The graph policy is the same idea one layer down.
* Privacy-separated domain data (Phase 5 `user_specific` features, Phase 6 dataset
  scope) is preserved: the graph references those objects, it does not widen who may
  read them.

See `docs/security.md` for the platform-wide boundary and the tests that hold it.

## Querying the graph — explorers, why-traces, search and tools

All reads go through `IntelligenceGraphService`, which applies authorization and
bounds every traversal. Nothing below runs an engine or invents a relationship: a
section with nothing behind it is returned empty with a note.

## The `/api/graph` surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/method` | the vocabulary, versions and storage description |
| `GET` | `/health` | the Caissa Graph Health Check (§36) |
| `GET` | `/position?fen=` | Position Explorer (§40) |
| `GET` | `/games/{game_id}` | Game Explorer (§41) |
| `GET` | `/players/{player_id}` | Player Explorer (§42) |
| `GET` | `/why/{node_type}/{node_key}` | the "Why?" trace (§43) |
| `GET` | `/neighborhood/{node_type}/{node_key}?depth=&limit=` | the Intelligence Map neighbourhood (§39) |
| `GET` | `/nodes/{node_type}?limit=` | a page of nodes of one kind (authorized) |
| `GET` | `/search?kind=` | graph-backed structured search (§38) |
| `GET` | `/concepts?query=` | the sourced chess concepts Caissa holds |
| `GET` | `/concepts/{slug}` | one sourced concept, with its licence |
| `POST` | `/snapshot` | persist a reproducible snapshot (§8) |
| `POST` | `/rebuild?limit_games=` | rebuild the graph from the domain |
| `POST` | `/games/{game_id}/update` | incremental update for one game (§34) |
| `POST` | `/players/{player_id}/update` | patterns + training links for one player |
| `POST` | `/opponents/update` | opponent links |
| `POST` | `/scenarios/update` | scenario links |
| `POST` | `/knowledge/seed` | seed the sourced concept base (idempotent) |
| `POST` | `/knowledge/link` | link stored positions to the concepts they exhibit (§27) |

## The explorers (§40–§42)

**Position Explorer** answers "what does Caissa know about this position?" from
stored evidence only:

* identity (`fingerprint`, `position_hash`), and whether the position is materialized
  as a node yet,
* **exact matches** — games that contain this exact position, labelled `exact: true`,
* **similar** positions grouped by controlled similarity level, from a bounded scan
  of the most recent stored positions, with the scan size stated,
* openings, pattern evidence, knowledge concepts, training positions and scenarios
  linked to the position.

An invalid FEN is refused with a reason. A position that is not yet in the graph
says so and invites an update — it is not reported as empty.

**Game Explorer** returns the opening link, contained positions, patterns,
training positions derived from the game, and related games that share an opening.

**Player Explorer** returns the player's games, openings, patterns, training
positions and attempts, a correct-attempt count, and opponents — respecting the
access policy, so a private game does not leak through it.

## The "Why?" trace (§43)

`GET /api/graph/why/{node_type}/{node_key}` returns the node's label, the hops taken
(edge type, endpoints, evidence count, sample size), the evidence grouped by kind,
the maximum sample size, the methodology version, the evidence date range, the
ranking methodology, and the **gaps** — derived edges whose references are missing
or can no longer be resolved. A gap is reported, never hidden.

## The Intelligence Map (§39)

`GET /api/graph/neighborhood/{node_type}/{node_key}` walks both directions from a
node and returns the subgraph the map draws: the reached `nodes`, the `edges`
actually followed (with their controlled type and evidence/sample counts), whether
the walk was `truncated` by the node limit, and how many nodes authorization
`denied_count` withheld — as a count, never as content. `depth` is 1–3 and `limit`
is 1–200. See The map.

## Structured search (§38)

`GET /api/graph/search` resolves a *structured* query (kind, ECO, result, opponent,
pattern type, training category) to stored objects. Only the filters Caissa can prove
are accepted; an unknown filter is reported, not ignored. This is the structured half
of unified search — free-text queries still go through the Phase 11 search.

## Health (§36)

`GET /api/graph/health` runs `check_graph` and reports orphaned nodes, invalid edge
shapes, missing evidence, dangling endpoints, duplicates and stale versions, plus
the service counters. `healthy` is the top-line answer; the report is the detail.

## The Intelligence Explorer (frontend)

The `/intelligence` page is one unified surface with three tabs — Position, Game
and Player — over the same endpoints. It renders the explorers verbatim: an exact
match is labelled `exact`, a weaker similarity keeps its own name, and a section
with nothing behind it says so. Each player pattern carries a **Why?** button that
opens the why-trace (relationships, evidence grouped by kind, sample, methodology
version and any gaps), so a claim on screen is one click from the stored evidence
behind it. A strip at the top publishes the schema, methodology, knowledge and
storage versions from `/api/graph/method`.

## The agent's graph tools (§31)

Nine schema-validated tools let the coach traverse the graph without ever touching
the database directly:

| Tool | Answers |
| --- | --- |
| `find_related_games` | games connected to a game or player |
| `find_related_positions` | positions by exact/similar level |
| `find_player_patterns` | a player's evidenced patterns |
| `find_pattern_evidence` | the positions/games behind a pattern |
| `find_training_history` | training positions and attempts linked to a player/pattern |
| `find_opponent_connections` | how an opponent connects to patterns, positions, openings |
| `find_opening_connections` | games/players/patterns reachable from an opening |
| `find_knowledge_for_position` | the concepts a position exhibits |
| `trace_insight_evidence` | the stored references behind an insight |

Each returns structured evidence, so a multi-hop answer can name the pattern, the
positions behind it and the games behind those — with a stored reference at every
step. Tools are available only when their context is present, and an unavailable
tool records its absence in the evidence packet rather than being silently skipped.

## The Intelligence Map (§39)

The Explorer answers *"what is connected to this?"* as lists. The Map answers the
same question as a picture: a bounded, force-directed view of one node's
**neighbourhood**, so a connection like *pattern → position → game → player*
reads as a path instead of four separate sections.

It is not a second source of truth. Every node and every edge drawn comes back
from the graph's authorized traversal; the map adds layout, not facts.

## Where it lives

| Piece | Location |
| --- | --- |
| Endpoint | `GET /api/graph/neighborhood/{node_type}/{node_key}` |
| Service | `graph_explorers.neighborhood()` — a bounded `IntelligenceGraphService.traverse` |
| Component | `apps/web/src/components/intelligence-map.tsx` |
| Layout | `apps/web/src/lib/force-layout.ts` (deterministic, no dependency) |
| Surface | the **Map** tab of `/intelligence` |

## What the endpoint returns

`neighborhood(node_type, node_key, depth=1..3, limit=1..200)` walks outward in
**both directions** and returns:

* `nodes` — the root and everything reached, each as a normal node payload;
* `edges` — the relationships actually followed, with their controlled type and
  their evidence and sample counts (evidence references are *not* inlined; the
  Why-trace is where references live);
* `truncated` — whether the node `limit` was hit;
* `denied_count` — how many reached nodes authorization withheld, as a count,
  never as content;
* `depth`, `limit` and `methodology_version` so the picture is reproducible.

A node the caller may not see is refused with a reason (`found: false`), exactly
like the Why-trace.

## What the map is careful to do

* **Keep relationship names.** An edge is labelled with its own type
  (`contains_position`, `has_pattern`, `similar_to`, …). A similarity is never
  drawn as an exact match.
* **Say when it is partial.** Hitting the node limit raises a visible banner; a
  withheld node count is stated. The map never implies the neighbourhood ends
  where the drawing stops.
* **Say when there is nothing.** A stored node with no relationship within the
  requested depth renders as "nothing connected", not as an empty canvas.
* **Repeat itself for keyboards.** The SVG is one image to assistive technology,
  so the same edges are listed as buttons in a **Connections** list; selecting a
  node there (or on the drawing) opens the Why-trace.
* **Draw the same way twice.** Layout seeds from a stable hash of each node key
  and never uses randomness, so the same subgraph produces the same picture — a
  screenshot in a bug report can be reproduced.

## Verification

`tests/test_intelligence_graph_api.py` covers the endpoint: a bounded subgraph
with `contains_position` edges, a limit that reports `truncated`, an unknown node
refused honestly, an unknown node type rejected with 422, and a node key
containing a `/` still addressable. The frontend is checked with
`npx tsc --noEmit`, `npm run lint` and `npm run build`.

## Performance, bounds and observability

The graph is designed to stay cheap and predictable. Every query is bounded, the
heavy access paths are indexed, and the counters that would reveal a problem are
published rather than hidden.

## Indexes (§48)

The graph tables carry explicit indexes for the access patterns the explorers use:

**`graph_nodes`**

* `unique(node_type, node_key)` — one node per domain object.
* `ix_graph_nodes_type(node_type)` — a page of one kind.
* `ix_graph_nodes_key(node_key)` — resolve an object across kinds.
* `ix_graph_nodes_type_methodology(node_type, methodology_version)` — staleness scans.

**`graph_edges`**

* `unique(edge_type, from_type, from_key, to_type, to_key)` — idempotent writes.
* `ix_graph_edges_from(from_type, from_key)` — out-edges of a node.
* `ix_graph_edges_to(to_type, to_key)` — in-edges of a node.
* `ix_graph_edges_type(edge_type)` — all edges of one kind.
* `ix_graph_edges_methodology(methodology_version)` — stale-edge scans.

**`graph_snapshots`** — `ix_graph_snapshots_cutoff(data_cutoff)`.

**Knowledge tables** — `knowledge_documents(source_id)`,
`knowledge_chunks(document_id)`, `knowledge_concepts(source_id)` and
`knowledge_concepts(category)`.

The two-column `from`/`to` indexes are what make a neighbour lookup an index hit
rather than a scan, which is the reason a graph database was not needed (see
the architecture section).

## Bounds (§47)

No traversal may walk the graph unbounded. The service enforces:

* `DEFAULT_MAX_DEPTH = 3` and `MAX_NODE_LIMIT = 500` (`DEFAULT_NODE_LIMIT = 200`),
  with the effective limit clamped before the walk.
* A traversal that hits its node limit sets `truncated = true` — the caller is told
  it saw a bounded view, not given a false "that is everything".
* The Position Explorer's similarity scan is bounded to the **most recent 400 stored
  positions**, and the response states the scan size so a reader knows the boundary.
* Structured search clamps `limit` (1–100) at the route.
* Incremental updates and rebuilds are bounded; `rebuild` accepts `limit_games`.

## Caching

Similarity classification is computed on demand against the bounded scan; it is not
a stored cache, and the response says how many positions were scanned. Where the
domain already caches (Phase 5 profile snapshots, Phase 9 opponent snapshots, the
Phase 3 position cache), the graph references the cached objects rather than
re-deriving them, so it does not create a second caching layer with its own
staleness rules.

## Observability (§54)

`IntelligenceGraphService` keeps counters and exposes them through `metrics()` and
`GET /api/graph/health`:

| Counter | Meaning |
| --- | --- |
| `graph_updates` | node/edge writes |
| `graph_rebuilds` | invalidations / rebuilds |
| `graph_traversals` | traversals served |
| `average_traversal_latency_ms` | mean traversal time (from `traversal_ms_total`) |
| `authorization_denials` | reads refused by the access policy |
| `evidence_validation_failures` | derived edges refused for missing evidence |
| `orphan_nodes` | orphans found by the last health check |
| `invalid_edges` | invalid edge shapes found by the last health check |
| `knowledge_retrievals` | concept retrievals |
| `agent_graph_tool_calls` | graph tools invoked by the agent |

Counters hold **counts and durations only** — never game content. As with the rest
of Caissa they are per process: a restart resets them, and a multi-worker deployment
will need a shared store.

## The health check as a performance signal

`check_graph` also catches the conditions that degrade a graph over time: orphaned
nodes that no edge reaches, dangling endpoints, duplicate identities and edges left
on an older methodology. Reporting them keeps the graph from slowly accumulating
unreachable data — the failure mode a "grows forever" graph store has.

## Verification

`scripts/verify_intelligence_graph.py` exercises these paths against a running stack
and asserts, after the work is done, that the graph has no dangling, invalid or
missing-evidence edges — performance and integrity checked in the same pass.

