# ARGUS Chess — Chess Analysis Design

How ARGUS Chess analyzes games and positions. Every number in a report is
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
winner — engine-evidenced), and `pending_sections` listing what is honestly
not implemented yet (narratives, tendencies, training plans).

Sections that would require fabricated insight are listed, never faked.

## Frontend analysis

The dashboard shows the chessboard (react-chessboard), game info, per-move
analysis panel, and report panel. Unimplemented functionality shows explicit
empty states — no fake statistics.
