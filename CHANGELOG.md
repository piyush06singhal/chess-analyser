# Changelog

All notable changes to Caissa are recorded here. The project is developed in
phases; each entry states what was built and links to its documentation. The rule
for every entry is the same as the rule for the product: nothing is listed as done
that was not measured, and a capability that is not built is listed as not built.

The format follows [Keep a Changelog](https://keepachangelog.com/); the project
uses phase-based versioning rather than SemVer.

## [Phase 17 / 1.0.0] — Final release candidate, verification & production sign-off

### Documentation
- **The README was rewritten as an overview rather than a manual.** It was 367
  lines of prose that restated the design documents, with no visual structure and
a stale test count (1584, when the suite was at 1599). It is now 231 lines built
around aligned tables — capabilities as a single glance table, the layered
architecture as a stage/responsibility table, the measured results as a
verification table with the command behind each number, and the deferred work as
an area/today/trigger table instead of twelve paragraphs. Real badges were added
(CI, release, Python, Node, MIT) and the CI badge links to the workflow that now
actually passes.

### Continuous integration (the first real GitHub Actions run)

The workflow had only ever been read, not run. Its first run on GitHub found six
defects that never appeared locally, and one product bug that only appears under
load. All are fixed.

- **`frontend`: type-check failed on a fresh checkout.** `LayoutProps` is
generated into `.next/types`, which is gitignored, and CI type-checked before it
built. `npx next typegen` now runs first, so type-check no longer depends on
build order (reproduced locally: the same error and exit code as CI, then clean).
- **`security`: two independent failures, both found only by running it.**
  - *It died during setup, before scanning anything.*
    `aquasecurity/trivy-action@v0.28.0` references
    `aquasecurity/setup-trivy@v0.2.1`, a tag that was never published, so the
    action could not resolve. Pinned to `v0.36.0`, which pins that dependency by
    commit SHA instead. The Trivy DB is still downloaded at run time, so this
    pins the wrapper, not the vulnerability data.
  - *Then it gated the npm audit on the wrong tree.* It failed on an advisory in
    the ESLint dev toolchain — `braces`, reached only through `eslint-config-next`,
    which is not in the runtime image — and it has **no patch to apply**:
    GHSA-vfj7-8cjw-p6xm lists affected versions `<= 3.0.3` and patched versions
    *none*, and 3.0.3 is the newest release on the registry. The gate now matches
    the policy `scripts/audit_dependencies.py` already documented — audit the
    **shipped** tree (`npm audit --omit=dev`: 0 vulnerabilities) — while a separate
    non-blocking step keeps the full tree visible, so an unpatchable dev finding
    stays in the record and a patched one is obvious as soon as a fix ships. This
    is the same treatment Trivy's `ignore-unfixed` already gives the containers.
- **`backend`: 45+ failures from a missing engine.** The suite is engine-free *by
policy* — `run_evaluation.py --no-engine` and `pytest -m "not engine"` — but
several tests that are not engine-marked drive a real analysis run (import →
analyse → read the report) and failed with
`Stockfish binary not found`. The job now installs Stockfish; apt's
`/usr/games/stockfish` was already in `locate_stockfish`'s search list, so no
configuration is needed.
- **`browser`: two defects at once.** CI installed only Chromium while
`playwright.config.ts` runs three engines, which would have taken two thirds of
the matrix with it; it now installs all three. And the map spec required stored
graph relationships that CI never created.
- **The graph is materialized in CI.** Journey 1 imports and analyses a real game,
but the intelligence graph is materialized *from* the domain rather than derived
on read, so the browser job rebuilds it (`POST /api/graph/rebuild`) first. The
journeys also now run **before** the browser tests, so data-dependent specs (the
map's neighbourhood, a finished game's report) assert against real relationships
instead of skipping for want of a fixture. Verified locally: a rebuild after one
imported game gives its players a 40-node neighbourhood.
- **`intelligence-map.spec.ts` no longer depends on a populated library.** It
probes the API for a node with a real neighbourhood and deep-links to that node —
a stronger test of the deep-link claim than relying on the picker's default — and
skips with a reason when the library is genuinely empty, which is the rule the
report suite already followed: a missing fixture is not a frontend defect.

### Fixed — a URL-backed control could revert itself
- **The Intelligence Explorer's depth control could snap back to its old value.**
`setParams` rebuilt the query string from the `searchParams` captured when its
callback was created. The map's auto-select resolves asynchronously and issues a
write from the render *before* the user's click, so its stale write could land
last and revert the control the user had just used. Found by the cross-browser
suite under load (`depth=3` reverting to `depth=1`), and reachable by any user who
clicks a control quickly after the page loads. Writes now build from the live
query string, a write that changes nothing is dropped, and the map's
`onSelect`/`onDepthChange` are stable identities so its node-list effect no longer
refetches on every URL change. Same fix applied to the coach tabs, which shared
the pattern. Verified: 48/48 on the map spec (8 repeats × 3 engines) and the full
210-test matrix green.

### Hardening (post-RC audit)
- **Every page now has its own title and one top-level heading.** Client routes
  (`/games`, `/coach`, `/training`, `/live`, …) fell back to the root title; they
  have route-level metadata now. An assertion that each page has exactly one `h1`
  was added after cross-browser testing found the report page shipped none.
- **Opponents, What-If Lab and Position Lab** were moved onto the shared page
  shell; `font-mono` was unified to the project's `.mono` class (which also adds
  tabular numerals), and a WCAG 2.5.8 minimum touch-target assertion was added.
