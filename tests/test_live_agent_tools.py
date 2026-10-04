"""Phase 12 agent-tool tests: live-game reads and the fair-play isolation (§53/§54).

Two properties matter here, and both are refusals:

* the live-game tools read the board and the clock and nothing more — they never
  return an engine opinion, because the provider they call never produces one;
* when the active context is a competitive live game, *every* engine-backed tool
  becomes unavailable, so a tool the agent reaches for anyway is refused in the
  backend rather than left to the model's restraint.

These run without a database, a WebSocket or an engine, exactly as the rest of the
tool-layer tests do.
"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.ai_agent.core.context import AgentContext  # noqa: E402
from argus.ai_agent.tools import (  # noqa: E402
    AgentProviders,
    ToolPermission,
    build_agent_toolbox,
)

from tests.conftest import START_FEN  # noqa: E402

LIVE_ID = "live-0001"

#: A competitive live game: no analysis permitted, hint-only coach.
COMPETITIVE_PAYLOAD = {
    "game_id": LIVE_ID,
    "status": "active",
    "mode": "private_match",
    "current_fen": START_FEN,
    "side_to_move": "white",
    "move_number": 1,
    "version": 3,
    "sequence": 5,
    "result": "*",
    "result_reason": None,
    "draw_offer": None,
    "visibility": "private",
    "rated": False,
    "viewer": "player",
    "clock": {"white_ms": 300000, "black_ms": 300000, "running": True, "display": {"white": "5:00"}},
    "clock_config": {"base_ms": 300000, "increment_ms": 0},
    "players": {
        "white": {"player_id": 1, "name": "Alice", "kind": "human"},
        "black": {"player_id": 2, "name": "Bob", "kind": "human"},
    },
    "seats": {"white": "human", "black": "human"},
    "permissions": {"may_give_engine_moves": False, "may_give_hints": True},
    "moves": [],
    "legal_moves": ["e2e4", "d2d4"],
}

COACH_REFUSAL = {
    "kind": "hint_only",
    "message": "This is a competitive game, so Caissa will not suggest a move.",
    "hint": "Look for checks, captures and threats first.",
    "permissions": {"may_give_engine_moves": False, "may_give_hints": True},
    "game_id": LIVE_ID,
    "status": "active",
}


def _providers(**overrides) -> AgentProviders:
    def live_game(live_game_id: str) -> dict | None:
        return COMPETITIVE_PAYLOAD if live_game_id == LIVE_ID else None

    def live_history(live_game_id: str, limit: int = 200) -> dict | None:
        if live_game_id != LIVE_ID:
            return None
        moves = [{"ply": i + 1, "san": "e4"} for i in range(300)]
        return {
            "live_game_id": live_game_id,
            "status": "active",
            "move_count": len(moves),
            "moves": moves[-limit:],
            "truncated": len(moves) > limit,
        }

    def live_coach_state(live_game_id: str, question: str | None = None) -> dict | None:
        return COACH_REFUSAL if live_game_id == LIVE_ID else None

    base = {
        "live_game": live_game,
        "live_history": live_history,
        "live_coach_state": live_coach_state,
    }
    base.update(overrides)
    return AgentProviders(**base)


def _context(*, forbidden: bool = True, allowed: list[str] | None = None) -> AgentContext:
    return AgentContext(
        active_live_game_id=LIVE_ID,
        available_live_game_ids=allowed if allowed is not None else [LIVE_ID],
        live_analysis_forbidden=forbidden,
    )


class TestLiveToolAvailability:
    def test_the_live_tools_need_an_active_live_game(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        empty = AgentContext()
        for name in (
            "get_live_game",
            "get_live_position",
            "get_live_game_history",
            "get_live_game_status",
            "get_live_clock",
            "get_training_coach_state",
        ):
            ok, reason = toolbox.get(name).is_usable(empty)
            assert not ok, name
            assert "live game" in (reason or "")

    def test_the_live_tools_declare_a_live_context_permission(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        assert toolbox.get("get_live_game").permission is ToolPermission.LIVE_CONTEXT


class TestLiveReads:
    def test_get_live_game_returns_the_state_without_an_engine_opinion(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        outcome = toolbox.call("get_live_game", {}, _context())
        assert outcome.ok
        assert outcome.data["current_fen"] == START_FEN
        # The payload must not carry a best move or evaluation.
        text = str(outcome.data).lower()
        assert "best_move" not in text and "eval" not in text

    def test_get_live_position_extracts_the_board(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        outcome = toolbox.call("get_live_position", {}, _context())
        assert outcome.ok
        assert outcome.data["fen"] == START_FEN
        assert outcome.data["side_to_move"] == "white"
        assert outcome.data["legal_moves"] == ["e2e4", "d2d4"]

    def test_get_live_game_history_is_bounded(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        capped = toolbox.call("get_live_game_history", {"limit": 10}, _context())
        assert capped.ok and len(capped.data["moves"]) == 10
        assert capped.data["truncated"] is True
        # An absurd limit is clamped rather than honoured.
        huge = toolbox.call("get_live_game_history", {"limit": 99999}, _context())
        assert not huge.ok  # schema maximum rejects it before it runs

    def test_get_live_game_status_and_clock(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        status = toolbox.call("get_live_game_status", {}, _context())
        assert status.ok and status.data["status"] == "active"
        clock = toolbox.call("get_live_clock", {}, _context())
        assert clock.ok and clock.data["clock"]["running"] is True

    def test_the_coach_state_relays_the_competitive_refusal(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        outcome = toolbox.call("get_training_coach_state", {}, _context())
        assert outcome.ok
        assert outcome.data["kind"] == "hint_only"
        assert outcome.data["permissions"]["may_give_engine_moves"] is False
        assert "will not suggest a move" in outcome.data["message"]


class TestLiveAuthorization:
    def test_an_unauthorized_live_game_is_refused(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        # The allow-list holds only LIVE_ID; naming another is refused.
        outcome = toolbox.call("get_live_game", {"live_game_id": "live-9999"}, _context())
        assert not outcome.ok
        assert "does not belong" in outcome.error_message

    def test_an_empty_allow_list_means_no_live_game(self) -> None:
        # Unlike stored games, an empty live allow-list is *not* "all of them".
        toolbox = build_agent_toolbox(_providers())
        context = _context(allowed=[])
        outcome = toolbox.call("get_live_game", {"live_game_id": LIVE_ID}, context)
        assert not outcome.ok

    def test_an_unknown_live_game_reports_absence(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        context = _context(allowed=[LIVE_ID, "live-0002"])
        outcome = toolbox.call("get_live_game", {"live_game_id": "live-0002"}, context)
        assert not outcome.ok
        assert "no live game" in outcome.error_message


class TestFairPlayIsolation:
    """The load-bearing test: a competitive live game disables the engine."""

    def test_engine_tools_are_unavailable_in_a_competitive_live_game(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        for name in ("analyze_position", "analyze_position_multipv", "compare_moves"):
            ok, reason = toolbox.get(name).is_usable(_context(forbidden=True))
            assert not ok, name
            assert "disabled" in (reason or "")

    def test_engine_tools_remain_available_in_a_training_live_game(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        ok, reason = toolbox.get("analyze_position").is_usable(_context(forbidden=False))
        assert ok and reason is None

    def test_a_called_engine_tool_is_refused_in_a_competitive_game(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        outcome = toolbox.call("analyze_position", {"fen": START_FEN}, _context(forbidden=True))
        assert not outcome.ok
        assert "disabled" in outcome.error_message
        # It never ran, so it produced no data.
        assert outcome.data == {}

    def test_non_engine_tools_still_work_while_the_engine_is_disabled(self) -> None:
        toolbox = build_agent_toolbox(_providers())
        outcome = toolbox.call("get_live_position", {}, _context(forbidden=True))
        assert outcome.ok
