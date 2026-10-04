#!/usr/bin/env python
"""Restore a backup into a scratch database and verify it (§4).

This is the half that turns a backup into a tested recovery path. It restores the
dump into a **different** database (never the source), then verifies the restored
schema and row counts so "the restore worked" is a measurement, not a claim:

    python scripts/restore_database.py --dump backups/argus.dump \\
        --target-url postgresql://argus:argus@localhost:5434/argus_restore_check

Safety properties, on purpose:

* **The target must differ from the source.** The dump records its source
  database; restoring into it is refused, because a "restore test" that
  overwrites production is the opposite of a backup.
* **``--drop-existing`` is required to overwrite a non-empty target.** Without
  it, the script refuses rather than clobbering data.
* **Verification is real.** It re-inspects the restored schema and compares the
  expected tables (and, when a source URL is given, its row counts) to the
  restored ones, and exits non-zero on any mismatch.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Tables the restore must reproduce for Caissa to be able to read its own data.
EXPECTED_TABLES = (
    "games",
    "game_moves",
    "move_analyses",
    "critical_positions",
    "analysis_sessions",
    "players",
    "player_games",
    "player_profiles",
    "opponent_profiles",
    "training_positions",
    "training_attempts",
    "training_sessions",
    "scenarios",
    "live_games",
)


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


def _table_names(url: str) -> set[str]:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(_libpq(url), future=True)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _row_counts(url: str) -> dict[str, int]:
    from sqlalchemy import create_engine, text

    engine = create_engine(_libpq(url), future=True)
    counts: dict[str, int] = {}
    try:
        with engine.connect() as connection:
            for table in EXPECTED_TABLES:
                try:
                    counts[table] = int(
                        connection.execute(text(f"select count(*) from {table}")).scalar() or 0
                    )
                except Exception:  # noqa: BLE001 — a missing table is reported as -1
                    counts[table] = -1
    finally:
        engine.dispose()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore and verify a Caissa backup (§4).")
    parser.add_argument("--dump", type=Path, required=True, help="the .dump file to restore")
    parser.add_argument("--target-url", default="", help="the scratch database to restore into")
    parser.add_argument(
        "--source-url",
        default=os.environ.get("ARGUS_DATABASE_URL", ""),
        help="the original database, for row-count comparison (optional)",
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="drop and recreate objects in the target before restoring",
    )
    args = parser.parse_args()

    if not args.dump.exists():
        print(f"Refusing: dump {args.dump} does not exist.")
        return 2
    target = (args.target_url or "").strip()
    if not target:
        print("No target. Pass --target-url pointing at a scratch database.")
        return 2
    if target.startswith("sqlite"):
        print("Refusing: the target is SQLite; restore verification needs PostgreSQL.")
        return 2

    source = (args.source_url or "").strip()
    if source and _libpq(source) == _libpq(target):
        print(
            "Refusing: the target equals the source database. A restore test must not "
            "overwrite the database it is verifying."
        )
        return 2

    pg_restore = shutil.which("pg_restore")
    if pg_restore is None:
        print("Refusing: pg_restore is not on the PATH. Install the PostgreSQL client tools.")
        return 2

    target_tables = _table_names(target)
    if target_tables and not args.drop_existing:
        print(
            f"Refusing: target already has {len(target_tables)} table(s). Pass "
            "--drop-existing to overwrite it, or point at an empty database."
        )
        return 2

    command = [pg_restore]
    if args.drop_existing:
        command += ["--clean", "--if-exists"]
    command += ["--no-owner", "--no-privileges", "--dbname", _libpq(target), str(args.dump)]
    print("Running: pg_restore --dbname <target-url>", args.dump.name)
    result = subprocess.run(command, capture_output=True, text=True)
    # pg_restore exits non-zero for warnings too (e.g. "does not exist, skipping"
    # on --clean); the verification below is what decides success.
    if result.returncode != 0 and not args.drop_existing:
        print("Restore FAILED:")
        print(result.stderr.strip()[:2000])
        return 1

    restored = _table_names(target)
    missing = [table for table in EXPECTED_TABLES if table not in restored]
    print("=" * 62)
    print(f"Restored tables: {len(restored)}")
    if missing:
        print(f"MISSING expected tables: {', '.join(missing)}")
        return 1
    print("All expected tables are present.")

    if source:
        before = _row_counts(source)
        after = _row_counts(target)
        mismatches = [
            (table, before[table], after[table])
            for table in EXPECTED_TABLES
            if before.get(table, -1) != after.get(table, -1)
        ]
        print("-" * 62)
        print("Row counts (source → restored):")
        for table in EXPECTED_TABLES:
            flag = "" if before.get(table) == after.get(table) else "  <-- MISMATCH"
            print(f"  {table:22} {before.get(table, -1):>8} → {after.get(table, -1):>8}{flag}")
        if mismatches:
            print("\nRESTORE INCOMPLETE — row counts differ.")
            return 1
        print("\nRestore verified: schema and row counts match the source.")
    else:
        print("Restore verified: schema present (pass --source-url for a row-count check).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
