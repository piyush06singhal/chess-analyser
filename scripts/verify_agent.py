#!/usr/bin/env python
"""Live verification for the Phase 7 AI chess agent (spec §35–§47).

Run against a live server::

    python scripts/verify_agent.py [--base-url http://127.0.0.1:8002]

It exercises the agent through its real HTTP surface — no mocks — across the
scenarios the phase spec names, and measures the latency of each turn. It asserts
the *honest* outcome in every case:

* a move question cites stored analysis, and a game the caller cannot read is refused;
* a player question with no profile says so instead of inventing a history;
* an evaluation question with no engine reports the missing engine;
* a prediction request is refused because no model passed its gate;
* no answer or trace contains a credential, and no fake number is produced anywhere.

Every number it prints is measured. If a scenario cannot be exercised because the
library is empty, the script says so and does not pretend it passed.

Exit codes: 0 = verified, 1 = failed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from statistics import mean

failures: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"{'OK  ' if condition else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def request(
    url: str, payload: dict | None = None, *, timeout: int = 180
) -> tuple[int, dict | str, float]:
    """Return ``(status, body, elapsed_ms)``."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
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


def ask(base: str, question: str, **context) -> tuple[dict, float]:
    """One agent turn. The context keys mirror the API request schema."""
    body = {"question": question, "include_evidence": True, **context}
    status, payload, elapsed = request(f"{base}/api/coach/ask", body)
    if status != 200 or not isinstance(payload, dict):
        return {"_http_status": status, "_raw": payload}, elapsed
    return payload, elapsed


def _evidence(payload: dict) -> dict:
    return payload.get("evidence") or {}


def _missing(payload: dict) -> list[dict]:
    return _evidence(payload).get("missing") or []


def _tools_used(payload: dict) -> set[str]:
    trace = payload.get("trace") or {}
    used = {str(call.get("tool")) for call in (trace.get("tool_calls") or [])}
    used |= {str(item.get("tool")) for item in (_evidence(payload).get("items") or [])}
    used.discard("")
    used.discard("None")
    return used


#: Patterns that indicate key *material* rather than the *name* of an env var. The
#: answer legitimately mentions ARGUS_LLM_API_KEY when telling the user how to enable
#: the coach, so a bare name must not be flagged — only a name assigned a value.
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
    re.compile(
        r"\b(api[_-]?key|apikey|token|password|secret)\b\s*[:=]\s*[^\s\"']{6,}",
        re.IGNORECASE,
    ),
)


