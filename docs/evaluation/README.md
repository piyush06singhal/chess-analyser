# Evaluation

## Evaluation methodology

Caissa is evaluated on **correctness, reliability, reproducibility, performance, AI
quality and chess quality** — not on feature count. This document states how a
result is produced and what it is allowed to claim.

## The shape of a result

Every benchmark produces `CheckResult`s in one of four states:

| State | Meaning |
| --- | --- |
| `pass` | the assertion held |
| `fail` | the assertion did not hold — a real defect |
| `skip` | the check could not run, with the reason (no engine, no data) |
| `warn` | the check held but something is worth recording |

A check may be **critical**. A critical failure in a blocking suite (§42) stops a
release. Skips are never passes: an all-skipped suite fails its gate, so "we could
not measure it" can never be reported as "it is fine".

## The three layers

```
suites        what to check, and why it can block            argus/evaluation/suites/
gates         which suites decide a quality question         argus/evaluation/gates.py
report        the machine-readable + human-readable result   argus/evaluation/report.py
```

Suites hold the policy (a check states its own reason for blocking). Gates hold
the grouping. The report holds no interpretation — it renders what the checks
found.

## Reproducibility (§46)

Every run records, before any suite runs:

* `git_commit` and whether the tree was dirty;
* the evaluation `methodology_version`;
* `engine_name`, `engine_version` and the full engine configuration;
* the model version (or `none`), feature/label/report/graph/knowledge versions;
* the effective `configuration` and the run timestamp;
* the Python version and platform.

A result without these is not comparable to another, so the report carries them at
the top. A value that cannot be determined is recorded as `unknown`, never guessed.

## Datasets (§4)

A benchmark dataset declares `dataset_id`, `version`, `source`, `license`,
`created_at`, `description` and `sample_count` before it is used. The framework
stamps each suite's provenance from that registry (`dataset_id@version`) in one
place, so a suite cannot report a version the registry disagrees with, and the run
header lists the datasets actually used — or `none` when the run used no dataset,
which is a different fact from `unknown`. No user data is used in a benchmark.

## Determinism

Every offline suite is deterministic: pure functions over fixed fixtures, or an
in-memory store. Engine results are the one exception — a search is not bit-stable
across machines — so the engine suite asserts *properties* (a legal best move, a
positive score for a winning side, a mate reported as mate) rather than exact
centipawns, and records the version and configuration that produced them.

## What the framework does not claim

* A green run does **not** mean Caissa plays good chess; it means the measured
  properties held.
* The hallucination validator catches *invention* (a number that appears nowhere in
  the evidence), not a plausible recombination of two real numbers.
* Performance budgets are generous multiples of a measured baseline; they catch
  order-of-magnitude regressions, not micro-variance.
* Coverage is stated honestly in the per-subsystem documents; the audit
  (`README.md`) lists what is still open.

## The benchmark suite

`python scripts/run_evaluation.py` runs every suite and writes
`evaluation/reports/argus-evaluation.{json,txt}`. `--no-engine` skips the engine
suite with a reason; `--only <names>` runs a subset (and then only the gates those
suites cover are evaluated); `--report-dir` chooses the output directory. The
command exits non-zero when a release blocker is present, so it is a CI gate.

## Suites and the gate each feeds

