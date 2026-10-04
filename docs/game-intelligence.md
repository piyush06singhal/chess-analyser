# Caissa — Game Intelligence Layer (Phase 4)

How Caissa turns already-computed engine analysis into **structured, evidenced
chess intelligence** and a `GameReport`. Nothing in this layer calls Stockfish,
invents a statistic, predicts an outcome, or writes prose. Natural language is a
later phase; it will only ever *explain* the structures documented here.

```
PGN → positions → Stockfish analysis → move classifications   (Phase 1–3)
                                    ↓
                    GAME INTELLIGENCE (this layer)
                                    ↓
                        structured GameReport              (Phase 4)
```

Package: `argus.intelligence` (`packages/argus/argus/intelligence/`).

---

## 1. The evidence model (the whole point)

Every claim the layer makes is wrapped in `Insight`/`Finding` and carries:

| Field | Meaning |
| ----- | ------- |
| `source` | `engine_fact` · `argus_derived_feature` · `argus_interpretation` |
| `certainty` | `confirmed` · `candidate` |
| `evidence` | the concrete numbers/positions the claim rests on |
| `ply` / `move_number` | where in the game it happened (nullable) |
| `policy` / `thresholds` | the configurable rule that fired, when relevant |

`EvidenceSource` is the mandatory separation the Phase 4 spec asks for:

- **`engine_fact` (`ENGINE FACT`)** — read straight from Stockfish output:
  evaluations, best moves, PVs, mate distances, centipawn loss, classifications.
  Caissa does not reinterpret these.
- **`argus_derived_feature` (`CAISSA-DERIVED FEATURE`)** — measured *from the
  board* by deterministic code: material balance, pawn structure, piece
  mobility, king-zone attackers, detected tactical configurations, phase
  indicators. Engine-free and reproducible from the FEN alone.
- **`argus_interpretation` (`CAISSA INTERPRETATION`)** — Caissa's own framework
  laid over the two above: advantage bands, turning points, error categories,
  conversion assessment, phase performance, accuracy. These are Caissa-defined
  and are labelled as such in the report (`evidence_policy`).
- **`FUTURE AI INTERPRETATION`** — *not present in this phase*. The explanation
  layer (LLM) will consume `GameReport`/the tool accessors and produce prose;
  it is not implemented here and produces nothing in the report.

`certainty` distinguishes a **confirmed** finding (rule matched on present data)
from a **candidate** (plausible but not provable from the data — e.g. a tactical
configuration Caissa recognises but cannot verify as the played idea). Candidates
are always surfaced as candidates, never promoted to facts.

`REPORT_VERSION = "4.2"`. It is stored alongside Phase 3's
`analysis_version = "3.1"`, so a report is always tied to the analysis it
described. (The analysis version moved to 3.1 because per-move rows now store
the played move's own score from the same search; stored 3.0 rows carry no such
score and are re-analyzed rather than silently reused.)

---

## 2. Inputs — reuse, never duplication

The layer consumes what Phase 3 already stored and **never re-runs the engine**:

| Source (Phase 3) | Used as |
| ---------------- | ------- |
| `move_analyses` | `MoveFact` per ply: evals before/after, CPL, classification, best move, PV, depth, phase |
| `critical_positions` | `CriticalFact`: reason, severity, swing, mate-related flag |
| `analysis_sessions` | provenance: engine, engine version, depth, multipv, profile |
| `games` / players | `GameContext`: names, ratings, result, ECO, opening header, initial/final FEN |

Perspective rule (unchanged): stored/displayed evaluations are **White
perspective, `+` = White better**. Mate is never rendered as centipawns — it is
a mate distance (`#n`) and only maps onto `MATE_SCORE_CEILING = 10_000` where a
mechanical comparison is required.

The API builds the service from storage in a single pass (`_build_intelligence`)
and answers `409 analysis_required` when a game has no stored analysis rather
than emitting sections it cannot support.

---

## 3. Game phase detection — `phases.py`

`GamePhaseDetector` classifies each position as **opening / middlegame /
endgame** from **board-state characteristics**, not move numbers. Move numbers
never appear in the decision.

Each of three *substantive* indicators votes, with a `PhaseIndicator` recording
whether it was evaluated and what it said:

