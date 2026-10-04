# Caissa — Product quality and launch record

## Caissa — Final Product Quality Report

**Date:** 2026-10-04
**Scope:** Phase 16 — final product polish, UX, accessibility, scale and launch
readiness.
**Method:** every status below is backed by a command that was run or a test that
passed. Nothing is reported `READY` that was not measured. Statuses are
`READY` / `PARTIAL` / `BLOCKED` — no subjective scores.

## Evidence this session (all re-run after the changes below)

| Check | Command | Result |
| --- | --- | --- |
| Unit / integration suite | `python -m pytest -o addopts= -q` | **1584 passed, 1 skipped** |
| Release gate | `python scripts/run_evaluation.py --quiet` | **165 passed, 0 failed, 0 blockers** |
| Static correctness (Python) | `python -m ruff check packages apps scripts tests` | **All checks passed** |
| Type check (web) | `npx tsc --noEmit` | **clean** |
| Lint (web) | `npx eslint .` | **clean** |
| Production build (web) | `npx next build` | **succeeds, 24 routes** |
| Accessibility (axe, 17 pages) | `npx playwright test tests/e2e/accessibility.spec.ts` | **18 passed** |
| Responsive layout (15 pages × 3 viewports) | `npx playwright test tests/e2e/responsive.spec.ts` | **47 passed** |
| Five user journeys | `python scripts/verify_journeys.py` | **ALL JOURNEYS COMPLETED** |
| Live system check | `python scripts/system_check.py` | **ALL CHECKS PASSED** |
| Config validation | `python scripts/validate_config.py --environment development` | **VALID** |

## Category status

### Product UX — READY

- **Evidence:** the five journeys pass end to end (`verify_journeys.py`); the
  dashboard renders real counts (verified in a live browser snapshot — 10 games,
  8 analysed, 307 moves evaluated); every empty surface explains how to fill it.
- **Remaining:** no support channel or legal/privacy pages.

### Visual Consistency — READY

- **Evidence:** one design system (`docs/product/design-system.md`); pages compose
  shared primitives (`ui.tsx`) and CSS component classes; `tsc` and `eslint` clean;
  no page defines its own button/panel/card.
- **Remaining:** none identified.

### Accessibility — PARTIAL

- **Evidence:** axe (WCAG 2.1 A/AA) passes with no serious/critical violations on
  17 pages; keyboard Tab lands on a real control; reduced-motion honoured;
  non-colour signals on quality/eval/outcome/source. Interactive boards are now
  **keyboard-operable**: `board-keyboard.tsx` lays a 64-square overlay grid of
  named buttons (`"e2, white pawn"`) with one roving tab stop, arrow keys to move
  the cursor and Enter/Space to pick up and drop, while the library's own pieces
  leave the tab order and the accessibility tree. Verified live by playing a
  training move with the keyboard alone (graded "Correct").
- **Remaining:** no manual screen-reader walkthrough; the force-directed map's
  spatial layout has only its list equivalent. Stated in
  `docs/product/accessibility.md`.

### Responsive Design — READY

- **Evidence:** `responsive.spec.ts` renders 14 primary pages at 390 / 768 /
  1280 px and asserts the document is never wider than the viewport. It found two
  real overflows — Dashboard (+268 px) and Live (+65 px) at 390 px — both fixed
  (`min-w-0` on the shrinking grid items, and the three-up control grid stacked on
  mobile), and the whole suite is green.
- **Remaining:** none measured. Touch-target sizing is not separately asserted.

### Performance — PARTIAL

- **Evidence:** core operation latency measured and inside budget (worst p95
  0.176 ms); production build measured (1.9 MB total static, largest chunk 224 KB);
  `docs/release/performance-report.md`.
- **Remaining:** no field page-load / Web Vitals figure (the app is instrumented
  via `vitals.tsx`, but there is no real traffic yet); per-route gzip transfer
  size not captured. Listed as **UNKNOWN**, not guessed.

