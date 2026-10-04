# Caissa — Chess Analysis Design

How Caissa analyzes games and positions. Every number in a report is
engine-derived or board-derived; the system never hardcodes results.

## Pipeline stages

1. **Parse** (`argus.chess_core.pgn`) — PGN text → `Game` with per-ply FENs.
   Main line only; games with illegal/unreadable moves are rejected with
   `InvalidPgnError` (never partially valid).
2. **Engine analysis** (`argus.analysis.engine`) — for each move:
   - position *before* the move, MultiPV (default 3 lines)
   - position *after* the move (single line) to obtain the played move's eval
     when it is not among the MultiPV lines
   - all scores reported from the **mover's perspective**
3. **Features** (`argus.analysis.features`) — raw board-state facts per
   position (material, mobility, king safety, pawn structure, center control,
   development, hanging pieces, phase inputs). Engine-free and deterministic.
4. **Game intelligence** (`argus.analysis.game_analyzer`) — enriches each move
   with phase, classification, sacrifice detection, and aggregates per side.
5. **Report** (`argus.analysis.reports`) — deterministic, explainable sections.

## Evaluations

- Centipawn (cp) and mate scores come straight from Stockfish UCI output.
- Mate scores map to a centipawn-equivalent scale: `±(10000 − |mate|)` — a
  documented convention so mate and cp scores compare mechanically.
- Centipawn loss (CPL) = best-line eval − played-move eval (≥ 0; 0 when the
  played move itself delivers mate).
- Terminal positions (checkmate/stalemate) are flagged `is_terminal` — the
  engine is not called (there is nothing to search).

## Move classification

Labels: `brilliant`, `best`, `good`, `inaccurate`, `mistake`, `blunder`
(plus `book` when PGN opening headers are present). Thresholds live in
`ClassificationThresholds` (configurable, documented heuristics):

| Label | Rule (CPL, mover perspective) |
| ----- | ----------------------------- |
| best | played move equals the engine's best line |
| brilliant | a sound sacrifice that keeps the best or near-best eval |
| good | CPL ≤ 25 |
| inaccurate | CPL ≤ 80 |
| mistake | CPL ≤ 200 |
| blunder | CPL > 200 |

Sacrifice detection is structural: the moved piece is captured by a
lower-value piece on the following position and the move is not a recapture.
Brilliance requires the sacrifice to be sound (engine-verified) — never a
random "creative" label.

## Game phases

Opening / middlegame / endgame classified from **board-state
characteristics**, not move numbers (`argus.analysis.phase`):

- **endgame**: queen off the board or non-pawn material ≤ ~14 points
- **middlegame**: development mostly complete (≥ 2 minor pieces developed per
  side) and king safety still relevant
- **opening**: otherwise (early development)

Phase thresholds are configurable via `PhaseThresholds`.

## Reports

`GameReport` contains: summary (per-side aggregates), opening (from PGN
headers when present), critical moments (largest CPL with engine evidence),
best moves, turning point (largest positive eval swing for the eventual
winner — engine-evidenced), and `pending_sections`, which names the sections this
per-game report object deliberately does not embed (narratives, player tendencies,
recommended training) instead of fabricating them. Player tendencies and training
are implemented as their own surfaces (Phases 5 and 8); they are simply not part of
this report object.

Sections that would require fabricated insight are listed, never faked.

## Import & validation (Phase 2)

PGN arrives as pasted text or an uploaded `.pgn` file and flows through the
same `GameImporter`:

1. **Validate** — structured issues, never exceptions: emptiness, readability,
   header-only games, illegal move sequences, overlong games, and
   result/checkmate consistency. Illegal moves are reported with their move
   number and ply.
2. **Normalize** — PGN headers map onto the internal `Game` (event, site,
   date, round, players, Elo, result, ECO/opening, time control, termination).
   Missing metadata stays `null`; nothing is invented.
3. **Generate positions** — the full sequence initial → move 1 → … → final,
   each with FEN, side to move, SAN/UCI, and previous/resulting FEN, plus
   transient facts (check, checkmate, stalemate, terminal reason).
4. **Persist** — game, moves, positions, players, and status in one transaction.

## Board representation

The frontend board is **position-driven**: it renders the exact FEN of the
selected stored position. There is no independent client-side chess state, so
the board can never drift from the backend. Navigation (first/prev/next/last,
move-list clicks, keyboard ←/→/Home/End) and the last-move highlight all move
an index into the canonical sequence.

