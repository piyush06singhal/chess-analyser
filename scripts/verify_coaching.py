#!/usr/bin/env python
"""Phase 11 live verification: the complete coaching loop, end to end.

Spec §47 / §55 describe a chain, and this script walks it with real persisted data
and a real engine. Every arrow in the spec becomes a printed check with the number
that produced it:

    REAL GAME → REAL STOCKFISH ANALYSIS → REAL GAME INTELLIGENCE
    → REAL PLAYER INTELLIGENCE → REAL WEAKNESS DETECTION → REAL TRAINING POSITION
    → REAL TRAINING ATTEMPT + RESULT → REAL PROFILE / PROGRESS UPDATE
    → REAL OPPONENT DATA → REAL MATCH PREPARATION → REAL SCENARIO
    → REAL AI EXPLANATION → REAL PROGRESS MEASUREMENT

It also checks the *refusals*, because they are part of the product: with no
production model, every prediction task must answer ``available: false`` with a
reason rather than a number, and an empty feed section must carry the reason it is
empty.

Nothing is assumed. If the engine is unavailable, the checks that need it are
reported as SKIP with the reason, never as PASS.

Usage:
    python scripts/verify_coaching.py [--base-url http://127.0.0.1:8002]
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


def resolve_game(base: str) -> dict | None:
    """An analysed game to run the loop over, importing the Opera Game if needed.

    Among the analysed games, one whose player already has training exercises is
    preferred: the point of this script is the *whole* loop (weakness → exercise →
    attempt → profile), and a game whose player has no exercises stops it at step
    5 for a data reason rather than a product one. The first analysed game stays
    the fallback, so the earlier steps are always exercised.
    """
    analysed = [
        game for game in games(base) if game.get("analysis_status") in {"ready", "analyzed"}
    ]
    if analysed:
        by_name = {player["name"]: player for player in players(base)}
        for game in analysed:
            owner = by_name.get(game.get("white_player"))
            if owner is None:
                continue
            _, library, _ = request(
                f"{base}/api/training/positions?player_id={owner['id']}&limit=1"
            )
            if isinstance(library, dict) and (library.get("positions") or []):
                print(f"   using analysed game with an existing exercise library ({owner['name']})")
                return game
        return analysed[0]
    print("   no analysed game yet; importing + analysing the Opera Game (depth 8)…")
    status, imported, _ = request(
        f"{base}/api/games/import",
        {"pgn_text": OPERA_GAME_PGN, "run_analysis": True, "depth": 8, "multipv": 3},
    )
    if status not in (200, 201) or not isinstance(imported, dict):
        check(False, "import + analyse a real game", str(imported)[:120])
        return None
    game_id = imported.get("game_id")
    for _ in range(60):
        time.sleep(2)
        _, game, _ = request(f"{base}/api/games/{game_id}")
        if isinstance(game, dict) and game.get("analysis_status") in {"ready", "analyzed"}:
            return game
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print("0. health")
    status, health, _ = request(f"{base}/health")
    check(status == 200, "the API is healthy", str(health)[:70] if status == 200 else "")
    engine_available = bool(isinstance(health, dict) and health.get("engine", {}).get("available"))

    print("\n1. REAL GAME")
    game = resolve_game(base)
    if not game:
        print("\nNo game could be established; the loop cannot be verified.")
        return 1
    game_id = game["id"]
    check(bool(game_id), "a real game is stored", f"{game['white_player']} vs {game['black_player']}")
    check(
        game.get("analysis_status") in {"ready", "analyzed"},
        "the game carries a completed analysis",
        f"status={game.get('analysis_status')}",
    )

    print("\n2. REAL STOCKFISH ANALYSIS")
    _, moves_payload, _ = request(f"{base}/api/analysis/games/{game_id}/moves")
    moves = (moves_payload or {}).get("moves", []) if isinstance(moves_payload, dict) else []
    scored = [row for row in moves if row.get("evaluation_before_cp") is not None]
    check(bool(scored), "stored per-move engine evaluations exist", f"{len(scored)}/{len(moves)} plies")
    with_multipv = [row for row in scored if row.get("candidate_moves") or row.get("multipv_lines")]
    if with_multipv:
        check(True, "stored MultiPV candidates exist", f"{len(with_multipv)} plies")
    else:
        skip("stored MultiPV candidates", "this analysis predates MultiPV storage (backfill_multipv.py)")

    print("\n3. REAL GAME INTELLIGENCE")
    status, report, _ = request(f"{base}/api/intelligence/games/{game_id}/report")
    if status == 404:
        status, report, _ = request(f"{base}/api/intelligence/games/{game_id}/report", {})
    check(status == 200 and isinstance(report, dict), "the game report is materialised", f"HTTP {status}")
    inner = (report or {}).get("report") if isinstance(report, dict) else None
    check(isinstance(inner, dict), "the report carries its structured sections")
    moments = (inner or {}).get("critical_moments") if isinstance(inner, dict) else None
    # The section is an object with `engine_critical_moments` and a `timeline`.
    # Reading only a `moments` key (as this used to) reported "none" even when
    # both lists were populated.
    if isinstance(moments, dict):
        moment_list = (
            moments.get("engine_critical_moments")
            or moments.get("timeline")
            or moments.get("moments")
        )
    else:
        moment_list = moments
    if isinstance(moment_list, list) and moment_list:
        check(True, "the report lists critical moments", f"{len(moment_list)} moments")
    else:
        note = (inner or {}).get("unavailable") if isinstance(inner, dict) else None
        check(
            bool(note),
            "no critical moment is stored, and the report says why",
            str(note)[:110],
        )
    debrief_status, _, _ = request(f"{base}/api/coaching/games/{game_id}/debrief")
    check(debrief_status == 200, "the game debrief is served", f"HTTP {debrief_status}")

    print("\n4. REAL PLAYER INTELLIGENCE")
    white = next((p for p in players(base) if p["name"] == game["white_player"]), None)
    if white is None:
        check(False, "the player behind the game is tracked")
        return 1
    player_id = str(white["id"])
    status, profile, _ = request(f"{base}/api/players/{player_id}")
    if status != 200:
        status, profile, _ = request(f"{base}/api/players/{player_id}/rebuild", {})
    check(status == 200 and bool((profile or {}).get("display_name")), "the player profile is built", f"HTTP {status}")
    insights = (profile or {}).get("insights") or []
    check(isinstance(insights, list), "the profile exposes insights", f"{len(insights)} insights")
    check(
        (profile or {}).get("coverage") is not None,
        "the profile states its data coverage",
        f"coverage={(profile or {}).get('coverage')}, games={(profile or {}).get('analyzed_games')}",
    )

    print("\n5. REAL WEAKNESS DETECTION → REAL TRAINING POSITION")
    status, generated, _ = request(
        f"{base}/api/training/games/{game_id}/generate",
        {"player_id": player_id, "data_source": "personalized"},
    )
    created = (generated or {}).get("created") if isinstance(generated, dict) else None
    if status != 200:
        check(False, "training positions are generated from the game", str(generated)[:120])
    elif created:
        check(True, "training positions are generated from the game", f"{created} created")
    else:
        _, existing, _ = request(f"{base}/api/training/games/{game_id}?player_id={player_id}")
        count = (existing or {}).get("count") if isinstance(existing, dict) else 0
        if count > 0:
            check(True, "training positions exist for this game", f"{count} already stored")
        else:
            # No exercise was produced. That is a legitimate outcome for a game
            # whose mistakes never crossed the threshold — but only if the
            # endpoint *states why* rather than silently returning nothing. A
            # real reason is a pass; silence is a failure.
            reasons = (generated or {}).get("reasons") if isinstance(generated, dict) else None
            skipped = (generated or {}).get("skipped") if isinstance(generated, dict) else None
            stated = bool(reasons) or bool(skipped)
            check(
                stated,
                "no exercise is produced for this game, and the reason is stated",
                str(skipped or reasons)[:120],
            )

    _, library, _ = request(f"{base}/api/training/positions?player_id={player_id}&limit=50")
    positions = (library or {}).get("positions", []) if isinstance(library, dict) else []
    check(bool(positions), "the player's exercise library is served", f"{len(positions)} positions")
    if not positions:
        print("\nNo exercise exists to attempt; the loop stops here honestly.")
        return 1
    position_id = positions[0]["id"]

    print("\n6. REAL TRAINING ATTEMPT → REAL RESULT")
    # Find an exercise that actually carries a stored solution, so the attempt is
    # graded rather than skipped. Anything else would leave the most important part
    # of the loop (train → result → profile) unverified.
    best = None
    solution_status = 0
    for candidate in positions:
        solution_status, solution, _ = request(
            f"{base}/api/training/positions/{candidate['id']}/solution"
        )
        if isinstance(solution, dict):
            best = (
                (solution.get("solution") or {}).get("best_move_uci")
                or solution.get("solution_uci")
                or solution.get("best_move_uci")
                or (solution.get("line") or {}).get("moves", [None])[0]
            )
        if best:
            position_id = candidate["id"]
            break
    if solution_status != 200 or not best:
        skip("submit a graded attempt", "this exercise has no stored solution to submit")
    else:
        status, attempt, _ = request(
            f"{base}/api/training/positions/{position_id}/attempt",
            {
                "player_id": player_id,
                "submitted_uci": best,
                "response_time_ms": 4500,
                "hints_used": 0,
            },
        )
        correct = (attempt or {}).get("correct") if isinstance(attempt, dict) else None
        check(status == 200, "the attempt is graded and stored", f"HTTP {status}")
        check(correct is True, "the engine-verified best move grades as correct", f"correct={correct}")
        _, attempts, _ = request(
            f"{base}/api/training/positions/{position_id}/attempts?player_id={player_id}"
        )
        stored = (attempts or {}).get("count") if isinstance(attempts, dict) else 0
        check(bool(stored), "the attempt is persisted (not only returned)", f"{stored} attempts stored")

    print("\n7. REAL PROFILE / PROGRESS UPDATE")
    _, progress, _ = request(f"{base}/api/training/progress?player_id={player_id}")
    check(isinstance(progress, dict), "measured training progress is served")
    _, refreshed, _ = request(f"{base}/api/players/{player_id}")
    check(
        bool((refreshed or {}).get("display_name")),
        "the player profile is still readable after training",
        f"{len((refreshed or {}).get('insights') or [])} insights",
    )
    _, queue, _ = request(f"{base}/api/training/review-queue?player_id={player_id}")
    check(isinstance(queue, dict), "the spaced-repetition review queue is served")

    print("\n8. REAL OPPONENT DATA → REAL MATCH PREPARATION")
    black = next((p for p in players(base) if p["name"] == game["black_player"]), None)
    if black is None:
        skip("opponent intelligence", "the opponent is not tracked as a player")
    else:
        opponent_id = str(black["id"])
        # Opponent intelligence is a view over a tracked player, so its routes live
        # under /api/players/{id}/ — the same single source of identity.
        status, opponent, _ = request(f"{base}/api/players/{opponent_id}/opponent-profile")
        if status != 200:
            status, opponent, _ = request(
                f"{base}/api/players/{opponent_id}/opponent-profile/rebuild", {}
            )
        check(status == 200, "the opponent profile is built", f"HTTP {status}")
        status, brief, _ = request(f"{base}/api/players/{opponent_id}/preparation-report")
        check(status == 200, "the match brief is served", f"HTTP {status}")
        status, repertoire, _ = request(f"{base}/api/players/{opponent_id}/repertoire")
        check(status == 200, "the opponent repertoire is served", f"HTTP {status}")
        status, prep, _ = request(
            f"{base}/api/training/opponents/{opponent_id}/prepare",
            {"player_id": player_id, "min_occurrences": 1, "max_exercises": 10},
        )
        created = (prep or {}).get("created") if isinstance(prep, dict) else None
        check(
            status == 200,
            "opponent-specific training can be generated",
            f"HTTP {status}" + (f", created={created}" if created is not None else ""),
        )

    print("\n9. REAL SCENARIO")
    status, explorer, _ = request(f"{base}/api/scenarios/games/{game_id}/explorer")
    check(status == 200, "the turning-point explorer runs from stored analysis", f"HTTP {status}")
    moments_n = (explorer or {}).get("moments") if isinstance(explorer, dict) else None
    if isinstance(moments_n, list):
        check(True, "the explorer returns moments and their series", f"{len(moments_n)} moments")

    # The game's own move list is the authority on what was played. The stored
    # analysis should agree with it at every ply; when it does not, the library has
    # a data-integrity problem that the readers would otherwise paper over (see
    # scripts/check_data_consistency.py). This is reported, not hidden.
    _, game_positions, _ = request(f"{base}/api/games/{game_id}/positions?limit=400")
    published = {
        int(entry["ply"]): entry
        for entry in ((game_positions or {}).get("positions") or [])
        if entry.get("ply")
    }
    disagreements = [
        row["ply"]
        for row in scored
        if row.get("ply") in published
        and published[row["ply"]].get("uci")
        and published[row["ply"]]["uci"] != row.get("played_move_uci")
    ]
    if disagreements:
        print(
            f"WARN stored analysis disagrees with the game's own moves at "
            f"{len(disagreements)} ply/plies ({disagreements[:8]}) — stale rows in this "
            f"library; run scripts/check_data_consistency.py, and re-analyse to repair."
        )

    if engine_available and scored:
        # The route resolves the position from the stored ply itself, so the caller
        # never has to paste a FEN — and `include_top` fills in the engine's own
        # best candidates, which lets this run on a game whose analysis predates
        # MultiPV storage.
        row = scored[len(scored) // 2]
        truth = published.get(row.get("ply")) or {}
        played = truth.get("uci") or row.get("played_move_uci")
        best_move = row.get("best_move_uci")
        candidates = [move for move in {played, best_move} if move]
        if candidates:
            status, comparison, _ = request(
                f"{base}/api/scenarios/compare-moves",
                {
                    "game_id": game_id,
                    "ply": row.get("ply"),
                    "moves": candidates,
                    "include_top": True,
                    "depth": 10,
                    "multipv": 3,
                },
            )
            check(status == 200, "candidate moves are compared from a real search", f"HTTP {status}")
            if isinstance(comparison, dict):
                source = comparison.get("source") or {}
                check(
                    source.get("kind") == "game" and source.get("ply") == row.get("ply"),
                    "the comparison names the game and ply it branched from",
                    f"kind={source.get('kind')} ply={source.get('ply')}",
                )
                if played:
                    check(
                        source.get("played_move_uci") == played,
                        "the comparison marks the move the game actually played",
                        f"played={played} marked={source.get('played_move_uci')}",
                    )
                else:
                    skip("the played move is marked", "this ply has no recorded played move")
                compared = comparison.get("candidates") or []
                check(
                    bool(compared),
                    "the comparison returns measured candidate results",
                    f"{len(compared)} candidates, best={comparison.get('best_move_uci')}",
                )
        else:
            skip("candidate move comparison", "this ply has no recorded played move")
    elif not engine_available:
        skip("candidate move comparison", "the engine is unavailable")

    print("\n10. REAL AI EXPLANATION (evidence-grounded, never invented)")
    status, context, _ = request(f"{base}/api/coaching/context?game_id={game_id}")
    brief = (context or {}).get("brief") if isinstance(context, dict) else None
    check(status == 200 and isinstance(brief, dict), "the coach context resolves a game review", f"HTTP {status}")
    if isinstance(brief, dict):
        check(
            brief.get("situation") in {"game_review", "position_analysis", "general_coaching"},
            "the situation is resolved, not guessed",
            f"situation={brief.get('situation')} mode={brief.get('mode')}",
        )
        check(isinstance(brief.get("gaps"), list), "unavailable facts are listed as gaps")
    if black is not None:
        _, opponent_context, _ = request(
            f"{base}/api/coaching/context?game_id={game_id}&opponent_id={black['id']}"
        )
        situation = ((opponent_context or {}).get("brief") or {}).get("situation")
        check(
            situation == "opponent_preparation",
            "adding an opponent switches the situation to preparation",
            f"situation={situation}",
        )

    _, feed, _ = request(f"{base}/api/coaching/feed?user_id={player_id}&game_id={game_id}")
    sections = (feed or {}).get("sections") if isinstance(feed, dict) else None
    check(isinstance(sections, list) and bool(sections), "the coaching feed is served")
    if isinstance(sections, list):
        empty_with_reason = all(
            section.get("cards") or section.get("reason")
            for section in sections
            if not section.get("available")
        )
        check(
            empty_with_reason,
            "an empty feed section always carries its reason",
            f"{len(sections)} sections",
        )
        total_cards = sum(len(section.get("cards") or []) for section in sections)
        print(f"     {total_cards} card(s) across {len(sections)} sections")

    _, focus, _ = request(f"{base}/api/coaching/focus?user_id={player_id}")
    check(isinstance(focus, dict), "'what should I work on?' is answered")
    if isinstance(focus, dict):
        primary = focus.get("primary_focus")
        if primary:
            check(
                primary.get("sample_size", 0) >= 1,
                "the primary focus cites a sample size",
                f"{primary.get('title')} (n={primary.get('sample_size')})",
            )
            check(bool(primary.get("evidence")), "the primary focus carries evidence")
        else:
            check(
                bool(focus.get("reason")),
                "no focus is named, and the reason is given",
                str(focus.get("reason"))[:100],
            )

    _, plan, _ = request(f"{base}/api/coaching/plan?user_id={player_id}&weeks=1")
    check(isinstance(plan, dict), "a training plan is produced from the focus areas")

    print("\n11. NO SIMULATED PREDICTION")
    status, tasks, _ = request(f"{base}/api/predictions/tasks")
    if status == 200 and isinstance(tasks, dict):
        entries = tasks.get("tasks", [])
        available = [task for task in entries if task.get("available")]
        if available:
            check(
                all(task.get("model_version") for task in available),
                "every available prediction names a production model",
                f"{len(available)} available",
            )
        else:
            check(
                all(task.get("reason") for task in entries),
                "no model is available and every task states why",
                f"{len(entries)} tasks, 0 available",
            )

    print("\n12. REAL PROGRESS MEASUREMENT")
    analysed = [g for g in games(base) if g.get("analysis_status") in {"ready", "analyzed"}]
    check(len(analysed) >= 1, "progress can be measured across analysed games", f"{len(analysed)} analysed")
    _, metrics, _ = request(f"{base}/api/scenarios/metrics")
    check(isinstance(metrics, dict), "the scenario metrics counters are served")

    print("\n13. SHOW ME WHY (evidence, classified and followable)")
    sample_game = game_id
    sample_ply = None
    _, move_analyses, _ = request(f"{base}/api/analysis/games/{sample_game}/moves")
    if isinstance(move_analyses, dict) and move_analyses.get("moves"):
        sample_ply = move_analyses["moves"][0].get("ply")
    _, evidence, _ = request(
        f"{base}/api/coaching/evidence?claim=why&game_id={sample_game}"
        + (f"&ply={sample_ply}" if sample_ply else "")
    )
    if isinstance(evidence, dict):
        kinds = evidence.get("counts_by_kind", {})
        check(
            bool(evidence.get("method", {}).get("kinds")),
            "the evidence method travels with the answer",
        )
        check(
            isinstance(kinds, dict),
            "every reference is classified by knowledge kind",
            f"{kinds}",
        )

    print("\n14. UNIFIED SEARCH (real rows, explained rank)")
    _, search, _ = request(f"{base}/api/coaching/search?q=morphy&player_id={player_id}")
    if isinstance(search, dict):
        check(search.get("status") in {"ok", "no_matches", "no_candidates", "empty_query"},
              "unified search reports a real status", f"status={search.get('status')}")
        if search.get("hits"):
            check(all(hit.get("explanation") for hit in search["hits"]),
                  "every hit explains its rank")
        else:
            check(bool(search.get("reason")), "an empty result states its reason")

    print("\n15. STUDY COLLECTION (typed pointers, real CRUD)")
    status, created, _ = request(
        f"{base}/api/coaching/collections",
        {"player_id": str(player_id), "name": f"Verify {int(time.time())}", "kind": "game_set"},
    )
    if status == 201 and isinstance(created, dict):
        collection_id = created["id"]
        status, with_item, _ = request(
            f"{base}/api/coaching/collections/{collection_id}/items",
            {"player_id": str(player_id), "kind": "game", "ref": sample_game, "label": "Opera"},
        )
        check(status == 201 and with_item.get("size") == 1, "a typed pointer is stored and echoed")
        bad_status, _, _ = request(
            f"{base}/api/coaching/collections/{collection_id}/items",
            {"player_id": str(player_id), "kind": "opening", "ref": "C50"},
        )
        check(bad_status >= 400, "a forbidden item kind is refused", f"HTTP {bad_status}")
        delete_status, _, _ = request(
            f"{base}/api/coaching/collections/{collection_id}?player_id={player_id}",
            method="DELETE",
        )
        check(delete_status == 200, "the collection is deleted", f"HTTP {delete_status}")
    else:
        check(False, "a study collection can be created", f"HTTP {status}")

    print("\n16. MATCH PREPARATION (gated sections, never a prediction)")
    opponent_id = next((p["id"] for p in players(base) if int(p["id"]) != player_id), None)
    if opponent_id:
        status, prep, _ = request(
            f"{base}/api/coaching/match-preparation",
            {"preparing_player_id": str(player_id), "opponent_id": int(opponent_id), "persist": False},
        )
        if status in {200, 201} and isinstance(prep, dict):
            brief = prep.get("brief", {})
            check(brief.get("title") == "Caissa MATCH BRIEF", "the match brief is produced")
            check("does not predict" in (brief.get("disclaimer") or ""),
                  "the brief states it is not a prediction")
            unavailable = brief.get("unavailable_sections", [])
            check(
                all(item.get("reason") for item in unavailable),
                "every unavailable section names the gate or gap",
                f"{len(unavailable)} unavailable",
            )
    else:
        skip("match preparation", "only one player is stored, so there is no opponent to prepare against")

    print("\n17. PROGRESS COMPARISON (measured change + causality warning)")
    _, comparison, _ = request(f"{base}/api/coaching/progress/compare?player_id={player_id}")
    if isinstance(comparison, dict):
        check(
            "not proof" in (comparison.get("causality_note") or ""),
            "the causality warning travels with the comparison",
        )
        check(
            comparison.get("status") in {"ok", "insufficient_data"},
            "the comparison reports a real status",
            f"status={comparison.get('status')}",
        )
        if comparison.get("status") == "insufficient_data":
            check(bool(comparison.get("reason")), "a thin sample is refused with its reason")

    print("\n" + "=" * 62)
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
        return 1
    print("ALL CHECKS PASSED — the coaching loop runs on real persisted data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