### Security — READY (single-instance) / BLOCKED (multi-tenant)

- **Evidence:** `tests/test_security.py`, `tests/test_phase15_privacy.py`; auth,
  authorization, rate limits, headers, upload validation, prompt-injection
  boundary; the audit log is now **actually emitted** (auth failures, import,
  delete, analysis start/cancel, live create, collection delete) and covered by
  `TestAuditLog`.
- **Remaining:** no user accounts, so multi-tenant isolation is not exercisable —
  the public-launch blocker.

### AI Coach — READY

- **Evidence:** live with Groq end to end; perspective and unit discipline in the
  prompt; the hallucination validator catches a fabricated evaluation written with
  a Unicode minus; provider artifacts (`【2†data】`) stripped; a provider outage
  degrades to an evidence-only answer that names the real reason.
- **Remaining:** none identified.

### Game Analysis — READY

- **Evidence:** Journey 1; the served report covers every ply, agrees with the
  game's own moves, and measures accuracy for both sides.

### Training — READY

- **Evidence:** Journey 3; exercises come only from the player's own analysed
  mistakes; the engine-verified best move grades as correct; attempts are stored.

### Opponent Intelligence — READY

- **Evidence:** Journey 4; observed history is never presented as guaranteed future
  behaviour; unavailable sections name their reason.

### Live Chess — READY

- **Evidence:** `scripts/verify_live.py`, `tests/test_live_api.py`; fair play is
  enforced in code (a competitive game is forced to `no_analysis`); create/play/
  reconnect/finish all pass.

### Production Infrastructure — READY (single-instance)

- **Evidence:** `system_check.py` passes 19/19 including Redis (the probe now
  authenticates) and Groq; backup/restore round-trip verified; config validation
  refuses a misconfigured production.
- **Remaining:** multi-instance needs a shared rate-limit/metrics store and Redis
  live fan-out (`docs/production/reliability-model.md`).

### Observability — READY

- **Evidence:** `/health`, `/ready`, `/metrics`, structured audit log; the audit
  log's call sites are tested.
- **Remaining:** per-process metrics; no distributed tracing (one service).

### Privacy — READY

- **Evidence:** cross-account privacy enforced in one policy; a stranger's read is
  a 404 (absent, not forbidden); `tests/test_phase15_privacy.py`.
- **Remaining:** no data-export bundle; no legal/privacy pages.

### Documentation — READY

- **Evidence:** `docs/product/` (information architecture, design system, user
  journeys, accessibility, analytics events, performance report, launch checklist,
  this report); `PRODUCTION_READINESS_REPORT.md`; per-subsystem docs under
  `docs/`.

## Changes made this session

**Dead code and technical debt (Phase 16 §44–§45)**

- Removed five unused Next.js boilerplate SVGs from `apps/web/public/`
  (`file`, `globe`, `next`, `vercel`, `window` — zero references in `src`).
- Confirmed **zero** `TODO`/`FIXME`/`HACK`/`TEMP`/`XXX` markers in production code
  (Python and web).
- Confirmed no dead components (every component in `src/components` is imported)
  and no duplicate routes (the `coach`/`coaching` split is two distinct jobs, not
  duplication).
- Fixed a stale docstring in `chess_core/models.py` that described opening
  detection as a placeholder (it is real).

**Real product-honesty bugs fixed**

- The coach's "Create a puzzle from this" action said *"Personalized training
  arrives in Phase 8"* — but training is built. It is now a live navigation to
  `/training?game=<id>`, offered only when a game is in context, and covered by
  two tests.
- Actions now fall back to the conversation's active game, so a deterministic
  answer still offers the actions that game supports.

**Wiring that was claimed but missing**

- The audit log (`audit()`) was defined but **never called**. It is now emitted
  from seven real code paths, with named constants and a test that pins them.
- Added `robots.ts`, `sitemap.ts`, and Open Graph / Twitter metadata. The sitemap
  lists only the public landing surface and is empty without a configured site
  URL — an honest "nothing public to advertise" rather than a guessed host.

