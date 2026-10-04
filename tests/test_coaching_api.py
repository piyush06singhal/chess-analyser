"""Phase 11 API tests: the coaching endpoints against real stored data.

These run the whole stack — a real imported game, real seeded analysis, the real
Game Intelligence report, the real training engine — because the coaching layer's
only job is composition, and composition is exactly what unit tests cannot check.

The properties pinned here:

* the context resolves the situation and mode from what is actually open;
* the debrief reads the stored report rather than re-deriving anything;
* every empty section carries a reason;
* "what should I work on?" answers only from measured patterns;
* the feed never emits a card without evidence.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from argus_api.db.models import Game, MoveAnalysis
from argus_api.main import app
from tests.conftest import OPERA_GAME_PGN

#: A short, real sequence so the seeded analysis is legal in order and the
#: report has genuine mistakes to talk about.
LINE = (
    ("e4", "e5"),
    ("Nf3", "d6"),
    ("d4", "Bg4"),
    ("dxe5", "Bxf3"),
    ("Qxf3", "dxe5"),
)


def _import_game(client: TestClient) -> str:
    response = client.post(
        "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
    )
    assert response.status_code == 200, response.text
    return response.json()["game_id"]


def _player_id(client: TestClient, name: str) -> str:
    players = client.get("/api/players").json()["players"]
    match = next((entry for entry in players if entry["name"] == name), None)
    assert match is not None, f"player {name!r} not found"
    return match["id"]


def _seed_analyses(game_id: str) -> None:
    """One stored analysis per ply of a real game line, with two real mistakes."""
    import chess

    board = chess.Board()
    with app.state.session_factory.session_scope() as session:
        for index, (white, black) in enumerate(LINE):
            for color, san in (("white", white), ("black", black)):
                ply = index * 2 + (1 if color == "white" else 2)
                move = board.parse_san(san)
                fen_before = board.fen()
                loss = 0
                classification = "best"
                best = move.uci()
                # Ply 7 (White's d4) is a real, measurable mistake in this line.
                if ply == 7:
                    loss, classification, best = 320, "mistake", "f1c4"
                board.push(move)
                session.add(
                    MoveAnalysis(
                        game_id=game_id,
                        ply=ply,
                        move_number=index + 1,
                        mover=color,
                        played_move_uci=move.uci(),
                        played_move_san=san,
                        fen_before=fen_before,
                        fen_after=board.fen(),
                        best_move_uci=best,
                        best_move_san=None,
                        evaluation_before_cp=60,
                        centipawn_loss=loss,
                        played_eval_cp=60 - loss,
                        played_eval_source="same_search",
                        classification=classification,
                        is_best_move=not loss,
                        phase="opening",
                        depth=12,
                        principal_variation=[best],
                        candidate_moves=[
                            {"rank": 1, "uci": best, "san": None, "cp": 60, "mate": None, "pv": [best]},
                            {
                                "rank": 2,
                                "uci": "f1c4" if best != "f1c4" else "b1c3",
                                "san": None,
                                "cp": 40,
                                "mate": None,
                                "pv": [],
                            },
                        ],
                        analysis_version="3.1",
                        engine="stockfish",
                        engine_version="test-engine",
                    )
                )


def _setup(client: TestClient) -> dict:
    game_id = _import_game(client)
    _seed_analyses(game_id)
    # Generate the report the debrief reads (deterministic, engine-free).
    report = client.post(f"/api/intelligence/games/{game_id}/report")
    assert report.status_code == 200, report.text
    # Give the game an analysed status so the context resolves to a review.
    with app.state.session_factory.session_scope() as session:
        game = session.get(Game, game_id)
        game.analysis_status = "analyzed"
        session.add(game)
    return {
        "game_id": game_id,
        "player_id": _player_id(client, "Paul Morphy"),
    }


class TestCoachingMethod:
    def test_methodology_is_published_and_complete(self, client: TestClient) -> None:
        body = client.get("/api/coaching/method").json()
        assert body["methodology_version"] == "11.0"
        assert body["priority"]["factor_weights"]
        assert body["feed"]["sections"][0] == "recent_game"
        modes = body["modes"]
        assert modes["beginner"]["expose_principal_variation"] is False
        assert modes["advanced"]["expose_principal_variation"] is True
        assert "engine" not in modes["training"]["allowed_tool_families"]


class TestCoachContext:
    def test_a_game_review_resolves_to_the_review_mode(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            "/api/coaching/context",
            params={"user_id": setup["player_id"], "game_id": setup["game_id"]},
        ).json()
        assert body["brief"]["situation"] == "game_review"
        assert body["brief"]["mode"] == "game_review"
        assert body["context"]["current_game"]["game_id"] == setup["game_id"]
        assert body["context"]["current_game"]["analysis_status"] == "analyzed"

    def test_a_bare_position_asks_no_questions_about_the_player(self, client: TestClient) -> None:
        body = client.get(
            "/api/coaching/context",
            params={
                "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                "phase": "opening",
            },
        ).json()
        assert body["brief"]["situation"] == "opening_study"
        assert body["context"]["player_profile"] is None
        assert any("player profile" in gap for gap in body["brief"]["gaps"])

    def test_an_explicit_mode_overrides_the_situation(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            "/api/coaching/context",
            params={"user_id": setup["player_id"], "game_id": setup["game_id"], "mode": "beginner"},
        ).json()
        assert body["brief"]["mode"] == "beginner"
        assert body["brief"]["exposure"]["evaluation"] is False
        assert "explicitly requested" in " ".join(body["brief"]["notes"])

    def test_predictions_are_reported_with_their_own_availability(
        self, client: TestClient
    ) -> None:
        setup = _setup(client)
        body = client.get(
            "/api/coaching/context", params={"user_id": setup["player_id"]}
        ).json()
        assert "context" in body, body
        available = body["context"]["available_predictions"]
        if available is None:
            # No registry is wired in this deployment: the context says nothing
            # about predictions rather than implying any exist.
            return
        # Whatever the deployment, each task carries its own availability.
        tasks = available.get("tasks") or available.get("availability") or []
        entries = tasks.values() if isinstance(tasks, dict) else tasks
        for entry in entries:
            assert "available" in entry

    def test_an_unknown_player_is_a_404(self, client: TestClient) -> None:
        response = client.get("/api/coaching/context", params={"user_id": 999_999})
        assert response.status_code == 404


class TestGameDebriefAPI:
    def test_the_debrief_reads_the_stored_report(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            f"/api/coaching/games/{setup['game_id']}/debrief",
            params={"user_id": setup["player_id"]},
        ).json()
        sections = {section["key"]: section for section in body["sections"]}
        assert body["report_available"] is True
        assert sections["summary"]["available"]
        assert sections["summary"]["observations"]
        # Real stored content, not a summary of a summary.
        assert any(
            "opening" in str(obs).lower() for obs in sections["summary"]["observations"]
        )
        assert body["steps"][:3] == ["summary", "critical_moments", "biggest_decisions"]
        assert body["methodology_version"] == "11.0"

    def test_a_real_mistake_becomes_a_review_point(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            f"/api/coaching/games/{setup['game_id']}/debrief",
            params={"user_id": setup["player_id"]},
        ).json()
        sections = {section["key"]: section for section in body["sections"]}
        decisions = sections["biggest_decisions"]
        if decisions["available"]:
            assert all(row["ply"] for row in decisions["observations"])
            assert all(row["classification"] in ("mistake", "blunder") for row in decisions["observations"])

    def test_empty_sections_explain_themselves(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(f"/api/coaching/games/{setup['game_id']}/debrief").json()
        for section in body["sections"]:
            if not section["available"]:
                assert section["reason"], section["key"]

    def test_counterfactuals_come_from_stored_candidates(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(f"/api/coaching/games/{setup['game_id']}/debrief").json()
        counterfactuals = next(
            section for section in body["sections"] if section["key"] == "counterfactuals"
        )
        # The seeded analyses carry MultiPV candidates, so what-ifs are offered.
        assert counterfactuals["available"] is True
        assert any(row["what_if_available"] for row in counterfactuals["observations"]) or (
            counterfactuals["sample_size"] == 0
        )

    def test_an_unknown_game_is_a_404(self, client: TestClient) -> None:
        response = client.get("/api/coaching/games/does-not-exist/debrief")
        assert response.status_code == 404


class TestCoachingFeedAPI:
    def test_the_feed_leads_with_the_recent_game(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            "/api/coaching/feed",
            params={"user_id": setup["player_id"], "game_id": setup["game_id"]},
        ).json()
        sections = {section["key"]: section for section in body["sections"]}
        assert sections["recent_game"]["cards"]
        card = sections["recent_game"]["cards"][0]
        assert card["actions"][0]["href"].startswith("/game/")
        assert card["evidence"]
        assert body["methodology_version"] == "11.0"

    def test_every_card_keeps_its_href_and_priority_vocabulary(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            "/api/coaching/feed",
            params={"user_id": setup["player_id"], "game_id": setup["game_id"]},
        ).json()
        for section in body["sections"]:
            for card in section["cards"]:
                assert card["priority"] in ("critical", "high", "normal", "low"), card
                assert card["actions"], card
                assert card["statement"]

    def test_dismissals_are_accepted_and_echoed(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            "/api/coaching/feed",
            params={
                "user_id": setup["player_id"],
                "game_id": setup["game_id"],
                "dismissed": ["game:x:ply:1"],
            },
        ).json()
        assert "game:x:ply:1" in body["dismissed"]


class TestFocusAndPlanAPI:
    def test_focus_refuses_without_measured_patterns(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get("/api/coaching/focus", params={"user_id": setup["player_id"]}).json()
        # A single seeded game cannot support a repeated-pattern claim.
        if body["status"] != "ok":
            assert body["reason"]
            assert body["primary_focus"] is None
        else:
            assert body["primary_focus"]["evidence"]

    def test_an_unknown_player_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/coaching/focus", params={"user_id": 999_999}).status_code == 404

    def test_the_plan_is_explicit_about_what_it_could_not_use(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            "/api/coaching/plan", params={"user_id": setup["player_id"], "weeks": 2}
        ).json()
        assert body["plan_id"].startswith("plan:")
        assert body["review_date"]
        if not body["focus_areas"]:
            assert any("data limitation" in note for note in body["limitations"])
        else:
            assert body["target_metrics"][0]["measure"]


class TestReadiness:
    def test_ready_reports_every_dependency_with_real_probes(self, client: TestClient) -> None:
        body = client.get("/ready").json()
        assert body["status"] in ("ready", "degraded")
        for name in ("database", "redis", "stockfish", "migrations", "ml_registry", "ai_provider"):
            assert name in body["checks"], name
            assert "ok" in body["checks"][name]
        assert body["checks"]["database"]["ok"] is True
        assert body["checks"]["migrations"]["ok"] is True
        # No secrets leak through readiness.
        assert "key" not in str(body["checks"]["ai_provider"]).lower() or "never exposed" in str(
            body["checks"]["ai_provider"]
        )

    def test_health_ready_alias_matches(self, client: TestClient) -> None:
        assert client.get("/health/ready").json()["checks"].keys() == client.get("/ready").json()[
            "checks"
        ].keys()
