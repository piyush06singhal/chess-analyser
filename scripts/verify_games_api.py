#!/usr/bin/env python
"""Live verification of the Phase 2 game-import pipeline against a running API.

Exercises: health, structured validation, import, positions, status, library,
file upload, and delete — using a real game (no fabricated data). Run the API
first (local uvicorn or docker compose), then:

    python scripts/verify_games_api.py [--base-url http://127.0.0.1:8002]
"""

from __future__ import annotations

import argparse
import io
import sys

import httpx

OPERA_GAME_PGN = """[Event "Paris Opera House"]
[Site "Paris FRA"]
[Date "1858.11.02"]
[Result "1-0"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[ECO "C41"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
"""

ILLEGAL_PGN = "1. e4 e5 2. Nf3 Nc6 3. Nf6 *"

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "OK  " if condition else "FAIL"
    print(f"[{status}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    with httpx.Client(base_url=base, timeout=60.0) as client:
        health = client.get("/health").json()
        check(
            "health: engine/db/redis connected",
            health["engine"]["available"] and health["database"]["connected"],
            f"engine={health['engine']['available']} db={health['database']['connected']}",
        )

        valid = client.post("/api/games/validate", json={"pgn_text": OPERA_GAME_PGN}).json()
        check("validate: opera game is valid", valid["is_valid"] and valid["ply_count"] == 33)

        invalid = client.post("/api/games/validate", json={"pgn_text": ILLEGAL_PGN}).json()
        check(
            "validate: illegal move reports move number",
            (not invalid["is_valid"]) and invalid["issues"][0]["move_number"] == 3,
        )

        imported = client.post(
            "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
        ).json()
        game_id = imported["game_id"]
        check("import: stored with ready status", imported["analysis_status"] == "ready")

        positions = client.get(f"/api/games/{game_id}/positions").json()
        check("positions: moves + 1 entries", positions["count"] == 34)
        check("positions: ply 0 is the start", positions["positions"][0]["san"] is None)
        check(
            "positions: final is checkmate",
            positions["positions"][-1]["is_checkmate"]
            and positions["positions"][-1]["terminal_reason"] == "checkmate",
        )

        status = client.get(f"/api/games/{game_id}/status").json()
        check("status: ready, nothing analyzed", status["analysis_status"] == "ready"
              and status["positions_analyzed"] == 0)

        library = client.get("/api/games").json()
        check("library: game listed", library["count"] >= 1)

        upload = client.post(
            "/api/games/import/file",
            params={"run_analysis": "false"},
            files={"file": ("game.pgn", io.BytesIO(OPERA_GAME_PGN.encode()), "application/x-chess-pgn")},
        )
        check("upload: pgn file accepted", upload.status_code == 200 and upload.json()["source"] == "pgn_file")

        bad_upload = client.post(
            "/api/games/import/file",
            params={"run_analysis": "false"},
            files={"file": ("evil.exe", io.BytesIO(b"x"), "application/octet-stream")},
        )
        check("upload: wrong extension rejected", bad_upload.status_code == 422)

        deleted = client.delete(f"/api/games/{game_id}")
        check("delete: 204", deleted.status_code == 204)
        check("delete: game gone", client.get(f"/api/games/{game_id}").status_code == 404)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("All game-pipeline checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