**Found and fixed in the live UI audit (this session)**

- **Two real responsive overflows.** The new viewport test caught the Dashboard
  268 px wider than a 390 px phone and Live 65 px wider; both are fixed and the
  test now guards them.
- **Three undefined CSS classes.** `.select`, `.field` and `.link` were used
  (8 / 8 / 6 times) but defined in no stylesheet — the selects rendered as raw OS
  widgets and the links were unstyled. All three are now defined in
  `globals.css` to match the design system, in both themes.
- **Keyboard-operable boards.** Interactive boards gained the accessible overlay
  grid; the library's nameless, focusable pieces were removed from the tab order.
- **Generated reports gitignored.** `evaluation/reports/` is output, not source,
  and is now ignored so a stray `git add -A` cannot commit a machine-specific run.

## Known limitations

- **No user accounts** — identity is an API key; public multi-user launch needs a
  real account system.
- **No shared state across instances** — rate limiting, metrics and the live hub
  are per process.
- **No mobile field measurement** (Web Vitals is instrumented but there is no
  real traffic yet) and no per-route gzip transfer figure.
- **No manual screen-reader walkthrough.** Automated axe scans pass and the boards
  are keyboard-operable, but no human has driven every flow with a screen reader.
- **No legal/privacy pages or support channel.**

## Verdict

**FINAL PRODUCT STATUS: READY** for the deployment it was built for — a
single-instance, self-hosted, authenticated Caissa.
**PUBLIC MULTI-TENANT LAUNCH: NOT READY** — blocked by the four items above, each
of which is scoped and stated rather than hidden.

## Caissa — Public Launch Checklist

**How to read this:** each item is marked with what is *verified*, by what, and
what remains. `DONE` means a command or test proves it; `PARTIAL` means the
mechanism exists but is not fully exercised; `OPEN` means it is not done. No item
is `DONE` because it "looks fine".

> **Verdict: NOT READY for public multi-user launch.** Caissa is READY as a
> single-instance, self-hosted, authenticated deployment. The blockers to a public
> multi-tenant launch are stated at the end.

## Product

| Item | Status | Evidence / remaining |
| --- | --- | --- |
| Onboarding | DONE | The empty states are the onboarding: every empty surface explains how to fill it. No separate tour. `docs/product/user-journeys.md`. |
| Dashboard | DONE | `/dashboard` shows real library counts and newest games; empty state is a 3-step first-run path. Verified in the browser snapshot. |
| Analysis | DONE | Import → analyze → report exercised by `scripts/verify_journeys.py` (Journey 1). |
| Training | DONE | Generate → solve → graded attempt → progress (Journey 3). |
| AI Coach | DONE | Plan → tools → evidence → answer → validate (Journey 5), live with Groq. |
| Opponent preparation | DONE | Profile → repertoire → tendencies → preparation (Journey 4). |
| Live chess | DONE | Create → play → reconnect → finish (Journey D, `scripts/verify_live.py`). |

## UX

| Item | Status | Evidence / remaining |
| --- | --- | --- |
| Responsive | DONE | Automated viewport test at 390 / 768 / 1280 px over 15 pages asserts no horizontal overflow (`responsive.spec.ts`); it found and fixed a dashboard and a live-page overflow. |
| Accessible | PARTIAL | axe scans pass on 17 pages (`accessibility.spec.ts`); interactive boards are keyboard-operable (arrow keys + Enter, named squares). Manual screen-reader audit not completed. |
| Consistent | DONE | One design system (`docs/product/design-system.md`); `tsc` + `eslint` clean. |
| Error states | DONE | `error.tsx`, `not-found.tsx`, `ErrorState`, and per-surface refusals with reasons. |
| Loading states | DONE | `SkeletonRows`, progress badges; long tasks report queued/processing/completed/failed. |
| Empty states | DONE | `empty-state.tsx` used on 21 surfaces; each states what is missing and how to fix it. |

## Technical

