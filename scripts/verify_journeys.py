#!/usr/bin/env python
"""Live verification: the five user journeys, start to finish, as one sequence (§50).

The pipelines and the primary pages are each covered elsewhere. What was missing is
the *sequence*: a new user walks these five journeys in order, each beginning where
the last ended, and this script walks them against a running API and a real engine.

    1. Bring in a game          import a real PGN → Stockfish analyses it → status ready
    2. Read the report          context, provenance, accuracy, positions, forecast
    3. Train the weakness       generate exercises → attempt the best move → progress
    4. Prepare for the opponent opponent profile → preparation report → prep exercises
    5. Ask the coach            an evidence-grounded answer, and an honest refusal

Journey 1 prefers a game already stored (identified by its players) so re-runs do
not pile up duplicate imports; with an empty library it imports the Immortal Game
and analyses it. Nothing is fabricated: if the engine is unavailable the steps that
need it are reported as SKIP with the reason, never as PASS.

Usage:
    python scripts/verify_journeys.py [--base-url http://127.0.0.1:8002]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

failures: list[str] = []

#: Anderssen–Kieseritzky, London 1851 (the Immortal Game) — a real, complete game.
IMMORTAL_GAME_PGN = """[Event "London"]
[Site "London ENG"]
[Date "1851.06.21"]
[Round "?"]
[Result "1-0"]
[White "Adolf Anderssen"]
[Black "Lionel Kieseritzky"]
[ECO "C33"]
[TimeControl "600+5"]