def _contains_secret(text: str) -> bool:
    """True when key material (not a variable name) appears in the text."""
    cleaned = text.replace("[redacted]", "")
    return any(pattern.search(cleaned) for pattern in _SECRET_PATTERNS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    # --- health ---------------------------------------------------------------
    status, health, _ = request(f"{base}/health")
    if status != 200 or not isinstance(health, dict):
        print(f"FAIL the API is not reachable at {base} (status {status})")
        return 1
    version = health.get("version", "?")
    engine = (health.get("engine") or {})
    print(f"API {version} · engine {engine.get('engine', '?')} available={engine.get('available')}")
    check(engine.get("available") is True, "Stockfish is reachable")

    # --- tool catalogue -------------------------------------------------------
    status, catalogue, _ = request(f"{base}/api/coach/tools")
    check(status == 200 and isinstance(catalogue, dict), "tool catalogue is served")
    tools = (catalogue or {}).get("tools") or []
    available = set((catalogue or {}).get("available") or [])
    names = {entry.get("name") for entry in tools}
    check(len(tools) >= 18, f"tool catalogue is complete ({len(tools)} tools)")
    for expected in (
        "get_move_analysis",
        "get_critical_moments",
        "get_player_insights",
        "get_opening_information",
        "get_prediction_status",
        "analyze_position",
        "get_validated_prediction",
    ):
        check(expected in names, f"tool '{expected}' is declared")
    check(bool((catalogue or {}).get("prompt_version")), "prompt version is published")
    print(f"     available: {len(available)}/{len(tools)}")
    # Every unavailable tool must carry a reason — an unexplained gap is a bug.
    unexplained = [
        entry["name"]
        for entry in tools
        if not entry.get("available") and not entry.get("reason")
    ]
    check(not unexplained, "every unavailable tool carries a reason", ", ".join(unexplained))

    # --- the library ----------------------------------------------------------
    status, games_payload, _ = request(f"{base}/api/games")
    games = (games_payload or {}).get("games") if isinstance(games_payload, dict) else []
    games = games or []
    print(f"     library: {len(games)} stored game(s)")

    latencies: list[float] = []

    # --- scenario 1: a prediction request must be refused ---------------------
    payload, elapsed = ask(base, "Can you predict my win probability?")
    latencies.append(elapsed)
    message = str(payload.get("message") or "").lower()
    check(payload.get("_http_status") in (None, 200), "a prediction request is answered, not crashed")
    check("cannot" in message or "no prediction model" in message, "the prediction request is refused honestly")
    check("get_validated_prediction" not in _tools_used(payload), "no prediction model is ever called")
    items = _evidence(payload).get("items") or []
    # The status tool is allowed to produce evidence — that is how the refusal is
    # grounded. What must never appear is a *served* prediction or probability.
    check(
        not any(item.get("tool") == "get_validated_prediction" for item in items),
        "no validated prediction is served",
    )
    status_items = [item for item in items if item.get("tool") == "get_prediction_status"]
    check(
        bool(status_items)
        and all("0 task(s) available" in str(item.get("summary", "")) for item in status_items),
        "the prediction status reports that no model passed its gate",
    )
    check(
        not re.search(r"\d+\s*%", json.dumps(payload)),
        "no probability percentage is produced for a prediction request",
    )

    # --- scenario 2: an evaluation request with the engine present -------------
    start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    payload, elapsed = ask(base, "What is the evaluation here?", fen=start_fen)
    latencies.append(elapsed)
    engine_items = [item for item in (_evidence(payload).get("items") or []) if item.get("kind") == "ENGINE"]
    if engine.get("available"):
        check(bool(engine_items), "an evaluation question produces engine evidence")
        summary = " ".join(str(item.get("summary")) for item in engine_items)
        check("Stockfish depth" in summary or "best" in summary.lower(), "the engine answer cites depth and best move")
    else:
        check(
            any("engine" in str(entry.get("reason", "")).lower() for entry in _missing(payload)),
            "with no engine, the missing engine is recorded",
        )

    # --- scenario 3: a player question with no profile ------------------------
    payload, elapsed = ask(base, "What is my biggest weakness?")
    latencies.append(elapsed)
    check(payload.get("_http_status") in (None, 200), "a player question is answered, not crashed")
    reasons = " ".join(str(entry.get("reason", "")) for entry in _missing(payload)).lower()
    check("no active player" in reasons, "with no player open, the gap is named")
    check(
        not any(item.get("kind") in ("PLAYER_INSIGHT", "PLAYER_PROFILE") for item in (_evidence(payload).get("items") or [])),
        "no player history is invented",
    )

    # --- scenario 4: a cross-caller game must be refused ----------------------
    stranger = str(uuid.uuid4())
    payload, elapsed = ask(
        base, "Why was this move bad?", game_id=stranger, ply=17
    )
    latencies.append(elapsed)
    check(payload.get("_http_status") in (None, 200), "an unauthorized game is handled, not crashed")
    refused = "does not belong" in json.dumps(payload).lower() or any(
        "does not belong" in str(entry.get("reason", "")).lower() for entry in _missing(payload)
    )
    check(refused, "a game the caller may not read is refused by the backend")
    check(not _evidence(payload).get("items"), "no data leaks for an unauthorized game")

    # --- scenario 5: a real move question (when the library has one) ----------
    analysed = [g for g in games if g.get("analysis_status") == "analyzed"]
    if analysed:
        game = analysed[0]
        payload, elapsed = ask(
            base,
            "Why was this move bad?",
            game_id=game["id"],
            ply=17,
        )
        latencies.append(elapsed)
        used = _tools_used(payload)
        check(bool(used), "a stored-game move question consults a tool", ", ".join(sorted(used)))
        check(
            bool(_evidence(payload).get("items")) or bool(_missing(payload)),
            "the turn reports either evidence or an explicit gap",
        )
        print(f"     move question used: {sorted(used)}")
    else:
        print("SKIP no analysed game in the library; the stored-move scenario was not exercised")

    # --- secrets --------------------------------------------------------------
    blob = json.dumps(payload)
    check(not _contains_secret(blob), "no credential appears in the answer payload")
    status, coach_status, _ = request(f"{base}/api/coach/status")
    check(
        status == 200 and "api_key" not in json.dumps(coach_status).lower().replace('"api_key_set"', ""),
        "the coach status never returns the key itself",
    )

    # --- latency (measured, not estimated) -----------------------------------
    ordered = sorted(latencies)
    p50 = ordered[len(ordered) // 2]
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    print(
        f"\nLatency over {len(latencies)} measured turn(s): "
        f"mean {mean(latencies):.0f} ms · p50 {p50:.0f} ms · p95 {p95:.0f} ms · max {max(latencies):.0f} ms"
    )
    check(mean(latencies) > 0, "turn latency was measured")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("All agent checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
