#!/usr/bin/env python
"""Phase 12 load probe (§49): real concurrent games, measured latencies.

Unlike the invariant tests (which assert *behaviour* under load), this script
reports *numbers* against a running Caissa: move latency, state-read latency, sync
latency, WebSocket event-delivery latency, and how many spectators a game can
carry. It plays real legal moves on real games in a real database.

It is a probe, not a benchmark harness: it uses the standard library plus the
already-installed ``websockets`` client, and it fails only on a generous
correctness/latency bound so a noisy laptop does not report a false failure. The
measured figures are what it is for.

Usage:
    python scripts/load_live.py --base-url http://127.0.0.1:8002 --games 6 --moves 12
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import chess
import random

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "argus"))

from argus.shared.stats import percentile as _percentile  # noqa: E402  (needs the path above)


def request(url: str, payload: dict | None = None, method: str | None = None, timeout: int = 60):
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
            return response.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body), elapsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw), (time.perf_counter() - started) * 1000
        except Exception:  # noqa: BLE001
            return exc.code, raw, (time.perf_counter() - started) * 1000


def percentile(values: list[float], pct: float) -> float:
    """The shared nearest-rank percentile, as a float.

    The shared helper returns ``None`` for an empty sample (a percentile of
    nothing is unknown, not zero). This probe formats its numbers eagerly, so it
    maps that to ``nan`` — which prints as ``nan`` and compares false, exactly the
    "no measurement" behaviour the probe wants.
    """
    value = _percentile(values, pct)
    return float("nan") if value is None else value


def ws_scheme(base: str) -> str:
    return base.replace("https://", "wss://").replace("http://", "ws://")


def players(base: str) -> list[dict]:
    _, payload, _ = request(f"{base}/api/players")
    return (payload or {}).get("players", []) if isinstance(payload, dict) else []


def legal_lines(count: int, plies: int, seed: int) -> list[list[str]]:
    """A distinct, legal, non-repeating line per game."""
    lines: list[list[str]] = []
    for game_index in range(count):
        rng = random.Random(seed + game_index)
        board = chess.Board()
        moves: list[str] = []
        for _ in range(plies):
            options = list(board.legal_moves)
            if not options or board.is_game_over():
                break
            move = rng.choice(options)
            moves.append(move.uci())
            board.push(move)
        lines.append(moves)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--games", type=int, default=6)
    parser.add_argument("--moves", type=int, default=12)
    parser.add_argument("--spectators", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261002)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    found = players(base)
    if len(found) < 2:
        print("Need at least two stored players. Import games first.")
        return 2
    white, black = int(found[0]["id"]), int(found[1]["id"])
    print(f"Caissa LIVE LOAD PROBE — {args.games} games, {args.moves} plies each")
    print("=" * 62)

    # --- create the games ----------------------------------------------------
    create_latency: list[float] = []
    game_ids: list[str] = []
    for _ in range(args.games):
        status, body, ms = request(
            f"{base}/api/live/games",
            {
                "player_id": white,
                "mode": "private_match",
                "opponent_player_id": black,
                # Long base time so no game flags during the probe.
                "time_control": "30+0",
            },
        )
        create_latency.append(ms)
        if status != 201:
            print(f"could not create a game: HTTP {status} {body}")
            return 1
        game_ids.append(body["state"]["game_id"])
    print(f"created {len(game_ids)} games   p50 create {percentile(create_latency, 50):.0f}ms")

    lines = legal_lines(len(game_ids), args.moves, args.seed)
    move_latency: list[float] = []
    read_latency: list[float] = []
    event_latency: list[float] = []
    delivered_events = 0
    errors: list[str] = []

    def play(game_id: str, line: list[str]) -> None:
        nonlocal delivered_events
        socket = None
        try:
            from websockets.sync.client import connect

            socket = connect(
                f"{ws_scheme(base)}/api/live/games/{game_id}/ws?player_id={white}",
                open_timeout=5,
                legacy=True,
            )
            socket.recv(timeout=5)  # initial sync frame
        except Exception as exc:  # noqa: BLE001 — a probe records, it does not crash
            errors.append(f"{game_id}: websocket {exc}")
            socket = None

        for index, uci in enumerate(line):
            player = white if index % 2 == 0 else black
            sent_at = time.perf_counter()
            status, body, ms = request(
                f"{base}/api/live/games/{game_id}/move",
                {"player_id": player, "uci": uci},
            )
            if status != 200:
                errors.append(f"{game_id}: move {uci} -> HTTP {status}")
                break
            move_latency.append(ms)
            # The home side subscribes to the socket, so its move is echoed back;
            # time from response to the MOVE_MADE event on the stream.
            if socket is not None:
                try:
                    while True:
                        frame = json.loads(socket.recv(timeout=0.3))
                        if frame.get("type") == "event":
                            delivered_events += 1
                            payload = frame.get("event", {})
                            # The first MOVE_MADE after our own move is that move's
                            # broadcast; measure the time the stream took to carry it.
                            if payload.get("event_type") == "MOVE_MADE":
                                event_latency.append((time.perf_counter() - sent_at) * 1000)
                                break
                        elif frame.get("type") == "sync":
                            break
                except Exception:  # noqa: BLE001 — no event within the window is data, not a failure
                    pass
        if socket is not None:
            try:
                socket.close()
            except Exception:  # noqa: BLE001
                pass

    # --- play moves across games in parallel --------------------------------
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=min(len(game_ids), 8)) as pool:
        list(pool.map(lambda pair: play(*pair), zip(game_ids, lines)))
    total_seconds = time.perf_counter() - started

    # --- state reads and reconnection sync ----------------------------------
    for game_id in game_ids:
        _, _, ms = request(f"{base}/api/live/games/{game_id}?player_id={white}")
        read_latency.append(ms)
        _, sync_body, sync_ms = request(
            f"{base}/api/live/games/{game_id}/sync?after_sequence=0&player_id={white}"
        )
        read_latency.append(sync_ms)
        if isinstance(sync_body, dict) and sync_body.get("events"):
            delivered_events += len(sync_body["events"])

    # --- spectators ----------------------------------------------------------
    spectator_ok = 0
    try:
        from websockets.sync.client import connect

        status, public_body, _ = request(
            f"{base}/api/live/games/{game_ids[0]}/visibility",
            {"player_id": white, "visibility": "public"},
        )
        if status == 200:
            for _ in range(args.spectators):
                try:
                    with connect(
                        f"{ws_scheme(base)}/api/live/games/{game_ids[0]}/ws",
                        open_timeout=5,
                    ) as spectator:
                        spectator.recv(timeout=5)
                        spectator_ok += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"spectator: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"spectator setup: {exc}")

    # --- report --------------------------------------------------------------
    total_moves = len(move_latency)
    print(f"moves played .............. {total_moves} in {total_seconds:.2f}s")
    print(
        f"move latency .............. p50 {percentile(move_latency, 50):.0f}ms  "
        f"p95 {percentile(move_latency, 95):.0f}ms  max {max(move_latency or [0]):.0f}ms"
    )
    print(
        f"state/sync read ........... p50 {percentile(read_latency, 50):.0f}ms  "
        f"p95 {percentile(read_latency, 95):.0f}ms"
    )
    if event_latency:
        print(
            f"ws event latency .......... p50 {percentile(event_latency, 50):.0f}ms  "
            f"p95 {percentile(event_latency, 95):.0f}ms  (n={len(event_latency)})"
        )
    else:
        print("ws event latency .......... (no events observed — check the socket path)")
    print(f"events delivered .......... {delivered_events}")
    print(f"spectators attached ....... {spectator_ok}/{args.spectators}")

    if errors:
        print(f"\nerrors ({len(errors)}):")
        for line in errors[:10]:
            print(f"  {line}")

    # A generous correctness bound: the probe fails only if the server is
    # genuinely struggling, not because a laptop hiccuped.
    ok = (
        total_moves == sum(len(line) for line in lines)
        and percentile(move_latency, 95) < 2000
        and not errors
    )
    # Leave the environment as we found it: abort the probe's own games so a
    # repeated run does not accumulate open games against the per-player limit.
    for game_id in game_ids:
        request(
            f"{base}/api/live/games/{game_id}/abort",
            {"player_id": white, "reason": "load probe"},
        )

    print("\n" + "=" * 62)
    print("PROBE PASSED — latencies within bound, every move accepted." if ok else "PROBE FAILED — see the figures and errors above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
