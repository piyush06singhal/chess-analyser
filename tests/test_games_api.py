"""API tests for the game import/validation/library/positions/status endpoints.

Imports here run without engine analysis (``run_analysis=false``) so they are
fast and deterministic; the engine pipeline itself is covered by the engine and
end-to-end tests.
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from tests.conftest import CHECK_PGN, ILLEGAL_MOVE_PGN, OPERA_GAME_PGN, SCHOLARS_MATE_PGN, START_FEN


def _import(client: TestClient, pgn: str = SCHOLARS_MATE_PGN) -> dict:
    response = client.post(
        "/api/games/import", json={"pgn_text": pgn, "run_analysis": False}
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestValidate:
    def test_valid_pgn(self, client: TestClient) -> None:
        body = client.post("/api/games/validate", json={"pgn_text": OPERA_GAME_PGN}).json()
        assert body["is_valid"] is True
        assert body["game_count"] == 1
        assert body["ply_count"] == 33
        assert body["issues"] == []
        assert "pgn_text" in body["available_sources"]

    def test_invalid_pgn_lists_structured_issues(self, client: TestClient) -> None:
        body = client.post("/api/games/validate", json={"pgn_text": ILLEGAL_MOVE_PGN}).json()
        assert body["is_valid"] is False
        assert body["issues"][0]["type"] == "ILLEGAL_MOVE"
        assert body["issues"][0]["move_number"] == 3
        assert body["errors"]

    def test_unknown_source_is_rejected_honestly(self, client: TestClient) -> None:
        # A platform Caissa cannot read must 422 with the real list, never fall back.
        response = client.post(
            "/api/games/validate", json={"pgn_text": OPERA_GAME_PGN, "source": "someothersite"}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_source"

    def test_lichess_pgn_validates_under_its_own_source(self, client: TestClient) -> None:
        response = client.post(
            "/api/games/validate", json={"pgn_text": OPERA_GAME_PGN, "source": "lichess"}
        )
        assert response.status_code == 200
        assert response.json()["is_valid"] is True

    def test_chesscom_pgn_validates_under_its_own_source(self, client: TestClient) -> None:
        """A Chess.com PGN is parsed by the same importer, with Chess.com provenance."""
        response = client.post(
            "/api/games/validate", json={"pgn_text": OPERA_GAME_PGN, "source": "chess_com"}
        )
        assert response.status_code == 200
        assert response.json()["is_valid"] is True


class TestImportAndLibrary:
    def test_import_persists_and_lists(self, client: TestClient) -> None:
        imported = _import(client)
        assert imported["moves"] == 7
        assert imported["analyzed"] is False
        assert imported["analysis_status"] == "ready"

        library = client.get("/api/games").json()
        assert library["count"] == 1
        item = library["games"][0]
        assert item["id"] == imported["game_id"]
        assert item["analysis_status"] == "ready"
        assert item["move_count"] == 7

    def test_invalid_import_returns_422(self, client: TestClient) -> None:
        response = client.post("/api/games/import", json={"pgn_text": ILLEGAL_MOVE_PGN})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_pgn"

    def test_empty_pgn_is_a_validation_error(self, client: TestClient) -> None:
        response = client.post("/api/games/import", json={"pgn_text": "   "})
        assert response.status_code == 422


class TestDetailPositionsStatus:
    def test_detail_has_moves_and_metadata(self, client: TestClient) -> None:
        imported = _import(client, OPERA_GAME_PGN)
        detail = client.get(f"/api/games/{imported['game_id']}").json()
        assert detail["white_player"] == "Paul Morphy"
        assert detail["eco_code"] == "C41"
        assert len(detail["moves"]) == 33
        assert detail["analysis_status"] == "ready"

    def test_positions_sequence(self, client: TestClient) -> None:
        imported = _import(client, OPERA_GAME_PGN)
        body = client.get(f"/api/games/{imported['game_id']}/positions").json()
        positions = body["positions"]
        assert body["count"] == 34
        assert positions[0]["ply"] == 0
        assert positions[0]["fen"] == START_FEN
        assert positions[0]["san"] is None
        assert positions[-1]["is_checkmate"] is True
        assert positions[-1]["terminal_reason"] == "checkmate"

    def test_status_endpoint(self, client: TestClient) -> None:
        imported = _import(client, CHECK_PGN)
        body = client.get(f"/api/games/{imported['game_id']}/status").json()
        assert body["analysis_status"] == "ready"
        assert body["positions_analyzed"] == 0

    def test_missing_game_is_404(self, client: TestClient) -> None:
        assert client.get("/api/games/does-not-exist").status_code == 404
        assert client.get("/api/games/does-not-exist/positions").status_code == 404
        assert client.get("/api/games/does-not-exist/status").status_code == 404
        assert client.delete("/api/games/does-not-exist").status_code == 404


class TestDelete:
    def test_delete_removes_game(self, client: TestClient) -> None:
        imported = _import(client)
        assert client.delete(f"/api/games/{imported['game_id']}").status_code == 204
        assert client.get(f"/api/games/{imported['game_id']}").status_code == 404
        assert client.get("/api/games").json()["count"] == 0


class TestFileUpload:
    def test_upload_valid_pgn_file(self, client: TestClient) -> None:
        files = {"file": ("game.pgn", io.BytesIO(OPERA_GAME_PGN.encode()), "application/x-chess-pgn")}
        response = client.post(
            "/api/games/import/file?run_analysis=false", files=files
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"] == "pgn_file"
        assert body["moves"] == 33

    def test_upload_wrong_extension_is_rejected(self, client: TestClient) -> None:
        files = {"file": ("evil.exe", io.BytesIO(b"1. e4 e5"), "application/octet-stream")}
        response = client.post("/api/games/import/file?run_analysis=false", files=files)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "upload_error"

    def test_upload_bad_content_is_rejected(self, client: TestClient) -> None:
        files = {"file": ("notes.pgn", io.BytesIO(b"hello world, not chess"), "text/plain")}
        response = client.post("/api/games/import/file?run_analysis=false", files=files)
        assert response.status_code == 422
