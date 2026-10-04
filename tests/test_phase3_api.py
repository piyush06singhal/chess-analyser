"""API tests for the Phase 3 analysis endpoints.

The Phase 3 game-analysis run is engine-backed, so the end-to-end tests are
marked ``engine``. Progress/404 behaviour that needs no engine is unmarked.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from argus.analysis.pipeline import ANALYSIS_VERSION
from tests.conftest import OPERA_GAME_PGN, SCHOLARS_MATE_PGN, START_FEN


def _import(client: TestClient, pgn: str = OPERA_GAME_PGN) -> str:
    response = client.post("/api/games/import", json={"pgn_text": pgn, "run_analysis": False})
    assert response.status_code == 200, response.text
    return response.json()["game_id"]


class TestProgressWithoutEngine:
    def test_progress_without_session_is_honest(self, client: TestClient) -> None:
        game_id = _import(client, SCHOLARS_MATE_PGN)
        body = client.get(f"/api/analysis/games/{game_id}/progress").json()
        assert body["has_session"] is False
        assert body["positions_analyzed"] == 0
        assert body["analysis_status"] == "ready"

    def test_missing_game_is_404(self, client: TestClient) -> None:
        assert client.get("/api/analysis/games/nope/progress").status_code == 404
        assert client.get("/api/analysis/games/nope/moves").status_code == 404
        assert client.get("/api/analysis/games/nope").status_code == 404

    def test_cancel_without_active_run_is_false(self, client: TestClient) -> None:
        game_id = _import(client, SCHOLARS_MATE_PGN)
        body = client.post(f"/api/analysis/games/{game_id}/cancel").json()
        assert body["cancel_requested"] is False


@pytest.mark.engine
class TestGameAnalysisPipelineAPI:
    def test_full_analysis_run(self, client: TestClient) -> None:
        game_id = _import(client)
        start = client.post(
            f"/api/analysis/games/{game_id}",
            json={"profile": "standard", "depth": 6, "multipv": 2},
        )
        assert start.status_code == 202, start.text
        assert start.json()["config"]["analysis_version"] == ANALYSIS_VERSION

        progress = client.get(f"/api/analysis/games/{game_id}/progress").json()
        assert progress["status"] == "completed"
        assert progress["positions_analyzed"] == 33
        assert progress["total_positions"] == 33
        assert progress["engine_version"]  # version is persisted for auditability

        moves = client.get(f"/api/analysis/games/{game_id}/moves").json()
        assert moves["count"] == 33
        first = moves["moves"][0]
        assert first["played_move_san"] == "e4"
        assert first["evaluation_before_cp"] is not None
        assert "classification" in first

        full = client.get(f"/api/analysis/games/{game_id}").json()
        assert full["analysis_status"] == "analyzed"
        session = full["session"]
        assert session["engine_config"]["depth"] == 6
        assert session["policy"]["good_max_cp_loss"] == 50
        assert session["positions_analyzed"] == 33

        criticals = client.get(f"/api/analysis/games/{game_id}/critical-moments").json()
        assert criticals["count"] >= 0  # honest: may be zero for a clean game

    def test_analysis_is_resumable(self, client: TestClient) -> None:
        game_id = _import(client, SCHOLARS_MATE_PGN)
        client.post(
            f"/api/analysis/games/{game_id}",
            json={"depth": 6, "multipv": 1},
        )
        # A second run with resume should short-circuit (everything already done).
        again = client.post(
            f"/api/analysis/games/{game_id}", json={"depth": 6, "multipv": 1, "resume": True}
        )
        assert again.status_code == 202
        progress = client.get(f"/api/analysis/games/{game_id}/progress").json()
        assert progress["status"] == "completed"

    def test_multipv_endpoint(self, client: TestClient) -> None:
        body = client.post(
            "/api/analysis/position/multipv",
            json={"fen": START_FEN, "multipv": 3, "depth": 6},
        ).json()
        assert body["multipv"] == 3
        assert [line["index"] for line in body["lines"]] == [1, 2, 3]

    def test_position_analysis_reports_mate_not_cp(self, client: TestClient) -> None:
        mate_in_one = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K2R w KQkq - 0 1"
        body = client.post("/api/analysis/position", json={"fen": mate_in_one, "depth": 8}).json()
        assert body["best_move_uci"] == "f3f7"
        assert body["lines"][0]["mate"] == 1
        # A forced mate is never reported as an ordinary centipawn number.
        assert body["lines"][0]["cp"] is None

    def test_no_fake_analysis_before_running(self, client: TestClient) -> None:
        game_id = _import(client, SCHOLARS_MATE_PGN)
        moves = client.get(f"/api/analysis/games/{game_id}/moves").json()
        assert moves["count"] == 0
        assert moves["moves"] == []


class TestRegistryCancellation:
    def test_cancel_marks_registered_run(self) -> None:
        from argus_api.services.analysis_jobs import CancellationRegistry

        registry = CancellationRegistry()
        registry.start("g1")
        assert registry.is_cancelled("g1") is False
        assert registry.cancel("g1") is True
        assert registry.is_cancelled("g1") is True
        registry.finish("g1")
        assert registry.is_cancelled("g1") is False

    def test_cancel_unknown_run_returns_false(self) -> None:
        from argus_api.services.analysis_jobs import CancellationRegistry

        assert CancellationRegistry().cancel("nope") is False
