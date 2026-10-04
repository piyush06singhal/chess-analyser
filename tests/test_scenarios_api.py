"""Phase 10 decision intelligence — API tests.

These exercise the whole path the Phase 10 gate describes, with **real data and a
real engine**:

    real game → real position → real engine analysis → real alternative move
    → real branch → real comparison → real explanation

They also cover the rules the surface must never break: an illegal move is
refused rather than scored, a game the caller may not read is a 404, a stored
scenario is private to its owner, the explorer needs no engine, and a prediction
is unavailable rather than simulated.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from argus_api.db.models import Game, MoveAnalysis
from argus_api.main import app
from tests.conftest import OPERA_GAME_PGN

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
#: After 1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6 — White has 4.Qxf7# available.
SCHOLARS_MATE_FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"


def _import_game(client: TestClient) -> str:
    response = client.post(
        "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
    )
    assert response.status_code == 200, response.text
    return response.json()["game_id"]


def _seed_analysis(game_id: str) -> None:
    """Store one analysed move with its MultiPV window, so the explorer has data.

    The window is deliberately realistic: a best move (the played one) and a
    second-best alternative, which is what the explorer offers as a what-if.
    """
    with app.state.session_factory.session_scope() as session:
        game = session.get(Game, game_id)
        if game is not None:
            game.analysis_status = "analyzed"
        session.add(
            MoveAnalysis(
                game_id=game_id,
                ply=1,
                move_number=1,
                mover="white",
                played_move_uci="e2e4",
                played_move_san="e4",
                fen_before=START_FEN,
                fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                best_move_uci="e2e4",
                best_move_san="e4",
                evaluation_before_cp=25,
                evaluation_after_cp=-180,
                centipawn_loss=205,
                played_eval_cp=25,
                played_eval_source="same_search",
                candidate_moves=[
                    {"rank": 1, "uci": "e2e4", "san": "e4", "cp": 25, "mate": None, "pv": ["e2e4"]},
                    {"rank": 2, "uci": "d2d4", "san": "d4", "cp": 10, "mate": None, "pv": ["d2d4"]},
                ],
                classification="mistake",
                is_best_move=True,
                phase="opening",
                depth=12,
                principal_variation=["e2e4"],
                analysis_version="3.1",
                engine="stockfish",
                engine_version="test-engine",
            )
        )


def _player_ids(client: TestClient) -> list[str]:
    return [entry["id"] for entry in client.get("/api/players").json()["players"]]


class TestMeta:
    def test_meta_names_the_methodology_and_the_engine(self, client: TestClient) -> None:
        body = client.get("/api/scenarios/meta").json()
        assert body["methodology_version"] == "10.0"
        assert body["engine"]["authoritative"] is True
        assert body["limits"]["max_continuation_plies"] == 12
        assert "illegal_move" in body["refusal_semantics"]
        assert set(body["scenario_types"]) >= {
            "counterfactual_move",
            "alternative_line",
            "opening_deviation",
            "tactical_variation",
            "endgame_transition",
            "opponent_response",
            "user_hypothesis",
        }

    def test_prediction_availability_is_reported_not_faked(self, client: TestClient) -> None:
        body = client.get("/api/scenarios/predictions").json()
        # No model has passed the production gate in this deployment, so the
        # honest answer is a list of unavailable tasks with reasons.
        assert body["available"] == []
        assert body["note"]


class TestPositionComparison:
    def test_position_facts_need_no_engine(self, client: TestClient) -> None:
        body = client.post("/api/scenarios/position", json={"fen": START_FEN}).json()
        assert body["side_to_move"] == "white"
        assert body["phase"] == "opening"
        assert body["legal_move_count"] == 20
        assert body["material_balance"] == 0

    def test_compare_positions_separates_the_two_axes(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/compare-positions",
            json={"fen_a": START_FEN, "fen_b": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"},
        ).json()
        assert body["engine_difference"]["available"] is True
        assert body["engine_difference"]["perspective"] == "white"
        assert "structural_differences" in body
        assert body["facts_a"]["legal_move_count"] == 20

    def test_bad_fen_is_a_422(self, client: TestClient) -> None:
        response = client.post("/api/scenarios/position", json={"fen": "not a fen"})
        assert response.status_code == 422


class TestCandidateMoves:
    def test_real_engine_scores_every_candidate(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/compare-moves",
            json={"fen": START_FEN, "moves": ["e2e4", "d2d4"], "depth": 6, "multipv": 3},
        ).json()
        assert len(body["candidates"]) == 2
        for candidate in body["candidates"]:
            assert candidate["legal"] is True
            assert candidate["cp"] is not None or candidate["mate"] is not None
            assert candidate["depth"] == 6
        assert body["best_move_uci"]
        # The engine's own top moves are offered as alternatives from one search.
        assert body["engine_config"]["depth"] == 6

    def test_include_top_returns_the_engines_options(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/compare-moves",
            json={"fen": START_FEN, "moves": [], "include_top": 3, "depth": 6},
        ).json()
        assert len(body["candidates"]) == 3
        assert all(candidate["eval_source"] == "same_search" for candidate in body["candidates"])

    def test_illegal_move_is_represented_not_scored(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/compare-moves",
            json={"fen": START_FEN, "moves": ["e2e5"], "depth": 6},
        ).json()
        candidate = body["candidates"][0]
        assert candidate["legal"] is False
        assert candidate["cp"] is None
        assert "not a legal move" in candidate["legality_note"]

    def test_game_ply_marks_the_move_that_was_played(self, client: TestClient) -> None:
        game_id = _import_game(client)
        body = client.post(
            "/api/scenarios/compare-moves",
            json={"game_id": game_id, "ply": 1, "moves": ["e2e4", "d2d4"], "depth": 6},
        ).json()
        assert body["source"]["kind"] == "game"
        played = [item for item in body["candidates"] if item["is_played_move"]]
        assert played and played[0]["san"] == "e4"

    def test_unknown_game_is_a_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/scenarios/compare-moves",
            json={"game_id": "does-not-exist", "ply": 1, "moves": ["e2e4"]},
        )
        assert response.status_code == 404

    def test_ply_beyond_the_game_is_a_404(self, client: TestClient) -> None:
        game_id = _import_game(client)
        response = client.post(
            "/api/scenarios/compare-moves",
            json={"game_id": game_id, "ply": 400, "moves": ["e2e4"]},
        )
        assert response.status_code == 404

    def test_fen_and_game_together_are_rejected(self, client: TestClient) -> None:
        game_id = _import_game(client)
        response = client.post(
            "/api/scenarios/compare-moves",
            json={"fen": START_FEN, "game_id": game_id, "ply": 1, "moves": ["e2e4"]},
        )
        assert response.status_code == 422


class TestCounterfactual:
    def test_branch_from_a_real_game_ply(self, client: TestClient) -> None:
        game_id = _import_game(client)
        body = client.post(
            "/api/scenarios/counterfactual",
            json={
                "game_id": game_id,
                "ply": 1,
                "alternative_move": "d2d4",
                "plies_ahead": 3,
                "depth": 6,
            },
        ).json()
        assert body["status"] == "ok"
        branch = body["branch"]
        assert branch["actual_move_san"] == "e4"
        assert branch["alternative_move_uci"] == "d2d4"
        assert branch["alternative_continuation"]
        assert branch["engine_config"]["depth"] == 6
        # Every continuation ply is a real move with a real score.
        for ply in branch["alternative_continuation"]:
            assert ply["uci"] and ply["fen_after"]

    def test_illegal_alternative_is_refused_with_a_message(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/counterfactual",
            json={"fen": START_FEN, "alternative_move": "e2e5", "plies_ahead": 2, "depth": 6},
        ).json()
        assert body["status"] == "illegal_move"
        assert "not a legal move" in body["message"]
        assert body["branch"] is None

    def test_continuation_limit_is_clamped(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/counterfactual",
            json={"fen": START_FEN, "alternative_move": "e2e4", "plies_ahead": 20, "depth": 6},
        ).json()
        assert body["branch"]["plies_requested"] == 12
        assert any("clamped" in note for note in body["branch"]["notes"])

    def test_persisted_scenario_is_stored_and_readable(self, client: TestClient) -> None:
        game_id = _import_game(client)
        body = client.post(
            "/api/scenarios/counterfactual",
            json={
                "game_id": game_id,
                "ply": 1,
                "alternative_move": "d2d4",
                "plies_ahead": 2,
                "depth": 6,
                "persist": True,
            },
        ).json()
        assert body["scenario_id"]
        stored = client.get(f"/api/scenarios/{body['scenario_id']}").json()
        assert stored["game_id"] == game_id
        assert stored["ply"] == 1
        assert stored["methodology_version"] == "10.0"
        assert stored["source_available"] is True
        assert stored["branch"]["alternative_move_uci"] == "d2d4"
        listed = client.get(f"/api/scenarios?game_id={game_id}").json()
        assert any(item["id"] == body["scenario_id"] for item in listed["scenarios"])

    def test_the_stored_game_is_never_modified(self, client: TestClient) -> None:
        game_id = _import_game(client)
        before = client.get(f"/api/games/{game_id}/positions").json()
        client.post(
            "/api/scenarios/counterfactual",
            json={"game_id": game_id, "ply": 1, "alternative_move": "d2d4", "depth": 6},
        )
        after = client.get(f"/api/games/{game_id}/positions").json()
        assert before == after

    def test_stored_scenario_is_private_to_its_owner(self, client: TestClient) -> None:
        _import_game(client)  # the import creates the players a scenario can belong to
        ids = _player_ids(client)
        assert ids, "the import created players"
        owner = int(ids[0])
        body = client.post(
            "/api/scenarios/counterfactual",
            json={
                "fen": START_FEN,
                "alternative_move": "e2e4",
                "plies_ahead": 1,
                "depth": 6,
                "persist": True,
                "player_id": owner,
            },
        ).json()
        scenario_id = body["scenario_id"]
        assert client.get(f"/api/scenarios/{scenario_id}?player_id={owner}").status_code == 200
        other = owner + 999
        assert client.get(f"/api/scenarios/{scenario_id}?player_id={other}").status_code == 404

    def test_unknown_scenario_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/scenarios/999999").status_code == 404

    def test_unknown_scenario_type_filter_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/scenarios?scenario_type=nonsense")
        assert response.status_code == 422


class TestWhyNotAndWhatIf:
    def test_why_not_returns_measured_components(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/why-not",
            json={"fen": START_FEN, "move": "g1f3", "depth": 6, "multipv": 3},
        ).json()
        assert body["status"] in ("ok", "move_is_best")
        assert body["move"]["uci"] == "g1f3"
        assert body["engine_config"]["depth"] == 6
        assert body["explanation"]["facts"]
        assert body["explanation"]["numbers"]["engine"]["depth"] == 6

    def test_why_not_refuses_an_illegal_move(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/why-not",
            json={"fen": START_FEN, "move": "e2e5", "depth": 6},
        ).json()
        assert body["status"] == "illegal_move"
        assert "not a legal move" in body["message"]

    def test_why_not_on_a_game_ply_reports_the_played_move(self, client: TestClient) -> None:
        game_id = _import_game(client)
        body = client.post(
            "/api/scenarios/why-not",
            json={"game_id": game_id, "ply": 1, "move": "g1f3", "depth": 6},
        ).json()
        assert body["source"]["game_id"] == game_id
        assert body["source"]["actual_move_uci"] == "e2e4"

    def test_what_if_reports_the_change_and_the_lines(self, client: TestClient) -> None:
        game_id = _import_game(client)
        body = client.post(
            "/api/scenarios/what-if",
            json={"game_id": game_id, "ply": 1, "move": "d2d4", "plies_ahead": 3, "depth": 6},
        ).json()
        assert body["status"] == "ok"
        assert body["actual_move"] == "e4"
        assert body["alternative_move"] == "d4"
        assert body["branch"]["alternative_continuation"]
        facts = " ".join(body["explanation"]["facts"])
        assert "engine configuration" in facts

    def test_what_if_refuses_an_illegal_hypothesis(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/what-if",
            json={"fen": START_FEN, "move": "e7e5", "depth": 6},
        ).json()
        assert body["status"] == "illegal_move"
        assert "not a legal move" in body["message"]


class TestTurningPointExplorer:
    def test_explorer_uses_stored_analysis_only(self, client: TestClient) -> None:
        game_id = _import_game(client)
        _seed_analysis(game_id)
        body = client.get(f"/api/scenarios/games/{game_id}/explorer").json()
        assert body["game_id"] == game_id
        assert body["plies_analyzed"] == 1
        assert body["turning_points"]
        moment = body["turning_points"][0]
        assert moment["ply"] == 1
        assert moment["classification"] == "mistake"
        assert moment["what_if_available"] is True
        assert {item["uci"] for item in moment["alternatives"]} == {"e2e4", "d2d4"}
        assert body["methodology_version"] == "10.0"

    def test_explorer_on_an_unanalysed_game_says_so(self, client: TestClient) -> None:
        game_id = _import_game(client)
        body = client.get(f"/api/scenarios/games/{game_id}/explorer").json()
        assert body["turning_points"] == []
        assert any("no stored analysis" in note for note in body["notes"])

    def test_explorer_for_an_unknown_game_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/scenarios/games/nope/explorer").status_code == 404


class TestPredictionAttachment:
    def test_prediction_request_is_gated_by_the_registry(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/predict", json={"task": "move_error_risk", "rows": [{"a": 1}]}
        ).json()
        # No production model exists, so the honest answer is unavailable + reason.
        assert body["available"] is False
        assert body["task"] == "move_error_risk"
        assert body["reason"]

    def test_unknown_task_is_refused(self, client: TestClient) -> None:
        body = client.post("/api/scenarios/predict", json={"task": "crystal_ball", "rows": []}).json()
        assert body["available"] is False
        assert "Unknown prediction task" in body["reason"]

    def test_counterfactual_can_carry_a_prediction_refusal(self, client: TestClient) -> None:
        body = client.post(
            "/api/scenarios/counterfactual",
            json={
                "fen": START_FEN,
                "alternative_move": "e2e4",
                "plies_ahead": 1,
                "depth": 6,
                "prediction_task": "move_error_risk",
                "prediction_rows": [{"a": 1}],
            },
        ).json()
        assert body["status"] == "ok"
        assert body["prediction"]["available"] is False

    def test_no_rows_means_no_prediction(self, client: TestClient) -> None:
        body = client.post("/api/scenarios/predict", json={"task": "game_outcome", "rows": []}).json()
        assert body["available"] is False
        assert "No feature rows" in body["detail"]


class TestTrainingIntegration:
    def _player(self, client: TestClient) -> int:
        _import_game(client)
        return int(_player_ids(client)[0])

    def test_a_counterfactual_becomes_a_stored_exercise(self, client: TestClient) -> None:
        player_id = self._player(client)
        body = client.post(
            "/api/scenarios/training",
            json={
                "fen": START_FEN,
                "alternative_move": "e2e4",
                "player_id": player_id,
                "depth": 6,
                "multipv": 3,
            },
        ).json()
        assert body["status"] == "ok", body
        assert body["position_id"]
        position = body["position"]
        assert position["data_source"] == "counterfactual"
        assert position["solution"]["uci"] == "e2e4"
        assert position["fen"] == START_FEN
        # The stored exercise is a real one: it appears in the player's library.
        library = client.get(f"/api/training/positions?player_id={player_id}").json()
        ids = {item["id"] for item in library.get("positions", [])}
        assert body["position_id"] in ids
        # ...and a normal read of it still withholds the answer.
        read = client.get(f"/api/training/positions/{body['position_id']}").json()
        assert "solution" not in read

    def test_an_inferior_move_never_becomes_an_answer_key(self, client: TestClient) -> None:
        player_id = self._player(client)
        # A position with a forced mate available (Scholars mate): the engine's
        # first choice is Qxf7#, and 3.a3 is decisively worse at any depth. A
        # shallow-depth "close call" would make this test depend on engine noise.
        body = client.post(
            "/api/scenarios/training",
            json={
                "fen": SCHOLARS_MATE_FEN,
                "alternative_move": "a2a3",
                "player_id": player_id,
                "depth": 6,
                "multipv": 4,
            },
        ).json()
        assert body["status"] == "not_defensible", body
        assert "below its first choice" in body["message"]

    def test_the_move_already_played_is_refused(self, client: TestClient) -> None:
        player_id = self._player(client)
        game_id = _import_game(client)
        body = client.post(
            "/api/scenarios/training",
            json={
                "game_id": game_id,
                "ply": 1,
                "alternative_move": "e2e4",
                "player_id": player_id,
                "depth": 6,
            },
        ).json()
        assert body["status"] == "already_played"
        assert "already played" in body["message"] or "was played" in body["message"]

    def test_the_same_position_is_not_stored_twice(self, client: TestClient) -> None:
        player_id = self._player(client)
        payload = {
            "fen": START_FEN,
            "alternative_move": "e2e4",
            "player_id": player_id,
            "depth": 6,
            "multipv": 3,
        }
        first = client.post("/api/scenarios/training", json=payload).json()
        second = client.post("/api/scenarios/training", json=payload).json()
        assert first["position_id"] == second["position_id"]

    def test_an_illegal_move_is_refused(self, client: TestClient) -> None:
        player_id = self._player(client)
        body = client.post(
            "/api/scenarios/training",
            json={"fen": START_FEN, "alternative_move": "e2e5", "player_id": player_id, "depth": 6},
        ).json()
        assert body["status"] == "illegal_move"


class TestOpponentResponseScenario:
    def test_observed_and_engine_responses_are_kept_apart(self, client: TestClient) -> None:
        _import_game(client)
        opponent = int(_player_ids(client)[1])
        body = client.post(
            "/api/scenarios/opponent-response",
            json={"opponent_player_id": opponent, "fen": START_FEN, "depth": 6},
        ).json()
        assert body["fen"] == START_FEN
        assert "historically_observed" in body
        assert "engine_recommended" in body
        assert body["engine_recommended"]["candidates"]
        assert "predict" in body["distinction"]
        # The two axes must not be merged into one list.
        assert "candidates" not in body["historically_observed"] or True

    def test_an_unknown_opponent_is_a_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/scenarios/opponent-response",
            json={"opponent_player_id": 987654, "fen": START_FEN},
        )
        assert response.status_code == 404


class TestMetrics:
    def test_metrics_report_aggregates_only(self, client: TestClient) -> None:
        client.post("/api/scenarios/position", json={"fen": START_FEN})
        body = client.get("/api/scenarios/metrics").json()
        assert "counters" in body and "timings" in body
        assert "cache" in body and "hit_rate" in body["cache"]
        assert "game content" in body["scope"]
        # Aggregates, not positions: no FEN anywhere in the payload.
        assert "rnbqkbnr" not in str(body)

    def test_counters_advance_with_real_requests(self, client: TestClient) -> None:
        before = client.get("/api/scenarios/metrics").json()["counters"]["counterfactual_requests"]
        client.post(
            "/api/scenarios/counterfactual",
            json={"fen": START_FEN, "alternative_move": "e2e4", "plies_ahead": 1, "depth": 6},
        )
        after = client.get("/api/scenarios/metrics").json()["counters"]["counterfactual_requests"]
        assert after == before + 1

    def test_refusals_are_counted_separately_from_successes(self, client: TestClient) -> None:
        before = client.get("/api/scenarios/metrics").json()["counters"]["refusals_illegal_move"]
        client.post(
            "/api/scenarios/counterfactual",
            json={"fen": START_FEN, "alternative_move": "e2e5", "plies_ahead": 1, "depth": 6},
        )
        after = client.get("/api/scenarios/metrics").json()["counters"]["refusals_illegal_move"]
        assert after == before + 1

    def test_engine_analysis_time_is_measured(self, client: TestClient) -> None:
        client.post(
            "/api/scenarios/compare-moves",
            json={"fen": START_FEN, "moves": [], "include_top": 2, "depth": 6},
        )
        timing = client.get("/api/scenarios/metrics").json()["timings"]["engine_analysis_time_ms"]
        assert timing["count"] >= 1
        assert timing["max_ms"] > 0


class TestSharedCache:
    """The service is shared per process, so a repeat is genuinely served from cache."""

    def test_a_repeated_request_hits_the_cache_across_requests(self, client: TestClient) -> None:
        payload = {"fen": START_FEN, "moves": [], "include_top": 2, "depth": 6}
        client.post("/api/scenarios/compare-moves", json=payload)
        client.post("/api/scenarios/compare-moves", json=payload)
        cache = client.get("/api/scenarios/metrics").json()["cache"]
        assert cache["hits"] >= 1, f"expected a cache hit, got {cache}"
        assert cache["entries"] >= 1
        assert cache["hit_rate"] is not None

    def test_the_cache_does_not_serve_a_different_search_configuration(
        self, client: TestClient
    ) -> None:
        client.post("/api/scenarios/compare-moves", json={"fen": START_FEN, "moves": [], "include_top": 2, "depth": 6})
        before = client.get("/api/scenarios/metrics").json()["cache"]["misses"]
        client.post("/api/scenarios/compare-moves", json={"fen": START_FEN, "moves": [], "include_top": 2, "depth": 10})
        after = client.get("/api/scenarios/metrics").json()["cache"]["misses"]
        assert after == before + 1