| Item | Status | Evidence / remaining |
| --- | --- | --- |
| Production build | DONE | `npx next build` succeeds; 24 routes. |
| CI/CD | DONE | `.github/workflows/quality.yml`. |
| Monitoring | PARTIAL | `/health`, `/ready`, `/metrics`, structured audit log. Per-process; shared store is a multi-instance follow-up. |
| Backups | DONE | `scripts/verify_backup_restore.py` — real round-trip. |
| Rollback | DONE | Model rollback + additive migrations with a guarded drop script. |
| Security | PARTIAL | Auth, authorization, rate limits, headers, upload validation, prompt-injection boundary; `tests/test_security.py`. Multi-tenant isolation is the launch blocker below. |

## Data

| Item | Status | Evidence / remaining |
| --- | --- | --- |
| Privacy | DONE | Cross-account privacy enforced in one policy; `tests/test_phase15_privacy.py`. |
| Deletion | DONE | `DELETE /api/games/{id}` cascades and is audited. |
| Export | PARTIAL | PGN and report export exist; a full "export everything" bundle is not built. |
| Isolation | PARTIAL | Ownership column + policy are real; in an **open** deployment every caller is `local` (single-tenant). |

## Performance

| Item | Status | Evidence / remaining |
| --- | --- | --- |
| Load test | PARTIAL | `scripts/benchmark_api.py` / `load_live.py` are runnable; results depend on host. |
| Mobile performance | PARTIAL | Web Vitals instrumented (`src/components/vitals.tsx`); no real traffic yet, so no field figure. |
| API performance | DONE | Core operation latency measured and inside budget (`docs/release/performance-report.md`). |

## Launch

| Item | Status | Evidence / remaining |
| --- | --- | --- |
| Domain | OPEN | Operator sets `NEXT_PUBLIC_SITE_URL`; no domain is assumed by the code. |
| HTTPS | OPEN | Terminated by the operator's ingress; not configured in the repo. |
| Metadata | DONE | `layout.tsx` metadata, Open Graph, `robots.ts`, `sitemap.ts`. |
| Landing page | DONE | `/` is a public, indexable landing page; the workspace lives at `/dashboard`. |
| Support/contact | OPEN | None. |
| Legal/privacy pages | OPEN | None. Required before a public launch that stores user games. |

## Blockers to a public multi-user launch

1. **No user accounts.** Identity is an API key mapped to a caller id. There is no
   sign-up, login, password reset or account deletion. A public product needs a
   real account system (the ownership seam is ready for it — `security.py`).
2. **No shared state across instances.** Rate limiting, metrics and the live
   WebSocket hub are per process. A public launch behind a load balancer needs the
   shared store and Redis fan-out named in
   `docs/production/reliability-model.md`.
3. **No legal/privacy pages or data-export bundle.** Required for a public service
   that stores personal game data.
4. **No support channel.**

None of these is hidden; each is a real, scoped piece of work rather than a
mystery. Caissa is a complete, honest product for the single-instance deployment it
was built for.

## Caissa — Product Information Architecture

**Status:** current implementation. This document describes the navigation as it
is, not as it might become.

## Principle

Navigation is one entry per **job**, not per system. Caissa contains a dozen
backend subsystems (Game Intelligence, Player Intelligence, the intelligence
graph, training, live chess, scenarios, datasets). None of them is a top-level
menu item. A user opens Caissa to do one of a few things — play, analyse, get
coached, prepare, explore — and the navigation is organised by those verbs.

The grouping lives in one place: `apps/web/src/components/navigation.tsx`
(`GROUPS`). A new top-level surface is one entry there.

## Structure

