# Caissa — Deferred Work Register

**Release:** 1.0.0
**Date:** 2026-10-04
**Purpose:** record the work that is deliberately **not** done for this release,
why it is deferred rather than rushed, and what "done" would mean — so a future
contributor inherits a reason, not a mystery.

The rule this document follows is the product's own: a capability that is not
built is reported as not built. Nothing here is a promise that the work is easy;
several items are genuine projects.

---

## 1. Production ML model / predictions — **deferred, deliberately**

**State:** the ML registry holds **0 registered models and none production-approved**.
Every prediction surface therefore reports itself unavailable and refuses to give a
win probability, with that reason.

**Why not now:** shipping a model that has not passed its production gate would
directly violate the product's central promise — "a number that was not measured is
not shown". A rushed model is *worse* than the honest refusal, because it would be
the one place in the product where an unverified number appears. There is also no
real work to skip: training, evaluation, gating and registration are each required
before a single prediction can be shown.

**What "done" means:**
1. a training run producing a candidate over a dataset with a documented
   train/validation split;
2. evaluation that clears the pre-registered gates in
   `docs/ml-and-data.md` / `docs/ml-and-data.md`;
3. registration with the gate result, and a rollback path (already built);
4. only then does the prediction endpoint return a number.

**Owner:** ML workstream. **Not a release blocker** for single-instance self-host.

---

## 2. Alembic migration tooling — **deferred**

**State:** the schema bootstraps with `create_all` plus additive, idempotent,
hand-written upgrades; both a clean bootstrap and an upgrade path are tested
(`tests/test_schema_upgrades.py`).

**Why not now:** for a single-instance deployment the current path works and is
tested. Introducing Alembic is a real refactor of how every future schema change is
expressed, with its own failure modes (autogenerate drift, down-migrations), for
near-zero user-facing gain.

**What "done" means:** an Alembic baseline matching the current schema, all existing
upgrades expressed as revisions, and the bootstrap/upgrade tests re-pointed at
`alembic upgrade head`.

**Trigger to revisit:** committing to long-lived multi-instance operations, or the
first destructive (non-additive) schema change.

---

## 3. Distributed tracing — **deferred**

**State:** request and correlation IDs are present; `/health`, `/ready`, `/metrics`
and a structured audit log exist.

**Why not now:** Caissa is a single service. There is no cross-service span to
correlate, so a tracing backend would add an operational dependency without
increasing insight.

**Trigger to revisit:** the architecture becomes more than one service.

---

## 4. User accounts & multi-tenant isolation — **deferred (public-launch blocker)**

**State:** ownership column + a single authorization policy are real and tested;
in an *open* deployment every caller maps to `local`, so isolation is not
exercisable.

**Why not now:** it is out of scope for the supported single-operator topology and
is genuinely large (sign-up, login, password reset, account deletion, session
management, migration of existing data to owners).

**Trigger:** any plan to serve untrusted public users.

---

## 5. Multi-instance horizontal scaling — **deferred (public-launch blocker)**

**State:** the rate-limit window, metrics counters and the live WebSocket hub are
**per process**.

**Why not now:** one instance is the supported topology, where per-process state is
correct. Scaling out requires a shared rate-limit/metrics store and Redis fan-out
for live games (`docs/production/reliability-model.md`).

---

## 6. Legal/privacy pages, support channel, data-export bundle — **deferred**

**State:** deletion (`DELETE /api/games/{id}`, cascading and audited) and
cross-caller privacy are enforced; PGN and report export exist. There is no
"export everything" bundle, no terms/privacy pages, and no support channel.

**Why not now:** required before storing the public's personal data, not before
running the product for its operator and trusted users.

---

## Verification items kept honest (not deferred — genuinely unmeasurable here)

These are **not** work items that were skipped; they cannot be produced in this
environment and remain labelled as gaps rather than converted into passes:

- **Field Web Vitals** — needs real traffic. A *lab* measurement is recorded in
  `performance-report.md` and labelled as lab; the field figure stays `UNKNOWN`.
- **Manual screen-reader walkthrough** — needs a human with VoiceOver/NVDA.
  Automated axe (17 pages) plus keyboard-operable boards are the automated
  substitute; the manual certification remains open.
- **The four §52 release gates** (real-host deploy, rollback, DR host-loss drill,
  alert test) — need a production host and an alerting target. See
  `RELEASE_STATUS.md`.
- **Image signing / SBOM** — not configured. The container CVE scan *is* now run
  (`scripts/audit_dependencies.py`); signing and SBOM generation are not.

---

## What was resolved in this pass (so it is not re-listed as open)

- **Cross-browser coverage** — the full e2e suite now runs on **Chromium, Firefox
  and WebKit** (210 passed). It found a real missing-`<h1>` defect on the report
  page, now fixed.
- **Python and container dependency scanning** — `pip-audit` and `docker scout`
  now run and their real results are recorded in `security-report.md`.
- **Bundled npm in the web runtime image** — removed; the web image scans at
  0 critical / 0 high (was 11 high).
