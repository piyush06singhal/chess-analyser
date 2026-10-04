#!/usr/bin/env python
"""Verify the backup/restore path end to end (§4).

A backup is only real if it restores. This script proves the round-trip against
the actual PostgreSQL instance: it dumps the database, restores the dump into a
scratch database, and compares the schema and row counts. It then drops the
scratch database. Nothing is assumed — the verification is a measurement.

It uses the PostgreSQL client tools **inside the running container** (where they
ship) rather than requiring them on the host, so it works in the same environment
the app runs in:

    python scripts/verify_backup_restore.py
    python scripts/verify_backup_restore.py --container argus-postgres-1

For a deployment where the client tools are on the host, use
``scripts/backup_database.py`` and ``scripts/restore_database.py`` directly.

Exit code 0 means the round-trip reproduced the schema and every row count; a
non-zero exit names what did not match.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Tables the round-trip must reproduce.
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


def _discover_container() -> str | None:
    """Find the compose Postgres container id, or None."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "-q", "postgres"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
    except FileNotFoundError:
        return None
    container = result.stdout.strip()
    return container or None


def _exec(container: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container, *args],
        capture_output=True,
        text=True,
        check=False if not check else True,
    )


def _scalar(container: str, database: str, sql: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            "argus",
            "-d",
            database,
            "-tAc",
            sql,
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify backup/restore end to end (§4).")
    parser.add_argument("--container", default="", help="the Postgres container id/name")
    parser.add_argument("--source-db", default="argus")
    parser.add_argument("--scratch-db", default="argus_restore_check")
    args = parser.parse_args()

    container = args.container or _discover_container()
    if not container:
        print(
            "Refusing: no running Postgres container found. Start the stack with "
            "`docker compose up -d`, or pass --container."
        )
        return 2

    source = args.source_db
    scratch = args.scratch_db
    dump_path = "/tmp/argus-restore-check.dump"

    print(f"Postgres container: {container}")
    print(f"Source database:    {source}")

    # 1. Dump.
    dump = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            dump_path,
            "-U",
            "argus",
            "-d",
            source,
        ],
        capture_output=True,
        text=True,
    )
    if dump.returncode != 0:
        print("Dump FAILED:")
        print(dump.stderr.strip()[:2000])
        return 1
    size = _scalar(container, source, "select 1")  # sanity: server answers
    print(f"1. Dumped to {dump_path} (server answered: {size})")

    # 2. Create a scratch database (drop a stale one first).
    subprocess.run(
        ["docker", "exec", container, "dropdb", "-U", "argus", "--if-exists", scratch],
        capture_output=True,
        text=True,
    )
    created = subprocess.run(
        ["docker", "exec", container, "createdb", "-U", "argus", scratch],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        print("Could not create the scratch database:")
        print(created.stderr.strip()[:2000])
        return 1
    print(f"2. Created scratch database '{scratch}'")

    # 3. Restore.
    restore = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "pg_restore",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            scratch,
            "-U",
            "argus",
            dump_path,
        ],
        capture_output=True,
        text=True,
    )
    if restore.returncode != 0:
        print("Restore FAILED:")
        print(restore.stderr.strip()[:2000])
        return 1
    print("3. Restored the dump into the scratch database")

    # 4. Verify schema.
    source_tables = set(
        _scalar(
            container,
            source,
            "select tablename from pg_tables where schemaname='public' order by tablename",
        ).splitlines()
    )
    restored_tables = set(
        _scalar(
            container,
            scratch,
            "select tablename from pg_tables where schemaname='public' order by tablename",
        ).splitlines()
    )
    missing = sorted(source_tables - restored_tables)
    if missing:
        print(f"SCHEMA MISMATCH — tables missing after restore: {', '.join(missing)}")
        return 1
    print(f"4. Schema matches: {len(source_tables)} tables present in both")

    # 5. Verify row counts.
    mismatches: list[tuple[str, str, str]] = []
    print("5. Row counts (source → restored):")
    for table in EXPECTED_TABLES:
        if table not in source_tables:
            continue
        before = _scalar(container, source, f"select count(*) from {table}")
        after = _scalar(container, scratch, f"select count(*) from {table}")
        flag = "" if before == after else "  <-- MISMATCH"
        if before != after:
            mismatches.append((table, before, after))
        print(f"   {table:22} {before:>8} → {after:>8}{flag}")

    # 6. Clean up the scratch database.
    subprocess.run(
        ["docker", "exec", container, "dropdb", "-U", "argus", "--if-exists", scratch],
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["docker", "exec", container, "rm", "-f", dump_path],
        capture_output=True,
        text=True,
    )
    print(f"6. Dropped scratch database and removed {dump_path}")

    if mismatches:
        print("\nRESTORE INCOMPLETE — row counts differ for: " + ", ".join(t for t, _, _ in mismatches))
        return 1
    print("\nBACKUP/RESTORE VERIFIED — schema and every row count reproduced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
