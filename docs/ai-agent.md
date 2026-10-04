# Caissa — AI Chess Agent Design

The Phase 7 agent is **not a chatbot with chess opinions**. It is a tool-using
chess intelligence system: it resolves what the user is talking about, retrieves
the relevant game/position/player data through a permissioned tool surface, runs
Stockfish when a question genuinely needs an evaluation, and writes its answer
**only** from the evidence it gathered. The language model supplies language. It
supplies no chess facts, and the architecture is arranged so that it cannot.

```
User
 ↓
AI Chess Agent
 ↓
Intent detection            (deterministic, no model — argus.ai_agent.core.planner)
 ↓
Tool selection              (permissioned, budgeted — argus.ai_agent.tools)
 ↓
Caissa services
 ├── Chess engine           analyze_position, analyze_position_multipv, compare_moves
 ├── Game Intelligence      get_game, get_game_moves, get_move_analysis,
 │                          get_critical_moments, get_game_summary,
 │                          get_game_analysis, get_game_trajectory
 ├── Player Intelligence    get_player_profile, get_player_statistics,
 │                          get_player_insights, get_player_evidence
 ├── Opening knowledge      get_opening_information, search_chess_knowledge,
 │                          list_chess_concepts
 ├── ML prediction          get_prediction_status, get_validated_prediction
 ├── Training system        get_training_requirements, get_training_recommendations
 ├── Live chess             get_live_game, get_live_position, get_live_game_status,
 │                          get_live_clock, get_live_game_history
 └── Intelligence graph     find_related_games, find_related_positions,
                            find_player_patterns, find_pattern_evidence,
                            find_training_history, find_opponent_connections,
                            find_opening_connections, find_knowledge_for_position,
                            trace_insight_evidence
 ↓
Evidence packet             (typed, with provenance, and recorded absences)
 ↓
LLM reasoning               (from the packet, under an explicit prohibition)
 ↓
Validation                  (high-value claims checked against the packet)
 ↓
Answer + evidence + actions + trace
```

Prompt version: **7.0**.

## Package layout

| Module | Responsibility |
| ------ | -------------- |
| `ai_agent/core/planner.py` | intent detection, tool shortlisting, deterministic fast paths |
| `ai_agent/core/loop.py` | the turn: resolve → plan → tools → evidence → generate → validate |
| `ai_agent/core/context.py` | board awareness: game, ply, FEN, player, mode, skill, authorized games |
| `ai_agent/core/evidence.py` | `EvidenceItem` / `EvidencePacket` — the only basis an answer may rest on |
| `ai_agent/core/response.py` | `Claim`, `ClaimKind`, `AgentAction`, `ValidationReport`, `AgentAnswer` |
| `ai_agent/core/collection.py` | tool payload → evidence item, with provenance assigned once |
| `ai_agent/tools/base.py` | tool contract: JSON Schema, permission, authorization, `ToolOutcome` |
| `ai_agent/tools/providers.py` | `AgentProviders` — the narrow seam onto Caissa services |
| `ai_agent/tools/*` | the tool families (position, engine, game, player, opening, knowledge, prediction, training, opponent, scenario, live, graph) |
| `ai_agent/memory/` | bounded conversation memory + context resolution |
| `ai_agent/safety/limits.py` | iteration, tool, engine, prompt and response budgets |
| `ai_agent/safety/validation.py` | the hallucination validator |
| `ai_agent/observability.py` | `AgentTrace` — what happened, with credentials scrubbed |
| `ai_agent/streaming.py` | the turn's lifecycle event stream |
| `ai_agent/performance.py` | latency measurement harness |
| `ai_agent/evaluation.py` | the runnable tool-selection + adversarial suite |
| `ai_agent/prompts/system.md` | the system prompt, versioned |

## Tools and schemas

A tool is the **only** way the agent can learn a chess fact. Every tool declares a
JSON-Schema for its arguments and an explicit list of its output fields, and
arguments are validated *before* the handler runs — an LLM emitting `{"depth":
"deep"}` or forgetting `ply` gets a structured refusal, not a stack trace or a
handler that silently does something else.

The declared catalogue (63 tools, as served by `GET /api/coach/tools`):