```text
Caissa
├── Dashboard        /                one page: what is in the library, what to do next
├── Play             (group)
│   ├── Live         /live            a real game with a real clock and the coach
│   ├── Library      /games           every imported game and its analysis
│   ├── Import       /import          paste a PGN or read a platform account
│   └── Position Lab /lab             send any FEN to the engine
├── Coach            (group)
│   ├── AI Coach     /coach           conversation grounded in evidence
│   ├── Today        /coach?tab=today focus, feed and the game debrief
│   ├── Training     /training        exercises from your own mistakes
│   └── What-If Lab  /scenarios       compare moves and lines
├── Players          (group)
│   ├── Players      /players         Chess DNA and player intelligence
│   ├── Opponents    /opponents       repertoire, tendencies, preparation
│   └── Collections  /collections     curated sets worth returning to
└── Analytics        (group)
    ├── Explorer     /intelligence    follow the evidence graph
    ├── Progress     /progress        measured change between periods
    └── Search       /search          one query across everything stored
```

Deeper routes that are reached *from* a surface rather than from the nav:

| Route | Reached from |
| --- | --- |
| `/game/[id]` | Library row, dashboard card, a coach action |
| `/game/[id]/report` | Game page "report" tab, dashboard card |
| `/players/[id]` | Players list, a coach "View player pattern" action |
| `/live/[id]` | Live list |
| `/training/solve/[id]` | Training library, a coach "Practise this game" action |
| `/system` | Footer only (operator view, not a player surface) |

## Why `/system` is not in the main navigation

`/system` is an operational view: readiness of each dependency, queue depth,
migration count. It is useful to an operator and noise to a player, so it lives
in the footer. It renders status and counts only, never game content.

## Why "Today" is a tab, not a page

`/coach?tab=today` (focus, feed, debrief) is a different *view of coaching*, not a
different job, so it is a tab under Coach rather than a sixth top-level link.

## Cross-cutting entry points

- **Connect an account** (header, right) jumps to `/import?source=platform`.
- **Global search** (`/search`) is the one place a query spans games, players,
  openings, training and knowledge.
- **Empty states are the onboarding.** Every empty surface explains how to fill
  it; there is no separate tour that duplicates that guidance.

## What is deliberately absent

- No account/settings page. Caissa has no user accounts today (see
  `docs/production/security.md`); a settings page with no backend would be a dead
  control, which the quality bar forbids.
- No per-subsystem pages. The intelligence graph is the `/intelligence` Explorer,
  not four separate routes; the game report is a tab on the game page, not a
  sibling route.

## Caissa — Analytics & Event Model

**Status:** the events below are the ones the system actually records. Caissa has
no third-party analytics, no tracking pixels and no client-side behavioural
profiling. What it has is a **server-side audit log** of security- and
lifecycle-relevant actions.

## Principle

Caissa records *what happened to the data*, not *who the person is*. An event
carries a caller identity (the authenticated key's caller id, or `local` in an
open deployment), a request id, and the identifiers needed to audit the action.
It never carries game content, a coach conversation, or a credential.

The rule mirrors the product rule: an event that is claimed but not emitted is
worse than none, because it implies coverage that does not exist. Every event
below is emitted from a real code path.

## Where events go

`argus_api.observability.audit(event, **fields)` writes one JSON line to the
`argus_api.observability` logger, with the request id and caller id attached from
the request context. An operator routes that logger to tamper-resistant storage.
`ARGUS_LOG_FORMAT=json` (required in production) makes the line aggregatable.

## Events (named constants in `observability.py`)

| Event | Constant | Emitted from | Fields |
| --- | --- | --- | --- |
| `auth.failed` | `AUDIT_AUTH_FAILED` | `security.authenticate` | `reason` (`missing_key` / `invalid_key`) |
| `game.imported` | `AUDIT_GAME_IMPORTED` | `routes/games.py` import path | `game_id`, `source`, `plies` |
| `game.deleted` | `AUDIT_GAME_DELETED` | `DELETE /api/games/{id}` | `game_id` |
| `analysis.started` | `AUDIT_ANALYSIS_STARTED` | `POST /api/analysis/games/{id}` | `game_id`, `depth`, `resume` |
| `analysis.cancelled` | `AUDIT_ANALYSIS_CANCELLED` | `POST /api/analysis/games/{id}/cancel` | `game_id`, `cancel_requested` |
| `live_game.created` | `AUDIT_LIVE_GAME_CREATED` | `POST /api/live/games` | `live_game_id`, `mode`, `visibility`, `rated` |
| `collection.deleted` | `AUDIT_COLLECTION_DELETED` | `DELETE /api/coaching/collections/{id}` | `collection_id`, `player_id` |

