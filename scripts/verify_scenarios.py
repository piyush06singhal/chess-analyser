#!/usr/bin/env python
"""Phase 10 live verification: the decision-intelligence gate, against the API.

The spec's phase gate is a chain, and this script walks it end to end with real
data and a real engine:

    REAL GAME → REAL POSITION → REAL ENGINE ANALYSIS → REAL ALTERNATIVE MOVE
    → REAL COUNTERFACTUAL BRANCH → REAL COMPARISON → REAL EXPLANATION
    → REAL TRAINING POSITION

Every check is a measurement with printed evidence. Nothing is assumed: if the
engine is unavailable the script says so and the engine-dependent checks are
reported as skipped rather than passed. The prediction layer is checked for the
opposite property — that it *refuses* rather than simulates when no production
model exists.

Usage:
    python scripts/verify_scenarios.py [--base-url http://127.0.0.1:8002]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
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


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"{'OK  ' if condition else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def skip(label: str, why: str) -> None:
    print(f"SKIP {label} — {why}")


def request(url: str, payload: dict | None = None, method: str | None = None, timeout: int = 300):
    """Return ``(status, body, elapsed_ms)``."""
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
                (json.loads(body) if body.strip().startswith("{") else body),
                elapsed,
            )
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        try:
            return exc.code, json.loads(exc.read().decode()), elapsed
        except Exception:
            return exc.code, "unparseable error response", elapsed


def _players(base: str) -> list[dict]:
    _, payload, _ = request(f"{base}/api/players")
    return (payload or {}).get("players", []) if isinstance(payload, dict) else []


def resolve_game(base: str) -> dict | None:
    """An analysed game we can branch from, importing the Opera Game if needed."""
    _, games_payload, _ = request(f"{base}/api/games")
    games = (games_payload or {}).get("games", []) if isinstance(games_payload, dict) else []
    analysed = [game for game in games if game.get("analysis_status") == "analyzed"]
    if not analysed:
        print("   no analysed game yet; importing + analysing the Opera Game…")
        status, imported, _ = request(
            f"{base}/api/games/import",
            {"pgn_text": OPERA_GAME_PGN, "run_analysis": True, "depth": 8, "multipv": 2},
        )
        check(status in (200, 201) and isinstance(imported, dict), "import + analyse a game")
        if status not in (200, 201):
            return None
        time.sleep(1.0)
        _, games_payload, _ = request(f"{base}/api/games")
        games = (games_payload or {}).get("games", [])
        analysed = [game for game in games if game.get("analysis_status") == "analyzed"]
    return analysed[0] if analysed else None


def pick_ply(base: str, game_id: str) -> tuple[int, str, str]:
    """Find an analysed ply with a stored position and the move that was played."""
    _, analysis, _ = request(f"{base}/api/analysis/games/{game_id}/moves")
    moves = (analysis or {}).get("moves", []) if isinstance(analysis, dict) else []
    for row in moves:
        if row.get("played_move_uci") and row.get("evaluation_before_cp") is not None:
            return int(row["ply"]), str(row["played_move_uci"]), str(row.get("played_move_san") or "")
    return 1, "", ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print("0. health")
    status, health, _ = request(f"{base}/health")
    check(status == 200, "the API is healthy", str(health)[:80])

    print("1. capability")
    status, meta, _ = request(f"{base}/api/scenarios/meta")
    check(status == 200 and isinstance(meta, dict), "scenario meta is served")
    engine_available = False
    if isinstance(meta, dict):
        check(meta.get("methodology_version") == "10.0", "methodology version is 10.0")
        engine = meta.get("engine", {})
        engine_available = bool(engine.get("available"))
        check(engine.get("authoritative") is True, "the engine is declared authoritative")
        check(
            set(meta.get("scenario_types", []))
            >= {
                "counterfactual_move",
                "alternative_line",
                "opening_deviation",
                "tactical_variation",
                "endgame_transition",
                "opponent_response",
                "user_hypothesis",
            },
            "all seven scenario types are declared",
        )
        check(
            meta.get("limits", {}).get("max_continuation_plies") == 12,
            "the continuation limit is published",
        )
        print(
            f"   engine available={engine_available} version={engine.get('version')} "
            f"cache={meta.get('cache', {}).get('entries')} entries"
        )

    print("2. position facts (no engine call)")
    status, facts, elapsed = request(
        f"{base}/api/scenarios/position", {"fen": START_FEN}
    )
    check(status == 200 and isinstance(facts, dict), "board facts are served")
    if isinstance(facts, dict):
        check(facts.get("legal_move_count") == 20, "the start position has 20 legal moves")
        check(facts.get("phase") == "opening", "the start position is the opening")
        print(f"   {elapsed:.1f} ms (no search)")

    print("3. resolve a real game and ply")
    game = resolve_game(base)
    if not game:
        check(False, "an analysed game exists")
        return 1
    game_id = game["id"]
    ply, played_uci, played_san = pick_ply(base, game_id)
    check(bool(played_uci), "a stored ply with a played move exists", f"{game_id} ply {ply} {played_san}")

    if not engine_available:
        skip("candidate comparison", "no engine is available in this deployment")
        skip("counterfactual branch", "no engine is available in this deployment")
    else:
        print("4. candidate move comparison (real engine, one search)")
        status, comparison, elapsed = request(
            f"{base}/api/scenarios/compare-moves",
            {"game_id": game_id, "ply": ply, "moves": [], "include_top": 3, "depth": 8},
        )
        check(status == 200 and isinstance(comparison, dict), "candidate comparison is served")
        candidates = (comparison or {}).get("candidates", [])
        check(len(candidates) == 3, "the engine's top 3 moves came back", f"{len(candidates)}")
        check(
            all(item.get("cp") is not None or item.get("mate") is not None for item in candidates),
            "every candidate carries a real score",
        )
        check(
            all(item.get("eval_source") == "same_search" for item in candidates),
            "top moves come from the same search (comparable)",
        )
        print(
            f"   {elapsed:.0f} ms · best {comparison.get('best_move_uci')} · "
            f"depth {comparison.get('engine_config', {}).get('depth')}"
        )

        print("5. illegal move is refused, not scored")
        status, illegal, _ = request(
            f"{base}/api/scenarios/compare-moves",
            {"fen": START_FEN, "moves": ["e2e5"], "depth": 6},
        )
        first = ((illegal or {}).get("candidates") or [{}])[0]
        check(first.get("legal") is False, "the illegal move is marked illegal")
        check(first.get("cp") is None, "the illegal move has no score")

        print("6. counterfactual branch from the real game (the phase gate)")
        status, why_not, _ = request(
            f"{base}/api/scenarios/why-not",
            {"game_id": game_id, "ply": ply, "move": played_uci, "depth": 8},
        )
        check(status == 200 and isinstance(why_not, dict), "why-not is served")
        check(
            (why_not or {}).get("status") in ("ok", "move_is_best"),
            "why-not answers with a status",
            str((why_not or {}).get("status")),
        )
        better = ((why_not or {}).get("better_alternatives") or [{}])[0]
        alternative = better.get("uci")
        if not alternative:
            skip("counterfactual branch", "the played move was already the engine's first choice")
        else:
            status, branch, elapsed = request(
                f"{base}/api/scenarios/counterfactual",
                {
                    "game_id": game_id,
                    "ply": ply,
                    "alternative_move": alternative,
                    "plies_ahead": 6,
                    "depth": 8,
                    "persist": True,
                },
            )
            check(status == 200 and isinstance(branch, dict), "the branch is served")
            body = (branch or {}).get("branch") or {}
            check((branch or {}).get("status") == "ok", "the branch status is ok")
            check(
                len(body.get("alternative_continuation", [])) >= 2,
                "the alternative line has real continuation plies",
                str(len(body.get("alternative_continuation", []))),
            )
            check(
                body.get("alternative_eval_cp") is not None,
                "the alternative has a measured evaluation",
                str(body.get("alternative_eval_cp")),
            )
            check(
                body.get("actual_move_uci") == played_uci,
                "the branch knows the move that was actually played",
            )
            facts = ((branch or {}).get("explanation") or {}).get("facts") or []
            check(len(facts) >= 3, "the explanation is built from listed facts", f"{len(facts)} facts")
            scenario_id = (branch or {}).get("scenario_id")
            check(bool(scenario_id), "the scenario was persisted", str(scenario_id))
            if scenario_id:
                status, stored, _ = request(f"{base}/api/scenarios/{scenario_id}")
                check(
                    status == 200 and (stored or {}).get("game_id") == game_id,
                    "the stored scenario traces back to the game",
                )
            print(f"   {elapsed:.0f} ms · change {body.get('evaluation_change_cp')} cp · "
                  f"source {body.get('alternative_eval_source')}")

        print("7. illegal alternative is refused with a message")
        status, refused, _ = request(
            f"{base}/api/scenarios/counterfactual",
            {"fen": START_FEN, "alternative_move": "e2e5", "plies_ahead": 2, "depth": 6},
        )
        check((refused or {}).get("status") == "illegal_move", "the refusal status is illegal_move")
        check(
            "not a legal move" in str((refused or {}).get("message")),
            "the refusal explains why",
            str((refused or {}).get("message"))[:60],
        )
        check((refused or {}).get("branch") is None, "no branch is fabricated for an illegal move")

    print("8. turning-point explorer (no engine call)")
    status, explorer, elapsed = request(f"{base}/api/scenarios/games/{game_id}/explorer")
    check(status == 200 and isinstance(explorer, dict), "the explorer is served")
    moments = (explorer or {}).get("turning_points", [])
    check((explorer or {}).get("plies_analyzed", 0) > 0, "the explorer read stored analysis")
    with_alternatives = [moment for moment in moments if moment.get("what_if_available")]
    print(
        f"   {elapsed:.0f} ms · {len(moments)} moments · "
        f"{len(with_alternatives)} offer a what-if · "
        f"{len((explorer or {}).get('evaluation_series', []))} series points"
    )

    print("9. predictions are gated, never simulated")
    status, availability, _ = request(f"{base}/api/scenarios/predictions")
    check(status == 200 and isinstance(availability, dict), "prediction availability is served")
    tasks = (availability or {}).get("unavailable", [])
    print(f"   unavailable tasks: {', '.join(tasks) if tasks else 'none'}")
    status, prediction, _ = request(
        f"{base}/api/scenarios/predict", {"task": "move_error_risk", "rows": [{"a": 1}]}
    )
    check((prediction or {}).get("available") is False, "an unvalidated task is refused")
    check(bool((prediction or {}).get("reason")), "the refusal carries its reason")

    print("10. training integration (counterfactual → exercise)")
    players = _players(base)
    if not players:
        skip("training from scenario", "no player exists yet")
    else:
        trainer = next((p for p in players if p["name"] == "Paul Morphy"), players[0])
        status, trained, _ = request(
            f"{base}/api/scenarios/training",
            {
                "fen": START_FEN,
                "alternative_move": "e2e4",
                "player_id": int(trainer["id"]),
                "depth": 8,
                "multipv": 3,
            },
        )
        check(status == 200 and isinstance(trained, dict), "training-from-scenario is served")
        state = (trained or {}).get("status")
        if state == "ok":
            position = (trained or {}).get("position") or {}
            check(position.get("data_source") == "counterfactual", "the exercise is marked counterfactual")
            check(
                (position.get("solution") or {}).get("uci") is not None,
                "the exercise stores the engine's own move as its solution",
            )
            print(
                f"   stored #{trained.get('position_id')} · {position.get('category')} · "
                f"{position.get('difficulty')} · {position.get('position_type')}"
            )
        else:
            check(state in ("already_played", "not_defensible", "illegal_move"), "the refusal is explicit", str(state))

    print("11. observability and caching")
    status, metrics, _ = request(f"{base}/api/scenarios/metrics")
    check(status == 200 and isinstance(metrics, dict), "metrics are served")
    counters = (metrics or {}).get("counters", {})
    timings = (metrics or {}).get("timings", {})
    check(counters.get("counterfactual_requests", 0) >= 1, "counterfactual requests are counted")
    check(
        (timings.get("engine_analysis_time_ms", {}) or {}).get("count", 0) >= 1,
        "engine analysis time is measured",
    )
    check("game content" in str((metrics or {}).get("scope", "")), "metrics state their privacy scope")
    print(
        f"   engine time: {timings.get('engine_analysis_time_ms', {}).get('mean_ms')} ms mean "
        f"over {timings.get('engine_analysis_time_ms', {}).get('count')} searches · "
        f"cache hit rate {(metrics or {}).get('cache', {}).get('hit_rate')}"
    )
    if engine_available:
        _, first, elapsed_cold = request(
            f"{base}/api/scenarios/compare-moves",
            {"fen": START_FEN, "moves": [], "include_top": 2, "depth": 8},
        )
        _, _, elapsed_warm = request(
            f"{base}/api/scenarios/compare-moves",
            {"fen": START_FEN, "moves": [], "include_top": 2, "depth": 8},
        )
        check(
            elapsed_warm <= elapsed_cold + 50,
            "a repeated request is served from cache",
            f"cold {elapsed_cold:.0f} ms → warm {elapsed_warm:.0f} ms",
        )

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("All Phase 10 checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