| Tool | Permission | Engine | Notes |
| ---- | ---------- | ------ | ----- |
| `analyze_position` | `any` | ✅ | depth clamped to the policy maximum (24) |
| `analyze_position_multipv` | `any` | ✅ | MultiPV width capped at 5 |
| `compare_moves` | `any` | ✅ | accepts UCI or SAN; illegal moves refused |
| `inspect_position` | `any` | – | python-chess board facts (never an evaluation) |
| `get_current_position` | `game_context` | – | board awareness from context, no FEN pasting |
| `get_game` | `game_context` | – | stored game record and status |
| `get_game_moves` | `game_context` | – | stored ply rows |
| `get_move_analysis` | `game_context` | – | the tool for "why was this bad?" |
| `get_critical_moments` | `game_context` | – | engine-confirmed moments, swing-ordered |
| `get_game_summary` | `game_context` | – | deterministic game summary |
| `get_game_analysis` | `game_context` | – | the full Phase 4 GameReport |
| `get_game_trajectory` | `game_context` | – | evaluation trajectory |
| `get_player_profile` | `player_context` | – | Phase 5 profile, verbatim coverage bands |
| `get_player_statistics` | `player_context` | – | Phase 5 game statistics |
| `get_player_insights` | `player_context` | – | Phase 5 insights with claim levels |
| `get_player_evidence` | `player_context` | – | the evidence behind an insight |
| `get_opening_information` | `any` | – | curated, versioned base; can say "unknown" |
| `search_chess_knowledge` | `any` | – | curated concept base; can say "not stored" |
| `list_chess_concepts` | `any` | – | the stored concept index |
| `get_prediction_status` | `any` | – | declared prediction tasks + requirements |
| `get_validated_prediction` | `production_model` | – | refuses unless a model passed its gate |
| `get_training_requirements` | `any` | – | the contract a legitimate exercise must satisfy |
| `get_training_recommendations` | `player_context` | – | evidenced training priorities (Phase 8) |
| `generate_training_position` | `player_context` | – | one exercise, **solution withheld** |
| `generate_training_explanation` | `player_context` | – | full evidence incl. the verified solution (Phase 8) |
| `evaluate_training_attempt` | `player_context` | ⚙️ | grades a move **without storing an attempt** |
| `get_review_queue` | `player_context` | – | exercises whose spaced-repetition review is due |
| `get_training_progress` | `player_context` | – | measured progress with sample sizes |
| `get_training_from_game` | `game_context` | – | exercises derived from one game |
| `get_opponent_profile` | `player_context` | – | opponent identity, history, repertoire, stats (Phase 9) |
| `get_opponent_games` | `player_context` | – | the opponent's stored game history |
| `get_opponent_repertoire` | `player_context` | – | what they play as a colour, sample-gated |
| `get_opponent_recent_repertoire` | `player_context` | – | their repertoire lately, with its own sample |
| `get_opponent_position_responses` | `player_context` | – | how they answered one FEN |
| `get_opponent_tendencies` | `player_context` | – | measured, evidence-gated regularities |
| `get_opponent_phase_statistics` | `player_context` | – | performance by phase |
| `get_opponent_preparation_report` | `player_context` | – | the composed preparation document |
| `generate_opponent_brief` | `player_context` | – | a deterministic brief (no model inference) |
| `generate_opponent_training` | `player_context` | – | preparation exercises from an opponent's games |
| `get_opponent_requirements` | `any` | – | the contract an opponent claim must satisfy |
| `compare_candidate_moves` | `any` | ✅ | several moves, one search (Phase 10) |
| `compare_positions` | `any` | ⚙️ | engine axis and board axis, kept apart |
| `analyze_counterfactual` | `any` | ✅ | actual line vs alternative line, ply by ply |
| `explain_why_not_move` | `any` | ✅ | the measured case against a move |
| `explain_what_if` | `any` | ✅ | one hypothesis, both continuations |
| `explore_turning_points` | `any` | – | where a game could have gone differently |
| `create_training_from_scenario` | `any` | ✅ | store a counterfactual as an exercise |
| `get_opponent_response_scenario` | `any` | ✅ | observed responses beside engine ones |
| `find_related_games` | `any` | – | games connected to a game/player (Phase 13) |
| `find_related_positions` | `any` | – | positions by exact/similar level |
| `find_player_patterns` | `player_context` | – | a player's evidenced patterns |
| `find_pattern_evidence` | `any` | – | the positions/games behind a pattern |
| `find_training_history` | `player_context` | – | training linked to a player/pattern |
| `find_opponent_connections` | `player_context` | – | how an opponent connects to patterns/positions/openings |
| `find_opening_connections` | `any` | – | games/players/patterns reachable from an opening |
| `find_knowledge_for_position` | `any` | – | the concepts a position exhibits |
| `trace_insight_evidence` | `any` | – | the stored references behind an insight |