Every row is covered by `tests/test_phase15_hardening.py::TestAuditLog`, which
asserts the auth events are written and a valid key is *not* logged as a failure.

## What is deliberately NOT collected

- **No coach conversation text.** An AI turn's *observability trace* (tool calls,
  timings, token budget) is returned to the caller and stored with the turn where
  the product needs it, but the conversation is not copied into the audit log.
- **No game content.** Events carry ids and counts, never moves or evaluations.
- **No credentials.** The caller is responsible for not putting a secret in
  `fields`; the auth path logs only the reason, never the presented key.
- **No client telemetry by default.** The only client-side signal is the optional
  Web Vitals beacon (`vitals.tsx`): it is **off unless** the operator sets
  `NEXT_PUBLIC_VITALS_ENDPOINT`, and it sends a single anonymous performance
  metric (CLS/INP/LCP/FCP/TTFB plus the path) — no events, no identifiers. With the
  variable unset, nothing leaves the browser.

## Metrics (separate from the audit log)

Operational metrics are a different concern and live in the same module:
`METRICS` is a dependency-free Prometheus registry labelled by route template,
served at `/metrics`. Metrics are counts and histograms, not events, and carry no
caller identity. Like the rate limiter, they are per process; a multi-instance
deployment needs a shared store (stated in `docs/production/reliability-model.md`).

## If product analytics is added later

The honest place to add it is a small, documented event emitter with an explicit
allow-list of fields and a consent flag — never a third-party script that ships
user data off-host by default. That is a deliberate future decision, not a gap in
today's system: today Caissa collects nothing it cannot justify.

## Caissa — Performance Report

**Method:** every number here was measured on this machine or by the evaluation
framework. Nothing is estimated. Where a figure was not measured it is listed as
**UNKNOWN**, not guessed.

**Environment:** Apple Silicon (arm64 macOS), CPython 3.14, Stockfish 19, Next.js
production build, PostgreSQL 17 in Docker. Measurements are single-run unless a
sample size is stated.

## Backend — core operation latency

From `python scripts/run_evaluation.py` (`performance` suite), budgets in
parentheses, sample size per operation:

| Operation | p50 | p95 | Budget | Status |
| --- | --- | --- | --- | --- |
| `fingerprint` | 0.035 ms | 0.043 ms | 5.0 ms | MEASURED — pass |
| `validate_fen` | 0.019 ms | 0.024 ms | 5.0 ms | MEASURED — pass |
| `classify` (move) | 0.130 ms | 0.176 ms | 10.0 ms | MEASURED — pass |
| `exhibited_concepts` | 0.034 ms | 0.040 ms | 25.0 ms | MEASURED — pass |
| `graph_traversal` | 0.030 ms | 0.037 ms | 15.0 ms | MEASURED — pass |

Worst p95 across all core operations: **0.176 ms**. All well inside budget.

## Frontend — production build

From `npx next build` in `apps/web` (Next.js 16):

| Figure | Value | Status |
| --- | --- | --- |
| Compile time | 4.8 s | MEASURED |
| Type-check time | 2.9 s | MEASURED |
| Static page generation (24 routes) | 242 ms | MEASURED |
| Total static asset payload (`.next/static`) | 1.9 MB | MEASURED |
| Largest single JS chunk | 224 KB | MEASURED |
| Routes prerendered static | 19 of 24 | MEASURED |
| Routes server-rendered on demand | 5 of 24 (the `[id]` pages) | MEASURED |

The 1.9 MB total is every asset for every route, not one page's download; a
single page loads a subset. The largest chunk is the shared React/Next runtime.

## Interactive performance