| Suite | Gate | Needs engine | What it proves |
| --- | --- | --- | --- |
| `chess_rules` | CHESS | – | legal moves, castling, en passant, promotion, pins, repetition, endgame rules |
| `fen_benchmark` | CHESS | – | valid/malformed/impossible FENs classify correctly |
| `pgn_benchmark` | CHESS | – | every PGN category parses (or fails) as expected; no illegal move in output |
| `engine` | ENGINE | ✅ | legal best moves, mate/terminal handling, perspective, MultiPV, consistency |
| `move_classification` | INTELLIGENCE | – | classification bands, mate, and no over-firing on a small loss |
| `accuracy` | INTELLIGENCE | – | the accuracy formula is bounded, monotone and documented |
| `game_intelligence` | INTELLIGENCE | – | phase and material on known positions |
| `player_intelligence` | INTELLIGENCE | – | small samples do not produce strong claims |
| `opponent` | OPPONENT | – | claims are sample-gated and never predictions |
| `training` | TRAINING | – | acceptance fairness, difficulty ≠ depth, mastery needs a streak |
| `ml_evaluation` | ML | – | metric arithmetic, calibration, conservative gating, and model regression |
| `agent` | AGENT | – | grounding, hallucination refusal, prompt-injection defence |
| `knowledge` | KNOWLEDGE | – | sourced concepts and deterministic position linkage |
| `graph` | GRAPH | – | evidence, edges, traversal bounds, authorization, similarity |
| `realtime` | REALTIME | – | state machine, timestamp clock, fair play |
| `security` | SECURITY | – | secret redaction, upload safety, injection |
| `privacy` | SECURITY | – | cross-user isolation, fail-closed |
| `performance` | PERFORMANCE | – | percentile latency for the core operations |

## Gates (§41)

| Gate | Passes when |
| --- | --- |
| CHESS | legal moves, FEN and PGN are correct |
| ENGINE | the wrapper returns correct, perspective-normalised results |
| INTELLIGENCE | game & player intelligence are rule- and sample-correct |
| ML | metrics, calibration and gating are computed correctly |
| AGENT | the agent selects tools, stays grounded and refuses to invent |
| TRAINING | exercises are valid and the acceptance policy is fair |
| OPPONENT | opponent claims are evidenced, never predictions |
| GRAPH | nodes, edges, evidence and traversal are consistent |
| KNOWLEDGE | knowledge is sourced, deterministic and refuses the absent |
| REALTIME | live-game state and event ordering stay consistent |
| SECURITY | authorization, injection and secret handling hold |
| PERFORMANCE | core operations stay within their measured budgets |

A **critical** gate failing blocks a release. A full run covers every gate; a
subset run evaluates only the gates it exercised (and says so by omission rather
than by a false pass).

## Release blockers (§42)

An automated release is blocked for: an illegal chess state, a data-integrity
failure, a cross-user access, a fabricated numerical output, an invalid engine
perspective, a broken training solution, a prediction-leakage finding, a critical
security issue, or a live-game synchronization corruption. A busy-but-honest
warning never blocks a release; a correctness or privacy failure always does.

## Beyond the suites

Not everything fits an in-process suite, so three tools sit alongside the
framework and are run deliberately rather than on every commit:

| Tool | Covers | Needs |
| --- | --- | --- |
| `scripts/benchmark_api.py` | API p50/p95/p99 and error rate (§33) | a running stack |
| `scripts/benchmark_db.py` | query latency **and query plans** (§34) | `ARGUS_DATABASE_URL` |
| `scripts/load_live.py` | live-game load and WebSocket latency (§31) | a running stack |

**Model regression (§44)** lives in `argus.evaluation.model_regression` and is
exercised by the `ml_evaluation` suite. It compares a candidate against production
on the same dataset and reports per-metric deltas, subgroup and temporal
regressions, leakage and reproducibility — and never promotes. A candidate that
improves the average while regressing a subgroup, fails its leakage check or is not
reproducible is recommended against. With no production model there is nothing to
regress against, and it says so rather than promoting the first model.

**CI (§54)** is `.github/workflows/quality.yml`: the evaluation framework offline,
the test suite with `engine`-marked tests excluded, and the frontend type-check,
lint and build. The engine and load runs are a release step, so CI stays fast.

## Offline integrity check

`tests/test_evaluation_framework.py` runs every offline suite in the ordinary test
suite and fails if any check fails, if a gate references a suite that does not
exist, or if a failing suite fails to block its gate. So a regression in the
evaluation *harness* is caught the same way a product regression is.

