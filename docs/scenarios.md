# Decision Intelligence — counterfactuals, comparison and scenarios (Phase 10)

Caissa can answer a class of question the earlier phases could not:

> "What could have happened if I had played differently?"

That question has a reputation problem. Answering it badly is easy — invent a
plausible line, attach a number, sound confident — and the result is
indistinguishable from analysis. This phase exists to answer it *measurably*.

Three rules shape everything below.

**Stockfish is the calculation.** Every evaluation, every principal variation and
every ply of a branch is a real engine result. There is no interpolation, no
"expected" score, and no line the engine did not produce.

**A refusal is a result.** An illegal move, a missing engine, or a search that
returned nothing comes back as a status with a reason. `"e2e5" is not a legal move
in this position` is an answer; a fabricated plausible score is not.

**Comparison keeps two axes apart.** An engine difference is a search result. A
structural difference is a board fact. They are reported separately and never
merged into one number, because merging them is how an invented relationship gets
into a product.

---

## 1. Where it lives

```
packages/argus/argus/scenarios/
    policy.py         versions, the closed set of scenario types, resource limits
    models.py         facts, engine metrics, comparisons, branches, explanations
    positions.py      board facts + PositionComparisonService (two axes, kept apart)
    candidates.py     CandidateMoveComparison (one search, several moves)
    counterfactual.py CounterfactualAnalyzer (actual line vs alternative line)
    whatif.py         "why not this move?" and "what if I had played …?"
    explorer.py       TurningPointExplorer (engine-free, reads stored analysis)
    service.py        ScenarioService: caching, limits, refusals, prediction gate
    metrics.py        counters and timings (spec §40)

apps/api/argus_api/
    services/scenario_service.py   the API seam: game→FEN+ply, authorization, persistence
    services/authorization.py      the single game-visibility decision
    routes/scenarios.py            15 endpoints under /api/scenarios
    db/models.py                   the `scenarios` table (immutable records)

packages/argus/argus/ai_agent/tools/scenarios.py   eight agent tools
packages/argus/argus/training/from_scenario.py     the Phase 8 bridge
apps/web/src/app/scenarios/page.tsx                the What-If Lab
```

Nothing here duplicates an earlier phase. Board reading comes from
`analysis.features.extractor`, phase detection from `analysis.phase`, the engine
interface from `analysis.engine.base`, and opponent responses from Phase 9.

---

## 2. Position comparison

`PositionComparisonService.compare(fen_a, fen_b)` returns two things:

| Axis | Source | What it means |
| --- | --- | --- |
| `engine_difference` | a search | both positions scored, normalised to **White's** perspective, `delta_cp_white` |
| `structural_differences` | the board | per-feature deltas: material, pawn structure, activity, king safety, tactics |

`scores are normalised to White` is an explicit, documented transformation so two
positions with different side-to-move can be compared on one scale. It does not
remove the tempo difference, and the result says so when the sides differ
(`notes`).

The engine axis reports `available: false` when no result exists — a comparison
that only has board facts is still useful and is never padded with a guess.

**No engine call is needed for the structural axis.** `POST /api/scenarios/position`
returns board facts for a FEN in ~10 ms with zero searches.

---

## 3. Candidate move comparison

`POST /api/scenarios/compare-moves` answers "which of these moves is better?" with
one MultiPV search, so the moves are compared under identical conditions.

