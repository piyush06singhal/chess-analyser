# Caissa — Release status

## Caissa — Release Sign-Off

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)
**Commit:** `3c5dd53f5259bdc8cbe60e3c6663109edfd73c57` + Phase 17 working-tree fixes
**Date:** 2026-10-04
**FINAL DECISION:** `HOLD`

## Why HOLD

Not because the code is wrong — every **executable** gate that was run passed
(1584 tests, the 165-check release gate with 0 blockers, system check 19/19,
config valid, all 5 journeys, 210 frontend e2e across three browser engines,
backup/restore verified, data
integrity clean). HOLD because several §52 gate items were **not executed in this
environment**, and one production decision that only the operator can make is
still open. Releasing on unverified gates would be exactly the "force a release
because Phase 17 was reached" failure the phase warns against.

### 1. Verification gates not yet executed

| Gate | State | What is needed |
| --- | --- | --- |
| Deployment dry run | manifest validates; **not deployed to a real host** | deploy `docker-compose.production.yml` to a staging host and smoke-test |
| Rollback | **NOT RUN** | a real deploy, then roll back to the previous image tag |
| Disaster recovery | restore verified in-place; **no host-loss drill** | restore onto a fresh host from backup and start Caissa |
| Alerts | **NOT RUN** | wire an alerting target and fire a test alert |

### 2. Scope decisions only the operator can make

The release is scoped in `RELEASE_STATUS.md` to a **single-instance, self-hosted**
deployment. Under that scope the open items below are `POST-RELEASE`, not
blockers. If the target is a **public multi-tenant launch**, they become blockers:

- no user accounts / sign-up / account lifecycle;
- per-process rate-limit window, metrics and live hub (no shared fan-out);
- no legal/privacy pages or support channel;
- no data-export bundle;
- no manual screen-reader certification; automated axe passes on 17 pages, and
  the full e2e suite now runs on Chromium, Firefox **and** WebKit (210 passed).

## Conditions that flip the decision to RELEASE

Either condition is sufficient:

- **A. Complete the four unexecuted gates** (deploy + rollback to a staging host,
  DR drill, an alert test), with the results recorded here; **and** confirm the
  scope is single-instance self-hosted (so the scope items stay `POST-RELEASE`).
- **B. Confirm the target is single-instance/self-hosted and accept the four
  gates as post-rollout verification** — the operator signs that the deployment
  will be to one trusted host, with restore-only DR (already verified) rather
  than a host-loss drill.

## Sign-off checklist (as executed)

| Gate | Result |
| --- | --- |
| Clean build succeeds | ✅ |
| Migrations (bootstrap + upgrade) succeed | ✅ (no Alembic; documented) |
| Data integrity passes | ✅ (no serious inconsistency) |
| Chess correctness passes | ✅ |
| Stockfish validation passes | ✅ |
| Game Intelligence passes | ✅ |
| Player Intelligence passes | ✅ |
| ML production validation passes | ✅ |
| AI Agent validation passes | ✅ |
| Hallucination tests pass | ✅ |
| Prompt injection tests pass | ✅ |
| RAG validation passes | ✅ |
| Graph validation passes | ✅ |
| Training validation passes | ✅ |
| Opponent validation passes | ✅ |
| What-If validation passes | ✅ |
| Live chess validation passes | ✅ |
| Frontend regression passes | ✅ |
| Mobile validation passes | ✅ (no device farm) |
| Accessibility validation passes | ✅ automated / ⚠ manual not done |
| Security validation passes | ✅ (`pip-audit` 0; npm prod 0; `docker scout` 0 critical — 3 base-OS HIGH dispositioned as accepted risk) |
| Privacy validation passes | ✅ isolation / ⚠ no accounts |
| Load testing completes | ✅ |
| Performance measurements recorded | ✅ |
| Observability works | ✅ |
| Alerts are tested | ❌ not run |
| Backup succeeds | ✅ |
| Restore succeeds | ✅ |
| Deployment dry run succeeds | ⚠ manifest only |
| Rollback succeeds | ❌ not run |
| Disaster recovery verified | ⚠ restore verified, host-loss not |
| Documentation complete | ✅ |
| Release notes exist | ✅ |
| Known limitations documented | ✅ |
| No unresolved release blocker (for the scoped release) | ✅ |

## Signatures

