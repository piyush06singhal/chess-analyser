# Caissa 1.0.0 — Release Notes

**Release candidate:** `v1.0.0-rc.1`
**Date:** 2026-10-04

## What this release is

A **self-hosted, single-instance** chess intelligence platform: engine-grade game
analysis, game/player/opponent intelligence, personalized training, an
evidence-gated AI coach, What-If analysis, server-authoritative live chess, and an
evidence-driven intelligence graph — run by one operator for one or a few trusted
users behind an API-key boundary.

The product rule that governs every surface is enforced in code: *a capability
that is not built is reported as not built; a number that was not measured is not
shown; a claim without evidence is refused.*

## What is new in 1.0.0

- **The product is now called Caissa** (it was “ARGUS Chess” during development).
  The name change is display-only: the `argus` package, `ARGUS_*` environment
  variables and image names are unchanged, so existing deployments keep working.
- **A real landing page.** `/` is now a public, indexable landing page; the
  workspace dashboard lives at `/dashboard`. The landing shows only measured,
  live numbers — a stat strip that renders nothing rather than a placeholder when
  there is no data.

## Highlights

- **Engine-truthful analysis.** Stockfish is the only source of an evaluation;
  the LLM never computes a chess fact. Accuracy is Caissa-defined and says so.
- **Explainable reports.** Game intelligence surfaces carry their evidence and
  certainty; unavailable sections name the gap instead of smoothing it.
- **Training from your own mistakes**, with spaced repetition and an honest review
  queue — an exercise is produced only where the analysis supports it.
- **A coach that refuses to guess.** 63 declared tools; answers rest on an
  evidence packet and pass a hallucination validator; with no provider configured
  it still answers stored-fact questions and says it cannot write an explanation.
- **Live chess** that is server-authoritative, with fair-play enforcement that
  denies engine help mid-game.
- **An evaluation framework** that blocks a release on failures that must never
  pass.

## Fixed in the release candidate (Phase 17 verification)

- **Opponent preparation endpoint returned HTTP 500** — the `data_source` column
  was too narrow for `opponent_preparation` (20 chars). Widened on fresh schemas
  and on existing databases (additive upgrade). Re-verified live: HTTP 200.
- **Intelligence graph dangling edges** — derived edges referenced game/position
  nodes that were never materialised. The updater now materialises each source
  game's structural graph first. Re-verified: 0 dangling edges.
- **Legacy coach shell broke against a real provider** — the tool loop never sent
  the assistant `tool_calls` message, so Groq rejected it ("Tools should have a
  name!") and the endpoint leaked a raw 502. Fixed; the shell now also carries the
  same no-invention system prompt as the main agent, so it refuses a fabricated
  premise ("assume my blunder rate is 42%") instead of agreeing.
- **Schema upgrade could crash on an old database** — the unique index over
  `(source, source_game_id)` assumed `source` existed; the upgrade now adds it
  first. Pinned by `tests/test_schema_upgrades.py`.
- **Critical dependency advisory** — `next` was upgraded from 16.3.5 to 16.3.8
  (GHSA-vcvr-r3jv-pc5j, RCE in `next/og`). Not exploitable here (no `next/og`
  path) but resolved for the release. `npm audit --omit=dev`: 0 vulnerabilities.
- Two verifier defects corrected (a stale expected methodology version, a wrong
  solution submitted; a coaching check that treated a legitimate "no weakness
  crossed the threshold" refusal as a failure).
- UI over-explanation trimmed on the coach, game, lab, games and opponents pages.

## Verification summary

| Gate | Result |
| --- | --- |
| Full test suite | 1584 passed, 1 skipped |
| Ruff | clean |
| Release evaluation gate (18 suites, 12 gates) | 165 passed, 0 failed, 0 blockers |
| System check | 19/19 |
| Config validation (development) | VALID |
| Journey verification | all 5 completed |
| Frontend e2e (a11y + responsive + system + report + graph) | 210 passed (3 engines) |
| `next build` | 24 routes |
| Backup/restore drill | verified — 30 tables, all row counts reproduced |
| Data integrity audit | no serious inconsistency |

## Getting started

See `README.md` (Quickstart) and `docs/deployment.md`. Validate configuration
before starting in production:
`ARGUS_ENV=production python scripts/validate_config.py --environment production`.

## Known limitations

See `docs/release/RELEASE_STATUS.md` and `docs/release/RELEASE_STATUS.md`.
The most consequential are the absence of user accounts, per-process shared state,
and legal/privacy pages — deliberate boundaries for a 1.0 self-hosted release.

## Compatibility

Python ≥ 3.11 (tested on 3.14); Node ≥ 20; PostgreSQL 17 (production) or SQLite
(dev/tests); Redis 7 optional; Stockfish is an external binary whose version is
recorded per analysis.
