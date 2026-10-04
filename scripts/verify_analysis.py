#!/usr/bin/env python
"""Live Phase 3 verification: full-game analysis + performance baseline.

Imports a real game, runs the Stockfish analysis pipeline against a running
API, polls progress, and reports stored records and timings.

    python scripts/verify_analysis.py --base-url http://127.0.0.1:8002 --depth 12
"""

from __future__ import annotations

import argparse
import sys
import time

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--depth", type=int, default=12)
    parser.add_argument("--multipv", type=int, default=3)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=300.0) as client:
        health = client.get("/health").json()
        print(f"engine={health['engine']['available']} db={health['database']['connected']}")

        game_id = client.post(
            "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
        ).json()["game_id"]
        print(f"imported game {game_id}")

        start = client.post(
            f"/api/analysis/games/{game_id}",
            json={"profile": "standard", "depth": args.depth, "multipv": args.multipv},
        ).json()
        print(f"analysis started: {start['config_label']}")
        started = time.perf_counter()

        while True:
            progress = client.get(f"/api/analysis/games/{game_id}/progress").json()
            running = (
                progress["status"] == "running"
                or progress["analysis_status"] == "analyzing"
            )
            if not running:
                break
            print(
                f"  progress {progress['current_position']}/{progress['total_positions']}"
                f" ({time.perf_counter() - started:.1f}s)",
                flush=True,
            )
            time.sleep(2)
        elapsed = time.perf_counter() - started

        moves = client.get(f"/api/analysis/games/{game_id}/moves").json()
        full = client.get(f"/api/analysis/games/{game_id}").json()
        criticals = client.get(f"/api/analysis/games/{game_id}/critical-moments").json()

        n = moves["count"]
        print("\n=== Results ===")
        print(f"status            : {progress['status']}")
        print(f"positions analyzed: {n} / {progress['total_positions']}")
        print(f"total time        : {elapsed:.1f}s")
        print(f"per position      : {elapsed / max(n, 1):.2f}s")
        print(f"engine            : {progress.get('engine_version')}")
        print(f"classification    : {full['classification_counts']}")
        print(f"critical moments  : {criticals['count']}")

        print("\n=== Sample moves ===")
        for row in moves["moves"][:4]:
            print(
                f"  ply {row['ply']:2d} {row['played_move_san']:6s} "
                f"cpl={row['centipawn_loss']} cls={row['classification']} "
                f"best={row['best_move_san']} depth={row['depth']} "
                f"pv={' '.join(row['principal_variation'][:4])}"
            )

        if not moves["moves"] or n != progress["total_positions"]:
            print("\nFAILED: analysis did not complete fully")
            return 1
        print("\nAnalysis complete.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