For each candidate the response carries: evaluation (`cp`/`mate`, mover's
perspective and White's), centipawn loss against the engine's first choice, a
quality band (`best … blunder`, from documented thresholds), the principal
variation (UCI and SAN), the material consequence read from the resulting board
(captures, promotions, the swing in centipawns), tactical consequences (check,
mate, pieces left hanging, insufficient material), the resulting position type
(phase + pawn-structure shape), and the structural deltas that actually carry
information about a *move* — material, structure, tactics.

Two details matter more than the rest:

* **`eval_source`.** A move inside the MultiPV window is scored by the same search
  as the best move (`same_search`) and is directly comparable. A move outside it
  gets a separate search of the resulting position
  (`resulting_position`) — honest, but not like-for-like, and the response says so
  in `notes`. Presenting the two as equivalent is how a 0.05-pawn "difference"
  gets manufactured.
* **Illegal moves are refused, not scored.** `legal: false`, a reason, and no
  evaluation. Nothing is spent searching a move that cannot be played.

Mobility and king-safety counts are deliberately *excluded* from per-move
structural deltas: they change for every legal move simply because a piece moved,
so reporting them per move would dress a tautology up as an insight.

---

## 4. Counterfactual scenarios

`POST /api/scenarios/counterfactual` builds one branch:

```
source position (a FEN, or a game_id + ply)
      │
      ├── root MultiPV search  ──►  score of the played move
      │                        └─►  score of the alternative move
      │
      ├── actual line:       the played move, then the engine's own best replies
      └── alternative line:  the alternative, then the engine's own best replies
                                    │
                                    └── resulting positions compared (two axes)
```

Each continuation ply is re-searched in the position it reaches, rather than read
from an earlier PV, so every recorded score belongs to the position it is attached
to. The branch reports `actual_eval_source` and `alternative_eval_source`
individually, and `evaluation_change_cp` is only computed when both sides have a
real score.

**A branch never modifies a game.** It is a value object built from a FEN; the
stored game is read-only input. `POST /api/scenarios/counterfactual` with
`persist: true` writes an immutable row to `scenarios` (with `ON DELETE SET NULL`
on the game foreign key, so a deleted game leaves the analysis readable with its
origin marked unavailable).

### Scenario types

The closed set (validated at the API boundary):

```
counterfactual_move   alternative_line    opening_deviation   tactical_variation
endgame_transition    opponent_response   user_hypothesis
```

They are different *questions*, not different methods: all of them run the same
engine-grounded branching and differ in what they set up and emphasise.

---

## 5. "Why not this move?" and "what if I had …?"

Both features are framings over the same measured pieces.

`POST /api/scenarios/why-not` returns: what the move does (its own score, PV,
material and tactical consequences), the opponent's strongest reply the engine
expects, the resulting evaluation, the most concrete measured problem
(`critical_issue`, ordered from hardest evidence to softest), the better
alternatives from the same search, and an `ExplanationBundle`.

If the move **is** the engine's first choice, the status is `move_is_best` with
"there is no inferiority to explain" — rather than inventing a flaw to fill the
panel.

`POST /api/scenarios/what-if` returns the same branch framed as a hypothesis,
including both continuations and the measured change.

### The explanation bundle

No natural-language generation happens in the analysis layer. Both features return
an `ExplanationBundle`: a list of atomic, sourced facts plus the raw numbers.

```
"Engine score after the played move Nf3: -0.40 (mover's perspective, same_search)"
"The alternative e4 scores +0.30 (mover's perspective, same_search)"
"The alternative is 0.70 pawns better than the played move"
"Played-move continuation the engine chose: Nf3 Nc6 Bb5"
"Structural difference after the two moves: passed_pawns_white 0 -> 1"
"All numbers come from one engine configuration: stockfish 17 depth=14 pv=4"
```

A language model may phrase these. It cannot add a claim that is not in the
bundle, which is what stops "the knight becomes dominant" from appearing when
nothing measured it.

---

## 6. Turning-point explorer

`GET /api/scenarios/games/{id}/explorer` is **engine-free by design**. It reads the
stored analysis and lists the moments worth branching from, together with the
alternative moves the analysis already recorded.

A moment qualifies when the intelligence layer flagged it as critical, when its
classification is `inaccuracy`/`mistake`/`blunder`/`missed_win`, or when the
evaluation swing reaches 100cp. Moments are ranked critical-first, then by
classification, then by swing.

When an analysis predates MultiPV storage, a moment is still listed but reports
`what_if_available: false` and a note explains that no branch can be offered
there — rather than offering a branch that would have to be invented.

Measured cost on this machine: the explorer over 120 plies plus 200 board-fact
reads takes ~88 ms with **zero** engine searches.

---

## 7. The prediction layer: gated, or absent

Phase 10 may attach a prediction to a scenario, and only under the Phase 6 rules.

```
POST /api/scenarios/predict            → one task, one feature row
GET  /api/scenarios/predictions        → per-task availability and reasons
POST /api/scenarios/counterfactual     → optional `prediction_task` + `prediction_rows`
```

A prediction is served **only** by a model whose registry status is `PRODUCTION`.
When none exists the answer is `available: false` with the reason — not a
heuristic, not a fallback, not an "estimate". The response carries the model's
measured metrics, its calibration state and its data coverage, and the phase
documents that a probability is a model output, not a guarantee.

In this deployment every task is currently unavailable:

```
game_outcome, move_error_risk, player_performance, position_difficulty, position_outcome
```

That is the correct state for a phase in which no model has passed its gate, and
the API says it out loud rather than quietly omitting a section.

Model validation, calibration, temporal validation and player-holdout validation
remain the Phase 6 gates documented in `ml-and-data.md` and
`ml-and-data.md`; Phase 10 adds no new modelling and cannot loosen a gate.

---

## 8. Integrations

**Phase 8 — training.** `POST /api/scenarios/training` turns a counterfactual into
an exercise. The position is the real one, the solution is the move the engine
measured here, and the category (`tactical`, else `calculation`) is assigned from
board evidence. The exercise is refused when the move is illegal, is the move that
was already played, or is scored below the near-best tolerance the grader itself
uses — a move the engine calls inferior never becomes an answer key. Stored
positions carry `data_source: counterfactual` (schema widened additively and
guarded, PostgreSQL-only) and dedupe on the existing normalized-FEN key.

**Phase 9 — opponents.** `POST /api/scenarios/opponent-response` reports what the
opponent *has played* in a position beside what the engine *recommends*. They are
different questions answered separately and never merged, and neither predicts the
opponent's next move.

---

## 9. Limits, caching and observability

**Limits** live in `policy.py`, not in a prompt: MultiPV ≤ 8, continuation ≤ 12
plies, depth 6–24, and at most 8 candidate moves per request. A clamp is applied
*and disclosed* in the response.

**Caching** is a bounded TTL cache keyed by the positions *and* the search
configuration (depth, MultiPV, engine version). A result from a different depth is
a different result and is never served. The service is process-wide, so a repeat
is genuinely a cache hit; the engine's own position cache (`CachingEngine`)
remains underneath.

**Observability** (`GET /api/scenarios/metrics`) reports aggregate counters and
timings only:

```
counterfactual_requests, comparison_requests, position_comparison_requests,
explorer_requests, refusals_illegal_move, refusals_unavailable,
prediction_requests, prediction_rejections, training_from_scenario_count,
scenario_persist_count
engine_analysis_time_ms, scenario_generation_time_ms
agent_tool_usage, model_version_usage, cache hit rate
```

Engine time is measured by wrapping the engine itself, so it counts searches that
actually happened rather than the wall time of the request that triggered them.
The registry is in-process and says so; a shared store (Redis is already
configured) is the natural next step and would not change the interface. **No game
content is logged**: a counter is a count and a timing is a duration.

---

## 10. Privacy and authorization

* A game the caller cannot read is a **404**, not a 403 — existence itself is
  information. The decision has one home: `services/authorization.py`.
* A stored scenario is private to its owner (`owner_player_id`); a different
  player's id gets a 404, so nobody can confirm another player's analysis exists
  by probing ids.
* Scenario records are immutable and always inserted; re-asking writes a new row
  and both remain auditable.
* Metrics are aggregates. No FEN, move or score is ever logged or returned there.

---

## 11. Honest limitations

1. **A counterfactual is analysis, not history.** It says what the engine sees in a
   hypothetical position, not what would have happened at the board. The API
   states this in `meta.limitations` and the UI repeats it.
2. **Separate-search scores are not comparable to same-search scores.** They are
   labelled, but a small difference between them is still not meaningful.
3. **Low-depth engine noise.** At shallow depths (6–8) several moves can sit inside
   30cp of each other. `move_is_best` and `not_defensible` are therefore functions
   of the requested search configuration, which is always reported.
4. **Training from a scenario needs a good move.** A counterfactual whose
   alternative is merely interesting but inferior produces no exercise.
5. **The explorer is only as good as the stored analysis.** Games analysed before
   MultiPV storage have no alternatives to offer, and say so.
6. **Metrics are per process.** A restart resets them, and a future multi-worker
   deployment will need a shared store.
7. **No production model exists yet**, so the prediction layer is unavailable
   rather than simulated.

---

## 12. Verification

* `tests/test_scenarios.py` — 29 package tests over a recording stub engine: real
  engine calls for every number, `resulting_position` labelling, illegal moves
  refused, branches immutable, limits clamped, the explorer engine-free.
* `tests/test_scenarios_api.py` — 45 API tests against the **real** engine and
  imported games, including the full phase gate, authorization/404s, scenario
  privacy, prediction gating and the shared cache.
* `tests/test_scenario_agent_tools.py` — 10 tests over the agent surface.
* `tests/test_scenarios_performance.py` — 10 tests: a repeat costs no engine work,
  a different configuration is never served from cache, the cache is bounded and
  expires, concurrent readers are safe, and the engine-free operations measure
  zero searches.
* `scripts/verify_scenarios.py` — the live gate, end to end:

```
REAL GAME → REAL POSITION → REAL ENGINE ANALYSIS → REAL ALTERNATIVE MOVE
→ REAL COUNTERFACTUAL BRANCH → REAL COMPARISON → REAL EXPLANATION
→ REAL TRAINING POSITION
```
