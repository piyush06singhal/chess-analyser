"""Phase 11 workspace API tests: the endpoints against real stored data.

Same discipline as ``test_coaching_api``: a real imported game, real seeded
analysis, the real report and the real services. What is pinned here:

* "Show me why" resolves to stored engine facts and refuses what it cannot follow;
* collections enforce their own item rules and ownership;
* unified search separates "nothing stored" from "nothing matched";
* match preparation stores a snapshot and a brief that never predicts;
* the progress comparison refuses a thin sample and always carries the warning.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from argus_api.db.models import Game, MoveAnalysis
from argus_api.main import app
from tests.conftest import OPERA_GAME_PGN

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


def _player_id(client: TestClient, name: str) -> int:
    players = client.get("/api/players").json()["players"]
    match = next((entry for entry in players if entry["name"] == name), None)
    assert match is not None, f"player {name!r} not found"
    return int(match["id"])


def _seed_analyses(game_id: str) -> None:
    import chess

    board = chess.Board()
    with app.state.session_factory.session_scope() as session:
        for index, (white, black) in enumerate(LINE):
            for color, san in (("white", white), ("black", black)):
                ply = index * 2 + (1 if color == "white" else 2)
                move = board.parse_san(san)
                fen_before = board.fen()
                loss, classification, best = 0, "best", move.uci()
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
                        evaluation_before_cp=60,
                        centipawn_loss=loss,
                        played_eval_cp=60 - loss,
                        played_eval_source="same_search",
                        classification=classification,
                        phase="opening",
                        depth=12,
                        principal_variation=[best],
                        candidate_moves=[{"rank": 1, "uci": best, "cp": 60, "mate": None, "pv": [best]}],
                        analysis_version="3.1",
                        engine="stockfish",
                        engine_version="test-engine",
                    )
                )


def _setup(client: TestClient) -> dict:
    game_id = _import_game(client)
    _seed_analyses(game_id)
    report = client.post(f"/api/intelligence/games/{game_id}/report")
    assert report.status_code == 200, report.text
    with app.state.session_factory.session_scope() as session:
        game = session.get(Game, game_id)
        game.analysis_status = "analyzed"
        session.add(game)
    return {"game_id": game_id, "player_id": _player_id(client, "Paul Morphy")}


class TestEvidenceAPI:
    def test_show_me_why_resolves_engine_facts(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            "/api/coaching/evidence",
            params={"claim": "d4 was a mistake", "game_id": setup["game_id"], "ply": 7},
        ).json()
        assert body["items"]
        engine = [item for item in body["items"] if item["kind"] == "engine_fact"]
        assert engine and engine[0]["followable"]
        assert engine[0]["href"] == f"/game/{setup['game_id']}?ply=7"

    def test_a_ply_with_no_stored_analysis_is_a_gap(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            "/api/coaching/evidence",
            params={"claim": "something", "game_id": setup["game_id"], "ply": 999},
        ).json()
        assert any("no stored engine analysis" in gap for gap in body["gaps"])

    def test_the_method_travels_with_the_answer(self, client: TestClient) -> None:
        body = client.get("/api/coaching/evidence", params={"claim": "x"}).json()
        assert body["method"]["kinds"]


class TestCollectionsAPI:
    def test_create_add_and_delete_a_collection(self, client: TestClient) -> None:
        setup = _setup(client)
        created = client.post(
            "/api/coaching/collections",
            json={"player_id": str(setup["player_id"]), "name": "Morphy", "kind": "game_set"},
        )
        assert created.status_code == 201, created.text
        collection = created.json()
        assert collection["size"] == 0
        collection_id = collection["id"]

        with_item = client.post(
            f"/api/coaching/collections/{collection_id}/items",
            json={
                "player_id": str(setup["player_id"]),
                "kind": "game",
                "ref": setup["game_id"],
                "label": "Opera Game",
            },
        )
        assert with_item.status_code == 201, with_item.text
        assert with_item.json()["size"] == 1
        assert with_item.json()["items"][0]["ref"] == setup["game_id"]

        listed = client.get("/api/coaching/collections", params={"player_id": setup["player_id"]}).json()
        assert listed["count"] == 1

        removed = client.delete(
            f"/api/coaching/collections/{collection_id}/items",
            params={"kind": "game", "ref": setup["game_id"], "player_id": setup["player_id"]},
        )
        assert removed.status_code == 200
        assert removed.json()["size"] == 0

        deleted = client.delete(
            f"/api/coaching/collections/{collection_id}", params={"player_id": setup["player_id"]}
        )
        assert deleted.status_code == 200
        assert client.get(
            f"/api/coaching/collections/{collection_id}", params={"player_id": setup["player_id"]}
        ).status_code == 404

    def test_a_forbidden_item_kind_is_refused(self, client: TestClient) -> None:
        setup = _setup(client)
        collection_id = client.post(
            "/api/coaching/collections",
            json={"player_id": str(setup["player_id"]), "name": "Games", "kind": "game_set"},
        ).json()["id"]
        response = client.post(
            f"/api/coaching/collections/{collection_id}/items",
            json={"player_id": str(setup["player_id"]), "kind": "opening", "ref": "C50"},
        )
        assert response.status_code == 422, response.text

    def test_a_duplicate_name_is_a_clean_conflict(self, client: TestClient) -> None:
        setup = _setup(client)
        first = client.post(
            "/api/coaching/collections",
            json={"player_id": str(setup["player_id"]), "name": "Repertoire"},
        )
        assert first.status_code == 201, first.text
        second = client.post(
            "/api/coaching/collections",
            json={"player_id": str(setup["player_id"]), "name": "Repertoire"},
        )
        assert second.status_code == 409, second.text
        body = second.json()
        assert body["error"]["code"] == "conflict"
        assert "already have a collection" in body["error"]["message"]
        # No raw SQL or constraint names leak into the client message.
        assert "uq_" not in second.text
        assert "psycopg" not in second.text.lower()

    def test_a_foreign_collection_is_a_404(self, client: TestClient) -> None:
        setup = _setup(client)
        collection_id = client.post(
            "/api/coaching/collections",
            json={"player_id": str(setup["player_id"]), "name": "Private"},
        ).json()["id"]
        response = client.get(
            f"/api/coaching/collections/{collection_id}", params={"player_id": 999_999}
        )
        assert response.status_code == 404


class TestSearchAPI:
    def test_search_finds_a_stored_game(self, client: TestClient) -> None:
        _setup(client)
        body = client.get("/api/coaching/search", params={"q": "Morphy"}).json()
        assert body["status"] == "ok"
        assert any(hit["kind"] == "game" for hit in body["hits"])
        for hit in body["hits"]:
            assert hit["matched_terms"]
            assert "Matched" in hit["explanation"]

    def test_an_empty_query_is_reported_as_such(self, client: TestClient) -> None:
        body = client.get("/api/coaching/search", params={"q": "  "}).json()
        assert body["status"] == "empty_query"
        assert body["reason"]

    def test_no_matches_names_the_searched_count(self, client: TestClient) -> None:
        _setup(client)
        body = client.get("/api/coaching/search", params={"q": "zzzznotathing"}).json()
        assert body["status"] == "no_matches"
        assert body["total_candidates"] > 0
        assert "none matched" in body["reason"]


class TestMatchPrepAPI:
    def test_prepare_stores_a_snapshot_and_a_brief(self, client: TestClient) -> None:
        setup = _setup(client)
        opponent_id = _player_id(client, "Paul Morphy") if False else setup["player_id"]
        # Prepare as the same player against themselves: the object is honest about
        # a thin profile rather than inventing sections.
        body = client.post(
            "/api/coaching/match-preparation",
            json={"preparing_player_id": str(setup["player_id"]), "opponent_id": opponent_id},
        ).json()
        assert body["brief"]["title"] == "Caissa MATCH BRIEF"
        assert "does not predict" in body["brief"]["disclaimer"]
        preparation_id = body["preparation"]["id"]
        assert preparation_id

        fetched = client.get(
            f"/api/coaching/match-preparation/{preparation_id}",
            params={"preparing_player_id": setup["player_id"]},
        ).json()
        assert fetched["preparation"]["id"] == preparation_id
        listed = client.get(
            "/api/coaching/match-preparation",
            params={"preparing_player_id": setup["player_id"]},
        ).json()
        assert listed["count"] >= 1

    def test_an_unknown_opponent_is_a_404(self, client: TestClient) -> None:
        setup = _setup(client)
        response = client.post(
            "/api/coaching/match-preparation",
            json={"preparing_player_id": str(setup["player_id"]), "opponent_id": 999_999},
        )
        assert response.status_code == 404, response.text

    def test_a_thin_opponent_still_yields_an_honest_brief(self, client: TestClient) -> None:
        setup = _setup(client)
        # The opponent exists but has no analysed games: every section must name
        # the gate or gap rather than inventing a finding.
        body = client.post(
            "/api/coaching/match-preparation",
            json={"preparing_player_id": str(setup["player_id"]), "opponent_id": setup["player_id"], "persist": False},
        ).json()
        assert body["brief"]["unavailable_sections"]
        assert all(item["reason"] for item in body["brief"]["unavailable_sections"])
        assert body["preparation"]["id"] is None


class TestProgressAPI:
    def test_compare_refuses_a_thin_sample_with_the_warning(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            "/api/coaching/progress/compare", params={"player_id": setup["player_id"]}
        ).json()
        assert body["causality_note"]
        assert "not proof" in body["causality_note"]
        assert body["method"]["minimum_games_per_period"]
        # With one analysed game there is nothing comparable in either period.
        assert body["status"] == "insufficient_data"
        assert body["reason"]

    def test_an_unknown_player_is_a_404(self, client: TestClient) -> None:
        response = client.get(
            "/api/coaching/progress/compare", params={"player_id": 999_999}
        )
        assert response.status_code == 404