1. e4 e5 2. f4 exf4 3. Bc4 Qh4+ 4. Kf1 b5 5. Bxb5 Nf6 6. Nf3 Qh6 7. d3 Nh5
8. Nh4 Qg5 9. Nf5 c6 10. g4 Nf6 11. Rg1 cxb5 12. h4 Qg6 13. h5 Qg5 14. Qf3 Ng8
15. Bxf4 Qf6 16. Nc3 Bc5 17. Nd5 Qxb2 18. Bd6 Bxg1 19. e5 Qxa1+ 20. Ke2 Na6
21. Nxg7+ Kd8 22. Qf6+ Nxf6 23. Be7# 1-0
"""


def check(condition: bool, label: str, detail: str = "") -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)
    return bool(condition)


def skip(label: str, why: str) -> None:
    print(f"SKIP {label} — {why}")


def request(url: str, payload: dict | None = None, method: str | None = None, timeout: int = 600):
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


def games(base: str) -> list[dict]:
    _, payload, _ = request(f"{base}/api/games")
    return (payload or {}).get("games", []) if isinstance(payload, dict) else []


def players(base: str) -> list[dict]:
    _, payload, _ = request(f"{base}/api/players")
    return (payload or {}).get("players", []) if isinstance(payload, dict) else []


def _has_readable_analysis(base: str, game_id: str) -> bool:
    """True only when the stored analysis a reader would resolve actually covers plies."""
    _, payload, _ = request(f"{base}/api/analysis/games/{game_id}/moves")
    return bool(
        isinstance(payload, dict)
        and payload.get("count")
        and payload.get("analysis_complete") is not False
    )


def journey_1_import(base: str, engine_available: bool) -> dict | None:
    """Journey 1 — bring in a game and let the engine analyse it."""
    print("\nJOURNEY 1 — Bring in a game")
    existing = next(
        (
            game
            for game in games(base)
            if game.get("white_player") == "Adolf Anderssen"
            # A fully-played game, not a one-ply live-game stub: the report must
            # measure accuracy for *both* sides and the training journey must have
            # moves to derive exercises from. Picking the newest analysed game
            # alone found a 1-move live game and failed two checks for a data
            # reason rather than a product one.
            and (game.get("move_count") or 0) >= 20
            and game.get("analysis_status") in {"ready", "analyzed"}
            and _has_readable_analysis(base, game["id"])
        ),
        None,
    )
    if existing:
        print("   reusing the stored Immortal Game (already analysed)")
        check(True, "a real game is in the library", f"{existing['white_player']} vs {existing['black_player']}")
        return existing

    if not engine_available:
        skip("import + analyse a real game", "the engine is unavailable")
        return None

    print("   importing + analysing the Immortal Game (depth 8)…")
    status, imported, _ = request(
        f"{base}/api/games/import",
        {"pgn_text": IMMORTAL_GAME_PGN, "run_analysis": True, "depth": 8, "multipv": 3},
    )
    if status not in (200, 201) or not isinstance(imported, dict) or not imported.get("game_id"):
        check(False, "the PGN is accepted", str(imported)[:140])
        return None
    game_id = imported["game_id"]
    check(bool(imported.get("moves")), "the game's moves are parsed", f"{imported.get('moves')} plies")

    game: dict | None = None
    for _ in range(90):
        time.sleep(2)
        _, payload, _ = request(f"{base}/api/games/{game_id}")
        if isinstance(payload, dict) and payload.get("analysis_status") in {"ready", "analyzed"}:
            game = payload
            break
    if not game:
        check(False, "the engine finishes analysing the game", "still not ready after ~3 minutes")
        return None
    check(
        game.get("analysis_status") in {"ready", "analyzed"},
        "the engine finishes analysing the game",
        f"status={game.get('analysis_status')}",
    )
    return game


def journey_2_report(base: str, game: dict) -> dict | None:
    """Journey 2 — read the report and follow it down to the board."""
    print("\nJOURNEY 2 — Read the report")
    game_id = game["id"]
    status, report, _ = request(f"{base}/api/intelligence/games/{game_id}/report")
    if status == 404:
        status, report, _ = request(f"{base}/api/intelligence/games/{game_id}/report", {})
    inner = (report or {}).get("report") if isinstance(report, dict) else None
    if not check(status == 200 and isinstance(inner, dict), "the report is materialised", f"HTTP {status}"):
        return None

    context = inner.get("context") or {}
    check(
        bool(context.get("white_player") and context.get("black_player")),
        "the report names the players",
        f"{context.get('white_player')} vs {context.get('black_player')}",
    )
    provenance = inner.get("provenance") or {}
    check(
        bool(provenance.get("engine")) and provenance.get("positions_analyzed") is not None,
        "the report carries its provenance",
        f"engine={provenance.get('engine')} positions={provenance.get('positions_analyzed')}",
    )
    accuracy = ((inner.get("accuracy") or {}).get("analysis")) or {}
    white = accuracy.get("white") or {}
    black = accuracy.get("black") or {}
    check(
        white.get("accuracy") is not None and black.get("accuracy") is not None,
        "accuracy is measured for both sides",
        f"white={white.get('accuracy')} black={black.get('accuracy')}",
    )

    status, moves_payload, _ = request(f"{base}/api/analysis/games/{game_id}/moves")
    moves = (moves_payload or {}).get("moves", []) if isinstance(moves_payload, dict) else []
    scored = [row for row in moves if row.get("evaluation_before_cp") is not None]
    check(bool(scored), "stored per-move engine evaluations exist", f"{len(scored)}/{len(moves)} plies")
    check(
        isinstance(moves_payload, dict) and moves_payload.get("analysis_complete") is True,
        "the served analysis covers every ply",
        f"analysis_version={moves_payload.get('analysis_version') if isinstance(moves_payload, dict) else None}",
    )

    # The board must agree with the game's own move list, ply by ply.
    _, positions, _ = request(f"{base}/api/games/{game_id}/positions?limit=400")
    published = {
        int(entry["ply"]): entry
        for entry in ((positions or {}).get("positions") or [])
        if entry.get("ply")
    }
    check(bool(published), "every ply resolves to a position", f"{len(published)} positions")
    disagreements = [
        row["ply"]
        for row in scored
        if row.get("ply") in published
        and published[row["ply"]].get("uci")
        and published[row["ply"]]["uci"] != row.get("played_move_uci")
    ]
    check(
        not disagreements,
        "the stored analysis agrees with the game's own moves at every ply",
        f"{len(disagreements)} disagree" if disagreements else f"{len(scored)} plies agree",
    )

    if inner.get("forecast"):
        forecast = inner["forecast"].get("forecast") or {}
        check(
            bool(forecast.get("methodology")) and "not a trained" in (forecast.get("disclaimer") or "").lower(),
            "the outcome forecast states it is not a trained model",
        )
    else:
        skip("outcome forecast", "this report carries none")
    return inner


def journey_3_train(base: str, game: dict, report: dict | None) -> None:
    """Journey 3 — turn a stored weakness into an exercise and attempt it."""
    print("\nJOURNEY 3 — Train the weakness")
    game_id = game["id"]
    white = next((p for p in players(base) if p["name"] == game.get("white_player")), None)
    if white is None:
        check(False, "the game's player is tracked")
        return
    player_id = str(white["id"])

    status, generated, _ = request(
        f"{base}/api/training/games/{game_id}/generate",
        {"player_id": player_id, "data_source": "personalized"},
    )
    created = (generated or {}).get("created") if isinstance(generated, dict) else None
    if status != 200:
        check(False, "exercises are generated from the game", str(generated)[:140])
    elif created:
        check(True, "exercises are generated from the game", f"{created} created")
    else:
        _, existing, _ = request(f"{base}/api/training/games/{game_id}?player_id={player_id}")
        count = (existing or {}).get("count") if isinstance(existing, dict) else 0
        check(count > 0, "exercises exist for this game", f"{count} already stored")

    _, library, _ = request(f"{base}/api/training/positions?player_id={player_id}&limit=50")
    positions = (library or {}).get("positions", []) if isinstance(library, dict) else []
    if not check(bool(positions), "the player's exercise library is served", f"{len(positions)} positions"):
        return

    # Find an exercise with a stored solution so the attempt is genuinely graded.
    position_id = positions[0]["id"]
    best = None
    for candidate in positions:
        status, solution, _ = request(f"{base}/api/training/positions/{candidate['id']}/solution")
        if isinstance(solution, dict):
            nested = solution.get("solution") or {}
            best = (
                nested.get("uci")
                or nested.get("best_move_uci")
                or solution.get("best_move_uci")
                or solution.get("solution_uci")
                or next(iter(solution.get("continuation_line") or []), None)
            )
        if best:
            position_id = candidate["id"]
            break
    if not best:
        skip("submit a graded attempt", "no exercise in the library carries a stored solution")
        return

    status, attempt, _ = request(
        f"{base}/api/training/positions/{position_id}/attempt",
        {"player_id": player_id, "submitted_uci": best, "response_time_ms": 4200, "hints_used": 0},
    )
    check(status == 200, "the attempt is graded and stored", f"HTTP {status}")
    check(
        (attempt or {}).get("correct") is True,
        "the engine-verified best move grades as correct",
        f"outcome={(attempt or {}).get('outcome')}",
    )
    _, progress, _ = request(f"{base}/api/training/progress?player_id={player_id}")
    check(isinstance(progress, dict), "measured training progress is served")


def journey_4_opponent(base: str, game: dict) -> None:
    """Journey 4 — prepare for the opponent from their own stored games."""
    print("\nJOURNEY 4 — Prepare for the opponent")
    black = next((p for p in players(base) if p["name"] == game.get("black_player")), None)
    if black is None:
        skip("opponent preparation", "the opponent is not tracked as a player")
        return
    opponent_id = str(black["id"])
    status, profile, _ = request(f"{base}/api/players/{opponent_id}/opponent-profile")
    if status != 200:
        status, profile, _ = request(f"{base}/api/players/{opponent_id}/opponent-profile/rebuild", {})
    check(status == 200, "the opponent profile is built", f"HTTP {status}")

    status, brief, _ = request(f"{base}/api/players/{opponent_id}/preparation-report")
    check(status == 200, "the preparation report is served", f"HTTP {status}")
    if isinstance(brief, dict):
        unavailable = brief.get("unavailable_sections") or brief.get("unavailable") or []
        check(
            all(item.get("reason") for item in unavailable if isinstance(item, dict)),
            "every unavailable preparation section names its reason",
            f"{len(unavailable)} unavailable",
        )

    white = next((p for p in players(base) if p["name"] == game.get("white_player")), None)
    if white is None:
        skip("opponent-specific exercises", "the preparing player is not tracked")
        return
    status, prep, _ = request(
        f"{base}/api/training/opponents/{opponent_id}/prepare",
        {"player_id": str(white["id"]), "min_occurrences": 1, "max_exercises": 10},
    )
    check(status == 200, "opponent-specific exercises can be generated", f"HTTP {status}")


def journey_5_coach(base: str, game: dict) -> None:
    """Journey 5 — ask the coach, and check it refuses what it cannot support."""
    print("\nJOURNEY 5 — Ask the coach")
    payload = {
        "question": "Why did Black's position collapse in this game?",
        "game_id": game["id"],
        "include_evidence": True,
    }
    status, answer, _ = request(f"{base}/api/coach/ask", payload)
    if not check(status == 200 and isinstance(answer, dict), "the coach answers", f"HTTP {status}"):
        return
    check(
        answer.get("mode") in {"deterministic", "coach", "llm", "generated", "echo"},
        "the answer states how it was produced",
        f"mode={answer.get('mode')}",
    )
    evidence = answer.get("evidence") or {}
    check(
        isinstance(evidence.get("items"), list),
        "the answer carries the evidence it retrieved",
        f"{len(evidence.get('items') or [])} items",
    )

    # The coach must not emit a probability it has no model for (§44/privacy).
    status, prediction, _ = request(
        f"{base}/api/coach/ask",
        {"question": "What is the exact probability I win my next game?", "include_evidence": True},
    )
    if check(status == 200 and isinstance(prediction, dict), "the coach answers the prediction question too", f"HTTP {status}"):
        text = (prediction.get("message") or "").lower()
        check(
            "cannot" in text or "no model" in text or "not available" in text or "unavailable" in text,
            "it refuses a probability it has no model for",
            (prediction.get("message") or "")[:110],
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    status, health, _ = request(f"{base}/health")
    engine_available = bool(isinstance(health, dict) and health.get("engine", {}).get("available"))
    if not check(status == 200, "the API is healthy", str(health)[:70] if status == 200 else "unreachable"):
        print("\nThe API is not reachable; the journeys cannot be walked.")
        return 1

    game = journey_1_import(base, engine_available)
    if not game:
        print("\nNo game could be established; the sequence stops honestly at journey 1.")
        return 1
    report = journey_2_report(base, game)
    journey_3_train(base, game, report)
    journey_4_opponent(base, game)
    journey_5_coach(base, game)

    print("\n" + "=" * 62)
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
        return 1
    print("ALL JOURNEYS COMPLETED — the five user journeys run in sequence on real data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
