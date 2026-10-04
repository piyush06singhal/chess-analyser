#!/usr/bin/env python
"""Live verification for the Phase 8 personalized training engine.

Run against a live server::

    python scripts/verify_training.py [--base-url http://127.0.0.1:8002]

It walks the full loop through the real HTTP surface — no mocks:

    import/analyze a game → generate exercises → browse the library → hints →
    solve incorrectly → solve correctly → reveal → review queue → progress →
    recommendations → session lifecycle → agent tools.

Every assertion is against the API's own response. If the library has no analysed
game, the script imports and analyses the Opera Game first (real Stockfish). If a
scenario cannot be exercised (e.g. only one player, so a foreign-owner check has
no counterpart), the script says so rather than pretending it passed.

Exit codes: 0 = verified, 1 = failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

failures: list[str] = []

OPERA_GAME_PGN = """[Event "Paris Opera House"]
[Site "Paris FRA"]
[Date "1858.11.02"]
[Result "1-0"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[TimeControl "600+5"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
"""


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"{'OK  ' if condition else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


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
            return response.status, (json.loads(body) if body.strip().startswith("{") else body), elapsed
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        try:
            return exc.code, json.loads(exc.read().decode()), elapsed
        except Exception:
            return exc.code, "unparseable error response", elapsed


def _players(base: str) -> list[dict]:
    _, payload, _ = request(f"{base}/api/players")
    return (payload or {}).get("players", []) if isinstance(payload, dict) else []


def resolve_game_and_player(base: str) -> tuple[str | None, str | None]:
    """An analyzed game AND a tracked player who actually played in it.

    Training is generated for a side; picking an arbitrary player would make the
    generator (correctly) refuse. So the game is chosen to match a real player.
    """
    by_name = {player["name"]: str(player["id"]) for player in _players(base)}
    _, games_payload, _ = request(f"{base}/api/games")
    games = (games_payload or {}).get("games", []) if isinstance(games_payload, dict) else []
    for game in games:
        if game.get("analysis_status") != "analyzed":
            continue
        _, detail, _ = request(f"{base}/api/games/{game['id']}")
        if not isinstance(detail, dict):
            continue
        for name in (detail.get("white_player"), detail.get("black_player")):
            if name in by_name:
                print(f"   using analysed game {game['id']} with player {name!r}")
                return game["id"], by_name[name]
    # Nothing matched: import and analyse the Opera Game so a real player exists.
    print("   no analysed game with a tracked player; importing + analysing the Opera Game…")
    status, imported, _ = request(
        f"{base}/api/games/import",
        {"pgn_text": OPERA_GAME_PGN, "run_analysis": True, "depth": 8, "multipv": 2},
    )
    check(status in (200, 201) and isinstance(imported, dict), "import + analyse a game")
    if status not in (200, 201) or not isinstance(imported, dict):
        return None, None
    by_name = {player["name"]: str(player["id"]) for player in _players(base)}
    return imported.get("game_id"), by_name.get("Paul Morphy")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print("1. meta")
    status, meta, _ = request(f"{base}/api/training/meta")
    check(status == 200 and isinstance(meta, dict), "training meta is served")
    if isinstance(meta, dict):
        check(meta.get("methodology_version") == "8.2", "methodology version is 8.2")
        check(len(meta.get("session_kinds", [])) == 7, "seven session kinds are declared")

    print("2. analysed game + tracked player")
    game_id, player_id = resolve_game_and_player(base)
    check(bool(game_id), "an analysed game is available")
    check(bool(player_id), f"resolved a player who played in it ({player_id})")
    if not game_id or not player_id:
        return 1

    print("3. generate exercises")
    status, generated, _ = request(
        f"{base}/api/training/games/{game_id}/generate",
        {"player_id": player_id},
    )
    check(status == 200 and isinstance(generated, dict), "generation endpoint answers")
    accepted = (generated or {}).get("accepted", 0)
    print(f"   accepted={accepted} rejected={(generated or {}).get('rejected')} seen={(generated or {}).get('seen')}")
    if accepted == 0:
        print("   (no exercise qualified from this game; not a failure, but the solve loop is skipped)")
        _agent_and_empty(base, player_id)
        return 1 if failures else 0

    position = generated["generated"][0]
    position_id = position["id"]
    check(position.get("origin", {}).get("game_id") == game_id, "exercise traces to its source game")
    check("solution" in position, "generation response carries the verified solution")

    print("4. library + solution withheld")
    _, library, _ = request(f"{base}/api/training/positions?player_id={player_id}&limit=20")
    check((library or {}).get("count", 0) >= 1, "library lists the exercise")
    status, detail, _ = request(f"{base}/api/training/positions/{position_id}?player_id={player_id}")
    check(status == 200 and "solution" not in (detail if isinstance(detail, dict) else {}), "position view withholds the solution")

    print("5. hints")
    status, hints, _ = request(f"{base}/api/training/positions/{position_id}/hints?hint_index=1")
    check(status == 200 and isinstance(hints, dict), "hints are served")
    check(len((hints or {}).get("revealed", [])) == 1, "hint index reveals exactly one hint")

    print("6. reveal")
    _, revealed, _ = request(f"{base}/api/training/positions/{position_id}/solution")
    solution = (revealed or {}).get("solution", {})
    check(bool(solution.get("uci")), "solution is revealed on request")

    print("7. solve incorrectly")
    wrong = (revealed or {}).get("played_move", {}).get("uci")
    if wrong and wrong != solution.get("uci"):
        status, feedback, _ = request(
            f"{base}/api/training/positions/{position_id}/attempt",
            {"player_id": player_id, "submitted_uci": wrong},
        )
        check(status == 200 and feedback.get("outcome") in ("incorrect", "near_best"), f"wrong move graded ({feedback.get('outcome')})")
        check(bool(feedback.get("next_review_at")), "a review is scheduled after the attempt")
    else:
        print("   (no distinct played move recorded; skipping the incorrect-attempt check)")

    print("8. solve correctly")
    status, feedback, _ = request(
        f"{base}/api/training/positions/{position_id}/attempt",
        {"player_id": player_id, "submitted_uci": solution["uci"]},
    )
    check(status == 200 and feedback.get("outcome") == "correct", "correct move graded correct")
    check(feedback.get("state") in ("learning", "review", "mastered"), f"state advanced ({feedback.get('state')})")

    print("9. attempt history")
    _, history, _ = request(f"{base}/api/training/positions/{position_id}/attempts?player_id={player_id}")
    check((history or {}).get("count", 0) >= 1, "attempts are stored")

    print("10. review queue / progress / recommendations")
    _, queue, _ = request(f"{base}/api/training/review-queue?player_id={player_id}")
    check(isinstance(queue, dict) and "due" in queue, "review queue answers")
    _, progress, _ = request(f"{base}/api/training/progress?player_id={player_id}")
    check((progress or {}).get("attempts_total", 0) >= 1, "progress counts the attempts")
    check((progress or {}).get("library_size", 0) >= 1, "progress reports the library size")
    _, recs, _ = request(f"{base}/api/training/recommendations?player_id={player_id}")
    check(isinstance(recs, dict) and "evidence_policy" in recs, "recommendations carry their evidence policy")
    check("too little data" in (recs or {}).get("evidence_policy", ""), "recommendations refuse to guess without data")

    print("11. session lifecycle")
    status, session, _ = request(
        f"{base}/api/training/sessions", {"player_id": player_id, "kind": "quick"}
    )
    check(status == 200 and isinstance(session, dict), "a session starts")
    if isinstance(session, dict) and session.get("remaining_position_ids"):
        session_id = session["id"]
        first = session["remaining_position_ids"][0]
        _, resumed, _ = request(f"{base}/api/training/sessions/{session_id}?player_id={player_id}")
        check((resumed or {}).get("current", {}).get("id") == first, "the session resumes at its current exercise")
        # The session's first exercise is not necessarily the one solved above, so
        # its own verified solution must be fetched — submitting a different
        # position's move would (correctly) grade as wrong.
        _, first_revealed, _ = request(f"{base}/api/training/positions/{first}/solution")
        first_solution = ((first_revealed or {}).get("solution") or {}).get("uci")
        _, solved, _ = request(
            f"{base}/api/training/positions/{first}/attempt",
            {"player_id": player_id, "submitted_uci": first_solution, "session_id": session_id},
        )
        check(solved.get("outcome") == "correct", "an attempt inside a session is graded")
        _, after_session, _ = request(f"{base}/api/training/sessions/{session_id}?player_id={player_id}")
        check((after_session or {}).get("completed", 0) >= 1, "the session records the completed exercise")
        _, cancelled, _ = request(
            f"{base}/api/training/sessions/{session_id}/cancel?player_id={player_id}", method="POST"
        )
        check((cancelled or {}).get("status") == "cancelled", "a session cancels without deleting attempts")

    print("12. game → training link")
    _, from_game, _ = request(f"{base}/api/training/games/{game_id}?player_id={player_id}")
    check((from_game or {}).get("count", 0) >= 1, "the game's exercises are listed")

    print("13. authorization")
    _, players_payload, _ = request(f"{base}/api/players")
    players = (players_payload or {}).get("players", [])
    other = next((p for p in players if str(p["id"]) != player_id and p.get("name") != "Duke Karl / Count Isouard"), None)
    if other:
        status, _, _ = request(f"{base}/api/training/positions/{position_id}?player_id={other['id']}")
        check(status == 404, "a foreign player cannot read another player's exercise")
    else:
        print("   (no second player to test foreign access; skipped)")

    _agent_and_empty(base, player_id)
    return 1 if failures else 0


def _agent_and_empty(base: str, player_id: str) -> None:
    print("14. agent training tools")
    status, catalogue, _ = request(f"{base}/api/coach/tools")
    names = {entry["name"] for entry in (catalogue or {}).get("tools", [])}
    for tool in ("generate_training_position", "get_training_recommendations", "generate_training_explanation"):
        check(tool in names, f"agent declares {tool}")
    status, answer, _ = request(
        f"{base}/api/coach/ask",
        {"question": "What should I practise today?", "player_id": player_id},
    )
    check(status == 200 and bool((answer or {}).get("message")), "the agent answers a training question")


if __name__ == "__main__":
    sys.exit(main())