Availability is a function of **context first, then deployment**. When a tool is
both unavailable in this deployment *and* missing its context, the reason the user
can act on is the missing context ("no active game in this conversation"), because
that is the thing they can change; the deployment gap surfaces once the context is
supplied. A tool that cannot run is never silently skipped — the absence is
recorded and reaches the answer.

## Context and board awareness

The single most valuable thing this phase adds. `AgentContext` carries identifiers
only — never rows — plus the *authorized* game set:

| Field | Purpose |
| ----- | ------- |
| `active_game_id`, `selected_ply`, `selected_move_san` | "why is this bad?" without pasting a FEN |
| `current_fen` | a position the user pasted or the client reports |
| `player_id` | whose history may be discussed |
| `mode`, `skill` | how to tell it, never *what* is true |
| `available_game_ids` | what this caller may read — enforced in the backend |
| `user_id` | caller identity (single-user today; the check is written once) |

Resolution order (`ai_agent/memory/context.py`):

1. **explicit request context** — the client said which game and ply are open;
2. **a literal reference in this question** — "and what about move 3?" names ply 5,
   and naming a move *now* outranks anything merely remembered;
3. **declared context** — a previous turn asserted it;
4. **inferred focus** — a previous turn *discussed* it.

Each step fills only what the earlier step left empty. Resolution never *guesses*:
if nothing establishes a game, the tools raise their honest "no active game" error
and the agent reports the gap instead of inventing a position.

## Evidence

Every tool result is converted into `EvidenceItem`s by one module
(`core/collection.py`), which buys three things: provenance is assigned **once**
(engine fact vs. Caissa-derived feature vs. interpretation), summaries are rendered
**consistently** (the model, the UI and the validator read the same sentence), and
payloads are **bounded** (only the fields that matter travel forward).

Absence is recorded, not implied. When a tool is unavailable, fails, or returns
nothing, the packet gains a `missing` entry naming what could not be retrieved and
why — so the answer can say "Caissa has no stored analysis for that move" instead of
inventing one, and the omission is visible in the trace rather than silently absent.

## The answer: claims, validation, actions

Four sentence types are kept apart, because blending them is exactly how chess
commentary becomes untrustworthy:

| Kind | Example |
| ---- | ------- |
| `FACT` | "Stockfish evaluates the position at +2.1." |
| `OBSERVATION` | "Your move changed the evaluation by 5.1 pawns." |
| `INTERPRETATION` | "The move overlooks the reply Qh5+." |
| `COACHING` | "Before attacking, check your opponent's forcing moves." |

Actions are real navigation or real follow-up work, and are emitted **only when the
data exists** — a "show best line" button on an answer with no principal variation
would navigate to an empty board, which is precisely the fake functionality the
spec forbids. `[Practise this game]` links to `/training?game=<id>`, which generates
exercises from that game's own analysed mistakes, and is offered only when a game is
in context. `[Compare moves]` carries the played move and the engine's choice, and
the UI turns it into the *question* that reaches the stored analysis. When an action
cannot be performed in the current context it is shown disabled with its reason
rather than omitted, so the gap stays visible.

## Hallucination controls

Four independent layers, none of which trusts the others:

1. **Evidence before generation.** Tools run first; the model receives real numbers
   and is told explicitly it may not add to them. The prompt names the prohibition.
2. **The prompt** forbids inventing evaluations, probabilities, opening names and
   counts, and requires stating a sample size before a recurring claim.
3. **The validator** (`safety/validation.py`) re-reads the generated prose and checks
   every high-value claim against the packet: signed evaluations, centipawn
   magnitudes, mate distances, percentages, counts ("14 blunders in 20 games"),
   probabilities, opening names, FENs. A claim with no matching evidence is
   **refuted**, and the finding is returned to the client.
4. **The tool boundary** makes several inventions impossible rather than merely
   detectable: `get_opening_information` returns `unknown` when the base has no
   match, predictions refuse without a production-gated model, and the engine is the
   only source of an evaluation.