## Reproducing a report

The report's header carries the commit, engine version and configuration, and the
version of every subsystem, so a run can be reproduced (or declared
non-comparable) without asking which build it was. Each suite's **dataset** is
stamped from the registry (``dataset_id@version``) in one place, so a suite cannot
report a version the registry disagrees with; a run that used no dataset says
``none``, not ``unknown``.

## Chess correctness evaluation

The floor: Caissa must never accept or produce an illegal chess state. Three
suites, all engine-free and deterministic, decide the `CHESS_GATE`.

## `chess_rules` (§5)

Fixtures are standard positions with checkable properties (`fixtures.POSITION_CASES`):
the start position, checkmate, stalemate, a pin, castling, en passant, promotion,
fifty-move, insufficient material and a back-rank mate.

For each fixture the suite asserts:

* the expected property (legal-move count, check/mate/stalemate flags, etc.);
* **every legal move in the position validates** through Caissa's own
  `validate_uci` and `validate_san`, compared against python-chess as the
  reference — a disagreement is a defect;
* illegal moves (`e5` for White from the start, a pinned knight's move) are
  **refused**;
* a genuine threefold repetition replays to a claimable draw.

Using python-chess as the reference is deliberate: Caissa already depends on it, so
the comparison is mechanical rather than asserted from memory.

## `fen_benchmark` (§6)

Valid, malformed and impossible FENs classify exactly as expected, and Caissa's
verdict agrees with python-chess on every fixture. Impossible cases include an
empty board, a missing king, too many pawns, a pawn on the back rank and a
position where the side not to move is in check. Caissa returns *why* it refused,
and the suite checks the reason contains the right keyword.

## `pgn_benchmark` (§7)

Thirteen PGN categories: standard, a real theory line, castling, promotion, en
passant, draw, check, checkmate, annotated (comments and NAGs), multi-game,
partial (headers only), invalid (illegal move) and corrupted. For every valid
category the suite replays the parsed game and asserts **no output move is
illegal in sequence**; for invalid categories it asserts the failure is classified
with a reason. A headers-only record is refused, not stored as an empty game.

## Thresholds and limitations

* The reference is python-chess's definition of legality; Caissa does not
  independently re-derive chess rules, and does not claim to.
* The fixtures are a curated set of standard positions and real games, not an
  exhaustive perft. A perft comparison is a possible future addition.
* Chess variants are out of scope (standard chess only).

## Intelligence graph evaluation

The `graph` suite checks the graph's promises on an in-memory store, so it is
deterministic and needs no database. It decides the `GRAPH_GATE` and includes the
checks that block a release.

## What is measured (§28/§29)

| Case | Expected |
| --- | --- |
| a structural edge (game → position) with no evidence | allowed |
| a derived edge (`has_pattern`) with no evidence | **refused** |
| an evidenced derived edge | written, and traces to its reference with no gaps |
| a reversed edge shape | refused with the declared shape |
| a limited traversal | bounded, and reports `truncated` |
| an unauthorized node | hidden, denial counted, never returned |
| a consistent graph | healthy (no orphans, no invalid edges) |
| bookkeeping-only FEN differences | the same position |
| a different side to move | a different position |
| identical positions | similarity `exact` |
| a different position | never labelled `exact` |
| an unrelated position | `None` — no verified similarity |

The two "never" rows are the trust model: an exact historical match must mean the
same position, and a derived relationship must have evidence. Both are critical.

## Evidence provenance (§43)

`trace_evidence` resolves a node's justification to stored references and reports
*gaps* — derived edges whose evidence is missing or whose source can no longer be
found. The suite asserts a well-formed graph has no gaps and that a graph missing
evidence is caught by the health check.

## Authorization (§33)

The suite drives `GraphAccessPolicy` directly: a game-scoped node with no
resolvable game is denied (fail closed), and a caller restricted to their own
games cannot read another's. This is where the graph's isolation is asserted even
though today's deployment is single-user.

## Query and traversal performance

Latency of a bounded traversal is measured in the `performance` suite
(p95 well under the budget). Completeness and correctness of the multi-hop
queries are asserted in `tests/test_intelligence_graph_*.py` against real games.

## Limitations

* The non-exact similarity scan in the Position Explorer is bounded to the most
  recent stored positions and states its scan size; it is not an exhaustive search.
* The suite uses the in-memory store; the SQL store's indexes are exercised by the
  API tests.

## Live chess evaluation

The `realtime` suite checks the pure live-chess modules — the state machine, the
timestamp-derived clock, terminal detection and the fair-play clamp. The
multi-client behaviour (synchronization, reconnect, event ordering) is covered by
the API suite (`tests/test_live_api.py`) and by `scripts/load_live.py` and
`scripts/verify_live.py` against a running server.

## State machine (§30)

* a legal transition (`waiting → ready`) is allowed;
* an illegal one (`finished → active`) is refused with a reason;
* **no terminal state has an outgoing transition** — a finished game cannot be
  resumed.

## Clock

* the mover's clock is charged from timestamps (10s elapsed → 290,000ms), not a
  decrementing counter;
* the waiting side's bank is frozen;
* a move charges the mover and credits the increment;
* a clock past zero is flagged, and a late move is **refused as a timeout** — the
  clock, not the client, decides time;
* the snapshot exposes the server value.

## Terminal detection

Checkmate and stalemate are detected as terminal; the start position is not; and a
claimable fifty-move draw is reported as *claimable*, not auto-applied.

## Fair play (§12 phase spec)

A competitive game (`GameMode.LOCAL`) cannot enable engine analysis — a request
for `training_analysis` resolves to `no_analysis` — while a sandbox game may. This
is the structural guarantee that competitive play is engine-free, and it is
critical: it is a correctness property, not a preference.

## Load and synchronization

Move/event/sync latency and multi-client consistency are measured by
`scripts/load_live.py` (a probe, not a distributed benchmark) and asserted by
`scripts/verify_live.py`. The event log is the source of truth; the in-process hub
is a latency optimization. A shared fan-out for multi-worker deployments is a
documented follow-up.

## Security and privacy testing

Two suites, `security` and `privacy`, decide the `SECURITY_GATE`. Their failures
are release blockers: a leaked credential, an escaped upload path, a followed
instruction or a cross-user read must never ship.

## `security` (§37)

| Case | Expected |
| --- | --- |
| a credential in a trace (`api_key=…`) | redacted to `[redacted]` |
| a bare `token: …` | redacted |
| `../../etc/passwd` as an upload filename | neutralised (no separators, no `..`) |
| a backslash path | neutralised |
| the agent prompt | forbids following embedded instructions |
| a malicious PGN header | stored as data, never executed |

Redaction is checked against `ai_agent/observability.py::scrub`; upload safety
against `argus_api/services/uploads.py::sanitize_filename`.

## `privacy` (§38)

Cross-user isolation is asserted on the authorization policy itself, so it holds
regardless of the frontend:

* a game-scoped node with no resolvable owner is **denied** (fail closed);
* a caller restricted to their own players cannot read another player's private
  node, and the denial is counted;
* the caller's own data is readable;
* a caller cannot read another user's game.

Testing at the policy boundary (not through the UI) is the point: privacy is a
backend property.

## What is not automated here

* Full penetration testing (SQL/command injection against live endpoints, rate
  limiting, WebSocket authorization). The input-handling tests
  (`tests/test_security.py`) and the API suite cover the injected-string and
  authorization cases; a full pentest is an operational step, recorded as open in
  the audit.
* Secret scanning of the repository and environment (a CI/lint concern).
* Prompt-injection against a live model — the structural defences are asserted,
  but a live-model jailbreak suite needs an API key.

## Training evaluation

The `training` suite checks the properties a fair, honest training engine must
have: a move that is practically as good as the engine's is not marked wrong,
difficulty is measured rather than renamed depth, and mastery needs a streak.

## Acceptance (§17)

| Attempt | Expected |
| --- | --- |
| the exact solution | `correct` |
| a recorded equivalent | `correct` |
| 15cp worse | `correct` (within the 30cp tolerance) |
| 100cp worse | `near_best` (playable, not wrong) |
| 400cp worse | `incorrect` |
| a move with no stored evaluation | `incorrect`, refused with a reason |

The equivalent-move case is critical: telling a player "wrong" for a
practically-equivalent choice is untrue and demotivating, and it is the failure
mode the phase names explicitly. Grading in centipawns (with mate arithmetic) is
what makes it fair.

## Difficulty (§18)

The suite asserts `assess_difficulty` **takes no engine depth parameter** — a
structural guarantee that difficulty is not a rename of search depth — and that it
returns measured factors with a bounded score. The factors are forcing-move ratio,
evaluation tightness, piece density and solution type.

## Retention and adaptation (§19/§20)

A fresh exercise is driven through the scheduler with consecutive correct answers;
it may only reach `mastered` after the full documented streak
(`MultiplierSchedule.mastered_streak`), and a single incorrect answer must drop it
back out of mastered. Both are critical: mastering on one success would make the
review queue meaningless.

## Causal caution

The suite does **not** claim training causes improvement. Retention is a
schedule; the progress report measures change and carries the causality warning.
A causal claim would need a controlled design the product does not run.

## What is not covered here

* Position *generation* validity (legal FEN, verified solution) is exercised by
  `tests/test_training_engine.py` against real analysed games, because it needs
  an engine and stored analysis. The offline suite covers the policy layer that a
  generation bug would surface through.
* Difficulty calibration against real player performance — the bands are a
  documented heuristic, not a fitted model.

## AI agent evaluation

The agent's job is to answer from retrieved evidence and to refuse when it has
none. That is testable without an LLM, and the `agent` suite does exactly that:
it builds the evidence packet a grounded turn would produce and checks that the
**validator** accepts grounded prose and rejects invention.

## What is measured (§21–§26)

| Case | Expected |
| --- | --- |
| a grounded evaluation (`+2.1` against evidence `210cp`) | validates |
| an invented evaluation (`+3.4`) | caught |
| an invented FEN | caught |
| a fabricated count (`7 blunders` with no such number) | caught |
| a probability with no prediction evidence (`62%`) | caught |
| a claim against an empty packet | refused |
| the system prompt's data-vs-instruction rule | present |
| a malicious PGN header | stored verbatim, never executed |

Grounding is checked against `CoachingAgent`'s validator
(`ai_agent/safety/validation.py`), which is the only thing standing between a
confident model and a false number. If it did not catch `+3.4`, the whole trust
model would be decoration — so the suite treats those cases as critical.

## Numerical claim validation (§25)

The validator extracts every number from the packet and rejects a claimed number
that appears *nowhere* in the evidence, within a rounding tolerance for
evaluations. The suite verifies this on evaluations, counts, percentages and FENs.

## Prompt injection (§26)

Two structural defences are checked: the system prompt declares all chess content
to be data (never instruction) and names the "ignore previous instructions" class
of attack; and a malicious PGN header round-trips as a literal string. Tool
boundaries are enforced by the tool contract, tested in `tests/test_agent_*.py`.

## What this does not cover

* Tool **selection** against a real model. The existing scripted-provider suite
  (`argus/ai_agent/evaluation.py`) covers the deterministic half; a live-model
  run needs an API key and is not part of the offline gate.
* Full semantic verification of prose. The validator catches invention, not a
  plausible recombination of two real numbers — stated plainly, not hidden.
* Answer *quality* (helpfulness, tone), which is a product judgement, not a
  correctness property.

