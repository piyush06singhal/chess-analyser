# Training engine (Phase 8)

The training engine turns a player's **verified mistakes** into exercises, tracks
their solutions, schedules reviews, and measures improvement. It is built on one
rule that shapes every part of it:

> A training exercise must be traceable to a real, analysed position in the
> player's own games, and its answer must be engine-verified. When Caissa cannot
> justify an exercise, it refuses the position and reports why.

Nothing is generated from a template or invented by a model.

## The product loop

```
PLAY → ANALYZE → UNDERSTAND → TRAIN → RETEST → MEASURE IMPROVEMENT → PLAY AGAIN
```

Training is the step that closes the loop: the same stored analysis that powers
Game Intelligence (Phase 4) and Player Intelligence (Phase 5) is the source of the
exercises, so an exercise is never detached from the game it came from.

## 1. Origin chain

Every exercise stores where it came from:

```
game_id → source_ply → source_position_id → analysis_version → exercise
```

`TrainingPosition.source_reason` carries the human-readable version ("Played Nf6
(mistake) in the middlegame; tactical evidence: hanging_piece"). The exercise page
links back to `/game/{id}?ply={n}`, and the game page links forward to training.

If the source game is later deleted, the exercise and its attempt history survive
(`ON DELETE SET NULL`): `origin.available` becomes false and the UI says the
source is unavailable. Regenerating a game's exercises is the only path that
replaces them, and it is an explicit `regenerate: true` request.

## 2. Generation pipeline

`argus.training.generator` is deterministic and engine-free. It consumes the
stored per-move analysis and produces candidates:

1. **Skip** moves with no stored best move, or where the played move *was* the
   best move (there is nothing to train).
2. **Eligibility gate** — see below. A refusal is a result, carried with its
   reason.
3. **Build** the exercise from the position **before** the move, recording the
   engine-verified solution, the acceptable alternatives, the principal
   variation, and the move that was actually played.
4. **Assign a category only from evidence.** A tactical motif is assigned only
   when the stored classification/tags prove it; otherwise the honest category is
   `calculation`. Opening-phase positions become `opening`; sparse positions
   become `endgame`. `conversion` and `recovery` are assigned only from *whole-game*
   arc evidence — the stored evaluation trajectory plus the game result
   (`argus.training.gamearc`) — never from a single move.
5. **Deduplicate** by normalized FEN (board, side to move, castling, en passant)
   per player. The same position cannot become two exercises.

Generation happens at an explicit request (`POST /api/training/games/{id}/generate`),
never on a dashboard load. The stored solution is what a later attempt is graded
against, so serving a puzzle never runs Stockfish (spec §44).

## 3. Eligibility methodology

`TrainingEligibilityService` rejects a candidate when any of these hold. All
thresholds live in `EligibilityPolicy` and are documented in `GET /api/training/meta`.

| Rejection | Condition |
| --------- | --------- |
| `position_not_legal` / `solution_not_legal` | the FEN is invalid, the game is over, or the solution is not legal |
| `no_engine_analysis` | no stored evaluation, and the solution is not a mate score |
| `not_a_mistake` | the played move cost less than `min_gap_to_played_cp` (120 cp) |
| `solution_not_clear` | the best alternative is less than `min_advantage_cp` (60 cp) worse than the solution |
| `trivially_obvious` | the evaluation is beyond `max_solution_advantage_cp` (900 cp) — a decided position teaches nothing |
| `too_forced` | fewer than `min_legal_moves` (3) legal moves |
| `duplicate_position` | the normalized FEN is already in the player's library |

A mate-score solution *is* engine analysis, so mate-only candidates are not
rejected for a missing centipawn evaluation; centipawn clarity checks are skipped
for them, not guessed.

## 4. Difficulty methodology

`assess_difficulty` computes a difficulty band from **measurable factors**, never
from a move number or a guess:

- how deep the solution is in the engine's line (PV length),
- how clear the solution is (the gap to the best alternative),
- the evaluation gap the played move cost,
- the number of legal moves (branching),
- the piece count (positional complexity).

The factors are stored beside the label (`difficulty_factors`), so a difficulty
can be audited rather than trusted. Bands: `beginner`, `easy`, `intermediate`,
`advanced`, `expert`.

## 5. Move acceptance

`MoveAcceptancePolicy` decides whether an attempt was correct. The comparison is
always **from the mover's perspective**.

| Outcome | Rule |
| ------- | ---- |
| `correct` | the exact solution, a recorded equivalent, or within `equal_tolerance_cp` (30 cp) |
| `near_best` | within `near_best_cp` (150 cp): playable, not the engine's move |
| `incorrect` | worse than that, or a non-mating move in a forced-mate position |

Mate arithmetic is handled before centipawn arithmetic, because centipawns cannot
express "mated in two". Delivering the fastest mate is correct; a slower mate is
near-best; failing to mate in a forced-mate position is incorrect. When the player
is *being* mated, any defence that delays the mate past `hopeless_mate_threshold`
(2 moves) is credited as near-best — a hopeless position has no "best" worth
grading.

The evaluation of an alternative move comes from the stored data when it can
(the solution itself, an accepted equivalent, or the recorded played move) and
from the engine only when it genuinely cannot — an explicit validation at attempt
time, not a dashboard load.

## 6. Spaced repetition

`argus.training.scheduler` implements a deterministic state machine
(`new → learning → review → mastered`, with `needs_review` and `failed`). Every
transition is explicit in `STATE_TRANSITIONS`; an illegal transition raises.

- The first correct answer moves an exercise to `learning` with the initial
  interval (1 day).
- Each successful review doubles the interval (capped at 180 days).
- **Mastered requires a streak of 4 correct answers**, never a single success.
- A `near_best` answer keeps the exercise scheduled but does not advance it.
- An incorrect answer returns it to the relearn interval (1 day) and, from the
  `new` state, fails it outright.

Every attempt is stored forever (`TrainingAttempt`): submitted move, correctness,
evaluation and delta, hints used and response time. Progress is computed from the
full history, not from a last-known score.

## 7. Recommendations

`RecommendationEngine` ranks categories that need work from **measured**
performance. A category is only recommended when:

- the library can actually deliver exercises for it, and
- it has at least `MIN_SAMPLE_FOR_ACCURACY` decided attempts, and
- its measured accuracy is below the documented threshold.

Priority blends accuracy, sample size, attempt frequency, recency and whether the
category has never been attempted. Every recommendation carries its evidence lines
("3/12 decided attempts correct (25%) in Tactics", "recurring pattern
'hanging_piece' appears in 4 exercises"). With too little data, Caissa says so and
recommends nothing — it never invents a weakness.

## 8. AI integration

The Phase 7 agent exposes the engine read-only through these tools:

| Tool | Permission | What it returns |
| ---- | ---------- | --------------- |
| `get_training_recommendations` | player | prioritised opportunities with evidence |
| `generate_training_position` | player | one exercise, **solution withheld** |
| `generate_training_explanation` | player | the full evidence including the solution, PV and the played move |
| `get_review_queue` | player | exercises whose review is due |
| `get_training_progress` | player | measured progress with sample sizes |
| `get_training_from_game` | game | exercises derived from one game |
| `evaluate_training_attempt` | player | grades a move **without storing an attempt** |
| `get_training_requirements` | any | the contract a legitimate exercise must satisfy |

The agent can explain why a solution is right, what went wrong, and how a position
relates to the player's history — always from stored evidence, never invented. The
explanation tool is separate from the puzzle tool precisely so a coaching answer
cannot leak an answer the player has not attempted.

## 9. Progress and honesty

`compute_progress` reports attempts, accuracy by category and difficulty, state
counts, retained reviews, hint usage and response times — **each figure with its
sample size**. `ProgressReport.accuracy` is `None` when there are no decided
attempts, and the UI renders "—" rather than a fabricated `0%`.

## 10. Privacy: personalized vs general

- **Personalized** exercises carry the owning `player_id` and are derived from
  that player's games. They are private: an endpoint or agent tool asked for
  another player's exercise answers 404, never the exercise.
- **Opponent preparation** exercises also carry the owning `player_id` (the player
  preparing), and are built from the *opponent's* games — so the owner is the one
  who practises, and the source is someone else's game.
- **Counterfactual** exercises (below) carry the owning `player_id` too: the
  position is one they actually faced, and the solution is the engine's own move
  there.
- **General** exercises have a `NULL` player and are the only shareable ones. The
  data source is stored (`data_source`) and rendered verbatim in the UI.

### Exercises from a counterfactual (Phase 10)

`argus.training.from_scenario` builds an exercise from a counterfactual analysis,
under three rules that keep it honest:

1. **A measured solution only.** No engine score for the move means no exercise.
2. **Only a genuinely good answer becomes the solution.** The move must be the
   engine's first choice or within the near-best tolerance the *grader itself* uses
   (`MoveAcceptancePolicy.equal_tolerance_cp`, 30cp), so an exercise can never be
   built around a move the grader would have marked wrong.
3. **One position, one exercise.** The existing normalized-FEN dedupe key applies;
   asking twice returns the stored exercise rather than a duplicate.

The category is assigned from board evidence only: `tactical` when the solution
captures something or the engine's own consequences list a tactic, otherwise
`calculation`. `data_source` is `counterfactual`, and the source reason records
the move that was played and the measured difference, e.g.
`Counterfactual analysis: instead of Nf3, worth +0.70 pawns, engine score +0.30`.
The API refuses (`already_played`, `not_defensible`, `illegal_move`) rather than
storing an answer key it cannot justify, and the exercise's solution is withheld on
later reads the way every other training read withholds it.

## 11. Provenance

Each exercise stores the engine, engine version, depth, `analysis_version` and
`methodology_version` that produced its solution. The methodology version is
bumped whenever a rule in this document changes, so a stored exercise can be
traced to the rules that made it.

## 12. API

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/api/training/meta` | vocabulary, thresholds and methodology versions |
| POST | `/api/training/games/{game_id}/generate` | generate exercises from a game |
| GET | `/api/training/games/{game_id}` | exercises derived from one game |
| GET | `/api/training/positions` | the player's library (+ general) |
| GET | `/api/training/positions/{id}` | one exercise, solution withheld |
| GET | `/api/training/positions/{id}/hints` | progressive hints |
| GET | `/api/training/positions/{id}/solution` | reveal the verified solution |
| POST | `/api/training/positions/{id}/attempt` | grade an attempt and schedule the review |
| GET | `/api/training/positions/{id}/attempts` | full attempt history |
| GET | `/api/training/attempts` | a player's attempt history |
| GET | `/api/training/review-queue` | exercises due for review |
| GET | `/api/training/progress` | measured progress |
| GET | `/api/training/recommendations` | evidenced priorities |
| POST | `/api/training/sessions` | start a resumable session |
| GET | `/api/training/sessions` | list sessions |
| GET | `/api/training/sessions/{id}` | one session with its next exercise |
| POST | `/api/training/sessions/{id}/cancel` | cancel without deleting attempts |

## Methodology 8.1 additions

- **`continue_line` is now emitted** when the stored principal variation is long
  enough to grade move-by-move (≥ 3 plies). The exercise keeps the engine's own
  line *after* the solution (up to four plies) in `continuation_line`; 
  `POST /api/training/positions/{id}/continue` grades the solver's continuation
  moves against it, advancing the board by the stored opponent replies in
  between. It stores nothing (a continuation is a practice extension, not a new
  attempt type).
- **`conversion` and `recovery` are now generated** from whole-game evidence.
  `argus.training.gamearc.classify_arc` reads the player's stored evaluations and
  the game result: a win that slipped becomes `conversion`, a lost position that
  was held becomes `recovery`. The reason is stored in the exercise's provenance.
  Without a game result and colour, no arc label is produced.
- **`acceptable_moves` now comes from real data.** Analysis persists the stored
  MultiPV window (`candidate_moves`), and the generator derives the acceptable
  set from it, so a practically equivalent move grades correct instead of wrong.

## Known limitations (Phase 8 / 8.1)

- **`what_went_wrong` and `reconstruction` are declared but never emitted.** They
  are whole-game formats needing replay with per-move checking that the generator
  does not have; emitting an exercise that cannot be graded would be worse than
  not emitting it.
- **`evaluate_training_attempt` may run the engine** for a move the stored data
  cannot grade; it is an explicit user action, not a read.
- **Session planning is deterministic, not adaptive within a session** — the plan
  is fixed when the session starts; the spaced-repetition schedule still updates
  per attempt.
- **Difficulty is a heuristic over measurable factors**, not a calibrated model.

## Phase 9 — opponent intelligence

The training engine consumes the same stored analysis as Phase 9's opponent
analytics; the two share the arc evidence for conversion/recovery. See
[`docs/opponent-intelligence.md`](opponent-intelligence.md).
