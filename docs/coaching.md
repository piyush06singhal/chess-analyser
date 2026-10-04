# Coaching (Phase 11)

The coaching layer is what turns stored analysis into "what should I do about my
chess?". It is deliberately the *thinnest* layer in Caissa: it owns no chess fact,
calls no engine, and computes no statistic. Everything it says was already
computed, stored and labelled by the system that owns it.

Package: `argus/coaching` (`context.py`, `prioritize.py`, `feed.py`, `debrief.py`).
API: `apps/api/argus_api/routes/coaching.py`. Service:
`services/coaching_service.py`. UI: `/coach?tab=today`.

## 1. The unified context

`CoachContext` is assembled from what the caller is looking at:

| Field group | Source |
| --- | --- |
| identity, ids, FEN, side to move, selected move, phase | the request |
| `current_game` | stored game header |
| `player_profile`, `training_history`, `review_queue` | Player Intelligence, Training |
| `opponent_profile` | Opponent Intelligence |
| `training_position` | Training |
| `current_scenario` | Scenarios (when one is loaded) |
| `available_predictions` | the ML registry, through the same constructor the prediction routes use |

### Situation inference

The situation is *inferred*, in this priority order, and the reasoning is returned
in `brief.notes`:

1. an active training position → `training`
2. a game open with **no stored analysis** → `live_game`
3. an opponent selected → `opponent_preparation`
4. a game open with stored analysis → `game_review`
5. a bare position → `opening_study` / `endgame_study` / `position_analysis` by phase
6. nothing → `general_coaching`

Step 2's test is "is there analysis to discuss?", not "what does the pipeline's
status column say". A partially analysed game (an interrupted or resumed run)
still has stored rows, so it is a *review*, not a live game — otherwise the coach
would tell the user a game has not been analysed while the explorer can show its
turning points.