- **`non_pawn_material`** — total non-pawn material and piece count.
- **`development_and_castling`** — undeveloped minor pieces and castling rights
  (only votes while material is high enough for development to be meaningful:
  gated by `opening_development_min_phase_material = 30`).
- **`endgame_character`** — queens off, few total pieces, king activity.

`_decide()` documents the precedence explicitly:

1. **Endgame evidence** → `endgame`.
2. **Opening evidence** → `opening`.
3. Otherwise the **Phase 3 baseline** (`argus.analysis.phase.classify_position`).
4. Otherwise **middlegame** (the default).

Configurable thresholds (`GamePhasePolicy`): `endgame_max_phase_material = 14`,
`queens_off_endgame_max_phase_material = 26`, `endgame_max_total_pieces = 14`,
`opening_min_phase_material = 52`, `opening_min_undeveloped_total = 3`,
`opening_development_min_phase_material = 30`.

`confidence` is **not** a probability and is not shown as one: it is the ratio of
winning substantive votes to substantive indicators evaluated (an agreement
measure). `PhaseDetection.decision` records which rule actually decided, so the
reasoning is auditable. The result is a `PhaseDetection` with `phase`,
`confidence`, `decision`, `reasons`, `indicators`, plus the detected
`PhaseTransition`s (first ply of each phase).

---

## 4. Opening identification — `openings.py`, `opening_book.py`

`detect_opening` identifies the opening from the **played move sequence** first,
falling back to PGN ECO/opening headers. Identification source is recorded
(`move_sequence` / `eco` / `explicit`) so a header-derived name is never
confused with a verified line.

- Matching is against a curated in-repo line table (`OPENING_LINES`); the system
  reports `family`, `variation` where supported, `ECO`, and the matching line.
- If nothing matches confidently the result is **`Unknown / Unclassified`** —
  Caissa does not guess a name from superficial similarity.
- **Deviation** is tracked separately from error: `OpeningDeviation` records the
  **last known opening ply**, the **deviation ply**, the expected continuation
  when the line provides one, and the continuation actually played. Leaving
  theory is explicitly **not** an error — deviation and mistake are different
  fields in the report.

---

## 5. Material analysis — `material.py`

Board-measured, engine-free. `build_material_timeline` walks the positions and
produces:

- `MaterialSnapshot` per ply: per-side piece counts and `material_points` from
  `PIECE_POINTS` (`MaterialPolicy`: pawn 1, knight/bishop 3, rook 5, queen 9),
  plus the balance.
- `MaterialEvent`s: captures, gains/losses, exchanges, promotions, and
  transitions above `transition_min = 2` pawns.

**Material is never conflated with engine advantage.** A side can be a piece up
and worse; the material timeline and the evaluation trajectory are independent
sections and the report keeps them separate.

---

## 6. Tactical event detection — `tactics.py`

`detect_tactical_events` scans the board (and the engine PV where available) for
**configurations**, not conclusions. A blunder classification is *not* evidence
a tactic existed — classification and detection are separate inputs.

`TacticType`: `fork`, `double_attack`, `pin`, `skewer`, `discovered_attack`,
`hanging_piece`, `back_rank_weakness`, `mating_threat`, `forced_exchange`,
`overloaded_defender`.

Each `TacticalEvent` carries `type`, the move, **affected pieces/squares**, the
position, its evidence, and a severity. Config: `TacticPolicy`
(`min_target_value = 3`, `fork_min_targets = 2`). Configurations Caissa cannot
verify are emitted as **candidates** (`certainty=candidate`), never as fact.

`forced_exchange` requires a **real capture** of material worth reporting (at
least a minor piece, per `min_target_value`), answered by a recapture on the same
square. A quiet move that is simply taken is not a recapture, and a routine pawn
trade is left to the material timeline rather than surfaced as a tactic.

---

## 7. Positional features — `positional.py`, `structure.py`, `activity.py`

`PositionalEventType`: `isolated_pawn`, `doubled_pawn`, `backward_pawn`,
`passed_pawn`, `pawn_island`, `weak_square`, `open_file`, `semi_open_file`,
`pawn_break`, `restricted_piece`, `trapped_piece`, `undeveloped_piece`,
`lost_center_control`, `space_disadvantage`, `poor_rook_placement`.