| Role | Name | Decision | Date |
| --- | --- | --- | --- |
| Engineering | — | HOLD pending the conditions above | 2026-10-04 |
| Product owner | — | *(awaiting operator scope confirmation)* | — |

No tag or release branch is created until this sign-off reads `RELEASE`.

## Caissa — Release Scope

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)

This file states, without marketing language, what this release is and what it
is not. It is the contract the sign-off is judged against.

## Release type

A **self-hosted, single-instance** chess intelligence platform. One operator runs
it for one or a few trusted users behind an API-key boundary. It is *not* a
public multi-tenant SaaS in this release.

The product rule that governs every surface is enforced in code, not copy:
*a capability that is not built is reported as not built; a number that was not
measured is not shown; a claim without evidence is refused.*

## In scope (shipped and verified)

- Engine-grade game import and analysis (Stockfish, depth/time, MultiPV).
- Game, player and opponent intelligence with per-claim evidence and sample
  sizes.
- Personalized training generated only from the user's own analyzed mistakes,
  with spaced repetition and an honest review queue.
- An evidence-gated AI coach (63 tools) that answers from retrieved data,
  refuses fabricated numbers, and degrades honestly with no provider.
- What-If counterfactual analysis and match preparation.
- Server-authoritative live chess with fair-play enforcement.
- An evidence-driven intelligence graph with a health check.
- An evaluation framework that blocks a release on failures that must never
  pass.
- Production infrastructure: config validation, container images, health/ready/
  metrics probes, resource gating, backup/restore, additive schema upgrades.

## Out of scope (this release)

| Item | Why it is out | Classification |
| --- | --- | --- |
| Multi-user accounts | Identity is an API key → caller id; no sign-up/login/account deletion | POST-RELEASE |
| Multi-instance horizontal scaling | Rate-limit window, metrics and live hub are per-process | POST-RELEASE |
| Legal / privacy pages, support channel | Required before a public launch that stores personal game data | POST-RELEASE |
| Data-export bundle | Not built | POST-RELEASE |
| Production ML predictions | No model has passed its gate; the endpoint refuses with a reason | POST-RELEASE |
| Alembic migration tooling | `create_all` + additive, idempotent upgrades instead | POST-RELEASE |
| Distributed tracing | Single service; request correlation is present | POST-RELEASE |
| Manual screen-reader certification | Automated axe passes; manual walkthrough not performed | POST-RELEASE |
| Cross-browser matrix | Chromium, Firefox and WebKit all automated; 210 e2e tests pass | Done |

## Deployment targets supported

- **Development:** Docker Compose (`docker-compose.yml`), SQLite or Postgres.
- **Production template:** `docker-compose.production.yml` (pinned images,
  non-root, no published DB port, resource limits, config validated at start).

## Compatibility

- Python ≥ 3.11; tested on 3.14.
- Node ≥ 20.
- PostgreSQL 17 (production), SQLite (dev/tests).
- Redis 7 (optional; health + cache).
- Stockfish is an external binary; version is recorded per analysis. See
  `RELEASE_STATUS.md` for the host/container version note.

## Versioning

`VERSION` = `1.0.0`. The same value is reported by `apps/api/argus_api`
(`__version__`) and `apps/web/package.json`. Subsystem methodology versions
(analysis `3.1`, player `5.0`, graph `13.0`, …) are separate and version *what a
symbol means*, not the release.

## Caissa — Release Blocker Classification

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)
**Classified:** 2026-10-04
**Decision input for:** `RELEASE_STATUS.md`, `RELEASE_STATUS.md`

Every item below was observed, not assumed. A finding is a **blocker** only if it
would make the product wrong, unsafe or misleading in the release's intended
deployment. Missing functionality that the scope deliberately excludes is
classified `POST-RELEASE`, not a blocker.

Classification scale:

| Level | Meaning |
| --- | --- |
| BLOCKER | The release must not ship until this is resolved. |
| CRITICAL | Functionally wrong or unsafe under realistic use; must be fixed before a broad launch. |
| HIGH | Serious gap, degraded but honest behaviour; can ship only with an explicit scope statement. |
| MEDIUM | Noticeable but non-corrupting; safe to ship with documentation. |
| LOW | Cosmetic or convenience. |
| POST-RELEASE | Deliberately out of scope for 1.0.0. |

## Release blockers