Chess rules (castling, en passant, promotion, captures, check, checkmate,
draws) are handled by the shared chess core (python-chess) during position
generation, verified by `tests/test_positions.py`.

## Analysis lifecycle

`imported → ready → analyzing → analyzed` (or `failed`) is stored on the game
and exposed at `GET /api/games/{id}/status`. Until analysis runs, every
analysis surface shows **"Analysis not available yet."** — no accuracy,
evaluation, blunder, or win-probability numbers are ever fabricated.

## Stockfish integration & UCI abstraction (Phase 3)

All engine access goes through the `ChessEngine` interface
(`argus.analysis.engine`). UCI details never leak past `StockfishEngine`:
consumers call `analyze_position`, `analyze_position_multipv`, `analyze_move`,
`compare_moves`, and `analyze_game`, and receive typed Pydantic results.

Search limits:

- **Depth-based** — `go depth N` (the default).
- **Time-based** — `go movetime N` when `movetime_ms` is set; exactly one
  limit governs a search (setting time clears depth).

Configuration comes from the environment (`ARGUS_STOCKFISH_PATH`,
`ARGUS_ENGINE_DEPTH`, `ARGUS_ENGINE_MOVETIME_MS`, `ARGUS_ENGINE_THREADS`,
`ARGUS_ENGINE_HASH_MB`, `ARGUS_ENGINE_MULTIPV`). No machine-specific path is
hardcoded; `locate_stockfish` tries the configured path, then `PATH`, then
common install locations. Startup validates the configuration and logs a clear
warning when no engine is found (health reports `engine.available=false`).

**Reading the engine's output.** Stockfish block-buffers stdout when it is a
pipe, so a finished search (`bestmove`) can sit unflushed until new input
arrives. `_read_until` therefore nudges the engine with a harmless `isready` at
intervals to force the flush. That interval
(`StockfishSettings.flush_nudge_seconds`) is a **per-search latency floor**:
every search costs at least that long, no matter how shallow. It was 0.5 s,
which meant a 33-move game paid ~33 s of pure waiting; it is 0.05 s, which
makes shallow searches roughly an order of magnitude faster while keeping the
nudge that prevents the stall. Search timeouts, crash recovery (terminate +
lazy restart) and the `readyok`/`uciok` handshake are unchanged.