**Feature ≠ error.** `build_positional_analysis` first records the objective
structural feature. It only becomes an *error candidate* when the position /
evaluation context supports it — `PositionalPolicy` requires
`error_candidate_min_cp_loss = 100`, and thresholds like
`restricted_mobility = 1`, `lost_center_min = 2`,
`undeveloped_after_ply = 24` gate the candidate label. Features that are not
supported stay features.

- `structure.py` — `build_pawn_structure` / `pawn_structure_for`: pawn islands,
  files (open/semi-open), break opportunities, per-side `PawnStructureSide`.
- `activity.py` — `build_piece_activity` / `activity_for`: per-piece legal
  mobility, attacked and defended squares, `SideActivity` aggregates. Measured
  facts intended to feed player analysis, ML and future explanations — no
  good/bad judgement attached.
- `weak_squares()` — squares only one side can contest.

---

## 8. King safety — `king_safety.py`

`build_king_safety` produces structured `KingSafetyEvent`s (never prose) for
each side, over the `king_zone` around each king.

`KingSafetyEventType`: `castled`, `castling_rights_lost`, `pawn_shield_reduced`,
`open_file_near_king`, `king_exposed`, `piece_pressure_near_king`,
`check_given`, `king_mobility_limited`, `mating_net`.

A weighted score (`KingSafetyPolicy`: missing shield pawn 2, open file 2,
attacker 2, check 1, limited mobility 1) maps to severity via
`medium_score = 4`, `high_score = 8`. Severity is derived from the weights,
never asserted.

---

## 9. Game trajectory — `trajectory.py`

`build_trajectory` turns the per-ply evaluations into a `TrajectoryPoint` series
(each with the White-perspective eval, its display form, advantage band, mover),
split into `TrajectorySegment`s, and annotates **analytical states**
(`TrajectoryEventType`): `advantage_creation`, `advantage_loss`, `comeback`,
`collapse`, `stabilization`, `conversion`.

These are analysis states, not emotional labels — each is defined by the band
sequence it requires. Mate is carried as `mate_plies` plus an
`evaluation_display` (`#n`); mate distances are **never** folded into the
centipawn range (a bug fixed against real data). `final_evaluation_display`
gives the end position's honest reading.

---

## 10. Advantage states — `advantage.py`

`state_for` maps a White-perspective evaluation to a normalized band
(`AdvantageState`): `forced_mate`, `winning`, `advantage`, `slight_advantage`,
`equal`, `slight_disadvantage`, `disadvantage`, `losing`.

Configurable, documented (`AdvantagePolicy`): `slight_threshold = 60`,
`advantage_threshold = 150`, `winning_threshold = 300`,
`decisive_threshold = 600` centipawns. `describe_policy()` returns the thresholds
so every report can state the bands it used — the states are **Caissa-defined
categories, not universal chess truth**, and the report says so.

---

## 11. Turning points — `turning_points.py`

`detect_turning_points` finds positions where the game materially changes.
It deliberately does **not** simply take the largest centipawn swing.

`TurningPointType`: `evaluation_swing`, `advantage_lost`, `missed_win`,
`mate_change`, `material_transition`, `forced_sequence`, `conversion_failure`.

Each `TurningPoint` holds `move`, position, `evaluation_before`, `evaluation_after`,
`swing`, the `player` it affected, its `event_types`, severity and evidence.

Config (`TurningPointPolicy`): `min_swing_cp = 150`, `high_swing_cp = 300`,
`material_swing_pawns = 2`, and — importantly for robustness —
`persistence_plies = 4` (the swing must stick), `merge_window_plies = 2`
(nearby points merge), `max_turning_points = 8`, `missed_win_min_cp = 250`.
Multiple turning points may exist, and a swing is a **candidate**, not an
automatic verdict — a forced or already-lost swing is not a mistake.

Material swings are signed for the mover
(`material_balance_change_for_mover`) and measured over the **settled exchange
sequence** the move starts, not over a fixed one-reply window: same-square
recaptures are skipped so a recapture is not double-counted, and the window ends
at the first ply after which every square captured in the sequence has been
recaptured (reopened when the very next ply captures that square again).

- One reply is too short: `4.dxe5 Bxf3` alone reads as a two-pawn loss for the
  side that is winning a pawn, until `5.Qxf3 5...dxe5` settle it to zero.