None of the open items below is a correctness or safety hazard for the intended
deployment (single-instance, self-hosted, API-key boundary). The release is
scoped to that deployment so no item qualifies as a BLOCKER for it.

If the release target is changed to a **public multi-tenant SaaS**, the items
marked *(public-launch)* become blockers.

## Critical

*(none open)*

- The one historical correctness defect found during Phase 17 — the opponent
  preparation endpoint returning HTTP 500 because `data_source` was too narrow
  — was fixed and re-verified live (`HTTP 200`, `data_source` persisted). The
  underlying persistence error is no longer reproducible.

## High

| # | Item | Scope | Why it is not a BLOCKER for 1.0.0 |
| --- | --- | --- | --- |
| H1 | No user accounts; identity is an API key → caller id | *(public-launch)* | The release is single-operator; cross-caller privacy is enforced and tested. A public sign-up/login system is `POST-RELEASE`. |
| H2 | Rate-limit window, metrics counters and the live WebSocket hub are per-process | *(public-launch multi-instance)* | A single instance is the supported topology; per-process state is correct there. Horizontal scale-out is `POST-RELEASE`. |
| H3 | No legal/privacy pages or support channel | *(public-launch)* | Not required to run the product for trusted users; required before storing the public's personal data. |
| H4 | No data-export bundle | — | Deletion and per-caller isolation are enforced; an export bundle is a convenience feature, `POST-RELEASE`. |

## Medium

| # | Item | Note |
| --- | --- | --- |
| M1 | ~~Cross-browser automated coverage is Chromium only~~ **RESOLVED** | The whole e2e suite (a11y + responsive + report + graph + system) now runs on **Chromium, Firefox and WebKit** — 210 passed. It caught a real missing-`h1` defect on the report page, now fixed. |
| M2 | No manual screen-reader walkthrough | Automated axe (17 pages) passes and boards are keyboard-operable with live announcements; manual VoiceOver/NVDA certification is `POST-RELEASE`. |
| M3 | No field Web Vitals | No production traffic exists; field numbers are reported `UNKNOWN` rather than invented. Internal latency budgets are measured. |
| M4 | No Alembic | Schema bootstraps with `create_all` + additive, idempotent upgrades; verified against a clean DB and an upgrade path. Alembic is `POST-RELEASE`. |
| M5 | Stockfish version may differ host vs container | Documented; every analysis records the engine version that produced it. Not a defect. |

## Low

| # | Item | Note |
| --- | --- | --- |
| L1 | ~~`forced_exchange` tactic detector over-fires~~ **RESOLVED** | The detector now requires a real capture of a minor piece or better, recaptured on the same square. Quiet moves that are later taken, and routine pawn trades, are no longer mislabelled as recaptures. `REPORT_VERSION` bumped to `4.2`. |
| L2 | Retired `position_analyses` table on the live dev DB | Legacy artifact; drop script guarded with `--yes`. Fresh schema creates 29 tables. |

## POST-RELEASE candidates

- User accounts, sign-up/login, account lifecycle and deletion.
- Shared store + Redis fan-out for rate limit, metrics and the live hub.
- Legal/privacy pages, support channel.
- Data-export bundle.
- A production predictive ML model (none has passed its gate; the refusal is the
  shipped behaviour).
- Alembic migration tooling.
- Firefox/WebKit in the automated browser matrix.
- Manual screen-reader certification.
- Distributed tracing.

## Change log

| Date | Change |
| --- | --- |
| 2026-10-04 | Classification established at feature freeze. Fixes this phase: opponent-prepare persistence (critical, resolved); training verifier stale version + wrong solution (test defect, resolved); intelligence-graph dangling edges (correctness, resolved); `forced_exchange` mislabelling quiet moves and pawn trades (correctness, resolved, `REPORT_VERSION` → `4.2`). |

## Caissa — Known Limitations

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)

A limitation stated here is a boundary that was measured, not a guess. Nothing
in this file is hidden to make the release look better; a transparent limitation
is preferable to a fabricated capability.

## Product
- **No user accounts.** Identity is an API key mapped to a caller id. There is no
  sign-up, login, session or account deletion. Cross-account privacy is enforced
  between callers, but a public multi-user launch needs a real account system.
- **No shared state across instances.** Rate limiting, metrics counters and the
  live WebSocket hub are per process. A multi-instance deployment needs a shared
  store and Redis fan-out.
