#!/usr/bin/env python
"""Phase 4 verification: build and inspect a real GameReport.

Two modes:

* default (in-process): runs the real Stockfish pipeline over a complete game,
  then builds the intelligence report and writes it to
  ``data/processed/intelligence_report_<game>.json`` for inspection.
* ``--api``: drives a running API (import → analyze → report → tool endpoints)
  to verify persistence and the HTTP surface.

Examples::

    python scripts/verify_intelligence.py --depth 10 --multipv 3
    python scripts/verify_intelligence.py --api --base-url http://127.0.0.1:8002 --depth 8
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "argus"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

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


def _facts_from_analysis(analysis) -> list:
    from argus.intelligence import MoveFact

    return [
        MoveFact(
            ply=move.ply,
            move_number=move.move_number,
            mover=move.color,
            san=move.san,
            uci=move.uci,
            fen_before=move.fen_before,
            fen_after=move.fen_after,
            eval_before_cp=move.evaluation_before_cp,
            eval_before_mate=move.evaluation_before_mate,
            eval_after_cp=move.evaluation_after_cp,
            eval_after_mate=move.evaluation_after_mate,
            eval_change_cp=move.evaluation_change_cp,
            centipawn_loss=move.centipawn_loss,
            classification=move.classification,
            best_move_uci=move.best_move_uci,
            best_move_san=move.best_move_san,
            is_best_move=move.is_best_move,
            phase=move.phase,
            principal_variation=move.principal_variation,
            depth=move.depth,
        )
        for move in analysis.moves
    ]


def _context_from_game(game, game_id: str = "in-process"):
    from argus.intelligence import GameContext

    return GameContext(
        game_id=game_id,
        white_player=game.white_player.name,
        black_player=game.black_player.name,
        white_rating=game.white_rating,
        black_rating=game.black_rating,
        result=game.result.value,
        date=game.date,
        event=game.event,
        site=game.site,
        time_control=game.time_control.raw,
        eco_code=game.opening.eco_code,
        opening_name=game.opening.name,
        initial_position=game.initial_position,
        final_position=game.final_position,
        move_count=game.move_count,
    )


def in_process(depth: int, multipv: int, output: Path, pgn: str) -> int:
    from argus.analysis.engine.stockfish import StockfishEngine
    from argus.analysis.pipeline import GameAnalysisPipeline, build_analysis_config
    from argus.chess_core.pgn import parse_first_game
    from argus.intelligence import GameIntelligence

    game = parse_first_game(pgn)
    print(f"game: {game.white_player.name} vs {game.black_player.name} ({game.move_count} plies)")

    engine = StockfishEngine()
    config = build_analysis_config("standard", depth=depth, multipv=multipv)
    pipeline = GameAnalysisPipeline(engine)
    started = time.perf_counter()
    analysis = pipeline.analyze(game, config)
    analysis_seconds = time.perf_counter() - started
    print(
        f"engine: {engine.info().get('version')} | {config.label} | "
        f"{analysis_seconds:.1f}s ({analysis_seconds / max(1, len(analysis.moves)):.2f}s/position)"
    )

    moves = _facts_from_analysis(analysis)
    context = _context_from_game(game)
    intelligence = GameIntelligence(
        context,
        moves,
        analysis=None,
    )
    started = time.perf_counter()
    report = intelligence.build_report()
    report_seconds = time.perf_counter() - started
    print(
        f"intelligence layer: {report_seconds * 1000:.0f}ms for {len(moves)} plies "
        f"(engine calls made by the layer: {report.provenance.engine_calls_made_by_intelligence_layer})"
    )

    payload = report.model_dump(mode="json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2))
    print(f"report written to {output}")

    print("\n=== opening ===")
    print(
        json.dumps(
            {
                "identification": {
                    key: payload["opening"]["identification"][key]
                    for key in ("source", "name", "eco", "variation", "matched_plies", "header_agreement")
                },
                "deviation": {
                    key: payload["opening"]["deviation"][key]
                    for key in ("deviated", "move_number", "played_san", "expected_continuation_san")
                },
            },
            indent=2,
        )
    )

    print("\n=== phases ===")
    print("final:", payload["phases"]["final_phase"])
    for transition in payload["phases"]["transitions"]:
        print(f"  ply {transition['ply']}: {transition['from_phase']} -> {transition['to_phase']}")
    for side in ("white", "black"):
        for phase, stats in payload["phases"]["performance"][side].items():
            print(
                f"  {side:5s} {phase:11s} moves={stats['moves']:3d} cpl={stats['average_centipawn_loss']} "
                f"flagged={stats['problem_moves']} tactical={stats['tactical_events']} "
                f"small_sample={stats['small_sample']}"
            )

    print("\n=== trajectory ===")
    trajectory = payload["trajectory"]["trajectory"]
    print(
        f"evaluated={trajectory['evaluated_plies']} missing={trajectory['missing_plies']} "
        f"mate_plies={trajectory['mate_plies']} "
        f"range=[{trajectory['min_evaluation_white']}, {trajectory['max_evaluation_white']}] "
        f"final={trajectory['final_evaluation_display']}"
    )
    for event in trajectory["events"]:
        print(f"  ply {event['start_ply']}-{event['end_ply']}: {event['type']} ({event['side']})")
    for point in trajectory["points"]:
        if point["ply"] % 4 == 0 or point["ply"] == trajectory["points"][-1]["ply"]:
            print(
                f"  ply {point['ply']:3d} {point['evaluation_display']:>8s} "
                f"{point['white_state'] or 'unavailable'}"
            )

    print("\n=== accuracy ===")
    for side in ("white", "black"):
        row = payload["accuracy"]["analysis"][side]
        print(
            f"  {side:5s} accuracy={row['accuracy']} scored={row['scored_moves']} "
            f"excluded={row['excluded_decided_moves']} unscored={row['unscored_moves']} "
            f"raw_cpl={row['average_centipawn_loss']}"
        )

    print("\n=== turning points ===")
    for point in payload["turning_points"]["turning_points"]:
        print(f"  ply {point['ply']:3d} {point['type']:20s} {point['severity']:6s} {point['statement']}")

    print("\n=== critical-moment timeline ===")
    for entry in payload["critical_moments"]["timeline"]:
        print(f"  ply {entry['ply']:3d} [{entry['kind']}/{entry['certainty']}] {entry['label']}")

    print("\n=== material ===")
    material = payload["material"]["timeline"]
    print(
        f"  initial={material['initial_balance']} final={material['final_balance']} "
        f"captures={material['total_captures']} exchanges={material['exchanges']} "
        f"promotions={len(material['promotions'])} transitions={len(material['transitions'])}"
    )

    print("\n=== tactical events ===")
    print(f"  confirmed={payload['tactical']['analysis']['confirmed_count']} candidates={payload['tactical']['analysis']['candidate_count']}")
    for event in payload["tactical"]["analysis"]["events"]:
        print(f"  ply {event['ply']:3d} [{event['type']}/{event['certainty']}] {event['statement']}")

    print("\n=== positional (error candidates) ===")
    for event in payload["positional"]["analysis"]["events"]:
        if event["classification"] == "error_candidate":
            print(f"  ply {event['ply']:3d} {event['type']} :: {event['statement']}")

    print("\n=== error categories ===")
    print(" ", payload["error_categories"]["analysis"]["by_category"])

    print("\n=== key lessons ===")
    for lesson in payload["key_lessons"]:
        print(f"  [{lesson['source']}] {lesson['statement']}")

    print("\n=== training recommendations ===")
    for recommendation in payload["training_recommendations"]:
        print(
            f"  {recommendation['focus']} ({recommendation['observed_count']} obs, "
            f"plies {recommendation['evidence_refs'][:6]})"
        )

    print("\n=== unavailable / honesty notes ===")
    for item in payload["unavailable"]:
        print(f"  {item['section']}: {item['reason']}")

    _assert_invariants(payload)
    print("\ninvariants OK: every insight carries evidence, sources and certainty")
    return 0


def _assert_invariants(payload: dict) -> None:
    sources = {"engine_fact", "argus_derived_feature", "argus_interpretation"}
    for lesson in payload["key_lessons"]:
        assert lesson["source"] in sources, lesson
        assert lesson["statement"].strip()
    for point in payload["turning_points"]["turning_points"]:
        assert point["source"] in sources and point["evidence"]
        assert point["statement"].strip()
    for entry in payload["critical_moments"]["timeline"]:
        assert entry["source"] in sources and entry["kind"] and entry["ply"] >= 1
    for error in payload["error_categories"]["analysis"]["errors"]:
        assert error["basis"] and error["evidence"]
    for event in payload["tactical"]["analysis"]["events"]:
        assert event["certainty"] in {"confirmed", "candidate"}
    for point in payload["trajectory"]["trajectory"]["points"]:
        if point["mate_white"] is not None:
            assert point["evaluation_display"].startswith("#"), point
        if not point["available"]:
            assert point["evaluation_cp_white"] is None
    assert payload["provenance"]["engine_calls_made_by_intelligence_layer"] == 0
    assert "not Chess.com" in payload["accuracy"]["analysis"]["disclaimer"]


def api_mode(base_url: str, depth: int, multipv: int, pgn: str) -> int:
    import httpx

    with httpx.Client(base_url=base_url.rstrip("/"), timeout=900.0) as client:
        health = client.get("/health").json()
        print(f"engine={health['engine']['available']} db={health['database']['connected']}")

        game_id = client.post(
            "/api/games/import", json={"pgn_text": pgn, "run_analysis": False}
        ).json()["game_id"]
        print(f"imported game {game_id}")

        client.post(
            f"/api/analysis/games/{game_id}",
            json={"profile": "standard", "depth": depth, "multipv": multipv},
        )
        while True:
            progress = client.get(f"/api/analysis/games/{game_id}/progress").json()
            if progress["status"] != "running" and progress["analysis_status"] != "analyzing":
                break
            time.sleep(1.0)
        print(
            f"analysis {progress['status']} ({progress['engine_version']}) "
            f"{progress['positions_analyzed']}/{progress['total_positions']}"
        )

        started = time.perf_counter()
        report = client.post(f"/api/intelligence/games/{game_id}/report").json()
        print(
            f"report generated via API in {time.perf_counter() - started:.2f}s "
            f"[report v{report['report_version']}]"
        )

        stored = client.get(f"/api/intelligence/games/{game_id}/report").json()
        print(f"stored report: source={stored['source']} analysis_version={stored['analysis_version']}")

        status = client.get(f"/api/intelligence/games/{game_id}/report/status").json()
        print(f"status: has_report={status['has_report']} has_analysis={status['has_analysis']}")

        for path in (
            "summary",
            "trajectory",
            "critical-moments",
            "tactical-events",
            "positional-events",
            "phase-analysis",
            "material-timeline",
            "accuracy",
            "player-statistics",
            "tools",
        ):
            response = client.get(f"/api/intelligence/games/{game_id}/{path}")
            size = len(response.content)
            print(f"  GET {path:18s} {response.status_code} ({size} bytes)")

        positions = {position["ply"] for position in client.get(f"/api/games/{game_id}/positions").json()["positions"]}
        timeline = report["critical_moments"]["timeline"]
        missing = [entry["ply"] for entry in timeline if entry["ply"] not in positions]
        assert not missing, f"timeline plies without a stored position: {missing}"
        print(f"timeline: {len(timeline)} entries, all navigable")

        _assert_invariants(report)
        output = REPO_ROOT / "data" / "processed" / f"intelligence_report_api_{game_id[:8]}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2))
        print(f"API report written to {output}")
        print("\ninvariants OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", action="store_true", help="verify a running API instead of in-process")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--depth", type=int, default=10)
    parser.add_argument("--multipv", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "data" / "processed" / "intelligence_report_opera.json",
    )
    args = parser.parse_args()

    if args.api:
        return api_mode(args.base_url, args.depth, args.multipv, OPERA_GAME_PGN)
    return in_process(args.depth, args.multipv, args.output, OPERA_GAME_PGN)


if __name__ == "__main__":
    raise SystemExit(main())