- A whole run of captures is too long: `9.exd5 Nxd5 11.Nxf7 Kxf7` is two
  separate exchanges, and only the second one loses material.

The mate signal is stated in the direction it actually went: the mover
**delivered** checkmate (read from the board — a terminal position is never
evaluated, so a mating move is never mistaken for a mate that "disappeared"),
the mover's own forced mate was **no longer available**, or a forced mate
**appeared against** the mover. A mate change is never asserted when the
position after the move was not evaluated.`


---

## 12. Conversion — `conversion.py`

`analyse_conversion` tracks whether a winning position was converted.
`ConversionEventType`: `conversion_confirmed`, `advantage_conversion_candidate`,
`winning_became_equal`, `equal_became_losing`, `losing_recovered`.

Config (`ConversionPolicy`): `winning_state_cp = 300`,
`lost_advantage_max_cp = 120`, `min_winning_plies = 2`. Failure is only reported
when a sustained winning state (≥2 plies above the winning threshold) later
falls into the lost-advantage range.

---

## 13. Phase performance — `performance.py`

`build_phase_performance` aggregates each side's moves **per detected phase**:
counts, classifications, average centipawn loss, best moves, problem moves.
`phase_statement` renders a factual one-liner. `PhasePerformancePolicy`
(`reliable_min_moves = 10`, `reliable_min_scored = 8`) marks a phase's numbers
unreliable when the sample is too small — small samples are labelled, not
silently averaged.

This is what answers "which phase caused the largest deterioration", and it
reports it per side.

---

## 14. Accuracy methodology — `accuracy.py`

Caissa's accuracy is **its own documented metric** and is stated as such wherever
it appears.

- **Win expectation**: a logistic curve over centipawns,
  `scale_cp = 300`, with mate handled exactly (win), never as a large cp number.
- **Per-move loss**: `max(0, E_before − E_after) / max(E_before, denominator_floor)`
  with `denominator_floor = 0.5` — losing a move *after* a mistake costs less
  than losing a move from a balanced position.
- **Move accuracy**: `100 × (1 − loss)`.
- **Game accuracy**: arithmetic mean over **scored** moves only. Positions that
  were already decided (`E_before ≥ decided_win_expectation = 0.95`, or ≤ 0.05)
  are **excluded and counted separately** — a game can't gain accuracy by
  shuffling in a won position. `minimum_scored_moves = 5` labels thin samples.
- **Raw centipawn loss** is reported as a separate, different quantity —
  average CPL, best-move counts and problem-move counts are never merged into
  the accuracy figure.

### 14.1 The comparison must be like-for-like

Subtracting a score produced by one engine search from a score produced by
another is not a measurement of the move — a few centipawns of search noise are
not a loss, and reporting them as one would make the whole metric arguable.
Caissa therefore takes **both** sides of the comparison from **one** search, the
MultiPV search of the position before the move:

* `E_before` comes from the best line of that search;
* `E_after` comes from the same search's line for the move that was actually
  played — the played move's own score (`played_eval_cp`/`played_eval_mate`).

Every scored move records where its `E_after` came from
(`MoveAccuracy.evaluation_source`), and both sides count them:

| Source | Meaning |
| ------ | ------- |
| `same_search` | Exact: the played move was inside the MultiPV window, so best-line and played-move scores come from one search (identical depth, identical tree). |
| `resulting_position` | Approximate: the played move fell outside the window, so the score is the flipped evaluation of a **separate** search of the position after it. Honest, but a few centipawns of the difference is then search noise. |
| `unavailable` | No score for the played move at all — the move is **not scored** (never imputed). |

`SideAccuracy.exact_scores` / `approximate_scores` publish those counts and the
UI shows them next to the number, so a reader can always tell how much of an
accuracy figure rests on exact comparisons. Widening the MultiPV window raises
the exact share and costs engine time; the default is the analysis profile's
`multipv` (3), which on real games makes roughly half of the scored moves exact.
A stored analysis from `analysis_version` 3.0 has no played-move score at all,
and is honestly reported as entirely approximate until the game is re-analyzed.

### 14.2 Where the accuracy was lost

A single number per side is easy to argue with, so Caissa also slices the same
scored moves three ways, per side:

| Dimension | Slices |
| --------- | ------ |
| `by_phase` | opening, middlegame, endgame (board-state phase of the position before the move) |
| `by_classification` | blunder, mistake, inaccurate, good, excellent, best, brilliant |
| `by_material_state` | behind by 3+ pawns, behind by 1+, level, ahead by 1+, ahead by 3+ (from the mover's point of view) |

Each slice reports its own `scored_moves`, `accuracy`, `average_centipawn_loss`
and its `share_of_loss` — the fraction of that side's total normalized
win-expectation loss that came from that slice. Slices always partition the same
moves the game total is computed from, so they add up; a slice with nothing
scored in it is omitted rather than printed as a zero, and a slice with fewer
than `minimum_group_moves = 5` is flagged `small_sample`. When a side lost
nothing at all, `share_of_loss` is `null` for every slice — there is no loss to
attribute, and Caissa does not invent a 0% split.

`ACCURACY_DISCLAIMER` is attached to every accuracy payload:

> Caissa accuracy is Caissa's own documented metric. It is not Chess.com accuracy,
> not Lichess accuracy, and is not calibrated against either.

---

## 15. Error categories, lessons, recommendations

- `categories.py` — `categorise_errors` assigns each problem move an
  `ErrorCategory`: `tactical`, `material`, `king_safety`, `opening`, `endgame`,
  `positional`, `unclassified`. This answers "was the problem tactical,
  positional, material, king safety or conversion?" with a category derived from
  the co-occurring detected features, and `unclassified` when nothing supports a
  category rather than forcing one.
- `report.build_key_lessons` — lessons are generated from the evidence:
  the biggest phase deterioration, repeated error categories, missed wins,
  conversion failures. Each lesson carries its evidence.
- `report.build_training_recommendations` — recommendations count how many times
  an observed pattern occurred (`Recommendation.observed_count`), so a
  recommendation is traceable to a number of observations, never invented.

---

## 16. The `GameReport` structure

`report.py` defines the structured report (`REPORT_VERSION = "4.2"`). Top-level
sections:

| Section | Contents |
| ------- | -------- |
| `context` | players, ratings, result, date, event, ECO, move count |
| `provenance` | `AnalysisProvenance`: analysis version, engine + version, depth, multipv, profile, moves in game, evaluated moves |
| `summary` | `GameSummarySection` — factual headline statements |
| `opening` | identification (family/ECO/source) + deviation |
| `phases` | detected phases, transitions, per-phase performance |
| `trajectory` | points, segments, analytical states, final display |
| `material` | timeline, events, balances |
| `tactical` | confirmed events and separate candidates |
| `positional` | features and error candidates |
| `king_safety` | per-side events |
| `accuracy` | per-side accuracy with methodology + disclaimer |
| `conversion` | conversion assessment |
| `error_categories` | category breakdown of problem moves |
| `turning_points` | ranked, evidenced turning points |
| `critical_moments` | merged, clickable timeline (`TimelineEntry`, each navigable to a ply) |
| `key_lessons` | lessons, each with evidence |
| `training_recommendations` | recommendations with observation counts |
| `unavailable` | `Unavailable` entries — what is honestly not implemented (LLM narration, ML prediction, player profiles) and why |
| `evidence_policy` | the `EVIDENCE_POLICY` statement describing the three evidence sources |

Piece activity and pawn-structure analyses are carried within their sections
(rather than as separate top-level keys) and are available directly through the
service accessors.

---

## 17. Service surface (AI-ready tool accessors)

`GameIntelligence` (`service.py`) exposes one accessor per question, so a future
agent calls typed structured data rather than raw engine output:

`get_game_summary`, `get_game_trajectory`, `get_critical_moments`,
`get_tactical_events`, `get_positional_events`, `get_phase_analysis`,
`get_material_timeline`, `get_accuracy`, `get_player_game_statistics`,
`get_move_analysis`, plus `build_report` and `available_tools`.

### API endpoints (`apps/api/argus_api/routes/intelligence.py`)

Under `/api/intelligence`:

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/games/{id}/report` | generate **and persist** the report |
| GET | `/games/{id}/report` | stored report (`?refresh=true` regenerates) |
| GET | `/games/{id}/report/status` | whether a report/analysis exists (no generation) |
| GET | `/games/{id}/summary` | summary statements |
| GET | `/games/{id}/trajectory` | trajectory, bands, states |
| GET | `/games/{id}/critical-moments` | critical positions + merged timeline |
| GET | `/games/{id}/move-analysis` | stored per-move analysis (`?ply=`) |
| GET | `/games/{id}/tactical-events` | tactical events + candidates |
| GET | `/games/{id}/positional-events` | positional features + candidates |
| GET | `/games/{id}/phase-analysis` | phases, transitions, performance |
| GET | `/games/{id}/material-timeline` | material snapshots + events |
| GET | `/games/{id}/accuracy` | accuracy + methodology + disclaimer |
| GET | `/games/{id}/player-statistics` | single-game stats per side |
| GET | `/games/{id}/tools` | the agent tool surface |