- **No legal/privacy pages, data-export bundle or support
  channel.** Required before a public launch that stores personal game data.

## Chess Analysis
- **Classification thresholds are documented starting heuristics**, not universal
  chess truth. Trends are self-consistent only within a like-depth corpus.
- **Caissa accuracy is Caissa-defined** and is not comparable to Chess.com or
  Lichess accuracy; the metric travels with that disclaimer.
- **`forced_exchange` is deliberately narrow** — it reports a real capture of a
  minor piece or better, recaptured on the same square. Quiet moves and routine
  pawn trades are not reported; the other tactical detectors are conservative in
  the same way.

## AI
- **The coach is grounded, not omniscient.** It answers from retrieved evidence
  and explicitly states when something is unavailable. On a provider outage it
  returns an evidence-only answer naming the real reason.
- **Free-tier provider caps are real.** On an on-demand tier the tool list is
  bounded by the token budget; a 429 is retried briefly with backoff (3 attempts,
  5s cap) so a transient throttle still produces a real answer, and a persistent
  one degrades to an evidence-only answer rather than a fabricated one.
- **No model provider configured = no prose explanation.** The agent still answers
  stored-fact questions and says it cannot write an explanation.

## ML
- **Predictions are unavailable by design.** No model has passed its production
  gate, so every predictive endpoint returns an explicit unavailable response
  with its reason. There are no accuracy numbers for a model, because none has
  been measured.
- **The bundled corpus is small** — for pipeline verification only. The training
  gate correctly refuses it, so no dataset claim is made beyond the measured
  size.

## Training
- **Exercises exist only where the analysis supports them.** A game can yield
  zero exercises; that refusal is honest, not a bug.
- **Mastery needs a streak and sufficient attempts**; below threshold the
  progress surface reports `insufficient_data`.

## Live Chess
- **Per-process hub.** A live game is authoritative on the instance that serves
  it; there is no cross-instance fan-out.
- **Competitive games never receive engine help.** This is enforced, but it also
  means no engine-backed coaching mid-game by design.

## Performance
- **No field Web Vitals captured.** The app is instrumented
  (`src/components/vitals.tsx`) but there is no real traffic; field numbers are
  `UNKNOWN` rather than invented. Internal latency budgets are measured by the
  evaluation suite.
- **Per-route static payload sizes are not reported** beyond the Next.js build
  output.

## Infrastructure
- **No Alembic.** The schema bootstraps with `create_all` plus additive,
  idempotent upgrades that only add columns/indexes/constraints; they never drop,
  rename or back-fill.
- **Metrics and the live hub are per process** (repeated above for emphasis on
  operational impact).
- **No distributed tracing.** Request/correlation IDs are present in logs.
- **Engine version can differ between host and container.** The API container
  installs Stockfish from the distro (`17.1` observed) while a developer host may
  run a newer build (`19` observed). Analyses record the engine version that
  produced them; do not compare scores across engine versions.

## Mobile
- Layouts are verified for no horizontal overflow at 390 / 768 / 1280 px and the
  primary controls stay on screen at mobile width. Board interaction uses the
  same pointer/keyboard handlers as desktop. No device-farm test was run.

## Accessibility
- **No manual screen-reader walkthrough.** Automated axe scans pass on 17 pages
  and interactive boards are keyboard-operable with named squares and live
  announcements, but a full manual audit (VoiceOver/NVDA) has not been
  completed.
- The force-directed intelligence map provides a list equivalent (spatial layout
  is not the only access path).

## Caissa — Feature Freeze

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)
**Freeze date:** 2026-10-04
**Branch:** `main`

From this point no new major functionality enters the release. Only
release-blocking fixes and documentation corrections may land until sign-off.
Any newly discovered feature gap is classified `POST-RELEASE` unless it is a
genuine production blocker (see `RELEASE_STATUS.md`).

## What is frozen in

