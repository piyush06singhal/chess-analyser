"""API + end-to-end tests for the Intelligence Graph (Phase 13 §50/§51).

These drive the real FastAPI app over SQLite with real games (the Opera Game),
and assert the pipeline the phase gate (§57) requires: a real game becomes a real
position, whose evidence is traceable, and the graph never states a relationship
it cannot prove.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import OPERA_GAME_PGN, SCHOLARS_MATE_PGN, START_FEN

pytestmark = pytest.mark.engine


def _import(client: TestClient, pgn: str, *, analyze: bool = False, depth: int = 6) -> str:
    body: dict = {"pgn_text": pgn, "run_analysis": analyze}
    if analyze:
        body["depth"] = depth
    response = client.post("/api/games/import", json=body)
    assert response.status_code == 200, response.text
    return response.json()["game_id"]


def _player_id(client: TestClient, name: str) -> int:
    players = client.get("/api/players").json()["players"]
    match = next((entry for entry in players if entry["name"] == name), None)
    assert match is not None, f"player {name!r} not found"
    return int(match["id"])


def test_method_publishes_the_vocabulary(client: TestClient) -> None:
    body = client.get("/api/graph/method").json()
    assert body["schema_version"]
    assert "player" in body["node_types"]
    assert "has_pattern" in body["edge_types"]
    assert "postgresql" in body["storage"]


def test_game_materialization_and_explorer(client: TestClient) -> None:
    game_id = _import(client, OPERA_GAME_PGN, analyze=True)

    updated = client.post(f"/api/graph/games/{game_id}/update").json()
    assert updated["updated"] is True
    assert updated["nodes"] > 0

    explorer = client.get(f"/api/graph/games/{game_id}").json()
    assert explorer["found"] is True
    assert explorer["openings"]  # the Opera Game has an ECO code
    assert explorer["positions"]  # game → position edges exist

    health = client.get("/api/graph/health").json()
    assert health["report"]["dangling_edges"]["count"] == 0
    assert health["report"]["invalid_edges"]["count"] == 0


def test_position_explorer_finds_the_start_position_exactly(client: TestClient) -> None:
    game_id = _import(client, OPERA_GAME_PGN, analyze=True)
    client.post(f"/api/graph/games/{game_id}/update")

    body = client.get("/api/graph/position", params={"fen": START_FEN}).json()
    assert body["valid"] is True
    assert body["exact_match_count"] >= 1
    assert body["exact_matches"][0]["exact"] is True


def test_position_explorer_rejects_an_invalid_fen(client: TestClient) -> None:
    body = client.get("/api/graph/position", params={"fen": "not a fen at all"}).json()
    assert body["valid"] is False
    assert "note" in body


def test_knowledge_is_sourced_and_never_invented(client: TestClient) -> None:
    seeded = client.post("/api/graph/knowledge/seed").json()
    assert seeded["concepts"] > 0

    listed = client.get("/api/graph/concepts").json()
    assert listed["count"] > 0
    assert listed["source"]["licence"]

    fork = client.get("/api/graph/concepts/fork").json()
    assert fork["found"] is True
    assert fork["definition"]
    assert fork["source_id"]

    missing = client.get("/api/graph/concepts/not-a-real-concept").json()
    assert missing["found"] is False


def test_position_to_concept_links_are_deterministic(client: TestClient) -> None:
    game_id = _import(client, OPERA_GAME_PGN, analyze=True)
    client.post(f"/api/graph/games/{game_id}/update")
    client.post("/api/graph/knowledge/seed")
    linked = client.post("/api/graph/knowledge/link").json()
    assert linked["positions_scanned"] >= 1

    body = client.get("/api/graph/position", params={"fen": START_FEN}).json()
    # The start position exhibits nothing Caissa has a rule for — and says so.
    assert body["knowledge_concepts"] == []


def test_why_traces_a_node_and_reports_its_evidence(client: TestClient) -> None:
    game_id = _import(client, OPERA_GAME_PGN, analyze=True)
    client.post(f"/api/graph/games/{game_id}/update")

    body = client.get(f"/api/graph/why/game/{game_id}").json()
    assert body["found"] is True
    assert body["methodology_version"]
    assert "ranking_methodology" in body


def test_why_unknown_node_is_honest(client: TestClient) -> None:
    body = client.get("/api/graph/why/game/does-not-exist").json()
    assert body["found"] is False
    assert "note" in body


def test_why_rejects_an_unknown_node_type(client: TestClient) -> None:
    response = client.get("/api/graph/why/not_a_node/whatever")
    assert response.status_code == 422


def test_graph_search_is_structured_and_refuses_unknown_kinds(client: TestClient) -> None:
    game_id = _import(client, OPERA_GAME_PGN)
    client.post(f"/api/graph/games/{game_id}/update")

    games = client.get("/api/graph/search", params={"kind": "games"}).json()
    assert games["supported"] is True
    assert games["count"] >= 1

    unknown = client.get("/api/graph/search", params={"kind": "unicorns"}).json()
    assert unknown["supported"] is False


def test_snapshot_records_versions(client: TestClient) -> None:
    body = client.post("/api/graph/snapshot").json()
    assert body["graph_version"]
    assert body["schema_version"]
    assert body["methodology_version"]


def test_rebuild_is_bounded_and_healthy(client: TestClient) -> None:
    _import(client, OPERA_GAME_PGN)
    _import(client, SCHOLARS_MATE_PGN)
    body = client.post("/api/graph/rebuild", params={"limit_games": 2}).json()
    assert body["games"] == 2
    assert body["snapshot"]["total_nodes"] > 0
    health = client.get("/api/graph/health").json()
    assert health["report"]["missing_evidence"]["count"] == 0


def test_player_explorer_after_profile_rebuild(client: TestClient) -> None:
    game_id = _import(client, OPERA_GAME_PGN, analyze=True)
    client.post(f"/api/graph/games/{game_id}/update")
    player_id = _player_id(client, "Paul Morphy")
    client.post(f"/api/players/{player_id}/rebuild")
    updates = client.post(f"/api/graph/players/{player_id}/update").json()
    assert "patterns" in updates

    explorer = client.get(f"/api/graph/players/{player_id}").json()
    assert explorer["found"] is True
    assert explorer["game_count"] >= 1
    assert isinstance(explorer["patterns"], list)


def test_lists_nodes_by_kind(client: TestClient) -> None:
    game_id = _import(client, OPERA_GAME_PGN)
    client.post(f"/api/graph/games/{game_id}/update")
    body = client.get("/api/graph/nodes/game").json()
    assert body["count"] >= 1
    bad = client.get("/api/graph/nodes/unicorn")
    assert bad.status_code == 422


def test_knowledge_seed_orders_source_before_document(client: TestClient) -> None:
    """The document's foreign key must resolve; a second seed is idempotent."""
    from argus_api.db.session import SessionFactory
    from argus_api.main import app
    from argus_api.db.models import KnowledgeDocumentRecord, KnowledgeSourceRecord

    first = client.post("/api/graph/knowledge/seed").json()
    assert first["concepts"] > 0
    # Re-seeding must not raise (it is the documented idempotent operation).
    second = client.post("/api/graph/knowledge/seed").json()
    assert second["concepts"] == first["concepts"]

    factory = SessionFactory(app.state.db_engine)
    with factory.session_scope() as db:
        assert db.get(KnowledgeSourceRecord, "argus-curated") is not None
        document = db.get(KnowledgeDocumentRecord, "argus-concepts")
        assert document is not None
        assert document.source_id == "argus-curated"