**One search per comparison.** `analyze_game` searches the position before each
move with the configured MultiPV width, and then searches the position after the
move. When the played move is inside the MultiPV window its own score from the
*before* search is kept as `played_eval_cp`/`played_eval_mate`
(`played_eval_source = "same_search"`); only when it is outside the window does
the flipped after-search score stand in (`"resulting_position"`). Accuracy is
computed from the first whenever it exists — see
[game-intelligence.md](game-intelligence.md#141-the-comparison-must-be-like-for-like).

## Evaluation convention (single, enforced)

**Stored and displayed evaluations are White-perspective: positive = White is
better.** Stockfish reports from the side-to-move perspective, so every
conversion goes through `argus.analysis.perspective`
(`to_white_perspective`, `to_mover_perspective`, `for_color`) and is unit
-tested. The same position reads `+2.00` for White and `-2.00` for Black.

Mate scores are **never** ordinary centipawn numbers: they are carried as mate
distances and rendered as `#n` / `#-n`. Where a mate must be compared to a
centipawn value it maps onto a documented ceiling (`MATE_SCORE_CEILING =
10_000`, value `±(10000 − |mate|)`).

## Centipawn loss

`calculate_centipawn_loss(best_cp, best_mate, played_cp, played_mate)`:

1. Both scores are already in the **mover's** perspective (CFL is always the
   mover's loss; callers flip with `flip_score` first).
2. Mate is mapped to the ceiling scale so it is comparable to cp.
3. A move that delivers mate has loss `0`.
4. Otherwise `loss = max(0, best_value − played_value)` — the clamp at zero
   treats "better than best" as search noise, never a genuine improvement.
5. `None` when either evaluation is missing — unknown is reported as unknown.

## Move classification policy

`MoveClassificationPolicy` (aliased as `ClassificationThresholds`) holds
configurable thresholds. Default starting heuristics:

| Label | Rule (mover-perspective CPL) |
| ----- | ---------------------------- |
| brilliant | engine-best **and** sacrifices material **and** beats the second-best line by ≥ 150cp (all evidence required) |
| best | played the engine's best move, or CPL ≤ 10 |
| excellent | CPL ≤ 25 |
| good | CPL ≤ 50 |
| inaccurate | CPL ≤ 100 |
| mistake | CPL ≤ 300 |
| blunder | CPL > 300 |

Unavailable evaluations are **not** classified (`None`). Brilliancy is
evidence-gated (never awarded merely for a large gain). Thresholds are starting
heuristics to calibrate with real data — not universal truths.

## MultiPV

`analyze_position_multipv` returns the top `k` lines, each with move (UCI +
SAN), evaluation (cp or mate), principal variation, depth, and node counts where
reported. Lines are ordered by MultiPV index and are distinct first moves.

## Critical-position detection

`detect_critical_positions` turns an analyzed game into tagged candidates, each
with a `reason` and `severity`: `blunder`, `mistake`, `evaluation_swing`,
`missed_win`, `mate_change`, `material_transition`. A move may yield several
candidates. **A large swing is a candidate, never automatically a mistake** — a
swing can be forced or already lost; interpretation belongs to the intelligence
layer.

## Analysis lifecycle, progress, cancellation, resume

States: `ready → analyzing → analyzed` (or `failed`); interrupted runs become
`cancelled` with the game returning to `ready` (resumable). `paused` is
reserved. Each `AnalysisSession` records `started_at`, `completed_at`,
`current_position`, `total_positions`, `positions_analyzed`, `error`, the full
engine configuration, and the classification policy.

- **Progress** — `GET /api/analysis/games/{id}/progress`.
- **Incremental persistence** — each move is committed as it finishes, so an
  interrupted run keeps its completed plies.
- **Resume** — plies already analyzed at the same analysis version are skipped.
  A run that finds every ply already stored does **not** simply return: it still
  rebuilds the derived critical positions and closes its session, so a
  re-analysis can never report success while leaving the game half-updated.
- **One generation per game** — re-analyzing at a new `analysis_version` writes
  a second generation of rows (the unique key includes the version). Reads
  (`get_move_analyses`, `get_critical_positions`) resolve the **most recently
  written** generation and never return two rows for the same ply, because two
  rows for one ply is a wrong answer, not noise. Critical positions are
  additionally de-duplicated on `(ply, reason)`, keeping the most severe.
- **Cancellation** — cooperative, checked between plies; the engine stops after
  the current position and no orphan processes remain.

## Reproducibility & versioning

Every run records the Stockfish version, the exact parameters (profile, depth or
movetime, MultiPV, threads, hash), the classification policy, and
`ANALYSIS_VERSION`. A `EngineConfiguration` row (hash-keyed) captures the
configuration for audit. Analyses at different configurations coexist, so a
previously analyzed game can be reproduced or compared.

## Position-analysis cache

`PositionAnalysisCache` is keyed by **FEN + engine version + search limit
(depth or movetime) + MultiPV**. Entries are never served when any of those
differ — a result from a different depth or engine version would be misleading.
`CachingEngine` wraps the shared engine to apply it transparently.

## Analysis status meanings

`imported` (parsed only), `ready` (validated, analyzable), `analyzing`,
`analyzed` (**the completed state**), `failed`, `paused`/`cancelled`
(interrupted, resumable). The backend owns this value; the frontend never
infers it.

## Frontend analysis

The game view is position-driven and fully synchronized: board, evaluation bar,
move classification, engine line, and critical-moment list all reference the
same selected ply. Unimplemented functionality shows explicit empty states — no
fake statistics, evaluations, accuracy, or win probabilities.

## From analysis to game intelligence (Phase 4)

The stages above produce **facts**: evaluations, classifications, and candidate
critical positions. They deliberately stop short of interpretation — a large
swing is a candidate, never automatically a mistake; a classification is not a
lesson.

The Phase 4 intelligence layer (`argus.intelligence`) sits on top of the stored
analysis and turns it into structured chess intelligence and a `GameReport`:
board-state phase detection, opening identification and deviation, material
timeline, tactical and positional detection, king safety, piece activity, pawn
structure, turning points, advantage states, conversion, per-phase performance,
and Caissa accuracy. It **never re-runs Stockfish** and every insight records
whether it is an `ENGINE FACT`, an `CAISSA-DERIVED FEATURE`, or an
`CAISSA INTERPRETATION`, with its evidence attached. Natural-language
interpretation is a later phase and is not produced here.

See `docs/game-intelligence.md` for the full methodology, thresholds and report
structure.