| Figure | Value | Status |
| --- | --- | --- |
| Accessibility + page scan (17 pages, 3 workers) | 7.7 s | MEASURED |
| Single page axe scan | ~1.0–1.8 s | MEASURED |
| Lab Web Vitals (LCP) | 68–212 ms across 8 routes | MEASURED (lab) |
| Lab Web Vitals (CLS) | 0.000–0.068 across 8 routes | MEASURED (lab) |
| Lab Web Vitals (TTFB) | 7–29 ms | MEASURED (lab) |
| Per-route gzip document transfer | 4.8–6.5 KB | MEASURED (lab) |
| Field Web Vitals (CLS / INP / LCP / TTFB) | — | **UNKNOWN** (instrumented; no real traffic yet) |
| Board render on a phone viewport | — | **UNKNOWN** (not instrumented) |

### Lab Web Vitals (measured, not field)

`ARGUS_WEB_URL=http://localhost:3100 node scripts/measure-lab.mjs` — a cold load of
each route against the local production build, Chromium, 1280×900. This is **lab**
data: it is one load on one machine, not real-user monitoring. It is recorded
because a field figure needs traffic the deployment does not have, and a measured
lab number labelled as lab is more useful than a permanent `UNKNOWN`.

| Route | TTFB | FCP | LCP | CLS | load | gzip doc |
| --- | --- | --- | --- | --- | --- | --- |
| `/` | 29 ms | 212 ms | 212 ms | 0.046 | 210 ms | 6.5 KB |
| `/dashboard` | 7 ms | 68 ms | 68 ms | 0.007 | 81 ms | 4.9 KB |
| `/games` | 9 ms | 76 ms | 76 ms | 0.024 | 86 ms | 4.8 KB |
| `/coach` | 12 ms | 84 ms | 84 ms | 0.007 | 81 ms | 4.9 KB |
| `/training` | 7 ms | 68 ms | 68 ms | 0.068 | 68 ms | 4.8 KB |
| `/intelligence` | 25 ms | 92 ms | 92 ms | 0.000 | 89 ms | 4.9 KB |
| `/players` | 7 ms | 72 ms | 72 ms | 0.013 | 70 ms | 4.8 KB |
| `/system` | 8 ms | 72 ms | 72 ms | 0.048 | 70 ms | 5.0 KB |

Every route is inside the "Good" thresholds (LCP < 2.5 s, CLS < 0.1).

## Load and scale

From `scripts/benchmark_api.py`, `scripts/benchmark_db.py`, `scripts/load_live.py`
(run against the live stack):

| Test | What it measures | Status |
| --- | --- | --- |
| `benchmark_api.py` | API latency under a mixed request set | Runnable; see its own output |
| `benchmark_db.py` | Query latency with real `EXPLAIN` plans | Runnable; see its own output |
| `load_live.py` | Live-game move throughput and WebSocket fan-out | Runnable; see its own output |

These are **runnable measurements, not stored results**: the numbers depend on the
host, so the report points at the command rather than freezing a figure that would
be wrong on another machine. The Phase 15 report
(`docs/production/load-and-chaos.md`) documents the methodology.

## What is honest here

- **TARGET vs MEASURED is kept separate.** The only *targets* in Caissa are the
  latency budgets in the evaluation suite, and each is marked pass/fail by a
  measurement, not by an assertion that it is fast.
- **No page-load figure is claimed** because none was captured from real
  traffic. The app is now *instrumented* for Web Vitals (`src/components/vitals.tsx`
  via `next/web-vitals`): it logs CLS/INP/LCP/FCP/TTFB in development and, only
  when `NEXT_PUBLIC_VITALS_ENDPOINT` is configured, beacons them to that endpoint.
  With no endpoint and no real users there is still **no field data**, and none is
  invented — instrumentation is not a measurement until traffic exists.
- **Per-process caveat.** The rate limiter and metrics registry are per process
  (Phase 15); performance under a multi-worker deployment is not measured here.

