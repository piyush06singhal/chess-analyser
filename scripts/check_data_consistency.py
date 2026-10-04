#!/usr/bin/env python
"""Data-consistency check (§40): does the stored analysis still describe its game?

The database review in Phase 11 is about whether the schema *holds* together, and
the most damaging failure it can find is silent: rows that reference a game but
describe a different one. Every reader in Caissa trusts ``move_analyses.game_id`` —
the game intelligence report, the player profile, the training generator, the
scenario explorer, the coach — so one inconsistent game quietly produces wrong
answers in five places.

This script checks that trust, read-only, and prints one line per problem class:

* **analysis vs moves** — every stored per-move row must match the game's stored
  move at the same ply (SAN, UCI, and the position before the move).
* **ply coverage** — an analysis whose plies do not exist in the game.
* **stale generations** — more than one analysis version for a game (allowed, but
  reported). Readers resolve to the newest *complete* generation, so this check
  also reports which generation the API will read and whether it is complete —
  a newer partial re-run must never shadow the last good analysis.
* **stale snapshots** — a cached derived profile (`player_profiles`,
  `opponent_profiles`) whose stored analysed-game count no longer matches the
  games that are actually analysed. The service rebuilds these on the next read,
  so a stale row is a transient, not corruption; this reports it rather than
  letting it go unnoticed.
* **orphans** — analysis or training rows whose game no longer exists.
* **training provenance** — exercises whose source game has no stored analysis.

It never deletes or repairs anything. A database is not something a script should
"fix" silently; it reports, and a human decides.

Usage:
    python scripts/check_data_consistency.py [--database-url URL] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict


def _connect(url: str):
    from sqlalchemy import create_engine

    return create_engine(url, future=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("ARGUS_DATABASE_URL", ""))
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    url = args.database_url.strip()
    if not url:
        print("No database URL. Pass --database-url or set ARGUS_DATABASE_URL.")
        return 2

    from sqlalchemy import text

    engine = _connect(url)
    report: dict[str, list] = defaultdict(list)

    with engine.connect() as connection:
        games = {
            row.id: row
            for row in connection.execute(
                text(
                    "select id, white_player_name, black_player_name, move_count, "
                    "analysis_status from games"
                )
            )
        }

        moves: dict[str, dict[int, tuple[str, str, str]]] = defaultdict(dict)
        for row in connection.execute(
            text("select game_id, ply, san, uci, fen_before from game_moves")
        ):
            moves[row.game_id][int(row.ply)] = (row.san, row.uci, row.fen_before)

        analyses = list(
            connection.execute(
                text(
                    "select game_id, analysis_version, ply, played_move_san, played_move_uci, "
                    "fen_before, created_at "
                    "from move_analyses order by game_id, analysis_version, ply"
                )
            )
        )
        versions: dict[str, set] = defaultdict(set)
        #: Distinct plies stored per (game, generation) — how a part-way run is
        #: told apart from a complete one.
        coverage: dict[tuple[str, str], set[int]] = defaultdict(set)
        #: Newest write time per (game, generation), the generation ordering.
        newest_at: dict[tuple[str, str], object] = {}
        for row in analyses:
            versions[row.game_id].add(row.analysis_version)
            key = (row.game_id, row.analysis_version)
            coverage[key].add(int(row.ply))
            stamp = row.created_at
            if stamp is not None and key not in newest_at:
                newest_at[key] = stamp
            elif stamp is not None and stamp > newest_at[key]:
                newest_at[key] = stamp

        training_positions = list(
            connection.execute(
                text("select id, source_game_id from training_positions where source_game_id is not null")
            )
        )
        scenarios = list(
            connection.execute(
                text("select id, game_id from scenarios where game_id is not null")
            )
        )

        # Stored derived profiles, each with the analysed-game count it recorded
        # and the count the source games imply right now.
        player_snapshots = list(
            connection.execute(
                text(
                    "select pp.player_id, pp.profile_version, pp.analyzed_games, "
                    "(select count(distinct g.id) from games g "
                    " join player_games pg on pg.game_id = g.id "
                    " where pg.player_id = pp.player_id and g.analysis_status = 'analyzed') "
                    "as current_analyzed "
                    "from player_profiles pp"
                )
            )
        )
        opponent_snapshots = list(
            connection.execute(
                text(
                    "select op.player_id, op.analyzed_games, "
                    "(select count(distinct g.id) from games g "
                    " join player_games pg on pg.game_id = g.id "
                    " where pg.player_id = op.player_id and g.analysis_status = 'analyzed') "
                    "as current_analyzed "
                    "from opponent_profiles op"
                )
            )
        )

    # --- analysis rows vs the game's moves -----------------------------------
    for row in analyses:
        game_id = row.game_id
        if game_id not in games:
            report["analysis_orphaned"].append(
                {"game_id": game_id, "analysis_version": row.analysis_version, "ply": row.ply}
            )
            continue
        game_moves = moves.get(game_id, {})
        if not game_moves:
            report["game_without_moves"].append({"game_id": game_id})
            continue
        published = game_moves.get(int(row.ply))
        if published is None:
            report["analysis_ply_missing_from_game"].append(
                {"game_id": game_id, "ply": row.ply, "analysis_version": row.analysis_version}
            )
            continue
        san, uci, fen_before = published
        mismatches = []
        if row.played_move_uci != uci:
            mismatches.append(f"uci stored={uci} analysed={row.played_move_uci}")
        if (row.played_move_san or "").rstrip("?!#+") != (san or "").rstrip("?!#+"):
            mismatches.append(f"san stored={san} analysed={row.played_move_san}")
        if row.fen_before != fen_before:
            mismatches.append("fen_before differs")
        if mismatches:
            report["analysis_describes_another_game"].append(
                {
                    "game_id": game_id,
                    "ply": row.ply,
                    "analysis_version": row.analysis_version,
                    "mismatches": mismatches,
                }
            )

    # --- generations ---------------------------------------------------------
    # Mirrors `repository.resolve_analysis_version`: the newest generation that
    # covers every ply wins; when none is complete the newest is reported with
    # complete=False so the caller can say so rather than imply a full run.
    for game_id, found in versions.items():
        if len(found) <= 1:
            continue
        ordered = sorted(
            found,
            key=lambda version: (newest_at.get((game_id, version)) is not None, newest_at.get((game_id, version)), version),
            reverse=True,
        )
        required = int(games[game_id].move_count or 0) if game_id in games else 0
        resolved, complete = ordered[0], False
        for version in ordered:
            if required > 0 and len(coverage[(game_id, version)]) >= required:
                resolved, complete = version, True
                break
        report["multiple_analysis_generations"].append(
            {
                "game_id": game_id,
                "versions": sorted(found),
                "plies_required": required,
                "coverage": {v: len(coverage[(game_id, v)]) for v in sorted(found)},
                "readers_resolve_to": resolved,
                "complete": complete,
            }
        )

    # A game whose analysis is marked complete in the library but has no complete
    # generation in storage would be served as incomplete — a real defect.
    for row in games.values():
        if row.id in versions and (row.analysis_status or "") == "analyzed":
            if not any(
                len(coverage[(row.id, version)]) >= int(row.move_count or 0)
                for version in versions[row.id]
            ):
                report["analysed_game_without_a_complete_generation"].append(
                    {"game_id": row.id, "move_count": int(row.move_count or 0)}
                )

    # --- provenance ----------------------------------------------------------
    for row in training_positions:
        if row.source_game_id not in games:
            report["training_position_source_game_missing"].append(
                {"position_id": row.id, "game_id": row.source_game_id}
            )
        elif row.source_game_id not in versions:
            report["training_position_without_analysis"].append(
                {"position_id": row.id, "game_id": row.source_game_id}
            )

    for row in scenarios:
        if row.game_id not in games:
            report["scenario_game_missing"].append({"scenario_id": row.id, "game_id": row.game_id})

    # --- stale derived snapshots ---------------------------------------------
    for row in player_snapshots:
        if int(row.analyzed_games or 0) != int(row.current_analyzed or 0):
            report["stale_player_profile"].append(
                {
                    "player_id": row.player_id,
                    "profile_version": row.profile_version,
                    "stored_analyzed": int(row.analyzed_games or 0),
                    "current_analyzed": int(row.current_analyzed or 0),
                }
            )
    for row in opponent_snapshots:
        if int(row.analyzed_games or 0) != int(row.current_analyzed or 0):
            report["stale_opponent_profile"].append(
                {
                    "player_id": row.player_id,
                    "stored_analyzed": int(row.analyzed_games or 0),
                    "current_analyzed": int(row.current_analyzed or 0),
                }
            )

    # --- report --------------------------------------------------------------
    if args.json:
        print(json.dumps({key: value for key, value in report.items()}, indent=2, default=str))
        return 1 if report else 0

    print("Caissa DATA CONSISTENCY CHECK")
    print("=" * 62)
    print(f"games ................. {len(games)}")
    print(f"stored analysis rows .. {len(analyses)}")
    print(f"analysis generations .. {len(versions)}")
    print("-" * 62)
    if not report:
        print("CONSISTENT — every stored analysis describes the game it is attached to.")
        return 0

    labels = {
        "analysis_describes_another_game": "stored analysis contradicts the game's moves",
        "analysis_ply_missing_from_game": "stored analysis references a ply the game does not have",
        "analysis_orphaned": "stored analysis whose game no longer exists",
        "game_without_moves": "game with no stored moves",
        "multiple_analysis_generations": "game analysed more than once (readers resolve to the newest complete generation)",
        "analysed_game_without_a_complete_generation": "game marked analysed but no generation covers every ply",
        "stale_player_profile": "cached player profile older than its source games",
        "stale_opponent_profile": "cached opponent profile older than its source games",
        "training_position_source_game_missing": "training exercise whose source game is gone",
        "training_position_without_analysis": "training exercise whose source game has no analysis",
        "scenario_game_missing": "stored scenario whose game is gone",
    }
    for key, entries in sorted(report.items()):
        print(f"\n{labels.get(key, key)} — {len(entries)}")
        for entry in entries[:10]:
            print(f"   {entry}")
        if len(entries) > 10:
            print(f"   … and {len(entries) - 10} more")
    print("\n" + "=" * 62)
    serious = {
        key
        for key in report
        if key
        not in {
            "multiple_analysis_generations",
            "training_position_without_analysis",
            "stale_player_profile",
            "stale_opponent_profile",
        }
    }
    if serious:
        print("INCONSISTENT — see the classes above. Nothing was modified.")
        return 1
    print("INCONSISTENT (advisory only) — nothing was modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