`CoachContext.gaps` lists what Caissa knows it does not have ("no player profile is
computed for this user", "the game is not readable"). The UI shows the gaps; it
never fills them with advice.

### Isolation

The context is built per request from the caller's identifiers. Nothing is cached
across users, and a game the caller may not read does not exist (404) through the
same `require_authorized_game` seam every other subsystem uses.

## 2. Modes

`COACH`, `ANALYST`, `GAME_REVIEW`, `TRAINING`, `OPPONENT_PREP`, `OPENING_COACH`,
`ENDGAME_COACH`, `BEGINNER`, `ADVANCED`.

Each mode fixes four things, all reported in the brief so the UI can be honest
about them:

- `explanation_depth` (short / standard / deep)
- `exposure`: evaluation, candidate moves, principal variation, statistics
- `allowed_tool_families`: which agent tools may run in this mode
- `context_priority`: which facts lead the answer

A mode is *defaulted* from the situation and can be overridden explicitly; the
brief records which happened. Beginner mode hides engine detail rather than
dumbing it down — the numbers stay available one tap away.

## 3. Prioritisation (`prioritize.py`)

Findings are ranked by **documented, measured factors** — never an arbitrary
score:

| Factor | Measured as |
| --- | --- |
| frequency | occurrences / the games they occurred in |
| recurrence | how many separate games carry it |
| severity | the centipawn magnitude of the underlying events |
| confidence | the coverage band the profile assigned |
| recency | days since the most recent occurrence |
| training_history | measured accuracy on exercises tagged with the same pattern |
| sample_size | the number of games the finding rests on |

The weights, the recency bands and the final banding (`CRITICAL`, `HIGH`,
`NORMAL`, `LOW`) are served verbatim at `GET /api/coaching/method`, and the band
is *product workflow priority* — "look at this next" — not a judgement about the
user's ability. A finding capped by a small sample says which factor capped it
(`capped_by`) instead of pretending to be confident.

## 4. The feed ("Today's Caissa")

`GET /api/coaching/feed` returns fixed sections — recent game, important mistakes,
recurring patterns, training, opponent preparation, opening work, progress — each
a list of cards. Every card carries its statement, its sample size, its evidence
references, its priority, and the actions that actually navigate somewhere
(`View evidence`, `Train pattern`, `Open game`). Cards are dismissible, and
dismissal is respected across requests.

Rules the implementation enforces:

- a section with nothing shows the **reason** (`available: false` + `reason`);
  it is never padded with generic advice;
- a card always names the number of games behind it;
- nothing is generated from a template with the numbers missing — a card that
  cannot be evidenced is not emitted at all.

## 5. Game debrief

`GET /api/coaching/games/{game_id}/debrief` implements the guided review as eight
steps, each a section with its own evidence:

```
summary · critical moments · biggest decisions · recurring mistakes
what you did well · what to train · counterfactuals · improvement tracking
```

It reads the stored game report and the stored explorer output; it does not
re-run an engine. The caller's side is resolved from stored identity (their name
against the game's white/black players), so "what you did well" is scoped to *their*
moves; when the side cannot be determined the section says so instead of
describing both players as if both were the user.

## 6. "What should I work on?"

`GET /api/coaching/focus` returns:

```
primary_focus      one prioritised finding, or null
supporting_focus   the next few
evidence           the atomic facts behind the primary focus
affected_games     the games it was observed in
training_history   measured accuracy on the related exercises
recommended_exercises
how_progress_will_be_measured
```

If no finding clears the evidence threshold the endpoint answers `status:
"insufficient_data"` with a reason. There is no fallback list of "play more
tactics" advice.

## 7. Plan

`GET /api/coaching/plan?weeks=N` derives a short plan **only** from focus areas
that have evidence, and each entry names the metric that will show progress and
the `review_date`. Limitations are attached (`limitations`), so a plan built on
three games says so.

## 8. Conversation

The AI Coach (`/coach?tab=coach`) keeps session context — current game, ply,
player, mode — and reuses it for follow-ups ("Show me the position", "What if I
had played Rd1?"). Persistent facts come from stored data only; arbitrary
conversational claims are never promoted to permanent state.

## 9. Actions, not decorations

Every button in the coaching surfaces maps to a real endpoint or route:
`Analyze position`, `Compare moves`, `Show best line`, `Why?`, `What if?`,
`Train this`, `Open game`, `Prepare against opponent`. There is no action that
renders placeholder text.

## 10. The workspace surfaces

Beyond the conversation and the feed, four read-only surfaces complete the
workspace. Each is engine-free and composed from stored data:

- **Show me why** (`GET /api/coaching/evidence`, rendered by `EvidencePanel`) —
  resolves a claim to its evidence, classifying every reference as an engine
  fact, a Caissa-derived feature, an interpretation or a prediction. A reference
  Caissa cannot follow is returned as a gap with its reason.
- **Study Collections** (`/api/coaching/collections`) — named, typed pointers to
  stored things. A collection permits only certain item kinds, refuses duplicates
  and never copies data.
- **Unified search** (`GET /api/coaching/search`) — one ranked query across every
  stored kind, with each hit naming the terms that matched. "Nothing stored" and
  "nothing matched" are different statuses, each with a reason.
- **Match preparation** (`POST /api/coaching/match-preparation`) — a stored
  snapshot plus the `Caissa MATCH BRIEF`; every section is gated on its sample and
  none of it is a prediction.
- **Progress comparison** (`GET /api/coaching/progress/compare`) — the measured
  difference between two periods of your games, with the sample behind every
  measure and the causality note on every payload.

## 11. What the coaching layer refuses to do

- Predict an opponent's move, or assert that a scenario *will* happen.
- Claim causation from correlation: "your performance in this category improved
  after the training period", never "the training caused your improvement".
- Turn a small sample into a pattern: with limited coverage it reports
  observations and says the sample is small.
- Produce a number with no owner.

## Verification

`scripts/verify_coaching.py` walks the whole loop against a live deployment and
prints each measurement, including the refusals and each workspace surface.
`tests/test_coaching.py` and `tests/test_coaching_workspace.py` (package level),
plus `tests/test_coaching_api.py` and `tests/test_coaching_workspace_api.py`
(HTTP, real engine), cover context resolution, mode exposure, prioritisation, feed
rules, debrief sections, the focus/plan paths, evidence classification,
collections, search, match preparation and the progress comparison.
