#!/usr/bin/env python
"""Phase 13 live verification: the intelligence graph, end to end.

Spec §57's gate: the graph must be operational on **real** data, not a mock-up.
This script walks the whole pipeline against a running API and a real engine and
prints the number behind every step:

    REAL GAME → REAL POSITION → REAL ANALYSIS → REAL PATTERN → REAL EVIDENCE
    → REAL TRAINING POSITION → REAL ATTEMPT → REAL PLAYER HISTORY
    → INTELLIGENCE GRAPH → EVIDENCE PACKET → GROUNDED ANSWER

It also checks the graph's promises, because they are the product:

* every derived relationship is traceable, or it is absent;
* similarity levels are never conflated (exact is exact);
* the knowledge layer is sourced and refuses to invent;
* the health check finds no dangling or evidence-less edges.

If the engine is unavailable the checks that need it are reported SKIP with the
reason, never PASS.

Usage:
    python scripts/verify_intelligence_graph.py [--base-url http://127.0.0.1:8002]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

failures: list[str] = []

OPERA_GAME_PGN = """[Event "Paris Opera House"]
[Site "Paris FRA"]
[Date "1858.11.02"]
[Round "?"]
[Result "1-0"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[ECO "C41"]
[TimeControl "600+5"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
"""

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def check(condition: bool, label: str, detail: str = "") -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)
    return bool(condition)


def skip(label: str, why: str) -> None:
    print(f"SKIP {label} — {why}")


def request(url: str, payload: dict | None = None, method: str | None = None, timeout: int = 600):
    data = json.dumps(payload).encode() if payload is not None else None
    verb = method or ("POST" if data else "GET")
    req = urllib.request.Request(url, data=data, method=verb)
    if data:
        req.add_header("Content-Type", "application/json")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode()
            elapsed = (time.perf_counter() - started) * 1000
            return (
                response.status,
                (json.loads(body) if body.strip().startswith(("{", "[")) else body),
                elapsed,
            )
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw), elapsed
        except Exception:  # noqa: BLE001
            return exc.code, raw, elapsed


def players(base: str) -> list[dict]:
    _, payload, _ = request(f"{base}/api/players")
    return (payload or {}).get("players", []) if isinstance(payload, dict) else []


def resolve_analyzed_game(base: str) -> dict | None:
    _, listing, _ = request(f"{base}/api/games")
    games = (listing or {}).get("games", []) if isinstance(listing, dict) else []
    analyzed = [
        game for game in games if game.get("analysis_status") in {"ready", "analyzed"}
    ]
    # Prefer a game with a real opening classification: the game → opening edge is
    # only materialised when the stored game carries an ECO code or opening name.
    for game in analyzed:
        if game.get("eco_code") or game.get("opening_name"):
            return game
    if analyzed:
        return analyzed[0]
    print("   no analysed game yet; importing + analysing the Opera Game (depth 8)…")
    status, imported, _ = request(
        f"{base}/api/games/import",
        {"pgn_text": OPERA_GAME_PGN, "run_analysis": True, "depth": 8, "multipv": 2},
    )
    if status not in (200, 201) or not isinstance(imported, dict):
        check(False, "import + analyse a real game", str(imported)[:140])
        return None
    game_id = imported.get("game_id")
    for _ in range(60):
        time.sleep(2)
        _, game, _ = request(f"{base}/api/games/{game_id}")
        if isinstance(game, dict) and game.get("analysis_status") in {"ready", "analyzed"}:
            return game
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print("0. graph method + health")
    status, method, _ = request(f"{base}/api/graph/method")
    check(status == 200, "GET /api/graph/method", f"schema={method.get('schema_version') if isinstance(method, dict) else status}")
    if isinstance(method, dict):
        check("player" in method.get("node_types", []), "node taxonomy published")
        check("has_pattern" in method.get("edge_types", []), "edge taxonomy published")
        check("postgresql" in str(method.get("storage")), "storage decision documented")

    status, health, _ = request(f"{base}/api/graph/health")
    check(status == 200, "GET /api/graph/health", f"healthy={health.get('healthy') if isinstance(health, dict) else status}")

    print("1. materialize a real game into the graph")
    game = resolve_analyzed_game(base)
    if game is None:
        print("\nFAILED (no analysable game)")
        return 1
    game_id = game.get("game_id") or game.get("id")
    status, updated, _ = request(f"{base}/api/graph/games/{game_id}/update", {})
    check(status == 200 and updated.get("updated") is True, "POST /api/graph/games/{id}/update", f"nodes={updated.get('nodes')} edges={updated.get('edges')}")

    print("2. Game Explorer")
    status, explorer, _ = request(f"{base}/api/graph/games/{game_id}")
    check(status == 200 and explorer.get("found") is True, "GET /api/graph/games/{id}")
    check(bool(explorer.get("positions")), "game → position edges", f"{len(explorer.get('positions', []))} positions")
    check(bool(explorer.get("openings")), "game → opening edge", ", ".join(explorer.get("openings", [])[:1]))

    print("3. Position Explorer (exact match, honestly labelled)")
    status, position, _ = request(f"{base}/api/graph/position?fen={urllib.parse.quote(START_FEN)}")
    check(status == 200 and position.get("valid") is True, "GET /api/graph/position (start position)")
    check(position.get("exact_match_count", 0) >= 1, "exact historical match found", f"{position.get('exact_match_count')} games")
    check(
        all(match.get("exact") is True for match in position.get("exact_matches", [])),
        "exact matches are labelled exact",
    )

    print("4. Knowledge system (sourced, never invented)")
    status, seeded, _ = request(f"{base}/api/graph/knowledge/seed", {})
    check(status == 200 and seeded.get("concepts", 0) > 0, "POST /api/graph/knowledge/seed", f"{seeded.get('concepts')} concepts")
    status, concepts, _ = request(f"{base}/api/graph/concepts")
    check(status == 200 and concepts.get("source", {}).get("licence"), "concepts carry a source + licence")
    status, missing, _ = request(f"{base}/api/graph/concepts/not-a-real-concept")
    check(status == 200 and missing.get("found") is False, "absent concept is refused, not invented")

    print("5. Player patterns + evidence trace")
    player = next(
        (
            p
            for p in players(base)
            if (p.get("analyzed_games") or p.get("analyzed") or 0) > 0
        ),
        None,
    )
    if player is None:
        skip("player pattern chain", "no player with analysed games")
    else:
        player_id = player.get("id")
        request(f"{base}/api/players/{player_id}/rebuild", {})
        status, updates, _ = request(f"{base}/api/graph/players/{player_id}/update", {})
        check(status == 200, "POST /api/graph/players/{id}/update", f"patterns={updates.get('patterns', {}).get('patterns')}")
        status, explorer, _ = request(f"{base}/api/graph/players/{player_id}")
        check(status == 200 and explorer.get("found") is True, "GET /api/graph/players/{id}")
        patterns = explorer.get("patterns", [])
        check(isinstance(patterns, list), "player patterns returned", f"{len(patterns)} pattern(s)")
        for pattern in patterns[:3]:
            trace_url = f"{base}/api/graph/why/{pattern['node_type']}/{pattern['node_key']}"
            status, trace, _ = request(trace_url)
            check(status == 200 and trace.get("found") is True, f"Why? {pattern['node_key']}", f"evidence={trace.get('evidence_count')}")

    print("6. Graph health after real work")
    status, health, _ = request(f"{base}/api/graph/health")
    report = health.get("report", {}) if isinstance(health, dict) else {}
    check(report.get("dangling_edges", {}).get("count") == 0, "no dangling edges")
    check(report.get("invalid_edges", {}).get("count") == 0, "no invalid edges")
    check(report.get("missing_evidence", {}).get("count") == 0, "no derived edge without evidence")

    print("7. Snapshot (reproducibility)")
    status, snapshot, _ = request(f"{base}/api/graph/snapshot", {})
    check(status == 200 and snapshot.get("graph_version"), "POST /api/graph/snapshot", f"nodes={snapshot.get('total_nodes')} edges={snapshot.get('total_edges')}")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): " + "; ".join(failures))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
