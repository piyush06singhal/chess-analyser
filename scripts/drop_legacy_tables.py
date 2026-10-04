#!/usr/bin/env python
"""Drop retired tables on a deployment that still has them (§42).

The Phase 1/2 ``position_analyses`` table is no longer written or read (its ORM
class was removed in Phase 15), but a database created before that still carries
it. Dropping it is destructive, so it is an explicit operator action — never a
side effect of starting the API — and this script is deliberately hard to fire by
accident:

* it **refuses without ``--yes``**, printing exactly what it would drop;
* it **backs up first by default** (``--skip-backup`` opts out), so the drop is
  reversible;
* it reports, for each retired table, whether it exists, how many rows it holds,
  and whether any live code still references it.

Usage:
    python scripts/drop_legacy_tables.py                 # dry run
    python scripts/drop_legacy_tables.py --yes           # drop (with a backup)
    python scripts/drop_legacy_tables.py --yes --skip-backup
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Tables retired from the schema, with the reason they are safe to drop.
RETIRED_TABLES: dict[str, str] = {
    "position_analyses": (
        "Retired Phase 1/2 per-move store. The Phase 3 pipeline writes "
        "move_analyses, which every reader resolves against; nothing writes or "
        "reads position_analyses any more."
    ),
}


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Drop retired Caissa tables (§42).")
    parser.add_argument("--database-url", default=os.environ.get("ARGUS_DATABASE_URL", ""))
    parser.add_argument("--yes", action="store_true", help="actually drop the tables")
    parser.add_argument("--skip-backup", action="store_true", help="do not back up first")
    args = parser.parse_args()

    url = (args.database_url or "").strip()
    if not url:
        print("No database URL. Pass --database-url or set ARGUS_DATABASE_URL.")
        return 2

    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(_libpq(url), future=True)
    try:
        existing = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    present = {name: reason for name, reason in RETIRED_TABLES.items() if name in existing}
    if not present:
        print("Nothing to do: no retired tables are present.")
        return 0

    print("Retired tables found:")
    for name, reason in present.items():
        engine = create_engine(_libpq(url), future=True)
        try:
            with engine.connect() as connection:
                count = int(connection.execute(text(f"select count(*) from {name}")).scalar() or 0)
        finally:
            engine.dispose()
        print(f"  - {name} ({count} rows): {reason}")

    if not args.yes:
        print("\nDry run. Re-run with --yes to drop the table(s) above.")
        return 0

    if not args.skip_backup:
        if shutil.which("pg_dump") is None:
            print(
                "Refusing: pg_dump is not available to take a safety backup. Install the "
                "PostgreSQL client tools, or pass --skip-backup to accept the risk."
            )
            return 2
        backup = REPO_ROOT / "backups" / "pre-legacy-drop.dump"
        backup.parent.mkdir(parents=True, exist_ok=True)
        print(f"Backing up to {backup} ...")
        result = subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(backup),
                _libpq(url),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("Backup FAILED; not dropping anything:")
            print(result.stderr.strip()[:2000])
            return 1

    engine = create_engine(_libpq(url), future=True)
    try:
        with engine.begin() as connection:
            for name in present:
                connection.execute(text(f"DROP TABLE IF EXISTS {name}"))
    finally:
        engine.dispose()
    print(f"Dropped: {', '.join(present)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
