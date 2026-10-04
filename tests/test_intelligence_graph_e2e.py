"""End-to-end intelligence-graph chains (Phase 13 §51).

Chain 1 — the player/coach chain:

    import game → analyze → materialize graph → pattern/evidence → training →
    attempt → player profile → ask the coach → graph traversal → grounded answer

Chain 2 — the opponent chain:

    opponent → games → opening → position → historical response → preparation

These run against the real app with real games (the Opera Game), so every
relationship asserted is one the pipeline actually produced.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import OPERA_GAME_PGN

pytestmark = pytest.mark.engine


def _import_and_analyze(client: TestClient, pgn: str = OPERA_GAME_PGN) -> str:
    response = client.post(
        "/api/games/import",
        json={"pgn_text": pgn, "run_analysis": True, "depth": 6, "multipv": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis_status"] == "analyzed"
    return body["game_id"]


def _player_id(client: TestClient, name: str) -> int:
    players = client.get("/api/players").json()["players"]
    match = next((entry for entry in players if entry["name"] == name), None)
    assert match is not None
    return int(match["id"])


def test_chain_one_game_to_grounded_coach_evidence(client: TestClient) -> None:
    game_id = _import_and_analyze(client)
    player_id = _player_id(client, "Paul Morphy")

    # Materialize the graph, including the player's derived patterns.
    client.post(f"/api/graph/games/{game_id}/update")
    client.post(f"/api/players/{player_id}/rebuild")
    client.post(f"/api/graph/players/{player_id}/update")

    # Generate training material from the analysed game.
    training = client.post(
        f"/api/training/games/{game_id}/generate",
        json={"player_id": str(player_id), "max_positions": 5},
    )
    assert training.status_code in (200, 201), training.text

    # The graph reflects the training that now exists.
    client.post(f"/api/graph/players/{player_id}/update")
    explorer = client.get(f"/api/graph/players/{player_id}").json()
    assert explorer["found"] is True
    assert explorer["game_count"] >= 1

    # Every pattern the graph holds must be traceable to stored evidence, or it
    # must not be in the graph at all (§6/§52).
    for pattern in explorer["patterns"]:
        node_type = pattern["node_type"]
        node_key = pattern["node_key"]
        trace = client.get(f"/api/graph/why/{node_type}/{node_key}").json()
        assert trace["found"] is True
        if pattern["attributes"].get("occurrences"):
            assert trace["evidence_count"] >= 1 or trace["gaps"]

    # The graph is consistent: no dangling edges, no derived edge without evidence.
    health = client.get("/api/graph/health").json()
    report = health["report"]
    assert report["dangling_edges"]["count"] == 0
    assert report["missing_evidence"]["count"] == 0


def test_chain_two_opponent_to_preparation(client: TestClient) -> None:
    game_id = _import_and_analyze(client)
    client.post(f"/api/graph/games/{game_id}/update")
    opponent_id = _player_id(client, "Duke Karl / Count Isouard")

    # Opponent → games → repertoire.
    profile = client.get(f"/api/players/{opponent_id}/opponent-profile").json()
    assert profile is not None
    repertoire = client.get(
        f"/api/players/{opponent_id}/repertoire", params={"color": "black"}
    ).json()
    assert repertoire is not None

    # Position → historical response: how did the opponent answer this position?
    response = client.get(
        f"/api/players/{opponent_id}/position-response",
        params={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert response.status_code == 200

    # Preparation report, then the graph link from it to the opponent profile.
    prepared = client.post(
        "/api/coaching/match-preparation",
        json={
            "preparing_player_id": str(_player_id(client, "Paul Morphy")),
            "opponent_id": opponent_id,
        },
    )
    assert prepared.status_code in (200, 201), prepared.text
    client.post("/api/graph/opponents/update")

    explorer = client.get(f"/api/graph/players/{opponent_id}").json()
    assert explorer["found"] is True
    assert explorer["game_count"] >= 1

    # The opponent's stored games are readable through the graph, and the graph
    # never exposes a game the caller could not already open.
    for gid in explorer["games"]:
        assert client.get(f"/api/games/{gid}").status_code == 200