`player-statistics` is deliberately **single-game**: a player profile must never
be inferred from one game, and this phase does not build one.

### Persistence

`game_reports` (SQLAlchemy `GameReportRecord`) stores one row per
`(game_id, report_version, analysis_version)` with the full report in a `JSON`
`payload` column plus denormalized provenance columns (engine, depth, move
counts, `generated_at`) and an index on `game_id`. Deletes cascade from the game.
Schema is created on API startup (`create_all`); see limitations.

---

## 18. Frontend

- `apps/web/src/app/game/[id]/report/` — the report page (`page.tsx`,
  `report-view.tsx`).
- `components/eval-graph.tsx` — SVG evaluation graph, hover/click, mate-safe
  (mate is drawn from `mate_plies`, never as a cp spike).
- `components/critical-timeline.tsx` — the critical-moment timeline; **clicking
  an entry navigates to that exact position** on the board. Every timeline entry
  is navigable.
- `lib/api.ts` — typed report payloads (incl. `mate_plies`,
  `final_evaluation_display`).
- `analysis-view.tsx` — links from a completed analysis to the report.

The board remains position-driven (Phase 1): the report only selects a ply; it
never holds an independent board state.

---

## 19. Versioning, performance, and honest gaps

**Versioning.** `REPORT_VERSION = "4.2"` and Phase 3's `analysis_version = "3.1"`
are both stored. A report always identifies the analysis it describes and the
engine/parameters behind it. Re-analyzing a game at a new version leaves the old
per-move rows in place (the unique key includes the version), so **reads resolve
one generation**: `get_move_analyses`/`get_critical_positions` default to the
most recently written version and never return two rows for the same ply. A run
that finds every ply already stored still rebuilds the derived data and closes
its session, so a re-analysis can never report success while leaving the game
half-updated. A persisted report is self-healing: the stored snapshot is reused
only while its `report_version` matches the running code, and a report written by
an older version is rebuilt from the stored analysis on the next read
(`report_is_current`). So a detector-semantics change (e.g. `4.1` → `4.2`) never
keeps serving superseded answers. If the analysis behind a stale report is gone
and it cannot be rebuilt, the existing snapshot is served rather than a spurious
`409`.

