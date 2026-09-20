"""End-to-end game analysis verification: PGN → engine → features → report.

Uses the Opera Game (Morphy vs Duke Karl / Count Isouard, Paris 1858) — a real,
well-documented game — so results can be sanity-checked against known chess
knowledge (Black's rapid collapse, Morphy's queen sacrifices, Rd8# mate).

Usage:
    python scripts/verify_game_analysis.py [--depth 10]

Exit codes: 0 = verified, 1 = failed.
"""

from __future__ import annotations

import argparse
import sys

from argus.analysis.engine.stockfish import StockfishEngine
from argus.analysis.game_analyzer import GameAnalyzer
from argus.analysis.reports import build_report
from argus.chess_core.pgn import parse_first_game
from argus.shared.errors import ArgusError

OPERA_GAME_PGN = """[Event "Paris Opera House"]
[Site "Paris FRA"]
[Date "1858.11.02"]
[Round "?"]
[Result "1-0"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[ECO "C41"]
[TimeControl "-"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify end-to-end game analysis")
    parser.add_argument("--depth", type=int, default=10)
    args = parser.parse_args()

    try:
        game = parse_first_game(OPERA_GAME_PGN)
    except ArgusError as exc:
        print(f"FAIL: PGN parsing error: {exc}")
        return 1

    print(f"Parsed: {game.white_player.name} vs {game.black_player.name} "
          f"({game.result.value}, ECO {game.opening.eco_code}, {game.move_count} moves)")
    if game.move_count != 33:
        print(f"FAIL: expected 33 plies, got {game.move_count}")
        return 1

    engine = StockfishEngine()
    try:
        analyzer = GameAnalyzer(engine)
        analysis = analyzer.analyze(game, depth=args.depth, multipv=2)
    except ArgusError as exc:
        print(f"FAIL: analysis error: {exc}")
        return 1
    finally:
        engine.close()

    info = engine.info()
    report = build_report(
        analysis,
        game,
        engine="stockfish",
        engine_version=info.get("version"),
        depth=args.depth,
    )

    summary = report.summary
    print(f"Classified {summary.classified_moves}/{summary.total_moves} moves "
          f"(unclassified: {summary.unclassified_moves})")
    print(f"White avg CPL: {summary.white.average_centipawn_loss}, "
          f"Black avg CPL: {summary.black.average_centipawn_loss}")
    print(f"White counts: { {k.value: v for k, v in summary.white.counts.items()} }")
    print(f"Black counts: { {k.value: v for k, v in summary.black.counts.items()} }")
    print(f"Phases: { {k.value: v for k, v in summary.phase_move_counts.items()} }")
    if report.turning_point:
        print(f"Turning point: ply {report.turning_point.ply} — {report.turning_point.description}")
    print(f"Critical moments: {len(report.critical_moments)}")
    for moment in report.critical_moments[:5]:
        print(f"  ply {moment.ply}: {moment.description}")
    print(f"Best moves: {len(report.best_moves)}")
    print(f"Pending sections (honest, not implemented): {len(report.pending_sections)}")

    if summary.unclassified_moves == summary.total_moves:
        print("FAIL: no move could be classified — engine evidence missing")
        return 1
    if not report.turning_point:
        print("FAIL: no turning point found despite evaluations")
        return 1
    print("OK: end-to-end game analysis verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