Everything shipped across Phases 1–16. The authoritative, evidence-backed list
is the [README capabilities matrix](../../README.md#capabilities); this section
records the same set at freeze time.

| Subsystem | State at freeze | Evidence |
| --- | --- | --- |
| Importing | PGN paste/upload, Chess.com, Lichess; typed validation | `scripts/verify_games_api.py` — all checks pass |
| Chess core | Parsing, normalization, positions, FEN/SAN/UCI | `argus.chess_core`; pytest `test_chess_core`, `test_positions` |
| Engine | Stockfish over UCI, depth/time, MultiPV, cancellation | `scripts/verify_engine.py` — OK |
| Game analysis | Classification, CPL, critical moments, accuracy | `scripts/verify_analysis.py`, `verify_game_analysis.py` — OK |
| Game intelligence | Phases, openings, tactics, turning points, `GameReport` | `scripts/verify_intelligence.py` — invariants OK |
| Player intelligence | Versioned profile, Chess DNA, claim levels | pytest `test_player_intelligence`, `test_player_api` |
| ML | Registry, gating, calibration; predictions refused by design | pytest `test_ml_*`, `test_leakage`; system check ML registry |
| AI agent | 63 declared tools, evidence packet, refusal validator | `scripts/verify_agent.py` — all checks pass |
| Training | Mistake→exercise→attempt→scheduling; sessions | `scripts/verify_training.py` — passes |
| Opponent intelligence | Repertoire, tendencies, preparation, brief | `scripts/verify_opponent.py` — passes |
| Scenarios / What-If | Counterfactuals from legal states, engine solution | `scripts/verify_scenarios.py` — all checks pass |
| Coaching | Match preparation, progress comparison | `scripts/verify_coaching.py` — all checks pass |
| Live chess | Server-authoritative, clocks, sync, fair play | `scripts/verify_live.py` — all checks pass |
| Intelligence graph | Nodes/edges with evidence, bounded traversal, health | `scripts/verify_intelligence_graph.py` — all checks pass |
| Evaluation framework | 18 suites / 12 gates | `scripts/run_evaluation.py` — 165 passed, 0 blockers |
| API hardening | Auth, rate limit, resource gate, observability | pytest `test_security`, `test_phase15_*` |
| Frontend | 24 routes, keyboard boards, responsive, a11y | `next build`; Playwright 210 passed (Chromium + Firefox + WebKit) |
| Infrastructure | Docker, compose dev+prod, backup/restore, schema upgrades | `verify_backup_restore.py` — verified |

## Intentionally excluded (POST-RELEASE)

These are deliberate product boundaries, not defects. Each is documented in
`RELEASE_STATUS.md`.

- User accounts, sign-up/login, account lifecycle.
- Shared cross-instance state (rate-limit window, metrics, live WebSocket hub are
  per-process).
- Legal/privacy pages, a support channel, a data-export
  bundle.
- Distributed tracing.
- Alembic migrations (schema bootstraps with `create_all` + additive upgrades).
- Any production predictive ML model (none has passed its gate; the refusal is
  the shipped behaviour).

## Experimental / disabled features

- **Experimental:** the ML training/experiment pipeline. It runs and is guarded,
  but no model is production-approved (`system_check`: *0 registered, none
  production-approved*).
- **Disabled by configuration:** the LLM coach when `ARGUS_LLM_PROVIDER` is empty
  (it answers stored-fact questions and states it cannot write an explanation).
- **Fair-play lock:** competitive live games are forced to `no_analysis`; every
  engine-backed agent tool is unavailable during play.

## Change control

- No new major feature after this freeze.
- Every subsequent fix must name the issue it closes and be re-verified against
  the same suites above.
- The release candidate report (`RELEASE_STATUS.md`) is regenerated
  after any change that lands post-freeze.

## Caissa — Release Candidate Report

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)
**Commit:** `3c5dd53f5259bdc8cbe60e3c6663109edfd73c57` (working tree includes the
Phase 17 fixes; not yet committed)
**Tag:** none (created only after sign-off)
**Date:** 2026-10-04
**Environment:** repository `main`, local stack (`argus-chess-api:1.0.0`,
`argus-chess-web:1.0.0`, PostgreSQL 17, Redis 7)

No numeric score is given. Each area is a status with the evidence that produced
it. Statuses: **PASS**, **PASS WITH LIMITATIONS**, **FAIL**, **NOT RUN**.

## Scorecard

