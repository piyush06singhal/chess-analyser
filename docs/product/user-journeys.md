# Caissa — User Journeys

**Status:** the five journeys below are executed end to end by
`scripts/verify_journeys.py` against a live API, engine and database. The command
is the evidence; this document is the map.

```bash
python scripts/verify_journeys.py
```

The script walks each journey start-to-finish, each step beginning where the last
as the same user would experience them, and reports `OK`/`FAIL` per check. It is
wired into CI. A journey that is documented here but not exercised there is a
documentation bug.

## Journey 1 — Bring in a game and read the report

```text
Import (PGN / account)
  → validate (typed issues, never an exception)
  → generate positions
  → Stockfish analysis
  → Game Intelligence report
  → read: accuracy, turning points, material, king safety, phase performance
```

What must hold, and is checked:

- the import is validated and stored with its source;
- the analysis runs against the real engine and stores per-ply evaluations;
- the served report covers every ply and agrees with the game's own moves;
- accuracy is measured for **both** sides;
- the report carries its provenance (engine name, positions analysed).

## Journey 2 — Review a game's turning points

```text
Game report → critical moments → "Why?" → evidence → related positions
```

What must hold:

- critical moments are ranked by the measured evaluation swing, not by guesswork;
- each carries a `Why?` trace: relationship → evidence → sample → methodology;
- a claim with no evidence is refused, not softened.

## Journey 3 — Train the weakness

```text
Analysed game → generate exercises → solve → graded attempt → progress
```

What must hold:

- exercises come only from the player's own analysed mistakes (eligibility rules);
- the engine-verified best move grades as correct;
- an attempt is stored and progress is measured from stored attempts;
- training generation on a game with no eligible material honestly says so.

## Journey 4 — Prepare for the opponent

```text
Opponent → historical games → repertoire → tendencies → preparation → exercises
```

What must hold:

- the profile is built from the opponent's stored games only;
- **observed history is never presented as guaranteed future behaviour** — the
  report distinguishes "observed" from "potential preparation scenario";
- every unavailable section names its reason;
- opponent-specific exercises can be generated.

## Journey 5 — Ask the coach

```text
Question → plan → tools → evidence → answer → validate → actions
```

What must hold:

- the answer states how it was produced (mode, provider, whether deterministic);
- the answer carries the evidence the turn retrieved;
- a question about a probability with no validated model is refused honestly.

> The prediction refusal is **deterministic**: it is a stored fact ("no model has
> passed its production gate"), so it is answered without calling the LLM and
> cannot be degraded by a provider outage. When a validated model *is* available
> the refusal steps aside and the model's own output is presented through the
> gated tool.

## Cross-cutting journeys (manual, documented)

These are not scripted because they need human judgement (visual quality,
interaction feel), but their acceptance criteria are stated so a reviewer can
check them:

### New user

Landing → dashboard empty state → import → analyze → report → training.
Acceptance: the dashboard empty state explains the three steps and offers the two
real ways to add a game; nothing is populated with fake statistics.

### Returning user with many games

Dashboard → library filters → open a game → jump to a critical moment.
Acceptance: real counts, newest games first, and every row links to the real game.

### Incomplete / failed analysis

Game page while `analyzing`, and after a failure.
Acceptance: a status is shown, the UI does not present partial data as final, and a
failed analysis offers a retry.

### Mobile

Board + move list + coach on a phone viewport.
Acceptance: the board stays usable, the move list scrolls, no horizontal scroll on
primary surfaces.

### Keyboard / screen reader

Tab from the top through the nav, a form and the board.
Acceptance: focus is always visible, every control is reachable, and the board and
move list are operable and announced.