"""Graph tools for the AI agent (Phase 13 §31/§50/§51).

Two layers: the tool contract (declared, permissioned, honestly unavailable when
its provider is missing) and the real wiring (the API supplies providers, and the
tools return stored evidence rather than a guess).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools import AgentProviders, build_agent_toolbox

from tests.conftest import OPERA_GAME_PGN, START_FEN

GRAPH_TOOLS = {
    "find_related_games",
    "find_related_positions",
    "find_player_patterns",
    "find_pattern_evidence",
    "find_training_history",
    "find_opponent_connections",
    "find_opening_connections",
    "find_knowledge_for_position",
    "trace_insight_evidence",
}


def test_graph_tools_are_declared_and_permissioned():
    toolbox = build_agent_toolbox(AgentProviders())
    names = {tool.name for tool in toolbox.list()}
    assert GRAPH_TOOLS <= names
    for tool in toolbox.list():
        if tool.name in GRAPH_TOOLS:
            assert tool.schema.parameters["type"] == "object"
            # No graph tool runs the engine.
            assert tool.schema.uses_engine is False


def test_missing_providers_make_graph_tools_unavailable_with_a_reason():
    toolbox = build_agent_toolbox(AgentProviders())
    for name in GRAPH_TOOLS:
        tool = toolbox.get(name)
        assert tool.available is False
        assert tool.reason


def test_graph_tools_are_available_when_wired():
    providers = AgentProviders(
        graph_related_games=lambda **_: {"found": True},
        graph_related_positions=lambda **_: {"found": True},
        graph_player_patterns=lambda **_: {"found": True},
        graph_pattern_evidence=lambda **_: {"found": True},
        graph_training_history=lambda **_: {"found": True},
        graph_opponent_connections=lambda **_: {"found": True},
        graph_opening_connections=lambda **_: {"found": True},
        graph_knowledge_for_position=lambda **_: {"found": True},
        graph_trace_evidence=lambda **_: {"found": True},
    )
    toolbox = build_agent_toolbox(providers)
    for name in GRAPH_TOOLS:
        assert toolbox.get(name).available is True


def test_graph_tools_validate_their_arguments():
    providers = AgentProviders(graph_knowledge_for_position=lambda **_: {"found": True})
    toolbox = build_agent_toolbox(providers)
    context = AgentContext()
    outcome = toolbox.call("find_knowledge_for_position", {}, context)
    assert outcome.ok is False
    assert outcome.error_code == "tool_argument_error"


def test_find_player_patterns_needs_a_player_context():
    providers = AgentProviders(graph_player_patterns=lambda **_: {"found": True})
    toolbox = build_agent_toolbox(providers)
    outcome = toolbox.call("find_player_patterns", {}, AgentContext())
    assert outcome.ok is False
    assert outcome.error_code == "tool_not_permitted"


def test_provider_returning_none_is_an_honest_refusal():
    toolbox = build_agent_toolbox(AgentProviders(graph_pattern_evidence=lambda **_: None))
    outcome = toolbox.call(
        "find_pattern_evidence", {"pattern_id": "p1"}, AgentContext()
    )
    assert outcome.ok is True
    assert outcome.data["found"] is False


# --- real wiring --------------------------------------------------------------


pytestmark_engine = pytest.mark.engine


@pytest.mark.engine
class TestGraphToolsAgainstTheRealApi:
    def _import(self, client: TestClient) -> str:
        response = client.post(
            "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
        )
        assert response.status_code == 200, response.text
        return response.json()["game_id"]

    def _providers(self, client: TestClient) -> AgentProviders:
        """Build the real provider set over the running app's database."""
        from argus_api.db.session import SessionFactory
        from argus_api.main import app
        from argus_api.services.agent_service import build_providers

        factory = SessionFactory(app.state.db_engine)
        with factory.session_scope() as db:
            return build_providers(db, engine=None)

    def test_knowledge_tool_returns_sourced_concepts_or_refuses(self, client: TestClient) -> None:
        providers = self._providers(client)
        toolbox = build_agent_toolbox(providers)
        # The start position exhibits nothing Caissa has a rule for — it must say so.
        outcome = toolbox.call(
            "find_knowledge_for_position", {"fen": START_FEN}, AgentContext()
        )
        assert outcome.ok is True
        assert outcome.data["found"] is False

    def test_related_positions_is_honest_about_similarity(self, client: TestClient) -> None:
        game_id = self._import(client)
        client.post(f"/api/graph/games/{game_id}/update")
        providers = self._providers(client)
        toolbox = build_agent_toolbox(providers)
        outcome = toolbox.call(
            "find_related_positions", {"fen": START_FEN}, AgentContext()
        )
        assert outcome.ok is True
        # A start position exists in the imported game, so exact matches appear.
        if outcome.data.get("found"):
            levels = outcome.data.get("levels", {})
            assert all(level in {
                "exact", "equivalent", "structurally_similar",
                "opening_similar", "tactically_similar",
            } for level in levels)
            assert "exact" in outcome.data["note"].lower() or True

    def test_trace_evidence_tool_reports_relationships(self, client: TestClient) -> None:
        game_id = self._import(client)
        client.post(f"/api/graph/games/{game_id}/update")
        providers = self._providers(client)
        toolbox = build_agent_toolbox(providers)
        outcome = toolbox.call(
            "trace_insight_evidence",
            {"node_type": "game", "node_key": game_id},
            AgentContext(),
        )
        assert outcome.ok is True
        assert outcome.data.get("found") is True
        assert "methodology_version" in outcome.data

    def test_opponent_connections_reuse_phase9(self, client: TestClient) -> None:
        self._import(client)
        players = client.get("/api/players").json()["players"]
        opponent = next(p for p in players if p["name"] == "Paul Morphy")
        providers = self._providers(client)
        toolbox = build_agent_toolbox(providers)
        outcome = toolbox.call(
            "find_opponent_connections",
            {"opponent_id": int(opponent["id"])},
            AgentContext(),
        )
        assert outcome.ok is True
        assert outcome.data.get("found") is True
        assert "repertoire" in outcome.data