Adversarial coverage is explicit in `ai_agent/evaluation.py`: scripted providers
that invent a `+3.40` evaluation, a `62%` win probability, an assumed "100 analysed
games", an opening name and a principal variation. Each must be flagged.

## Provider

| Provider | Config | Notes |
| -------- | ------ | ----- |
| `openai` | `ARGUS_LLM_PROVIDER=openai` + `ARGUS_LLM_API_KEY` | chat completions with tool calling (httpx, no vendor SDK) |
| `anthropic` | `ARGUS_LLM_PROVIDER=anthropic` + `ARGUS_LLM_API_KEY` | messages API; tool specs translated |
| `groq` | `ARGUS_LLM_PROVIDER=groq` + `ARGUS_LLM_API_KEY` | OpenAI-compatible API (`https://api.groq.com/openai/v1`), default model `openai/gpt-oss-120b`; a fast, low-cost option |
| `echo` | `ARGUS_LLM_PROVIDER=echo` | keyless development provider; never invents chess facts |
| *(none)* | unset | the agent still works: deterministic answers, and a plain statement that it cannot write an explanation |

### Tool-catalogue bound

The tools offered to the model are **bounded per intent**, never the whole
catalogue. This is a correctness requirement, not a tuning choice: a real provider
rejects a request carrying every tool schema (Groq's on-demand tier caps a request
at 8000 tokens and answers 413), so an unbounded catalogue means the model never
gets to answer. The planner shortlists tools per intent (and a general question
gets a small, high-value default set), and the loop offers only those.

When no provider is configured the agent does **not** degrade into a fake
conversation. It runs the tools it can, answers stored-fact questions exactly, and
otherwise returns an honest unavailable result that lists the evidence it did
retrieve and what it could not. `/api/coach/ask` therefore never returns 501 — the
absence of a model is a state, not an error.

## Security and authorization

- **Keys** are read from the environment only — never source, never the browser,
  never a log or a trace. The trace records a provider *name* and nothing else, and
  a redaction pass (`observability.scrub`) runs over the finished trace as a backstop.
- **Authorization is server-side.** The request's authorized game set is attached to
  the context and every game tool refuses an id outside it — in the backend, never by
  asking the model to behave. Single-user today, so the set is the whole library;
  when accounts arrive, `authorized_game_ids()` is the only function that changes and
  every tool inherits the scoping.
- **Tool arguments are validated**, so an unknown parameter is a loud failure rather
  than a silent default.
- **No database session reaches a tool**: providers are narrow callables already
  scoped to the authenticated request.

## Memory

Follow-ups must not require repetition, and the transcript must not be sent forever.
`ConversationMemory` keeps a bounded window of full turns plus a deterministic digest
of what came before, under an explicit character budget. The digest is built by code,
not by an LLM, so it is reproducible, free, and cannot introduce a claim that was
never made. Memory is explicitly **not** a source of player facts: if a previous turn
said "you are weak at tactics", that sentence is conversational text and nothing
more. History arrives over HTTP in the same `{role, content}` shape the response
uses, translated by `ConversationMemory.from_messages`.

## Limits

Iterations, tool calls, engine searches, prompt characters and response characters
all have ceilings (`safety/limits.py`). Hitting one stops the loop and records it in
the trace and the answer's limitations — it never silently continues. Engine depth is
clamped at the tool boundary, not requested politely in the prompt, and a clamp is
disclosed in the evidence.

## Streaming

`ai_agent/streaming.py` turns the turn's stages into an ordered event stream
(`plan` → `tool_call`/`missing` → `evidence` → `answer` → `validation` → `done` →
terminal `result`/`error`) using a worker thread and a queue, so a transport can
forward progress while the turn is still running. What is honestly streamable today
is the **lifecycle**, because the loop emits each frame where it happens. Token-level
deltas are not, because the provider clients do not expose a streaming completion
API; the `answer_delta` kind is reserved so the vocabulary a client codes against
will not change when they do. A raising subscriber is logged and the turn continues.

## Performance (measured)

Measured, not estimated (`ai_agent/performance.py` and `scripts/verify_agent.py`).
The profile is bimodal because it is dominated by whether a turn searches:

Observed across repeated live runs of `scripts/verify_agent.py`, whose five turns
include one real engine search on a position:

