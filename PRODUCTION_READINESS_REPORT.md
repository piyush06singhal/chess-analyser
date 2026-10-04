# Caissa — Production Readiness Report

**Date:** 2026-10-04
**Scope:** Phase 15 — production hardening, deployment, reliability and launch
readiness.
**Method:** every line below is backed by a command that was run or a test that
was executed. Nothing is reported green that was not measured; nothing that is
not built is implied to be.

> The rule for this report is the same as the rule for the product: a capability
> that is not built is listed as not built, and a check that was not run is not
> reported as passing.

## Verdict

**READY for a single-instance, authenticated deployment.** The service runs, the
release gate passes, the backup/restore path is verified end to end, and the
security and reliability boundaries are enforced in code and covered by tests.

**NOT YET ready for a multi-instance deployment** without the documented
follow-ups (shared rate-limit/metrics store and a Redis fan-out for the live
hub) — these are stated, not hidden, in
[docs/production/reliability-model.md](docs/production/reliability-model.md).

**The LLM layer is live-verified, not just unit-tested.** A real Groq key drives
`/api/coach/ask` end to end (plan → tools → evidence → generation → validation),
and the system check plus the five user journeys pass against the running
deployment. Where the provider is rate-limited, Caissa degrades to an evidence-only
answer that names the real reason.

**No model has passed its production gate**, so prediction surfaces honestly
report themselves unavailable. This is a product decision, not a misconfiguration.

## Evidence (measured this session)

| Check | Command | Result |
| --- | --- | --- |
| Unit / integration suite | `python -m pytest -o addopts= -q` | **1584 passed, 1 skipped** |
| Release gate | `python scripts/run_evaluation.py --quiet` | **165 passed, 0 failed, 0 skipped, 0 warned — No release blockers** |
| Static correctness | `python -m ruff check packages apps scripts tests` | **All checks passed** |
| Config validation (dev) | `python scripts/validate_config.py --environment development` | **VALID** |
| Config validation (prod) | `python scripts/validate_config.py --environment production` | **INVALID without keys** (the rule works) |
| Backup/restore round-trip | `python scripts/verify_backup_restore.py` | **VERIFIED — schema + every row count reproduced** |
| Live system check | `python scripts/system_check.py` | **ALL CHECKS PASSED** (19/19, including Redis, Groq, fair play) |
| Live user journeys | `python scripts/verify_journeys.py` | **ALL JOURNEYS COMPLETED** |
| Live coach turn (real Groq) | `POST /api/coach/ask` with `ARGUS_LLM_PROVIDER=groq` | **answers from stored evidence, validator passes** |
| Legacy-table drop (dry run) | `python scripts/drop_legacy_tables.py` | reports the 0-row table, refuses without `--yes` |

## What Phase 15 implements

### Environments and configuration
- Four environments (`development`, `test`, `staging`, `production`), each with a
  template (`.env.<env>.example`) and its own rules.
- `validate_settings` enforces the rules; **production refuses to start**
  misconfigured (open mode, SQLite, text logs, the `echo` provider).
- `argus config validate --environment <env>` and `scripts/validate_config.py`.

### Containerization
- `docker/Dockerfile.api` and `docker/Dockerfile.web` pin base images by digest
  and run as non-root (uid 10001 / `node`).
- `docker-compose.production.yml`: no bind-mounts, no published database port,
  password-protected Redis, CPU/memory limits, healthchecks.

### Database and migrations
- Additive, idempotent schema upgrades; the retired `position_analyses` table and
  its ORM class removed, with a guarded, backed-up drop script.
- Ownership column (`games.owner`) added additively and indexed.

### Backup and restore
- `scripts/backup_database.py`, `scripts/restore_database.py` (refuses to
  restore into the source), and `scripts/verify_backup_restore.py` — a **real**
  round-trip against the running database.

### Reliability
- **Engine resource gate** (`services/resource_gate.py`): bounded concurrency,
  bounded queue, idempotency (409 for a duplicate run; 503 with `Retry-After`
  when full). State reported by `/ready` → `checks.engine_capacity`.
- **Rate limiting** per caller on analysis, import, coach, upload and search
  (429 with `Retry-After`).
- **Feature flags** (`feature_flags.py`), enforced by path in middleware so HTTP
  and WebSocket routes are covered; a disabled surface is a 404.
- **Redis hardening**: password, bounded memory, LRU eviction, dangerous
  commands renamed away.
- **Health/readiness**: real probes; `database` and `stockfish` block, the rest
  are reported. `/metrics` serves Prometheus text.

### Security
- **API-key authentication** (constant-time compare); open mode is explicit and
  refused in production.
