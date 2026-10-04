"""Phase 10 scenario tools — agent-surface tests.

The tools are thin adapters over the scenario engine, so what matters here is that
the *contract* holds: the right tools exist, a missing provider makes a tool
unavailable rather than broken, the tools resolve a position from a FEN or a game
ply, and a refusal from the engine (an illegal move) travels through the tool as a
result instead of an exception.
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools import AgentProviders, build_agent_toolbox

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

SCENARIO_TOOLS = (
    "compare_candidate_moves",
    "compare_positions",
    "analyze_counterfactual",
    "explain_why_not_move",
    "explain_what_if",
    "explore_turning_points",
)


def _record(calls: list[tuple], result: dict | None = None):
    def provider(*args: Any, **kwargs: Any) -> dict | None:
        calls.append((args, kwargs))
        return result if result is not None else {"ok": True, "args": list(args)}

    return provider


def _providers(**overrides: Any) -> AgentProviders:
    wired: dict[str, Any] = {
        "scenario_compare_moves": _record([]),
        "scenario_compare_positions": _record([]),
        "scenario_counterfactual": _record([]),
        "scenario_why_not": _record([]),
        "scenario_what_if": _record([]),
        "scenario_explorer": _record([]),
    }
    wired.update(overrides)
    return AgentProviders(**wired)


def _call(toolbox, name: str, context: AgentContext, **args: Any) -> dict:
    """Invoke a tool the way the agent loop does, returning its payload."""
    outcome = toolbox.call(name, args, context)
    assert outcome.ok, outcome.error
    return outcome.data


class TestRegistration:
    def test_the_scenario_family_is_registered(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        names = {tool.name for tool in toolbox.list()}
        assert set(SCENARIO_TOOLS) <= names

    def test_a_missing_provider_makes_the_tool_unavailable_with_a_reason(self) -> None:
        toolbox = build_agent_toolbox(AgentProviders())
        catalogue = {entry["name"]: entry for entry in toolbox.catalogue(AgentContext())}
        for name in SCENARIO_TOOLS:
            assert name in catalogue
            assert catalogue[name]["available"] is False, name

    def test_tools_are_available_when_their_providers_are_wired(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        catalogue = {entry["name"]: entry for entry in toolbox.catalogue(AgentContext())}
        for name in SCENARIO_TOOLS:
            assert catalogue[name]["available"] is True, name


class TestArgumentHandling:
    def test_a_fen_is_passed_through_without_a_game(self) -> None:
        calls: list[tuple] = []
        toolbox = build_agent_toolbox(
            _providers(scenario_compare_moves=_record(calls))
        )
        _call(
            toolbox,
            "compare_candidate_moves",
            AgentContext(),
            fen=START_FEN,
            moves=["e2e4"],
            include_engine_top=2,
            depth=12,
        )
        args = calls[0][0]
        assert args[0] == START_FEN
        assert args[1] is None and args[2] is None
        assert args[3] == ["e2e4"]
        assert args[4] == 2 and args[5] == 12

    def test_a_game_ply_is_resolved_from_the_context(self) -> None:
        calls: list[tuple] = []
        toolbox = build_agent_toolbox(_providers(scenario_what_if=_record(calls)))
        context = AgentContext(active_game_id="g-1", selected_ply=17)
        _call(toolbox, "explain_what_if", context, move="d2d4", plies_ahead=3)
        args = calls[0][0]
        # No fen, but the open game and the selected ply are used.
        assert args[0] is None and args[1] == "g-1" and args[2] == 17
        assert args[3] == "d2d4"

    def test_the_position_the_user_is_looking_at_is_used(self) -> None:
        calls: list[tuple] = []
        toolbox = build_agent_toolbox(_providers(scenario_why_not=_record(calls)))
        _call(
            toolbox,
            "explain_why_not_move",
            AgentContext(current_fen=START_FEN),
            move="g1f3",
        )
        assert calls[0][0][0] == START_FEN

    def test_naming_no_position_is_a_clear_error(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        outcome = toolbox.call(
            "compare_candidate_moves", {"moves": ["e2e4"]}, AgentContext()
        )
        assert outcome.ok is False
        message = outcome.error_message.lower()
        assert "fen" in message or "position" in message

    def test_a_game_without_a_ply_asks_for_one(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        outcome = toolbox.call(
            "compare_candidate_moves",
            {"moves": ["e2e4"]},
            AgentContext(active_game_id="g-1"),
        )
        assert outcome.ok is False
        assert "ply" in outcome.error_message.lower()

    def test_explorer_uses_the_open_game(self) -> None:
        calls: list[tuple] = []
        toolbox = build_agent_toolbox(_providers(scenario_explorer=_record(calls)))
        _call(toolbox, "explore_turning_points", AgentContext(active_game_id="g-2"), limit=5)
        assert calls[0][0][0] == "g-2"
        assert calls[0][0][1] == 5


class TestRefusals:
    def test_a_refusal_payload_travels_through_the_tool(self) -> None:
        refusal = {
            "status": "illegal_move",
            "message": "'e2e5' is not a legal move in this position",
        }
        toolbox = build_agent_toolbox(_providers(scenario_counterfactual=_record([], refusal)))
        payload = _call(
            toolbox,
            "analyze_counterfactual",
            AgentContext(),
            fen=START_FEN,
            alternative_move="e2e5",
        )
        assert payload["status"] == "illegal_move"
        assert "not a legal move" in payload["message"]
