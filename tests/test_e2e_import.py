"""End-to-end test: the same position must be consistent across the pipeline.

PGN -> import -> validation -> database -> position generation -> API response
-> the exact FEN the frontend chessboard renders. Every hop is checked against
the previous one so drift is impossible to miss.
"""

from __future__ import annotations

import chess
import pytest
from fastapi.testclient import TestClient

from tests.conftest import OPERA_GAME_PGN


class TestEndToEndImportToBoard:
    def test_positions_match_moves_at_every_ply(self, client: TestClient) -> None:
        # 1. Import (validation + position generation + persistence).
        imported = client.post(
            "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
        ).json()
        game_id = imported["game_id"]

        # 2. The frontend loads the game and its canonical positions.
        detail = client.get(f"/api/games/{game_id}").json()
        positions = client.get(f"/api/games/{game_id}/positions").json()["positions"]

        # 3. Position sequence agrees with the move list hop by hop.
        assert len(positions) == len(detail["moves"]) + 1
        assert positions[0]["fen"] == detail["initial_position"]
        assert positions[-1]["fen"] == detail["final_position"]
        for move, before, after in zip(detail["moves"], positions, positions[1:]):
            assert before["ply"] == move["ply"] - 1
            assert after["ply"] == move["ply"]
            assert after["fen"] == move["fen_after"]
            assert after["previous_fen"] == before["resulting_fen"]
            assert after["san"] == move["san"]
            assert after["uci"] == move["uci"]

    def test_navigating_the_board_never_drifts(self, client: TestClient) -> None:
        imported = client.post(
            "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
        ).json()
        game_id = imported["game_id"]
        positions = client.get(f"/api/games/{game_id}/positions").json()["positions"]

        # Simulate selecting any ply: the board FEN must be the stored FEN for
        # that ply, and it must be a legal, parseable position.
        for position in positions:
            board = chess.Board(position["fen"])
            assert board.turn == (chess.WHITE if position["side_to_move"] == "white" else chess.BLACK)

        # Landing on the final ply shows a checkmate, exactly as the game ended.
        assert chess.Board(positions[-1]["fen"]).is_checkmate()

    def test_deleting_removes_all_positions(self, client: TestClient) -> None:
        imported = client.post(
            "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
        ).json()
        game_id = imported["game_id"]
        assert client.get(f"/api/games/{game_id}/positions").json()["count"] == 34
        client.delete(f"/api/games/{game_id}")
        assert client.get(f"/api/games/{game_id}/positions").status_code == 404


@pytest.mark.engine
class TestEndToEndWithEngine:
    def test_import_analyzes_and_reaches_analyzed_state(self, client: TestClient) -> None:
        response = client.post(
            "/api/games/import",
            json={"pgn_text": OPERA_GAME_PGN, "run_analysis": True, "depth": 6},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["analysis_status"] == "analyzed"
        assert body["analyzed"] is True

        status = client.get(f"/api/games/{body['game_id']}/status").json()
        assert status["analysis_status"] == "analyzed"
        assert status["positions_analyzed"] > 0
