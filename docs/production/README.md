# Production

How to run Caissa as a real service: environments, configuration, containers,
migrations, backups, reliability, security, observability, and the release
process. Every claim here is backed by a command in `scripts/` or a test in
`tests/`, and the top-level [`PRODUCTION_READINESS_REPORT.md`](../../PRODUCTION_READINESS_REPORT.md)
states honestly what is implemented and what is not.

The guiding rule is the same as the rest of Caissa: **nothing is pretended.** A
capability that is not built is listed as not built; a check that was not run is
not reported green.

| Document | Covers |
| --- | --- |
| [environments.md](environments.md) | The four environments and what differs between them |
| [configuration.md](configuration.md) | Every setting, and `argus config validate` |
| [containerization.md](containerization.md) | Images, non-root, resource limits, production compose |
| [migrations.md](migrations.md) | Schema changes, the legacy-table drop, rollback |
| [backup-restore.md](backup-restore.md) | Dumps and the verified restore round-trip |
| [reliability-model.md](reliability-model.md) | Jobs, engine bounds, Redis, rate limits, health, shutdown, caching |
| [security.md](security.md) | Auth, headers, uploads, privacy, audit logging |
| [observability.md](observability.md) | Metrics, request correlation, health, SLI/SLO |
| [deployment.md](deployment.md) | CI/CD, staging, rollout, rollback, release checklist |
| [runbooks.md](runbooks.md) | Incident procedures |
| [load-and-chaos.md](load-and-chaos.md) | Load, chaos, consistency and smoke tests |

## Verifying a deployment, in one screen

```bash
python scripts/validate_config.py --environment production   # config is valid
python scripts/system_check.py                               # dependency-by-dependency state
python scripts/verify_backup_restore.py                      # a backup actually restores
python scripts/check_data_consistency.py                     # stored data is self-consistent
python scripts/verify_journeys.py                            # the five user journeys run
python scripts/run_evaluation.py --quiet                     # the release gate
```

A production deploy should not ship until all of these pass. The readiness report
lists which of them run automatically in CI and which are pre-release steps.