| Area | Status | Evidence |
| --- | --- | --- |
| Build from clean state | **PASS** | `.next` and caches removed; `next build` → 24 routes; `docker compose build api web` → `argus-chess-api:1.0.0`, `argus-chess-web:1.0.0` |
| Reproducibility | **PASS WITH LIMITATIONS** | documented prerequisites (Python ≥3.11, Node ≥20, Stockfish binary, Docker); no undocumented local dependency in code; a second machine was not exercised |
| Migrations / schema | **PASS WITH LIMITATIONS** | fresh `create_all` → 29 tables; additive upgrade applies and is idempotent (`tests/test_schema_upgrades.py`); **no Alembic** (documented) |
| Backup | **PASS** | `verify_backup_restore.py` — dump produced |
| Restore | **PASS** | 30 tables restored, every row count reproduced |
| Data integrity | **PASS WITH LIMITATIONS** | `check_data_consistency.py` — 0 serious; 2 versioned analyses + 2 self-healing caches (advisory) |
| Chess correctness | **PASS** | `test_chess_core`, `test_positions`, `test_analysis`, `test_engine`; real-game verifiers (Opera/Immortal/Anderssen) |
| Stockfish | **PASS WITH LIMITATIONS** | launches, analyzes, MultiPV, mate, cancellation, timeout, worker recovery; host vs container version differs (documented) |
| Game Intelligence | **PASS** | `verify_game_analysis.py`, `verify_intelligence.py`; invariants: every insight carries evidence, sources, certainty |
| Player Intelligence | **PASS** | `test_player_intelligence`, `test_player_api`; thin samples refused with a reason |
| ML | **PASS** | `verify_agent_ml.py`; 0 registered models, every task refused with reason; states respected |
| AI Agent | **PASS** | `verify_agent.py`; tool selection, evidence, refusal validator; adversarial set refuted |
| Hallucination tests | **PASS** | `test_agent_evaluation.py` (hallucinating provider refuted on every case); live `/ask` refused a fabricated premise |
| Prompt injection | **PASS** | `test_security.py` (payloads in headers/body); live `/ask` ignored an injection attempt and degraded honestly |
| RAG / knowledge base | **PASS** | `search_chess_knowledge`, `list_chess_concepts` return stored facts or "not stored"; never invented |
| Graph | **PASS** | `verify_intelligence_graph.py`; 0 dangling edges after the fix; snapshot nodes=150 edges=319 |
| Training | **PASS** | `verify_training.py`; exercises only where the analysis supports them |
| Opponent Intelligence | **PASS** | `verify_opponent.py`; preparation 500 fixed and re-verified |
| What-If | **PASS** | `verify_scenarios.py`; engine decides every evaluation; illegality is a refusal |
| Live Chess | **PASS** | `verify_live.py`; fair-play denies engine help mid-game |
| Frontend regression | **PASS** | `next build` (24 routes); Playwright **210 passed across Chromium, Firefox and WebKit** (routes, a11y, responsive, system, report, graph) |
| Mobile | **PASS WITH LIMITATIONS** | no overflow at 390/768/1280 px; no device-farm run |
| Accessibility | **PASS WITH LIMITATIONS** | automated axe on 17 pages + keyboard boards; **no manual screen-reader walkthrough** |
| Security | **PASS WITH LIMITATIONS** | prod `npm audit` 0; `pip-audit` 0; secrets clean; critical Next advisory patched; `docker scout`: web 0 high, api 0 critical / 3 base-OS high (no upstream fix — accepted risk, documented); no manual SBOM/signing |
| Privacy | **PASS WITH LIMITATIONS** | cross-caller isolation tested; **no accounts / no deletion UI** (documented) |
| Performance | **PASS WITH LIMITATIONS** | API/DB benchmarks + operation budgets recorded; **no field Web Vitals** |
| Observability | **PASS** | health/ready/metrics probes; request/correlation IDs; audit events |
| Alerts | **NOT RUN** | no external alerting target in this environment |
| Load testing | **PASS** | `load_live.py` — 6 games × 20 plies, 4 spectators; probe passed |
| Deployment dry run | **PASS WITH LIMITATIONS** | production compose manifest validates; images build; **no deploy to a real production host** |
| Rollback | **NOT RUN** | no production deployment to roll back; images are tagged for rollback |
| Disaster recovery | **NOT RUN** | restore procedure verified against the live DB; a full DR drill (host loss) not executed |
| Cost validation | **PASS WITH LIMITATIONS** | no paid infrastructure committed; optional LLM provider is the only variable cost; free-tier 429 degrades honestly |
| Documentation | **PASS** | README, CHANGELOG, `docs/**`, and `docs/release/**` audited; version references aligned to 1.0.0 |

