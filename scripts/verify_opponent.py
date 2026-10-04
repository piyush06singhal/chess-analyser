#!/usr/bin/env python
"""Live verification for Phase 9 opponent intelligence.

Run against a live server::

    python scripts/verify_opponent.py [--base-url http://127.0.0.1:8002]

It walks the opponent surface through the real HTTP API — no mocks:

    meta → identity/history → cached profile → repertoire (all-time + recent) →
    position response → tendencies → phase statistics → preparation report →
    agent tools.

Every assertion is against the API's own response. If the deployment has no
analysed game, the script imports and analyses the Opera Game first (real
Stockfish). Where a scenario cannot be exercised (e.g. only one player), it says
so rather than pretending it passed.

Exit codes: 0 = verified, 1 = failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
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

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


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


def resolve_analysed_player(base: str) -> str | None:
    """A tracked player who has at least one analysed game."""
    _, games_payload, _ = request(f"{base}/api/games")
    games = (games_payload or {}).get("games", []) if isinstance(games_payload, dict) else []
    if not any(game.get("analysis_status") == "analyzed" for game in games):
        print("   no analysed game yet; importing + analysing the Opera Game…")
        status, imported, _ = request(
            f"{base}/api/games/import",
            {"pgn_text": OPERA_GAME_PGN, "run_analysis": True, "depth": 8, "multipv": 2},
        )
        check(status in (200, 201) and isinstance(imported, dict), "import + analyse a game")
        if status not in (200, 201):
            return None
    players = _players(base)
    morphy = next((p for p in players if p["name"] == "Paul Morphy"), None)
    return str(morphy["id"]) if morphy else (str(players[0]["id"]) if players else None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print("1. meta")
    status, meta, _ = request(f"{base}/api/players/opponent-meta")
    check(status == 200 and isinstance(meta, dict), "opponent meta is served")
    if isinstance(meta, dict):
        check(meta.get("methodology_version") == "9.0", "methodology version is 9.0")
        check("psycholog" in str(meta.get("privacy", "")).lower(), "privacy states it is not psychology")
        check(meta.get("policy_defaults", {}).get("min_occurrences_for_tendency", 0) >= 1, "gates are documented")

    print("2. resolve an analysed player")
    player_id = resolve_analysed_player(base)
    check(bool(player_id), f"resolved a tracked player ({player_id})")
    if not player_id:
        return 1

    print("3. opponent profile (cached)")
    status, profile, elapsed = request(f"{base}/api/players/{player_id}/opponent-profile")
    check(status == 200 and isinstance(profile, dict), "opponent profile is served")
    if isinstance(profile, dict):
        check(profile.get("identity", {}).get("name"), "identity is present")
        stats = profile.get("statistics", {})
        check("analyzed_games" in stats and "total_games" in stats, "statistics carry sample sizes")
        check(profile.get("coverage") in ("insufficient", "limited", "moderate", "robust"), "coverage band is stated")
        check(isinstance(profile.get("limitations"), list) and profile["limitations"], "limitations are declared")
        print(f"   profile in {elapsed:.0f}ms · analyzed={stats.get('analyzed_games')}/{stats.get('total_games')}")
    status, cached, cached_elapsed = request(f"{base}/api/players/{player_id}/opponent-profile")
    check(status == 200 and (cached or {}).get("rebuilt") is False, "the second read is served from the snapshot")
    print(f"   cached read in {cached_elapsed:.0f}ms")
    print("4. game history")
    status, games, _ = request(f"{base}/api/players/{player_id}/opponent-games")
    check(status == 200 and isinstance(games, dict), "game history is served")
    check((games or {}).get("total", 0) >= 1, "the history has at least one game")

    print("5. repertoire (all-time + recent)")
    for color in ("white", "black"):
        status, repertoire, _ = request(f"{base}/api/players/{player_id}/repertoire?color={color}")
        check(status == 200 and isinstance(repertoire, dict), f"repertoire as {color} is served")
        if isinstance(repertoire, dict):
            check(isinstance(repertoire.get("nodes"), list), f"{color} repertoire nodes are a list")
            check(repertoire.get("coverage") in ("insufficient", "limited", "moderate", "robust"), f"{color} coverage stated")
    status, recent, _ = request(f"{base}/api/players/{player_id}/repertoire/recent?color=white")
    check(status == 200 and (recent or {}).get("recent") is True, "recent repertoire is windowed and says so")

    print("6. position response")
    status, response, _ = request(
        f"{base}/api/players/{player_id}/position-response?fen={urllib.parse.quote(START_FEN)}"
    )
    check(status == 200 and isinstance(response, dict), "position response is served")
    if isinstance(response, dict):
        check(response.get("match") in ("exact", "normalized_pieces", "none"), "match type is explicit")
        check(bool(response.get("sample_note")), "the response states its sample size")
    status, _, _ = request(f"{base}/api/players/{player_id}/position-response?fen=not-a-fen")
    check(status == 422, "an invalid FEN is rejected")

    print("7. tendencies + phase statistics")
    status, tendencies, _ = request(f"{base}/api/players/{player_id}/tendencies")
    check(status == 200 and isinstance(tendencies, dict), "tendencies are served")
    for tendency in (tendencies or {}).get("tendencies", []):
        check(bool(tendency.get("measurement")), f"tendency {tendency.get('key')} states its measurement")
        check("sample_size" in tendency, f"tendency {tendency.get('key')} carries a sample size")
    status, phases, _ = request(f"{base}/api/players/{player_id}/phase-statistics")
    check(status == 200 and isinstance(phases, dict), "phase statistics are served")
    check(bool((phases or {}).get("sample_note")), "phase statistics carry a sample note")

    print("8. preparation report")
    status, report, _ = request(f"{base}/api/players/{player_id}/preparation-report?color=white")
    check(status == 200 and isinstance(report, dict), "preparation report is served")
    if isinstance(report, dict):
        check(report.get("methodology_version") == "9.0", "report records its methodology version")
        check(isinstance(report.get("limitations"), list) and report["limitations"], "report states its limitations")
        for insight in report.get("insights", []):
            check(insight.get("claim_level") in ("insufficient", "observation", "pattern", "tendency"), "insight claim level is valid")
            check("sample_size" in insight, f"insight {insight.get('key')} carries a sample size")
        print(f"   insights={len(report.get('insights', []))} evidence_refs={report.get('evidence_count')}")

    print("9. opponent preparation training")
    players = _players(base)
    others = [p for p in players if str(p["id"]) != player_id]
    if not others:
        print("   (only one tracked player, so there is no second player to prepare for; skipped)")
    else:
        preparing = str(others[0]["id"])
        status, prep, elapsed = request(
            f"{base}/api/training/opponents/{player_id}/prepare",
            {"player_id": preparing},
        )
        check(status == 200 and isinstance(prep, dict), "preparation endpoint answers")
        if isinstance(prep, dict):
            check(prep.get("data_source") == "opponent_preparation", "exercises are labelled opponent preparation")
            check(bool(prep.get("note")), "preparation states its own note")
            print(
                f"   accepted={prep.get('accepted')} rejected={prep.get('rejected')} "
                f"seen={prep.get('seen')} in {elapsed:.0f}ms"
            )
            if prep.get("accepted", 0) >= 1:
                names = {entry["name"] for entry in (request(f"{base}/api/coach/tools")[1] or {}).get("tools", [])}
                check("generate_opponent_training" in names, "agent declares generate_opponent_training")
                library = request(f"{base}/api/training/positions?player_id={preparing}")[1] or {}
                owned = [p for p in library.get("positions", []) if p.get("is_opponent_preparation")]
                check(bool(owned), "the exercises belong to the preparing player")
                check(
                    all(p.get("data_source") == "opponent_preparation" for p in owned),
                    "preparation exercises keep their data source",
                )
            else:
                print("   (no move characteristic enough yet; refusal is itself honest)")
        # A deliberately impossible gate must refuse rather than invent.
        strict = request(
            f"{base}/api/training/opponents/{player_id}/prepare",
            {"player_id": preparing, "min_occurrences": 50},
        )[1] or {}
        check(strict.get("accepted") == 0, "an impossible occurrence gate prepares nothing")

    print("10. authorization")
    status, _, _ = request(f"{base}/api/players/999999/opponent-profile")
    check(status == 404, "an unknown player answers 404, not a fabricated profile")

    print("11. agent opponent tools")
    status, catalogue, _ = request(f"{base}/api/coach/tools")
    names = {entry["name"] for entry in (catalogue or {}).get("tools", [])}
    for tool in (
        "get_opponent_profile",
        "get_opponent_repertoire",
        "get_opponent_position_responses",
        "get_opponent_tendencies",
        "get_opponent_phase_statistics",
        "get_opponent_preparation_report",
        "generate_opponent_brief",
    ):
        check(tool in names, f"agent declares {tool}")
    status, answer, _ = request(
        f"{base}/api/coach/ask",
        {"question": "What does the data say about this opponent?", "player_id": player_id},
    )
    check(status == 200 and bool((answer or {}).get("message")), "the agent answers an opponent question")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) failed")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("All opponent-intelligence checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
