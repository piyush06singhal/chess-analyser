# Migrations

Caissa bootstraps its schema with `Base.metadata.create_all` plus
`ensure_schema_upgrades` (additive, idempotent column and index changes). There
is no Alembic tool wired in. This document is explicit about what that can and
cannot do, and how destructive changes are handled.

## What the current approach handles

`ensure_schema_upgrades` (`apps/api/argus_api/db/schema.py`) applies **additive**
changes at startup:

* adds a missing column (`games.source_game_id`, the `move_analyses` candidate
  and played-eval columns, the `players` identity columns, the
  `training_positions` continuation/replay columns);
* adds a missing index;
* widens a CHECK constraint or replaces a unique constraint on **PostgreSQL**
  only (SQLite cannot alter a constraint, so those steps are skipped there).

Every statement is guarded by inspecting the live schema, so running it twice
changes nothing. It never drops, renames or back-fills.

## What it deliberately does not do

Anything destructive or non-additive needs a decision and a migration, not a
startup side effect:

* **dropping a table** — e.g. the retired `position_analyses` table;
* **dropping or renaming a column**;
* **back-filling data**;
* **changing a column's type**.

Those are operator steps with their own scripts, run knowingly.

## The legacy table drop

`position_analyses` (Phase 1/2) is no longer written or read; its ORM class was
removed. A database created before that still carries the table. Dropping it is
destructive, so it is an explicit, guarded action:

```bash
# Dry run — reports the table, its row count, and why it is safe to drop.
python scripts/drop_legacy_tables.py

# Drop, taking a safety backup first.
python scripts/drop_legacy_tables.py --yes
```

The script refuses without `--yes`, backs up by default (`pg_dump`), and refuses
if `pg_dump` is unavailable unless `--skip-backup` is passed explicitly.

## Deploying a schema change

1. **Additive only** — ship it; startup applies it. Verify with
   `python scripts/system_check.py` (reports the migration state by table name).
2. **Destructive** — take a verified backup first
   (`python scripts/backup_database.py`), confirm it restores
   (`python scripts/restore_database.py`), run the change in **staging**, then in
   production during a maintenance window with a rollback ready.

## Rollback

Schema changes are forward-only in this scheme. The rollback for a destructive
change is the backup taken immediately before it: restore into a fresh database
and cut over. That is why the backup is not optional for a drop, and why
`restore_database.py` refuses to restore into the source database.

## Why no Alembic (stated, not hidden)

Alembic was evaluated and deferred: the schema is still evolving within the
project's own phases, and `create_all` + additive upgrades have covered every
change so far without a migration history to maintain. When the schema
stabilises, Alembic is the right next step; the additive module then becomes the
baseline migration. Until then, this document is the honest description of how
schema change is actually handled.