## Changes made during verification (post-freeze)

Correctness fixes only, each re-verified:

1. Opponent preparation persistence (column width) — `models.py`, `schema.py`.
2. Intelligence-graph dangling edges — `graph_intelligence.py`.
3. Legacy coach shell tool-loop wire format + no-invention system prompt —
   `ai_agent/agent.py` (+ regression test).
4. Schema upgrade robustness for a missing core column — `db/schema.py` (+) `tests/test_schema_upgrades.py`.
5. `next` 16.3.5 → 16.3.8 (security patch).
6. Verifier defects (`verify_training.py`, `verify_coaching.py`).
7. Version alignment (1.0.0 across `VERSION`, API, core library, web, compose).

## Decision input

Every **executable** gate that was run passed. The items that are **NOT RUN** or
**PASS WITH LIMITATIONS** are the reason the sign-off is `HOLD` rather than
`RELEASE`: they are verification-completeness gaps (deployment to a real host,
rollback, DR drill, alerts) and scope gaps (accounts, legal/privacy pages,
manual accessibility certification), not observed defects. See
`RELEASE_STATUS.md` for the exact decision and `RELEASE_STATUS.md` for the
classification.

## Caissa — Stockfish Configuration Record

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)
**Date:** 2026-10-04

Stockfish is the authoritative evaluator. The LLM never computes chess facts;
every evaluation, best move and classification traces to an engine search whose
settings are recorded here and stored with each analysis.

## Engine

| Field | Value |
| --- | --- |
| Engine | Stockfish (UCI) |
| Container path | `/usr/games/stockfish` (distro package) |
| Version observed in the API container | `17.1` |
| Version observed on the developer host | `19` (`/opt/homebrew/bin/stockfish`) |

**Cross-version rule.** Do not compare raw centipawn scores across engine
versions. A different Stockfish build can evaluate the same position
differently; Caissa records the engine version that produced each analysis, and
classifications are self-consistent only within a like-engine corpus. This is
documented in `RELEASE_STATUS.md`.

## Search settings

Defaults from `argus.analysis.engine.stockfish` (`EngineSettings`):

| Setting | Default | Notes |
| --- | --- | --- |
| `threads` | `1` | `threads_max = 0` means the engine's own default is not overridden at request time |
| `hash_mb` | `256` | `setoption name Hash value 256` |
| `multipv` | `3` | clamped in code to the number of legal moves |
| Depth / time | per profile | see below |

Analysis profiles (`argus.analysis.pipeline`):

| Profile | Limit | MultiPV |
| --- | --- | --- |
| `FAST` | `movetime = 400 ms` | 2 |
| `STANDARD` | `depth = 16` | 3 |
| `DEEP` | `depth = 22` | 3 |

Explicit `depth` / `movetime_ms` / `multipv` passed by a caller win over the
profile defaults.

## Verification performed this phase

`scripts/verify_engine.py` (host engine) and the API container engine were both
exercised:

- engine launches and answers UCI;
- `analyze(fen)` returns a best move and a PV with scores (perspective-normalised
  to the mover);
- MultiPV returns ranked lines with a second-best gap;
- mate scores are parsed as mate, not as a large centipawn value;
- cancellation and timeout return cleanly rather than hanging;
- a crashed worker is recovered on the next request.

Perspective normalisation is covered by `tests/test_perspective.py`; move
classification by `tests/test_analysis.py` and `tests/test_game_analysis`; the
stored engine baseline by `tests/test_engine_baseline.py`.

## Caissa — Data Integrity Report

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)
**Date:** 2026-10-04
**Tool:** `scripts/check_data_consistency.py`
**Target:** the live development PostgreSQL instance (`argus`)

Nothing was modified by this audit. Every number below is the script's actual
output, not a summary judgement.

## Method

The audit walks the relationships the product depends on and reports each
finding with its severity. **Advisory** findings are differences the readers
already resolve at read time (for example a cached document that will be
rebuilt). A **serious** finding would be a broken reference the readers cannot
resolve — there are none.

```
Player  ↕  Game  ↕  Move  ↕  Position  ↕  Analysis  ↕  Insight
        ↕  Training  ↕  Prediction  ↕  Graph
```

