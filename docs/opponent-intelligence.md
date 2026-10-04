# Opponent Intelligence + Advanced Chess Analytics (Phase 9)

Phase 9 answers one question from the games Caissa already stores:

> What can the available game data tell me about this opponent's chess
> tendencies, repertoire, recurring patterns, and preparation opportunities?

It is an **analytics and preparation system**, not a psychological profile and
not a prediction. Every output is a count, a share, or a centipawn measurement
over games that were actually played, each carrying its sample size and a claim
level. When the data cannot support a statement, the report says so.

## Where it lives

```
stored games + stored move analysis
      │  apps/api/argus_api/db/repository.opponent_game_rows
      ▼
OpponentGameInput / OpponentMoveInput        (engine-free vocabulary)
      │  apps/api/argus_api/services/opponent_service.build_inputs
      ▼
argus.opponent_intelligence
      ├─ repertoire.py    position-keyed opening tree per colour
      ├─ responses.py     exact/loose position answers + recurring positions
      ├─ statistics.py    phase statistics + measured tendencies
      └─ preparation.py   profile, evidence-gated insights, preparation report
      ▼
OpponentProfile / OpponentPreparationReport   (Pydantic, versioned)
      │  cached in opponent_profiles (fingerprint-guarded)
      ▼
/api/players/{id}/…  ·  agent tools  ·  /opponents UI
```

There is **no second player identity system**: Phase 9 reuses `Player`, `Game`,
`PlayerGame` and `MoveAnalysis` exactly as they are. The only new table is the
cached snapshot (`opponent_profiles`), which mirrors `PlayerProfileRecord`
(one row per player + profile version, replaced in place when rebuilt).

## The sample-size policy

All thresholds live in `argus.opponent_intelligence.policy.OpponentInsightPolicy`
and are stored inside every report under `policy`. The four named gates are:

| key | default | governs |
| --- | --- | --- |
| `min_games_for_repertoire_insight` | 3 | when a move may be called a *characteristic* choice |
| `min_occurrences_for_tendency` | 3 | when a behaviour is a pattern rather than an observation |
| `min_positions_for_structure_insight` | 3 | when a recurring position is reported |
| `min_games_for_phase_comparison` | 3 | when a phase comparison is a pattern |

Claim levels are `insufficient` < `observation` < `pattern` < `tendency`.
Nothing above `observation` is emitted until its gate is met; below the gate the
UI shows the raw count and the level, never a smoothed version of it. Coverage
bands (`insufficient` / `limited` / `moderate` / `robust`) describe the amount of
data, never the player.

## What each aggregator measures

**Repertoire** (`repertoire.py`). A tree keyed by the position *before* each of
the opponent's moves, restricted to the first 24 plies. A node records the move,
its occurrences, and the outcomes of the games it appeared in. `share` is
occurrences over the games that reached the position. `top_lines` are the
opponent's most-played continuations, reconstructed from real plies. No opening
book is consulted: every line printed was actually played.

