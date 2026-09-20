"""API tests: request validation, endpoints, error handling.

The engine-backed endpoints run against the real Stockfish binary at test
depth 6 (marked ``engine``); validation and persistence tests run without it.
"""

from __future__ import annotations

import pytest

from tests.conftest import OPERA_GAME_PGN, START_FEN

ENGINE_MARK = pytest.mark.engine


class TestHealth:
    def test_liveness(self, client):
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    @ENGINE_MARK
    def test_health_reports_dependencies_honestly(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"]["configured"] is True
        assert body["database"]["connected"] is True  # per-test SQLite
        assert body["engine"]["available"] is True


class TestRequestValidation:
    def test_import_requires_pgn_text(self, client):
        response = client.post("/api/games/import", json={})
        assert response.status_code == 422

    def test_import_rejects_empty_pgn_text(self, client):
        response = client.post("/api/games/import", json={"pgn_text": ""})
        assert response.status_code == 422

    def test_import_rejects_out_of_range_depth(self, client):
        response = client.post(
            "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "depth": 99}
        )
        assert response.status_code == 422

    def test_position_requires_fen(self, client):
        response = client.post("/api/analysis/position", json={})
        assert response.status_code == 422

    def test_validate_requires_pgn_text(self, client):
        response = client.post("/api/games/validate", json={})
        assert response.status_code == 422


class TestPgnValidationEndpoint:
    def test_valid_pgn(self, client):
        response = client.post("/api/games/validate", json={"pgn_text": OPERA_GAME_PGN})
        assert response.status_code == 200
        body = response.json()
        assert body["is_valid"] is True
        assert body["game_count"] == 1
        assert body["errors"] == []

    def test_invalid_pgn_is_rejected_with_errors(self, client):
        response = client.post("/api/games/validate", json={"pgn_text": "nonsense"})
        assert response.status_code == 200
        body = response.json()
        assert body["is_valid"] is False
        assert body["errors"]


class TestPositionAnalysisEndpoint:
    @ENGINE_MARK
    def test_position_analysis(self, client):
        response = client.post(
            "/api/analysis/position",
            json={"fen": START_FEN, "depth": 6, "multipv": 2},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["best_move_uci"]
        assert body["best_move_san"]
        assert len(body["lines"]) == 2
        assert body["lines"][0]["pv"]

    def test_invalid_fen_maps_to_422(self, client):
        response = client.post("/api/analysis/position", json={"fen": "not a fen"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_fen"

    @ENGINE_MARK
    def test_terminal_position_is_not_an_error(self, client):
        mate_fen = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
        response = client.post("/api/analysis/position", json={"fen": mate_fen, "depth": 6})
        assert response.status_code == 200
        body = response.json()
        assert body["is_terminal"] is True
        assert body["terminal_reason"] == "checkmate"


class TestGameImportAndAnalysis:
    @ENGINE_MARK
    def test_import_with_analysis_end_to_end(self, client):
        response = client.post(
            "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "depth": 6, "multipv": 2}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["moves"] == 33
        assert body["analyzed"] is True
        assert body["analysis"] is not None
        assert body["report"] is not None
        assert body["report"]["summary"]["total_moves"] == 33
        assert body["engine"]["available"] is True
        game_id = body["game_id"]
        assert game_id

        # The stored game is retrievable.
        detail = client.get(f"/api/games/{game_id}")
        assert detail.status_code == 200
        assert detail.json()["white_player"] == "Paul Morphy"
        assert len(detail.json()["moves"]) == 33

        # Stored analyses exist after import.
        stored = client.get(f"/api/analysis/{game_id}")
        assert stored.status_code == 200
        assert stored.json()["positions_analyzed"] == 33

        # Re-running analysis through the dedicated endpoint works.
        reanalysis = client.post("/api/analysis/game", json={"game_id": game_id, "depth": 6})
        assert reanalysis.status_code == 200
        assert reanalysis.json()["game_id"] == game_id

    def test_import_without_analysis_is_honest(self, client):
        response = client.post(
            "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["analyzed"] is False
        assert body["analysis"] is None
        assert body["report"] is None

    def test_invalid_pgn_import_maps_to_422(self, client):
        response = client.post("/api/games/import", json={"pgn_text": "garbage"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_pgn"

    def test_games_list_starts_empty(self, client):
        response = client.get("/api/games")
        assert response.status_code == 200
        assert response.json()["games"] == []
        assert response.json()["count"] == 0

    def test_missing_game_maps_to_404(self, client):
        response = client.get("/api/games/does-not-exist")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_missing_analysis_has_honest_note(self, client):
        # Import without analysis, then request stored analyses.
        imported = client.post(
            "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
        )
        game_id = imported.json()["game_id"]
        response = client.get(f"/api/analysis/{game_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["positions_analyzed"] == 0
        assert "No stored analysis" in body["note"]

    @ENGINE_MARK
    def test_analysis_of_unknown_game_maps_to_404(self, client):
        response = client.post("/api/analysis/game", json={"game_id": "missing"})
        assert response.status_code == 404
