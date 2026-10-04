#!/usr/bin/env python
"""Back up the Caissa database (§4).

A backup that has never been restored is not a backup; it is a hope. This script
produces the dump, and ``scripts/restore_database.py`` restores it into a scratch
database so the round-trip is a real, tested operation rather than an assumption.

    python scripts/backup_database.py --database-url "$ARGUS_DATABASE_URL"
    python scripts/backup_database.py --out backups/argus-2026-10-03.dump

It uses ``pg_dump`` in custom format (``-Fc``), which is compressed and can be
restored selectively and in parallel. The script refuses (exit 2) when
``pg_dump`` is not on the PATH or no database URL is given, and prints the exact
command it ran so the backup is reproducible by hand.

PostgreSQL only: SQLite is a local/test backend, and its backup is the file
itself. Passing a SQLite URL is refused with that reason rather than silently
producing nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _to_libpq_url(url: str) -> str:
    """Convert a SQLAlchemy URL to the ``postgresql://`` form ``pg_dump`` wants."""
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up the Caissa PostgreSQL database (§4).")
    parser.add_argument("--database-url", default=os.environ.get("ARGUS_DATABASE_URL", ""))
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "backups" / f"argus-{dt.date.today().isoformat()}.dump",
        help="destination file for the dump",
    )
    args = parser.parse_args()

    url = (args.database_url or "").strip()
    if not url:
        print("No database URL. Pass --database-url or set ARGUS_DATABASE_URL.")
        return 2
    if url.startswith("sqlite"):
        print(
            "Refusing: ARGUS_DATABASE_URL is SQLite, which is a local/test backend. "
            "Its backup is the database file itself."
        )
        return 2

    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        print(
            "Refusing: pg_dump is not on the PATH. Install the PostgreSQL client "
            "tools (they ship with the server image) and re-run."
        )
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    command = [
        pg_dump,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(args.out),
        _to_libpq_url(url),
    ]
    # The password never appears on the command line; pg_dump reads it from the
    # environment via libpq, which is why the URL is passed positionally.
    print("Running:", " ".join(command[:-1] + ["<database-url>"]))
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print("Backup FAILED:")
        print(result.stderr.strip()[:2000])
        return 1

    size = args.out.stat().st_size if args.out.exists() else 0
    print(f"Backup written: {args.out} ({size} bytes)")
    print(
        "Now verify it: python scripts/restore_database.py "
        f"--dump {args.out} --target-url <scratch-database-url>"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