| Measure | Observed |
| ------- | -------- |
| p50 | **7–17 ms** (deterministic and stored-evidence turns) |
| p95 | **0.7–5.3 s**, varying with engine warmth — the Stockfish search dominates |
| mean | 147–1067 ms, i.e. entirely a function of how many turns searched |

So the agent's own overhead is milliseconds; the latency profile is the engine's.
That is the useful finding, and it is why the deterministic path is separated in the
report: a no-LLM, no-engine turn answers in single-digit milliseconds, and a turn
that searches costs what searching costs. Every reported figure comes from
`time.perf_counter` around a real turn, and an empty measurement is reported as
`None`, never as `0`.

## Evaluation (measured)

`tests/test_agent_tools.py`, `tests/test_agent_behaviour.py`,
`tests/test_agent_e2e.py`, `tests/test_agent_streaming.py`,
`tests/test_agent_performance.py` and `tests/test_agent_evaluation.py` — **218 tests**
as of Phase 16, all green, part of the full suite that is green.

| Suite | Tests | Covers |
| ----- | ----- | ------ |
| `test_agent_tools.py` | 51 | schema validation, permissions, authorization, clamps, catalogue |
| `test_agent_behaviour.py` | 92 | planner, memory precedence, evidence, validation, actions, no-provider honesty |
| `test_agent_e2e.py` | 38 | whole turns across the four spec scenarios + adversarial prompts |
| `test_agent_streaming.py` | 12 | event order, terminal-frame equivalence, subscriber safety |
| `test_agent_performance.py` | 9 | the measurement harness itself (no fabricated numbers) |
| `test_agent_evaluation.py` | 6 | the evaluation suite run end to end (15 cases, 38 checks) |

`ai_agent/evaluation.py` provides a runnable suite (`run_evaluation`) that needs no
API key: the ten spec questions plus the adversarial set, with a faithful scripted
provider as the control and a hallucinating provider as the test. It is executed by
`test_agent_evaluation.py`, not just written — the suite's `expect_tools` check means
"at least one of these tools was called", because a question can be answerable
through more than one route and demanding all of them would fail a correct turn.

Live verification (`scripts/verify_agent.py`) against the real API confirms, with no
mocks: the catalogue and its reasons, a prediction request refused with no
probability produced anywhere in the payload, an evaluation question producing engine
evidence, a player question naming its gap, an unauthorized game refused by the
backend with no data leaked, a stored-game move question consulting
`get_move_analysis`, and no credential in any answer payload.

## UI

`apps/web/src/app/coach/page.tsx` (with `components/agent-answer.tsx`) is the agent's
front door. It is not a chat box: context (game, ply, player, style) is an explicit
control, every reply renders its evidence, its missing capabilities, the claims the
verifier checked and the buttons it can back, and the tool catalogue is shown with
available / needs-a-game / needs-a-player / not-yet grouped by the backend's own
reasons. The game page's **Ask Caissa** button deep-links `/coach?game_id=…&ply=…` so
the agent already knows which board is open. The page works with no LLM provider
configured — the input is never disabled for a missing model.

## Limitations (stated, not implied)

- **No trained prediction model has passed its production gate.** `get_validated_prediction`
  therefore serves nothing, and prediction tools return "unavailable" by design.
  Asked for a probability, the agent refuses rather than estimating.
- **Token-level streaming is not implemented** — the lifecycle event stream is.
- **The library is small.** With only a handful of stored games, every player-level
  claim is a small-sample observation. The agent states the sample size; it does not
  upgrade an observation to a tendency.
- **`player_performance` has no public operation**, so it stays declared with its
  requirements rather than being served through another path.
- **`Compare moves` without a FEN** routes to the stored analysis rather than a fresh
  engine search, which is exact but limited to moves that were analysed.
- **Single-user.** The authorization mechanism is real and enforced, but there are no
  accounts yet, so `available_game_ids` is the whole library.
- **The digest is deterministic, not semantic.** Long conversations are summarised by
  truncation and role labels, not by an LLM summary — deliberate, and lossy by design.

## Phase 1 shell (kept)

`argus.ai_agent.agent.ChessCoachAgent` with `build_default_registry` is the original
engine-only tool shell. It is retained because it is the registry to use when no data
layer is wired in, and because its behaviour is pinned by tests. It has no board
awareness and no evidence-packet validator, and `POST /api/coach/chat` returns 501
when no provider is configured — unlike the Phase 7 endpoint, which always answers.

