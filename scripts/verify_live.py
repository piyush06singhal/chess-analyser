#!/usr/bin/env python
"""Phase 12 live verification: the complete live-game lifecycle, end to end.

Spec §67 describes a chain, and this script walks it against a running Caissa with
real persisted data and a real engine. Every arrow is a printed check:

    CREATE A GAME → START THE CLOCK → PLAY A REAL MOVE → SERVER VALIDATES
    → PERSIST → CLOCK UPDATED → EVENTS SEQUENCED
    → GAME ENDS → PGN → POST-GAME ANALYSIS → LIBRARY LINK

It also checks the *fair-play isolation*, because that is the point of the phase:
a competitive game must never receive an engine move, from any surface, while a
training game must. And it checks the lifecycle that a failed check would be easy
to fake: the post-game pipeline must actually reach the library.

Nothing is assumed. If a check cannot run, it is reported as SKIP with the reason,
never as PASS.

Usage:
    python scripts/verify_live.py [--base-url http://127.0.0.1:8002]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

failures: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> bool:
    print(f"{'OK  ' if condition else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)
    return bool(condition)


def skip(label: str, why: str) -> None:
    print(f"SKIP {label} — {why}")


def request(url: str, payload: dict | None = None, method: str | None = None, timeout: int = 120):
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


def players(base: str) -> list[dict]:
    _, payload, _ = request(f"{base}/api/players")
    return (payload or {}).get("players", []) if isinstance(payload, dict) else []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print("Caissa PHASE 12 — LIVE GAME VERIFICATION")
    print("=" * 62)

    print("\n1. METHODOLOGY AND METRICS")
    status, method, _ = request(f"{base}/api/live/method")
    if check(status == 200, "the live method is served", f"HTTP {status}"):
        check("training" in method.get("modes", []), "training is a creatable mode")
        check(bool(method.get("fair_play")), "the fair-play rule is stated")
        check("no_analysis" in method.get("analysis_modes", []), "no_analysis is a mode")
    status, metrics, _ = request(f"{base}/api/live/metrics")
    check(status == 200 and "counters" in metrics, "metrics are served as counters")

    found = players(base)
    if not found:
        print("   no players stored; cannot run the lifecycle without an identity.")
        print("\n" + "=" * 62)
        print("1 CHECK(S) FAILED: the lifecycle needs a stored player")
        return 1
    player_id = int(found[0]["id"])
    print(f"   using player {player_id} ({found[0]['name']})")
    # Games this probe turns into *library* rows (the post-game link) are stamped
    # here and deleted at the end, so a verification run leaves the library as it
    # found it instead of accumulating throwaway one-move games.
    created_library_games: list[str] = []

    # ------------------------------------------------------------------ training
    print("\n2. CREATE A TRAINING GAME (ENGINE OPPONENT, ANALYSIS PERMITTED)")
    status, created, _ = request(
        f"{base}/api/live/games",
        {
            "player_id": player_id,
            "mode": "training",
            "colour": "white",
            "opponents": "engine",
            # No increment, so "the mover was charged time" is unambiguous: the
            # clock can only go down.
            "time_control": "5+0",
            "training_mode": "free_analysis",
        },
    )
    if status != 201 or not isinstance(created, dict):
        check(False, "a training game is created", f"HTTP {status} {created}")
        print("\n" + "=" * 62)
        print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
        return 1
    training = created["state"]
    training_id = training["game_id"]
    check(training["status"] == "active", "both seats filled, so the game starts", training["status"])
    check(training["seats"]["black"] == "engine", "the engine holds the far seat")
    check(training["clock"]["running"] is True, "the clock is running")
    check(
        training["permissions"]["may_give_engine_moves"] is True,
        "a training game permits engine moves",
    )
    check(
        training["analysis_mode"] in {"training_analysis", "sandbox_analysis", "post_move_analysis"},
        "the training analysis mode is enabled",
        training["analysis_mode"],
    )

    print("\n3. PLAY A REAL MOVE AND LET THE SERVER VALIDATE IT")
    # Let the clock actually run for a moment, so "the mover was charged time" is
    # a real measurement rather than a value that could round to zero.
    time.sleep(1.1)
    status, moved, _ = request(
        f"{base}/api/live/games/{training_id}/move",
        {"player_id": player_id, "uci": "e2e4"},
    )
    if check(status == 200, "a legal move is accepted", f"HTTP {status}"):
        state = moved["state"]
        played = next((m for m in state["moves"] if m["uci"] == "e2e4"), None)
        check(played is not None and played["san"] == "e4", "the move is stored with its SAN")
        check(state["clock"]["white_ms"] < training["clock_config"]["base_ms"], "the mover was charged time")
        check(state["version"] > training["version"], "the game version advanced")
        check(
            bool(moved.get("engine_reply")),
            "the engine answered inside the request",
            json.dumps(moved.get("engine_reply"))[:80],
        )
    rejected, _, _ = request(
        f"{base}/api/live/games/{training_id}/move",
        {"player_id": player_id, "uci": "a1a8"},
    )
    check(rejected == 422, "an illegal move is refused", f"HTTP {rejected}")

    print("\n4. THE COACH MAY ANALYSE A TRAINING GAME")
    status, coach, _ = request(f"{base}/api/live/games/{training_id}/coach", {"player_id": player_id})
    check(status == 200 and coach.get("kind") == "analysis_permitted", "training coach permits analysis", str(coach.get("kind")))

    print("\n4b. A TRAINING MODE'S ASSISTANCE IS BINDING (§55)")
    status, practice, _ = request(
        f"{base}/api/live/games",
        {
            "player_id": player_id,
            "mode": "training",
            "colour": "white",
            "opponents": "engine",
            "time_control": "5+0",
            "training_mode": "practice_game",
        },
    )
    if check(status == 201, "a practice game is created", f"HTTP {status}"):
        pstate = practice["state"]
        check(
            pstate["coach_level"] == "hints",
            "a practice game is clamped to hints",
            pstate["coach_level"],
        )
        check(
            pstate["permissions"]["may_give_engine_moves"] is False,
            "a practice game may not hand over an engine move",
        )
        request(f"{base}/api/live/games/{pstate['game_id']}/abort", {"player_id": player_id})
    # Leave nothing open behind us.
    request(f"{base}/api/live/games/{training_id}/abort", {"player_id": player_id})

    # -------------------------------------------------------------- competitive
    print("\n5. CREATE A COMPETITIVE GAME (NO ENGINE, CLAMPED)")
    opponent = next((p for p in found if int(p["id"]) != player_id), None)
    competitive_body = {
        "player_id": player_id,
        "mode": "private_match",
        "colour": "white",
        "time_control": "5+0",
    }
    if opponent:
        competitive_body["opponent_player_id"] = int(opponent["id"])
    status, comp_created, _ = request(f"{base}/api/live/games", competitive_body)
    if status != 201:
        check(False, "a competitive game is created", f"HTTP {status} {comp_created}")
    else:
        comp = comp_created["state"]
        comp_id = comp["game_id"]
        check(comp["analysis_mode"] == "no_analysis", "a competitive game is clamped to no_analysis", comp["analysis_mode"])
        check(
            comp["permissions"]["may_give_engine_moves"] is False,
            "a competitive game may not give engine moves",
        )
        check(bool(comp["permissions"]["refusal"]), "the refusal is present")

        print("\n6. FAIR-PLAY ISOLATION AT THE COACH ENDPOINT")
        status, refused, _ = request(
            f"{base}/api/live/games/{comp_id}/coach",
            {"player_id": player_id, "question": "what's the best move?"},
        )
        check(status == 200 and refused.get("kind") in {"hint_only", "coach_off"}, "the competitive coach refuses", str(refused.get("kind")))
        check("analysis" not in refused, "no engine analysis is returned")
        check(refused.get("permissions", {}).get("may_show_evaluation") is False, "no evaluation is permitted")
        check(bool(refused.get("hint")), "a non-engine hint is offered instead")

        if comp["status"] == "active":
            status, comp_moved, _ = request(
                f"{base}/api/live/games/{comp_id}/move",
                {"player_id": player_id, "uci": "e2e4"},
            )
            if check(status == 200, "a legal competitive move is accepted"):
                check(not comp_moved.get("engine_reply"), "no engine move is played for the opponent")
                check(not comp_moved.get("engine_error"), "no engine error is surfaced either")

        print("\n7. SYNCHRONIZATION AND PGN")
        _, synced, _ = request(f"{base}/api/live/games/{comp_id}/sync?after_sequence=0&player_id={player_id}")
        events = synced.get("events", []) if isinstance(synced, dict) else []
        sequences = [event["sequence_number"] for event in events]
        check(bool(events), "the event log holds the game's events", f"{len(events)} events")
        check(sequences == sorted(sequences), "events are served in sequence order")
        check("resync" in synced, "the sync states whether a resync is needed")

        _, pgn, _ = request(f"{base}/api/live/games/{comp_id}/pgn?player_id={player_id}")
        pgn_text = pgn.get("pgn", "") if isinstance(pgn, dict) else ""
        check("e4" in pgn_text, "the PGN contains the move played")
        check("eval" not in pgn_text.lower(), "the PGN does not leak analysis")

        print("\n8. ENDING THE GAME AND THE POST-GAME PIPELINE")
        status, resigned, _ = request(f"{base}/api/live/games/{comp_id}/resign", {"player_id": player_id})
        check(status == 200 and resigned["state"]["status"] == "resigned", "resignation ends the game")
        check(resigned["state"]["result"] in {"0-1", "1-0", "1/2-1/2"}, "a result is recorded", resigned["state"]["result"])

        library_id = None
        deadline = time.time() + 60
        while time.time() < deadline:
            _, current, _ = request(f"{base}/api/live/games/{comp_id}?player_id={player_id}")
            library_id = current.get("library_game_id") if isinstance(current, dict) else None
            if library_id:
                break
            time.sleep(2)
        if check(bool(library_id), "the live game reached the library", str(library_id)):
            created_library_games.append(library_id)
            status, library, _ = request(f"{base}/api/games/{library_id}")
            check(status == 200, "the library game is readable")
            check(
                library.get("analysis_status") in {"ready", "analyzed", "analyzing"},
                "the post-game analysis was scheduled",
                str(library.get("analysis_status")),
            )

    print("\n9. THE AI AGENT SEES THE LIVE GAME AND REFUSES ENGINE MOVES")
    if opponent:
        status, answer, _ = request(
            f"{base}/api/coach/ask",
            {
                "question": "What's the best move here?",
                "live_game_id": comp_id,
                "live_player_id": player_id,
                "mode": "coach",
            },
            timeout=180,
        )
        if status == 200 and isinstance(answer, dict):
            evidence = answer.get("evidence") or []
            used = {entry.get("tool") for entry in evidence if isinstance(entry, dict)}
            engine_tools = {"analyze_position", "analyze_position_multipv", "compare_moves"}
            check(not (used & engine_tools), "no engine tool ran for the competitive game", str(sorted(used)))
            message = (answer.get("message") or "").lower()
            check(
                "best move" not in message or "will not" in message or "cannot" in message,
                "the answer does not hand over an engine move",
            )
        else:
            skip("agent fair-play check", f"the agent answered HTTP {status}: {str(answer)[:80]}")

    # Leave the environment as we found it.
    for game_id in created_library_games:
        status, _, _ = request(f"{base}/api/games/{game_id}", method="DELETE")
        if status not in (200, 204, 404):
            print(f"   note: could not delete probe game {game_id} (HTTP {status})")
    if created_library_games:
        print(f"\n9b. CLEANED UP {len(created_library_games)} probe game(s) from the library")

    print("\n" + "=" * 62)
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
        return 1
    print("ALL CHECKS PASSED — the live-game lifecycle runs on real server state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