**Performance.** The layer is pure computation over already-stored analysis: no
Stockfish calls, one query per table per request (no N+1), and the generated
report is persisted so the UI can serve the stored copy. On the reference game
(33 plies, Opera Game) report generation took **≈0.16 s**.

**Known limitations (reported, not hidden).**

1. **No Alembic migrations.** Schema, including `game_reports`, is created with
   `create_all` on startup.
2. **In-process job runner** for computation-heavy steps; not a real queue.
   Cancellation stops after the current engine search.
3. **A legacy synchronous report endpoint** duplicates the report generation
   path and is kept for compatibility.
4. **No LLM key configured**, so the explanation layer is only exercised against
   an echo client. By design, no LLM chess claims exist in this phase.
5. **`forced_exchange`** is limited to a genuine capture of a minor piece or
   better, recaptured on the same square; routine pawn trades and quiet moves
   that are later taken are not reported. The detector is intentionally
   conservative elsewhere.
6. **Piece activity** underlies its own section and is reported as measured
   facts without good/bad judgement; interpreting it belongs to a later phase.

**Explicit non-goals.** No predictive ML, no player profile, no LLM narration,
no comparative accuracy claim against Chess.com or Lichess. Advantage bands,
phase boundaries and accuracy are Caissa-defined categories, documented and
configurable — not universal chess truth.
