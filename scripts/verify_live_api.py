"""Live end-to-end API test: health → import+analyze → fetch stored analysis.

Run against a live server:
    python scripts/verify_live_api.py [--base-url http://127.0.0.1:8001]

Exit codes: 0 = all live checks passed, 1 = failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

OPERA_GAME_PGN = """[Event "Paris Opera House"]
[Site "Paris FRA"]
[Date "1858.11.02"]
[Round "?"]
[Result "1-0"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[ECO "C41"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
"""

BAD_PGN = "1. e4 e5 2. d4 Nf3 3. Nc3 Bc4 [[[broken"


def _request(url: str, payload: dict | None = None) -> tuple[int, dict | str]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode()
            return response.status, json.loads(body) if body.strip().startswith("{") else body
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, "unparseable error response"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    status, health = _request(f"{base}/health")
    print(f"1. GET /health -> {status}")
    if status != 200 or not isinstance(health, dict):
        print("FAIL: health check failed")
        return 1
    engine_ok = health.get("engine", {}).get("available")
    db_ok = health.get("database", {}).get("connected")
    print(f"   engine available: {engine_ok}, database connected: {db_ok}")
    if not engine_ok:
        print("FAIL: engine unavailable on live server")
        return 1

    status, position = _request(f"{base}/api/analysis/position", {"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "depth": 8, "multipv": 2})
    print(f"2. POST /api/analysis/position -> {status}")
    if status != 200 or not isinstance(position, dict):
        print(f"FAIL: position analysis failed: {position}")
        return 1
    print(f"   best move: {position.get('best_move_san')} ({position.get('best_move_uci')}), "
          f"lines: {len(position.get('lines', []))}")

    status, invalid = _request(f"{base}/api/analysis/position", {"fen": "not-a-fen"})
    print(f"3. POST /api/analysis/position (invalid FEN) -> {status}")
    if status == 200:
        print("FAIL: invalid FEN was accepted")
        return 1
    error = invalid.get("error") if isinstance(invalid, dict) else None
    print(f"   error code: {error.get('code') if isinstance(error, dict) else invalid}")

    status, bad = _request(f"{base}/api/games/validate", {"pgn_text": BAD_PGN})
    print(f"4. POST /api/games/validate (invalid PGN) -> {status}")
    if status != 200 or not isinstance(bad, dict) or bad.get("is_valid"):
        print(f"FAIL: invalid PGN not rejected properly: {bad}")
        return 1
    print(f"   is_valid: {bad.get('is_valid')}, errors: {len(bad.get('errors', []))}")

    status, imported = _request(
        f"{base}/api/games/import",
        {"pgn_text": OPERA_GAME_PGN, "run_analysis": True, "depth": 10, "multipv": 2},
    )
    print(f"5. POST /api/games/import (analyze) -> {status}")
    if status not in (200, 201) or not isinstance(imported, dict):
        print(f"FAIL: import failed: {imported}")
        return 1
    game_id = imported.get("game_id")
    summary = (imported.get("analysis") or {}).get("summary") or {}
    print(f"   game_id: {game_id}, moves: {imported.get('moves')}, analyzed: {imported.get('analyzed')}")
    print(f"   white avg CPL: {(summary.get('white') or {}).get('average_centipawn_loss')}, "
          f"black avg CPL: {(summary.get('black') or {}).get('average_centipawn_loss')}")
    if not game_id or imported.get("moves") != 33 or not imported.get("analyzed"):
        print("FAIL: unexpected import result")
        return 1

    status, stored = _request(f"{base}/api/games/{game_id}")
    print(f"6. GET /api/games/{{id}} -> {status}")
    if status != 200 or not isinstance(stored, dict):
        print(f"FAIL: fetch game failed: {stored}")
        return 1
    print(f"   players: {stored.get('white_player')} vs {stored.get('black_player')}, "
          f"moves stored: {len(stored.get('moves') or [])}")

    status, analysis = _request(f"{base}/api/analysis/{game_id}")
    print(f"7. GET /api/analysis/{{id}} -> {status}")
    if status != 200 or not isinstance(analysis, dict):
        print(f"FAIL: fetch analysis failed: {analysis}")
        return 1
    rows = analysis.get("analyses") or []
    classified = sum(1 for row in rows if row.get("classification"))
    # Color is derivable from ply parity (odd = white) — PositionAnalysis rows
    # intentionally do not duplicate it.
    colors = {"white" if (row.get("ply") or 0) % 2 == 1 else "black" for row in rows}
    print(f"   stored rows: {len(rows)}, classified: {classified}, colors present: {sorted(colors)}")
    if len(rows) != 33 or classified == 0:
        print("FAIL: stored analysis incomplete")
        return 1

    print("OK: live end-to-end API verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
