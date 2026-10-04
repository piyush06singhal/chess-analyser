# Deployment and release

How a change moves from a commit to production, and how it is rolled back if it
does not. The rule throughout: a change is verified in the environment closest to
production before it reaches production.

## Pipeline

```
push / PR
  └── CI (quality.yml)
        ├── backend: ruff, evaluation, test suite (engine tests excluded)
        ├── security: pip-audit, npm audit, Trivy (vuln/secret/misconfig)
        ├── frontend: tsc, lint, build
        └── browser: Playwright + axe, and the five user journeys
                 │
                 ▼
        staging (mirrors production)
          ├── python scripts/validate_config.py --environment staging
          ├── python scripts/system_check.py
          ├── python scripts/verify_backup_restore.py
          ├── python scripts/check_data_consistency.py
          ├── load + chaos + smoke (see load-and-chaos.md)
          └── the migration dry run (see migrations.md)
                 │
                 ▼
        production
          ├── validate_config --environment production
          ├── migrations (backup verified first)
          ├── deploy
          ├── smoke test
          └── watch SLIs (observability.md); roll back if they regress
```

CI does not deploy. The promotion from staging to production is a deliberate,
human decision made on the staging evidence.

## Deployment strategy

* **Immutable images.** Deploy a built image, never a working tree (see
  [containerization.md](containerization.md)). The same image that passed staging
  is the one that reaches production.
* **Migrations before the new code.** Additive migrations are safe to run ahead
  of the deploy; destructive ones are a maintenance-window operation with a
  verified backup (see [migrations.md](migrations.md)).
* **Rolling restart** for a single-instance deployment: `docker compose -f
  docker-compose.production.yml up -d`. For a multi-instance deployment, roll
  instances one at a time and wait for `/ready` before moving to the next, so a
  bad image never takes every instance down at once.
* **Health-gated.** Compose healthchecks (and an orchestrator's readiness probe)
  gate traffic on `/ready`.

## Rollback

The rollback for a bad deploy is the previous image:

```bash
docker compose -f docker-compose.production.yml up -d --no-deps api
# with the previous image tag pinned
```

* **Code rollback** is immediate (re-run the previous image).
* **Schema rollback** is forward-only; the rollback for a destructive change is
  the verified backup taken before it (see [migrations.md](migrations.md)).
* **Feature rollback** without a redeploy: turn the surface off with a feature
  flag (`ARGUS_FEATURE_FLAGS=coach=false`), which makes it a 404. This is the
  fastest way to disable a misbehaving surface while keeping the rest up.

## Release checklist

Before promoting a release, confirm every line. A box that cannot be checked is a
reason to stop, not to skip.

- [ ] CI green on the release commit (backend, security, frontend, browser).
- [ ] `python scripts/validate_config.py --environment production` passes.
- [ ] `python scripts/system_check.py` reports the staging dependencies healthy.
- [ ] `python scripts/verify_backup_restore.py` passes on staging with a recent dump.
- [ ] `python scripts/check_data_consistency.py` reports no serious
      inconsistency on staging.
- [ ] `python scripts/run_evaluation.py --quiet` reports **No release blockers**.
- [ ] Load, chaos and smoke tests pass on staging (see [load-and-chaos.md](load-and-chaos.md)).
- [ ] Migrations are additive, or a verified backup exists and a window is set.
- [ ] Rollback path is known (previous image tag; feature flags for a surface).
- [ ] SLIs are being watched, and the on-call knows the [runbooks](runbooks.md).

## Post-deploy

1. `python scripts/system_check.py` against production — dependency-by-dependency.
2. Smoke test the primary journey (import → report → train) once.
3. Watch the SLIs for one release cycle; a regression is a reason to roll back,
   not to wait.

## Environment promotion

The same image, different configuration: `.env.staging.example` →
`.env.production.example`. Secrets are injected by the platform, never committed.
Staging and production secrets must be **different**.