- **Cross-browser testing.** The whole e2e suite now runs on **Chromium, Firefox
  and WebKit** (210 passed, was 69 on Chromium only). This found the missing-`h1`
  bug above and confirmed the landing contrast finding was a mid-animation frame,
  not a defect (the suite now runs with reduced motion so it measures the settled
  DOM). A hydration-race flake in the intelligence-map spec (a click delivered
  before React attached its handler) was fixed with Playwright's `toPass` retry;
  the full 210-test matrix then ran green four times in a row.
- **Dependency/container audit is now reproducible.** `scripts/audit_dependencies.py`
  runs `pip-audit`, `npm audit` and `docker scout`, reporting a skipped tool as
  skipped rather than clean. First real results: Python **0 known
  vulnerabilities**; the web runtime image carried **11 HIGH** npm advisories from
  the base image's bundled `npm`, which was removed — the web image now scans at
  **0 critical / 0 high**. The api image went from 4 HIGH to **3 HIGH / 0
  critical**, all base-OS packages with no upstream fix (recorded as accepted
  risk).
- **Lab Web Vitals + per-route gzip size measured** (`scripts/measure-lab.mjs`):
  LCP 68–212 ms, CLS 0.000–0.068, 4.8–6.5 KB gzip document per route across eight
  routes. Labelled as lab, not field.
- **Deferred work register** (`docs/release/DEFERRED_WORK.md`) records why the ML
  model, Alembic, tracing, accounts and multi-instance scaling are postponed —
  including that shipping an ungated model would violate the no-fabrication rule.

### Added
- A public **landing page** at `/`; the workspace dashboard moved to `/dashboard`.
  The nav, footer, sitemap and robots rules were updated for the split, and the
  landing shows a strip of real, live numbers (nothing rendered from a placeholder).
- `docs/release/` — feature freeze, release scope, known limitations, blocker
  classification, engine configuration, data-integrity report, ML model-state
  matrix, security report, performance & load report, release notes, the release
  candidate scorecard and the sign-off.
- `VERSION` (`1.0.0`), now the single release version reported by
  `apps/api/argus_api.__version__`, the core library, the web app and the Docker
  image tags/labels.
- `tests/test_schema_upgrades.py` — pins the additive schema-upgrade path and its
  idempotency.
- A regression test that the legacy coach shell emits the assistant `tool_calls`
  message before its results.

### Fixed
- **Opponent preparation returned HTTP 500** — `training_positions.data_source`
  was too narrow for `opponent_preparation`. Widened on fresh and existing
  databases; re-verified live (HTTP 200).
- **Intelligence graph dangling edges** — derived edges referenced game/position
  nodes never materialised; the updater now materialises each source game's
  structural graph first. Re-verified: 0 dangling edges.
- **Legacy coach shell broke against a real provider** — the tool loop never sent
  the assistant `tool_calls` message (Groq rejected it, the endpoint leaked a raw
  502). Fixed, and the shell now carries the same no-invention system prompt, so it
  refuses a fabricated premise instead of agreeing with it.