## Scale of the audited instance

| Metric | Value |
| --- | --- |
| Games | 14 |
| Stored analysis rows | 345 |
| Analysis generations | 13 |

## Findings — advisory only

### 1. Two games carry more than one analysis generation

| Game id | Generations | Plies required | Coverage | Readers resolve to |
| --- | --- | --- | --- | --- |
| `189d51ba-…` | 3.0, 3.1 | 24 | 24 / 24 | 3.1 |
| `71b2e6c4-…` | 3.0, 3.1 | 73 | 73 / 73 | 3.1 |

This is **by design**. Analysis is versioned; when the analysis methodology
(`ANALYSIS_VERSION`) advances, a game may be re-analyzed and both generations are
kept. Every reader resolves to the newest *complete* generation, so no surface
mixes versions. Both generations here are complete at full ply coverage.

### 2. Two cached profiles are older than their source games

| Player id | Cached analyzed | Current analyzed | Document |
| --- | --- | --- | --- |
| 11 | 1 | 7 | player + opponent profile |
| 13 | 9 / 8 | 10 | player + opponent profile |

These are **stale cache entries, not stale reads**. The profile cache detects
staleness from a signature that includes the number of analyzed games
(`player_input_signature` → `analyzed:{n}`). The next read recomputes and
persists the profile, so no consumer ever sees the older numbers. The audit
reports the cache state, not the served state.

## What was checked and found clean

- **Orphan records / broken foreign keys** — none.
- **Missing positions for stored moves** — none; every stored move has its
  position.
- **Inconsistent FENs** — none.
- **Invalid training references** — none; every exercise resolves to a game/ply.
- **Broken graph relationships** — none; the graph verifier reports 0 dangling
  edges after the fix in this phase (each derived edge materialises its source
  game's structural graph first).
- **Stale predictions / invalid model references** — none; no model is
  production-approved, so predictions are refused with a reason rather than
  stored.

## Verdict

**No serious inconsistency.** The instance is internally consistent; the open
items are two intentionally versioned analyses and two caches that self-heal on
read. No silent repair was performed.

## Caissa — ML Model-State Matrix

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)
**Date:** 2026-10-04
**Source of truth:** `GET /api/predictions/models` and `GET /api/predictions/tasks`

## Registry state (measured)

```json
{"registry": {"registered": 0, "by_task": {}, "production_models": []},
 "models": [],
 "statuses": ["experimental", "validated", "production", "retired"]}
```

| Field | Value |
| --- | --- |
| Registered models | 0 |
| Production-approved models | 0 |
| Registry file on disk (`data/models`) | empty |
| Defined lifecycle states | experimental, validated, production, retired |

## Per-task matrix

| Task | Unit | Available | Registered | Status | Gate reason |
| --- | --- | --- | --- | --- | --- |
| `game_outcome` | one game | no | 0 | experimental | No model has been trained and validated for this task in this phase, so no prediction is served. |

Every predictive task returns `available: false` with a task-specific reason and
its data requirements (e.g. `game_outcome` requires ≥ 20 000 games, ≥ 14 000
train rows, ≥ 3 000 validation rows, ≥ 3 000 test rows, ≥ 500 players).

## Lifecycle enforcement verified

`scripts/verify_agent_ml.py` (this phase) confirms:

- the registry exposes all eight specification tools;
- only the four *working* tools are handed to an LLM — the rest are marked
  unavailable with an honest reason;
- an unavailable tool raises `ToolUnavailableError` rather than returning a
  fabricated value;
- an insufficient dataset is refused by the split/quality gate, not silently
  used;
- dataset splits enforce the spec minimums (112 / 24 / 24 observed for the
  bundled corpus).

## What was tested

| Case | Result |
| --- | --- |
| Model loading with an empty registry | No model loaded; endpoint refuses with reason |
| Inference request with no production model | Refused (`available: false` + reason) |
| Invalid model id | Refused, not a crash |
| Version mismatch | Gate refuses a mismatched model |
| Rollback | N/A — no model is deployed to roll back |
| Serving an experimental model | Impossible; only `production` models are served |

## Verdict

**No model is silently served.** The shipped behaviour is an explicit, reasoned
refusal for every predictive task. Producing a production model is
`POST-RELEASE`; the refusal is the release's ML contract, not a missing feature
hidden behind a fake number.