def test_neighborhood_returns_a_bounded_subgraph(client: TestClient) -> None:
    """The Intelligence Map (§39) walks outward from a node and returns its edges."""
    game_id = _import(client, OPERA_GAME_PGN, analyze=True)
    client.post(f"/api/graph/games/{game_id}/update")

    body = client.get(f"/api/graph/neighborhood/game/{game_id}", params={"depth": 1}).json()
    assert body["found"] is True
    assert body["node"] == f"game:{game_id}"
    assert body["nodes"][0]["node_key"] == game_id
    assert len(body["nodes"]) >= 2  # at least the game and one stored position
    edge_types = {edge["edge_type"] for edge in body["edges"]}
    assert "contains_position" in edge_types
    # Evidence is summarised as counts, never inlined into the map payload.
    assert all("evidence" not in edge for edge in body["edges"])
    assert body["denied_count"] == 0


def test_neighborhood_limit_is_reported_not_hidden(client: TestClient) -> None:
    game_id = _import(client, OPERA_GAME_PGN, analyze=True)
    client.post(f"/api/graph/games/{game_id}/update")
    body = client.get(
        f"/api/graph/neighborhood/game/{game_id}", params={"depth": 2, "limit": 3}
    ).json()
    assert len(body["nodes"]) <= 3
    assert body["truncated"] is True


def test_neighborhood_unknown_node_is_honest(client: TestClient) -> None:
    body = client.get("/api/graph/neighborhood/game/does-not-exist").json()
    assert body["found"] is False
    assert "note" in body


def test_neighborhood_rejects_an_unknown_node_type(client: TestClient) -> None:
    response = client.get("/api/graph/neighborhood/not_a_node/whatever")
    assert response.status_code == 422


def test_neighborhood_resolves_a_node_key_containing_a_slash(client: TestClient) -> None:
    from argus_api.db.session import SessionFactory
    from argus_api.main import app
    from argus_api.services.graph_service import service_for
    from argus.intelligence_graph.models import GraphNode
    from argus.intelligence_graph.taxonomy import NodeType

    factory = SessionFactory(app.state.db_engine)
    with factory.session_scope() as db:
        service_for(db).upsert_node(
            GraphNode(
                node_type=NodeType.KNOWLEDGE_CONCEPT,
                node_key="opening-unknown_/_unclassified",
                label="unclassified opening",
                attributes={},
            )
        )
        db.commit()

    body = client.get("/api/graph/neighborhood/knowledge_concept/opening-unknown_/_unclassified").json()
    assert body["found"] is True
    assert body["node"] == "knowledge_concept:opening-unknown_/_unclassified"


def test_why_resolves_a_node_key_containing_a_slash(client: TestClient) -> None:
    """Real insight ids can contain '/', and the why route must still address them."""
    from argus_api.db.session import SessionFactory
    from argus_api.main import app
    from argus_api.services.graph_service import service_for
    from argus.intelligence_graph.models import GraphNode
    from argus.intelligence_graph.taxonomy import NodeType

    factory = SessionFactory(app.state.db_engine)
    with factory.session_scope() as db:
        service_for(db).upsert_node(
            GraphNode(
                node_type=NodeType.KNOWLEDGE_CONCEPT,
                node_key="opening-unknown_/_unclassified",
                label="unclassified opening",
                attributes={},
            )
        )
        db.commit()

    body = client.get("/api/graph/why/knowledge_concept/opening-unknown_/_unclassified").json()
    assert body["found"] is True
    assert body["node"] == "knowledge_concept:opening-unknown_/_unclassified"