- **Cross-account privacy is now real**: `games.owner` records the importer, and
  the authorization layer enforces it end to end. `tests/test_phase15_privacy.py`
  exercises two callers and asserts a stranger's read is a 404 (absent, not
  forbidden), including delete and analyze.
- Security headers on every response; request-correlation id in every error body.
- CI security job: `pip-audit`, `npm audit`, Trivy (vuln/secret/misconfig).

### LLM provider layer (hardened and live-verified)
- **Groq is a first-class provider**, selected by `ARGUS_LLM_PROVIDER=groq` and
  served through the OpenAI-compatible client with a Groq base URL. The key comes
  from configuration only; no key is hardcoded anywhere.
- **The tool catalogue sent to a provider is bounded.** Groq's on-demand tier caps
  a request at 8000 tokens, so the agent offers a *focused* tool list per intent
  rather than all ~57 schemas; the previous general-fallback that sent everything
  (and returned HTTP 413) is gone.
- **The wire format is provider-correct.** Assistant `tool_calls` carry an `id`, tool
  results carry a `tool_call_id` and a string `content`, and dash variants are
  normalised — the three defects that previously produced a 400 from a real provider.
- **The prompt is versioned** (`PROMPT_VERSION`, currently `8.1`) and recorded with
  every generated turn, so a stored answer is traceable to the prompt that produced
  it. The current prompt teaches perspective discipline (`+1.80 from White's
  perspective` is White ahead even when Black moved), unit discipline (100cp = 1
  pawn, with pre-computed `evaluation_pawns` supplied so no hand conversion is
  needed), and a forcing-moves-first reading order.
- **Provider artifacts are stripped and fabrication is caught.** Model-invented
  citation markers (`【2†data】`) are removed from the answer text, and the
  hallucination validator normalises Unicode minus signs so a fabricated evaluation
  written with an en dash cannot slip past the sign check.
- **A provider outage degrades honestly.** A transient 429 is retried briefly
  with exponential backoff (only 429 — a bad key or bad request fails at once,
  and an over-long `Retry-After` is surfaced immediately rather than honoured),
  so a momentary throttle resolves into a real answer. A persistent limit names
  the real reason (`Groq rate limit or quota exceeded (HTTP 429)`) and reports
  evidence only — it never invents an explanation, and never claims a provider is
  missing when it is merely rate-limited.

### Deterministic answers that must not depend on an LLM
- A stored-fact question is answered without generation when it is exact. The
  no-model probability refusal ("Caissa cannot provide a win probability; no model
  has passed its production gate") is now **deterministic**: a provider outage cannot
  turn an honest refusal into a degraded turn. When a model *is* available the fast
  path steps aside and the model's own output is presented through the gated tool.

### Observability
- Request/caller contextvars, a dependency-free Prometheus registry labelled by
  route template, and a structured audit log.
- **The Redis probe authenticates.** The health/readiness check reads the password
  from `ARGUS_REDIS_URL` and sends `AUTH` before `PING`, so the password-protected
  (hardened) Redis reports `connected: true` instead of an `-NOAUTH` false negative.

### Documentation
- `docs/production/` — environments, configuration, containerization,
  migrations, backup-restore, reliability-model, security, observability,
  deployment, runbooks, load-and-chaos.

## What is deliberately NOT done (stated, not hidden)

| Item | Why | Where it is stated |
| --- | --- | --- |
| Multi-instance rate limit / metrics / live fan-out | Per-process today; a single instance is fully correct | [reliability-model.md](docs/production/reliability-model.md) |
| Alembic | Schema still evolving; additive upgrades cover every change so far | [migrations.md](docs/production/migrations.md) |
| Distributed tracing | Caissa is one service; request correlation is present | [observability.md](docs/production/observability.md) |
| A real model baseline | No model has passed its production gate | [DEFERRED_WORK.md](docs/release/DEFERRED_WORK.md) |
| Content scanning of uploads | Bounded by size and type, not content-scanned | [security.md](docs/production/security.md) |

Each of these is a real, known boundary. None is a claim of completeness, and
none is papered over with a stub.

## How to verify this report yourself

```bash
python -m pytest -p no:warnings -o addopts= -q          # the suite
python scripts/run_evaluation.py --quiet                 # the release gate
python -m ruff check packages apps scripts tests         # static correctness
python scripts/validate_config.py --environment development
python scripts/system_check.py                           # a running deployment
python scripts/verify_journeys.py                        # the five journeys
python scripts/verify_backup_restore.py                  # a backup restores
python scripts/verify_journeys.py                        # the five journeys
python scripts/quality_check.py --with-frontend          # the pre-commit gate
```

If a command here does not pass in your environment, that is a finding — the
report is only as good as the environment it was measured in.
