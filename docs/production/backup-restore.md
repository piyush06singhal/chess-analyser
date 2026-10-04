# Backup and restore

A backup that has never been restored is a hope, not a backup. Caissa ships both
halves and verifies the round-trip.

## Taking a backup

```bash
python scripts/backup_database.py --database-url "$ARGUS_DATABASE_URL"
# → backups/argus-<date>.dump
```

Uses `pg_dump --format=custom` (compressed, restorable selectively). The script
refuses (exit 2) when no URL is given, when the URL is SQLite (its backup is the
file), or when `pg_dump` is not on the PATH, and prints the command it ran so the
backup is reproducible by hand. The password never appears on the command line —
`pg_dump` reads it from the environment via libpq.

## Restoring into a scratch database

```bash
python scripts/restore_database.py \
    --dump backups/argus-2026-10-03.dump \
    --target-url postgresql://argus:argus@localhost:5434/argus_restore_check \
    --source-url "$ARGUS_DATABASE_URL"
```

Safety properties, on purpose:

* **The target must differ from the source.** Restoring into the database being
  verified is refused — a restore test that overwrites production is the
  opposite of a backup.
* **`--drop-existing` is required** to overwrite a non-empty target.
* **Verification is real.** It re-inspects the restored schema and compares the
  expected tables and row counts, exiting non-zero on any mismatch.

## The verified round-trip (the check that matters)

```bash
python scripts/verify_backup_restore.py
```

This dumps the live database, restores the dump into a scratch database, and
compares the schema and every row count — using the PostgreSQL client tools
inside the running container. It then drops the scratch database. Exit 0 means
the round-trip reproduced the schema and every row count.

A representative run against the development stack:

```
4. Schema matches: 30 tables present in both
5. Row counts (source → restored):
   games                        11 →       11
   game_moves                  214 →      214
   move_analyses               311 →      311
   ...
BACKUP/RESTORE VERIFIED — schema and every row count reproduced.
```

This particular development database carries 30 tables because it predates the
retired `position_analyses` drop (see `migrations.md`). A freshly created
database has 29: `Base.metadata.create_all` defines every table the code uses,
and `position_analyses` is no longer among them.

## Recovery time and recovery point

| Metric | Value | Basis |
| --- | --- | --- |
| RPO (recovery point) | the last dump | backups are on-demand; schedule them (see below) |
| RTO (recovery time) | minutes for a small database | a restore into a fresh database + cutover |

These are honest measurements for the current deployment size, not guarantees
for every future one. A larger database restores proportionally slower; the
round-trip script measures the real numbers rather than assuming them.

## Scheduling backups

The scripts are the mechanism; the schedule is the operator's. For production,
run `backup_database.py` on a cron/systemd timer (or the platform's managed
backup) and **periodically run `verify_backup_restore.py` against a recent dump**
so the restore path is exercised, not just assumed. A backup schedule with no
restore test is the failure mode this document exists to prevent.

## What is not backed up

The model store (`ARGUS_MODELS_DIR`) holds trained artifacts. It is versioned and
reproducible from its training data and configuration; back it up separately if
a retrain is expensive, but it is not part of the database dump.