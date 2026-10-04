#!/usr/bin/env python
"""Database query benchmark (Phase 14 §34).

Measures the latency of the queries the API leans on, against the configured
database, and reports the **query plan** for each so a sequential scan on a large
table is visible rather than guessed. Latency is reported as nearest-rank
percentiles from the one shared definition (``argus.shared.stats``), not an
average.

The script is read-only: every statement is a ``SELECT``, so it never mutates
state and is safe to run against a live database.

Usage:

    python scripts/benchmark_db.py
    python scripts/benchmark_db.py --database-url postgresql+psycopg2://argus:argus@localhost:5434/argus
    python scripts/benchmark_db.py --rounds 30 --json evaluation/reports/db-benchmark.json

When no database is configured the script says so and exits non-zero — it does
not invent numbers from a database it never reached.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "argus"))

from argus.shared.stats import latency_summary  # noqa: E402  (needs the path above)

#: A table with fewer rows than this is small enough that a sequential scan is
#: the correct plan; only a scan on a larger table is worth reporting.
SMALL_TABLE_ROWS = 5_000


def _targets() -> list[tuple[str, object]]:
    """The hot queries, built from the ORM models so they match the real schema."""
    from argus_api.db.models import (
        Game,
        GamePosition,
        GraphEdgeRecord,
        GraphNodeRecord,
        MoveAnalysis,
        PlayerProfileRecord,
        TrainingAttempt,
        TrainingPosition,
    )

    return [
        ("games.list_recent", select(Game).order_by(Game.created_at.desc()).limit(50)),
        (
            "games.positions_by_game",
            select(GamePosition).order_by(GamePosition.game_id, GamePosition.ply).limit(200),
        ),
        (
            "games.move_analyses_by_game",
            select(MoveAnalysis).order_by(MoveAnalysis.game_id, MoveAnalysis.ply).limit(200),
        ),
        ("players.profile_by_player", select(PlayerProfileRecord).limit(20)),
        ("training.positions_by_player", select(TrainingPosition).limit(60)),
        ("training.attempts_by_player", select(TrainingAttempt).limit(60)),
        ("graph.nodes_by_type", select(GraphNodeRecord).where(GraphNodeRecord.node_type == "position").limit(50)),
        ("graph.edges_by_source", select(GraphEdgeRecord).limit(100)),
    ]


def _explain(engine: Engine, statement) -> list[str]:
    """The planner's own lines for a statement, whichever dialect we are on."""
    try:
        sql = str(statement.compile(engine, compile_kwargs={"literal_binds": True}))
    except Exception:  # noqa: BLE001 — literal binds are not always possible
        sql = str(statement)
    prefix = "EXPLAIN (FORMAT TEXT)" if engine.dialect.name == "postgresql" else "EXPLAIN QUERY PLAN"
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(f"{prefix} {sql}")).all()
    except Exception as exc:  # noqa: BLE001 — a plan we cannot read is reported, not hidden
        return [f"(plan unavailable: {type(exc).__name__})"]
    lines: list[str] = []
    for row in rows:
        lines.append(" ".join(str(value) for value in row if value is not None))
    return lines


def _scanned_tables(plan: list[str]) -> list[str]:
    """Tables the planner reads with a full scan (``SCAN``/``Seq Scan``)."""
    tables: list[str] = []
    for line in plan:
        lowered = line.lower()
        if "seq scan on " in lowered:
            tables.append(lowered.split("seq scan on ", 1)[1].split()[0].strip('"'))
        elif lowered.startswith("scan ") or " scan " in lowered:
            marker = "scan " if lowered.startswith("scan ") else " scan "
            candidate = lowered.split(marker, 1)[1].split()[0].strip('"')
            if candidate and candidate != "using":
                tables.append(candidate)
    return sorted(set(tables))


def _row_count(engine: Engine, table: str) -> int | None:
    if not table.isidentifier():
        return None
    try:
        with engine.connect() as connection:
            return int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar() or 0)
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Caissa database queries.")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ARGUS_DATABASE_URL", ""),
        help="SQLAlchemy URL; defaults to $ARGUS_DATABASE_URL",
    )
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument(
        "--json",
        default=str(REPO_ROOT / "evaluation" / "reports" / "db-benchmark.json"),
    )
    args = parser.parse_args()

    if not args.database_url.strip():
        print(
            "No database configured. Set ARGUS_DATABASE_URL or pass --database-url.\n"
            "Example: postgresql+psycopg2://argus:argus@localhost:5434/argus"
        )
        return 2

    engine = create_engine(args.database_url, future=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        print(f"Cannot reach the database: {type(exc).__name__}: {exc}")
        return 1

    results: dict[str, dict] = {}
    warnings: list[str] = []
    print(f"Database benchmark — {engine.dialect.name} ({args.rounds} rounds/query)\n")
    print(f"{'query':<28}{'p50':>9}{'p95':>9}{'max':>9}  scan")

    for name, statement in _targets():
        samples: list[float] = []
        with engine.connect() as connection:
            for _ in range(max(1, args.rounds)):
                started = perf_counter()
                connection.execute(statement).all()
                samples.append((perf_counter() - started) * 1000.0)
        summary = latency_summary(samples)
        plan = _explain(engine, statement)
        scans = _scanned_tables(plan)
        big_scans = []
        for table in scans:
            rows = _row_count(engine, table)
            if rows is not None and rows > SMALL_TABLE_ROWS:
                big_scans.append(f"{table} ({rows} rows)")
                warnings.append(f"{name}: sequential scan on {table} with {rows} rows")
        results[name] = {
            **summary,
            "scan_tables": scans,
            "large_sequential_scans": big_scans,
            "plan": plan,
        }
        scan_note = ", ".join(big_scans) if big_scans else ("ok" if not scans else "small tables only")
        print(
            f"{name:<28}{summary['p50_ms']:>9}{summary['p95_ms']:>9}{summary['max_ms']:>9}  {scan_note}"
        )

    print("\nPlans (per query):")
    for name, row in results.items():
        print(f"\n  {name}")
        for line in row["plan"][:8]:
            print(f"    {line}")

    payload = {
        "database": engine.dialect.name,
        "rounds": args.rounds,
        "queries": results,
        "warnings": warnings,
    }
    target = Path(args.json)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2))
    print(f"\nwritten: {target}")

    if warnings:
        print("\nNOTE (a scan on a large table is a real finding, not a failure):")
        for warning in warnings:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