The shell still carries the same **primary** honesty guard as the Phase 7 agent: a
system prompt that forbids inventing evaluations/percentages/counts/openings and
forbids confirming a number the user supplied as if Caissa had measured it. Two
defects found in Phase 17 were fixed here: the tool loop now appends the assistant
`tool_calls` message (with a matching `tool_call_id` on each result) before the
results, without which a real provider rejects the turn with "Tools should have a
name!"; and the system prompt stopped the shell from agreeing with a false premise
("assume my blunder rate is 42%"). A false premise the user supplies is now refused
because no tool produced it. What the shell still lacks — and the Phase 7 endpoint
has — is the layered evidence packet and the post-generation validator.

## Phase 8 training tools (implemented)

The declared training tools are now wired to the training engine (`docs/training-engine.md`).
They preserve the same claim discipline as the rest of the agent:

* **The puzzle tool withholds the answer.** `generate_training_position` returns an
exercise without its solution, so a coaching answer cannot leak an answer the
player has not attempted. `generate_training_explanation` returns the solution and
its line, and exists specifically for "why is this the right move?".
* **The agent never writes training history.** `evaluate_training_attempt` grades a
submitted move against the stored solution but stores nothing; attempts are written
only by the training endpoints, in the product flow.
* **Ownership is enforced in the backend.** An exercise owned by another player
returns nothing to the tool, exactly as a foreign game id is refused.
* **Recommendations carry their evidence** (sample sizes, accuracy, recurring
patterns); with too little data the tool returns no recommendation rather than a
guess.

## Phase 9 opponent tools (implemented)

The opponent tools are wired to the opponent intelligence engine
(`docs/opponent-intelligence.md`) and obey the same discipline:

* **Analytics, not psychology.** Every tool reports counts, shares and centipawn
  measurements over stored games; none infers intent, emotion or a result.
* **Sample sizes travel with every claim.** A tendency below its gate comes back
  marked `insufficient` rather than smoothed into a finding.
* **Preparation, not prediction.** `generate_opponent_brief` names openings and
  structures to prepare against, never outcomes, and is assembled
deterministically from the report — no model inference is involved.

## Phase 10 counterfactual tools (implemented)

The scenario tools are wired to the decision-intelligence service
(`docs/scenarios.md`) and exist to let the coach answer counterfactual questions
without being able to invent one:

* **The engine decides.** Every evaluation and every continuation ply comes from a
  real search; a tool never estimates a score or invents a line.
* **A refusal is an answer.** An illegal move comes back as `illegal_move` with
  "that move is not legal in this position"; a missing engine comes back as
  `unavailable`. Neither is converted into a plausible number.
* **The comparison says what it is.** Scores from a separate search are marked
  `resulting_position`, and the tool result repeats that, so the agent cannot
  present them as like-for-like.
* **The explorer is free.** `explore_turning_points` reads stored analysis only, so
  "where did this game turn?" costs no engine work.
* **Tool usage is observable.** Each of these tools increments a counter in the
  Phase 10 metrics registry (spec §40), which is exposed at
  `GET /api/scenarios/metrics`.

## Phase 13 intelligence-graph tools (implemented)

The nine graph tools are wired to the intelligence graph
(`docs/intelligence-graph/`) and let the coach connect evidence across questions
without ever inventing a link:

* **A relationship is stored with its evidence.** Each tool returns the edge and the
  stored references behind it; a tool that finds nothing returns an honest empty
  result — "no verified relationship found" — not a plausible default.
* **Similarity is never overstated.** `find_related_positions` labels every result
  with its controlled level (`exact` / `equivalent` / `structurally_similar` /
  `opening_similar` / `tactically_similar`); only `exact` is an exact match.
* **Multi-hop reasoning is checkable.** `find_player_patterns` →
  `find_pattern_evidence` → `find_related_games` names the pattern, the positions
  behind it and the games behind those, with a stored reference at every step — and
  the answer validator can refute a claim that does not match the packet.
* **Authorization is inherited.** The tools read through the same policy as every
  other graph read, so a private game does not surface through a graph hop.

## Related documentation

Phase 11 added the coaching workspace (`docs/coaching.md`), Phase 12 the live game
(`docs/live-chess.md`, `docs/realtime-protocol.md`), and Phase 13 the intelligence
graph (`docs/intelligence-graph/`).