- **Schema upgrade could crash on an old database** — the unique index over
  `(source, source_game_id)` assumed `source` existed; the upgrade now adds it
  first.
- **Critical dependency advisory** — `next` upgraded 16.3.5 → 16.3.8
  (GHSA-vcvr-r3jv-pc5j; no `next/og` path was used, so it was not exploitable, but
  it is resolved for the release). `npm audit --omit=dev`: 0 vulnerabilities.
- **`forced_exchange` mislabelled quiet moves and pawn trades** — the detector
  fired whenever the opponent's next move landed on the same square, so a quiet
  push that was simply captured ("3.e4 dxe4") was reported as a *recapture*, and a
  routine pawn trade was surfaced as a tactic. It now requires a real capture of a
  minor piece or better, recaptured on the same square; `REPORT_VERSION` → `4.2`.
  This is the last analysis-quality defect from the honest sweep.
- **An accessibility console warning on the live board was removed.** Clicking
  the visual board focused the library's decorative piece node, and hiding that
  node's subtree from assistive technology while it held focus is blocked by the
  browser (it logged "Blocked aria-hidden on an element because its descendant
  retained focus"). Focus is now dropped the moment it lands on a decorative
  board node, so the board is silent in the console and the overlay beside it
  still owns focus.
- **The coach now retries a throttled provider before giving up.** A free-tier
  `429` degraded the whole turn to an evidence-only answer on the *first*
  attempt. Provider calls now retry briefly with exponential backoff (3 attempts,
  5s cap, honouring a short `Retry-After`), so a transient throttle resolves into
  a real answer instead of a refusal. Only `429` is retried — a bad key or a bad
  request still fails immediately — and a `Retry-After` asking for a longer wait
  surfaces the `429` at once rather than stalling the request. A persistent limit
  still falls through to the honest evidence-only answer naming the real reason.
- **Stored reports now self-heal across a version change** — a persisted report
  was served as-is even after the intelligence semantics moved, so an upgraded
  detector could be masked by a stale snapshot. A report is now reused only while
  its `report_version` matches the running code (or its signature for a player
  profile matches); otherwise it is rebuilt from the stored analysis on read. If
  the analysis behind a stale report is gone, the existing snapshot is served
  rather than a spurious `409`.
- Two verifier defects: a stale expected methodology version and a wrong submitted
  solution in `verify_training.py`; a `verify_coaching.py` check that treated a
  legitimate refusal as a failure.
- UI over-explanation trimmed on the coach, game analysis, lab, games and
  opponents pages.

### Changed
- The product's display name is now **Caissa** (was “ARGUS Chess”), across the UI,
  metadata, docs, README, release notes and image labels. Internal identifiers are
  deliberately unchanged (`argus` package, `ARGUS_*` environment variables, image
  names), so nothing downstream breaks.
- UI copy tightened on the training, opponents, coach and dashboard pages — the
  same honesty promise stated once instead of repeated.

### Notes
- No new major feature entered after the freeze. The final decision is **HOLD**
  pending four unexecuted gates (deploy, rollback, DR drill, alerts) and the
  operator's scope confirmation — see `docs/release/RELEASE_STATUS.md`.

### Documentation consolidation
- **The documentation set was consolidated from 74 files to 40** so it reads as
  sections rather than a pile. The `intelligence-graph/`, `evaluation/`, `release/`
  and `product/` clusters were each merged into a single canonical file that keeps
  every section (nothing was deleted in the merge), the deferred ML/data design
  moved into `docs/ml-and-data.md`, and a task-oriented `docs/README.md` index was
  added — it did not exist before. The historical `evaluation/audit.md` (stale
  counts) and the duplicate performance reports were removed. Every inbound link
  was rewritten; a scan of all Markdown links reports zero broken.
- **A guided first run** (`getting-started.tsx`) now appears on the dashboard for
  the "imported but not yet analysed" state — the state that previously gave a new
  user no next step — and removes itself once a game is analysed.
- **A contradictory dashboard hint was corrected.** The "Awaiting analysis"
  tile showed a non-zero count beside "queue is clear"; it now reads "ready to
  analyse" while work is pending and "all analysed" only when the count is zero.
- **Every page header is now consistent.** The small caption above each title
  (the "eyebrow") is aligned to the navigation taxonomy — Play / Coach / Players /
  Analytics / Operations / Workspace — instead of one-off labels like "Chess
  intelligence workspace" or "Stockfish, directly". `/import`, `/training` and
  `/live` also shipped no `h1` in the server-rendered HTML because their
  `useSearchParams` Suspense fallbacks were a bare spinner; each fallback now
  carries the page header, so the title is in the first paint.
- **Methodology prose moved behind disclosures.** The remaining always-visible
  "how this is built" blocks on the player profile (profile notes, Chess DNA
  definitions) now sit one click away; the numbers lead, the reasoning stays
  available. The report page and the rest of the product already worked this way.

## [Phase 16] — Final product polish, UX, accessibility, scale & launch

### Added
- `docs/product/` — information architecture, design system, user journeys,
  accessibility, analytics events, performance report, public launch checklist and
  the final product quality report.
- `robots.ts` and `sitemap.ts`; Open Graph / Twitter metadata on the root layout.
- A keyboard layer for the interactive boards (`board-keyboard.tsx`): named
  squares, one roving tab stop, arrow keys and Enter to move without a pointer.
- An automated responsive/viewport suite (`responsive.spec.ts`).
- Web Vitals instrumentation (`vitals.tsx`, `next/web-vitals`); silent unless
  `NEXT_PUBLIC_VITALS_ENDPOINT` is configured.
- Server-side audit events with named constants (`auth.failed`, `game.imported`,
  `game.deleted`, `analysis.started`, `analysis.cancelled`, `live_game.created`,
  `collection.deleted`), each emitted from a real code path and covered by tests.

### Fixed
- The coach's training action claimed *"Personalized training arrives in Phase 8"*
  while training was already built; it is now a live link to `/training?game=<id>`.
- Coach actions now fall back to the conversation's active game, so a deterministic
  answer still offers that game's actions.
- A stale docstring described opening detection as a placeholder (it is real).
- `.select`, `.field` and `.link` were used in the UI but defined in no
  stylesheet, so selects rendered as raw OS widgets; all three are now real
  design-system classes in both themes.
- A Dashboard overflow (+268 px) and a Live overflow (+65 px) at phone width, both
  caught by the new viewport suite and fixed.

### Removed
- Five unused Next.js boilerplate SVGs from `apps/web/public/`.

### Changed
- `evaluation/reports/` is now gitignored: the reports are generated output, not
  source.

## [Phase 15] — Production hardening, deployment, reliability & launch readiness

### Added
- Environment separation (`development`/`test`/`staging`/`production`) with a
  validator that refuses a misconfigured production.
- Containerization with pinned, non-root images; a hardened Redis (password,
  bounded memory, dangerous commands renamed).
- Database migrations (additive, idempotent), a guarded legacy-table drop, and a
  verified backup/restore round-trip.
- Engine resource gate, per-caller rate limits, feature flags, health/readiness
  probes, Prometheus `/metrics`, structured audit log.
- API-key authentication and per-caller ownership enforced in one policy.

### Fixed
- The Redis health probe never sent `AUTH`, so a password-protected Redis always
  reported down. It now authenticates from the URL.
- Groq is a first-class provider; the agent sends a bounded tool list (Groq's
  on-demand tier caps a request at 8000 tokens), the tool-call wire format is
  correct, and a provider outage degrades honestly.

See `PRODUCTION_READINESS_REPORT.md` and `docs/production/`.

## [Phases 1–14]

Phase-by-phase feature descriptions and known limitations are documented in
`README.md`. In brief:

| Phase | Built |
| --- | --- |
| 1–3 | Ingestion, parsing, Stockfish analysis, deterministic reports |
| 4 | Game Intelligence (phases, openings, material, tactics, turning points, accuracy) |
| 5 | Player Intelligence / Chess DNA |
| 6 | Dataset engineering & statistical foundation |
| 7 | AI Chess Agent (plan → tools → evidence → answer → validate) |
| 8 | Personalized training engine |
| 9 | Opponent intelligence & advanced analytics |
| 10 | Decision intelligence (scenarios, what-if) |
| 11 | AI coach, match preparation & intelligence workspace |
| 12 | Live chess, real-time analysis & in-game coach |
| 13 | Intelligence graph + chess knowledge system |
| 14 | Evaluation, benchmarking & quality assurance framework |