**Position responses** (`responses.py`). Given a FEN, the opponent's answers are
aggregated by exact match first, then a looser piece-placement match; a position
they never faced returns `match: "none"` rather than a guess. Recurring positions
(those reached across at least the structure gate's number of games) are listed
with their measured answer distribution.

**Statistics and tendencies** (`statistics.py`). Performance by phase (moves,
significant errors ≥100cp, average centipawn loss, an accuracy *proxy* — the
share of scored moves within 30cp of the engine's best). Tendencies measure
castling side, check and capture rates, opening sharpness, where significant
errors concentrate, and the time controls actually played. The tactical /
positional split is a *proxy* from the engine's better move (a capture or check
is "tactical"); it is labelled as such everywhere.

**Preparation** (`preparation.py`). The profile and the preparation report:
the strongest findings first, each with its sample size and evidence refs that
resolve to `/game/{id}?ply={ply}`, plus a `preparation_hint` that says what to
prepare — never what will happen.

## API surface

| method | path | returns |
| --- | --- | --- |
| GET | `/api/players/opponent-meta` | vocabulary, gates, methodology version |
| GET | `/api/players/{id}/opponent-profile` | cached profile snapshot |
| POST | `/api/players/{id}/opponent-profile/rebuild` | forced rebuild |
| GET | `/api/players/{id}/opponent-games` | history + measured statistics |
| GET | `/api/players/{id}/repertoire` | repertoire as a colour |
| GET | `/api/players/{id}/repertoire/recent` | repertoire over the latest games |
| GET | `/api/players/{id}/position-response` | answers to one FEN |
| GET | `/api/players/{id}/responses` | recurring positions |
| GET | `/api/players/{id}/tendencies` | measured tendencies |
| GET | `/api/players/{id}/phase-statistics` | performance by phase |
| GET | `/api/players/{id}/preparation-report` | composed report + insights |

Authorization is server-side: the player must be a real tracked player (404
otherwise). There is no endpoint that returns a claim without its size.

## Opponent preparation training

Phase 9 also closes the loop from analysis to practice: Caissa can build exercises
*against* an opponent from that opponent's own games.

`argus.training.opponent_prep` selects a move the opponent plays
**characteristically** — the same position + move seen in at least
`min_occurrences` of their games (games, not plies, so a repetition inside one
game cannot inflate it). The exercise is the position **after** that move, and its
solution is the engine's stored best reply at the very next ply of the same game.
Everything is reused: the same eligibility gate, acceptance policy and Scheduler
as the personalized engine; nothing is predicted and nothing is synthesised.

Key properties:

* **A distinct data source.** Exercises carry `data_source =
  "opponent_preparation"` (never `personalized` — it is not the player's own
  mistake) and the `training_positions` CHECK constraint was widened for it.
* **Provenance first.** `source_reason` begins with the opponent, the move they
  play there and how many games show it — that, not the played move, is why the
  exercise exists. It still traces back to the opponent's game.
* **Owned by the preparing player.** The exercises belong to the player who will
  use them (private, 404 for anyone else), not to the opponent.
* **Refusal is a result.** If no move is yet characteristic, Caissa prepares
  nothing and says so rather than inventing a line.

`POST /api/training/opponents/{opponent_player_id}/prepare`
(body: `player_id`, optional `min_occurrences`, `max_exercises`) drives it; the
`/opponents` page has a **Preparation training** panel; and the agent exposes
`generate_opponent_training`.

## Agent tools

Ten tools were added (total now 40), each read-only and provider-gated:
`get_opponent_profile`, `get_opponent_games`, `get_opponent_repertoire`,
`get_opponent_recent_repertoire`, `get_opponent_position_responses`,
`get_opponent_tendencies`, `get_opponent_phase_statistics`,
`get_opponent_preparation_report`, `generate_opponent_brief`,
`generate_opponent_training` (plus `get_opponent_requirements`). When a provider
is missing the tool is declared *unavailable with a reason*, never silently broken.

## Frontend

`/opponents` is the preparation workbench: pick an opponent and a colour, and it
shows the preparation report, repertoire, tendencies, phase statistics, and a
position lookup — each figure with its claim badge and sample size.

## Performance

`tests/test_opponent_performance.py` measures the aggregation rather than assuming
it (and asserts the package contains **no engine call at all** — the invariant that
makes the budget meaningful). Measured on this machine, building a full profile:

| library | time (p50) | note |
| --- | --- | --- |
| 10 games / 600 plies | ~33 ms | |
| 40 games / 2400 plies | ~136 ms | 4× the games → **4.1×** the time (linear) |
| tendencies + phase stats, 20 games | ~62 ms | shares the same cheap path |

The profile is cached in `opponent_profiles` keyed by a source fingerprint, so a
second read of unchanged inputs is a database lookup, not a re-aggregation.

## Tests and verification

* `tests/test_opponent_intelligence.py` — the package (repertoire, responses,
  tendencies, phases, determinism, honesty rules).
* `tests/test_opponent_api.py` — the endpoints, caching, authorization, and the
  registered agent tools.
* `tests/test_opponent_performance.py` — measured cost, linear scaling, no engine
  in the path, and a documented budget.
* `scripts/verify_opponent.py` — walks the whole surface against a live server,
  including the preparation endpoint and cold-vs-cached timings.

Phase 9 adds 31 dedicated tests — 13 in `test_opponent_intelligence.py`, 12 in
`test_opponent_api.py` and 6 in `test_opponent_performance.py` — plus the
opponent-preparation cases in `test_training_api.py`. Phase 9 adds 31 dedicated
tests; the whole suite is green as of Phase 16 (see `README.md`).

## Related Phase 8 fixes shipped here

Phase 9 also closed three Phase 8 gaps, without fabricating data:

1. **`acceptable_moves` from real MultiPV data.** Move analysis now persists
   `candidate_moves` (the stored MultiPV window), the generator derives the
   acceptable set from it, and an alternative move within the acceptance
   tolerance is graded correct instead of wrong.
2. **Honest new exercise types.** `continue_line` is now emitted when the stored
   principal variation is long enough to grade move-by-move (the continuation is
   the engine's own stored line; `POST /api/training/positions/{id}/continue`
   grades it). `conversion` and `recovery` are now generated from *whole-game*
   arc evidence — the stored evaluation trajectory plus the result — via
   `argus.training.gamearc`. `what_went_wrong` and `reconstruction` remain
   declared but unemitted: they need whole-game replay with per-move checking
   that the generator does not have, and inventing it would violate the
   no-fabricated-exercise rule.
3. **An in-page "why is this right?" explanation.** The solve page now has a
   deterministic, LLM-free explanation assembled from stored evidence.

Phase 9 additionally implements **opponent-specific training** (see above) and a
**measured performance section**, which were the two remaining gaps.

## Limitations

* Analytics over stored games only — no live opponent modelling.
* Phase labels, tactical/positional splits and the accuracy proxy are derived
  from analysis output; they are documented as proxies, not ground truth.
* Opponent intelligence is per tracked player; cross-account identity linking is
  a separate concern handled by the existing player identity layer.
