#!/usr/bin/env python
"""Re-analyse games whose stored analyses predate MultiPV candidate storage.

Why this exists rather than a migration: the missing data is not a column, it is
a *search result*. When a game was analysed before Caissa stored MultiPV
candidates, its stored scores are perfectly valid — the played move, the best
move, the evaluation — but there are no alternative candidates, so the
turning-point explorer legitimately offers zero what-ifs for that game ("the
analysis predates candidate storage").

Inventing candidates, or deriving them from the single best move, would be
fabrication. The honest fix is to re-run the analysis with MultiPV enabled, using
the *existing* analysis pipeline, and let it overwrite the game's stored analysis
with a fresh, internally consistent generation. This script drives that through
the public API:

    POST /api/analysis/games/{game_id}   (multipv > 1)
    GET  /api/games/{game_id}/status     (until analysed)

It reports exactly which games it re-analysed, the depth and MultiPV used, and
which games it left alone (already carrying candidates, or unanalysable for a
stated reason). Nothing about the game itself is modified.

Usage:
    python scripts/backfill_multipv.py [--base-url http://127.0.0.1:8002]
                                       [--depth 12] [--multipv 4]
                                       [--game-id GAME] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8002"


def request(url: str, payload: dict | None = None, timeout: int = 600):
    """Return ``(status, body)``; HTTP errors come back as status + parsed body."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode()
            return response.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:  # noqa: BLE001 — report whatever the server said
            return exc.code, raw
    except urllib.error.URLError as exc:
        print(f"FAIL cannot reach {url}: {exc}")
        sys.exit(2)


def candidate_counts(base_url: str, game_id: str) -> dict:
    """Whether this game's stored analyses can offer alternatives.

    Measured through the turning-point explorer, which reads the stored analyses
    and reports per moment whether alternatives exist. That is the exact
    condition a re-analysis has to change — a moment that is branchable but has
    zero alternatives is the "analysis predates candidate storage" case.
    """
    status, body = request(f"{base_url}/api/scenarios/games/{game_id}/explorer")
    if status != 200 or not isinstance(body, dict):
        return {"error": body}
    moments = body.get("turning_points") or []
    return {
        "plies": body.get("plies_analyzed") or 0,
        "moments": len(moments),
        "branchable": sum(1 for moment in moments if moment.get("branchable")),
        "with_alternatives": sum(
            1 for moment in moments if (moment.get("alternative_count") or 0) > 0
        ),
        "analysis_version": body.get("analysis_version"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--depth", type=int, default=12)
    parser.add_argument("--multipv", type=int, default=4)
    parser.add_argument("--game-id", action="append", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    status, body = request(f"{base}/api/games")
    if status != 200 or not isinstance(body, dict):
        print(f"FAIL could not list games: {status} {body}")
        return 2
    games = body.get("games", [])
    if args.game_id:
        wanted = set(args.game_id)
        games = [game for game in games if game["id"] in wanted]

    analysed = [game for game in games if game.get("analysis_status") == "analyzed"]
    print(f"{len(analysed)} analysed game(s) of {len(games)} listed\n")

    pending: list[dict] = []
    for game in analysed:
        counts = candidate_counts(base, game["id"])
        if "error" in counts:
            print(f"SKIP {game['id'][:8]} — could not read analysis ({counts['error']})")
            continue
        if counts["branchable"] and counts["with_alternatives"] == 0:
            pending.append(game)
            print(
                f"NEEDS {game['id'][:8]} — {counts['plies']} stored plies, "
                f"{counts['branchable']} branchable moment(s), 0 with alternatives"
            )
        else:
            print(
                f"OK    {game['id'][:8]} — {counts['with_alternatives']}/"
                f"{counts['moments']} moment(s) already carry alternatives"
            )

    if not pending:
        print("\nNothing to re-analyse: every stored analysis already carries candidates.")
        return 0
    if args.dry_run:
        print(f"\nDry run: would re-analyse {len(pending)} game(s) at depth={args.depth} multipv={args.multipv}.")
        return 0

    failures = 0
    for game in pending:
        game_id = game["id"]
        print(f"\nRe-analysing {game_id[:8]} at depth={args.depth} multipv={args.multipv} ...")
        # ``resume: false`` is the whole point: with resume on, every ply is
        # already stored at this analysis version, the pipeline would skip the
        # engine entirely, and the candidates would still be missing. Only a real
        # fresh search produces them.
        status, response = request(
            f"{base}/api/analysis/games/{game_id}",
            {
                "depth": args.depth,
                "multipv": args.multipv,
                "resume": False,
            },
        )
        if status not in (200, 202):
            print(f"  FAIL could not start analysis: {status} {response}")
            failures += 1
            continue
        deadline = time.time() + 900
        final = None
        while time.time() < deadline:
            status, state = request(f"{base}/api/games/{game_id}/status")
            if status == 200 and isinstance(state, dict):
                final = state
                if state.get("analysis_status") in ("analyzed", "failed"):
                    break
            time.sleep(2)
        if not final or final.get("analysis_status") != "analyzed":
            print(f"  FAIL analysis did not complete: {final}")
            failures += 1
            continue
        counts = candidate_counts(base, game_id)
        ok = counts.get("with_alternatives", 0) > 0
        print(
            f"  {'OK  ' if ok else 'FAIL'} {counts.get('with_alternatives')}/"
            f"{counts.get('moments')} moment(s) now expose alternatives "
            f"({counts.get('plies')} plies, analysis_version={counts.get('analysis_version')})"
        )
        if not ok:
            failures += 1

    print(f"\nBackfill complete: {len(pending) - failures} game(s) updated, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
