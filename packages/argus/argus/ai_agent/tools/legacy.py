"""Phase 1 tool registry — kept, unchanged, for compatibility.

This is the original engine-only tool layer. Phase 7 supersedes it with the full
:mod:`argus.ai_agent.tools` toolbox (game, player, opening, prediction and training
families with permissions and schema validation), but this module is still the
contract that Phase 1's tests and the `verify_agent_ml` script exercise, and it is
still the right registry to use when no data layer is wired in: it registers only
tools whose services definitely exist.

It was a module (``argus/ai_agent/tools.py``) and is now ``tools/legacy.py`` inside
the package, because Python resolves a package before a sibling module — so leaving
both would have silently shadowed one of them. The import path
``argus.ai_agent.tools.ToolRegistry`` is unchanged, and re-exported from the package
initialiser.

Tools wrap the deterministic services (engine, game analysis) and return structured
data — the agent never calculates chess positions itself. Tools whose backing
services are not built yet are registered with ``available=False`` and a reason, so
an LLM layer will not call them and the UI can show honest states.

``ToolRegistry.to_tool_specs`` emits OpenAI-style function-calling specs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import chess

from argus.analysis.engine.base import ChessEngine
from argus.analysis.game_analyzer import GameAnalyzer
from argus.chess_core.models import Game
from argus.shared.errors import NotFoundError, ToolNotFoundError, ToolUnavailableError


@dataclass(frozen=True)
class AgentTool:
    """A single agent tool: spec + handler + availability."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the tool's arguments
    handler: Callable[..., dict[str, Any]] | None = None
    available: bool = True
    reason: str | None = None  # why unavailable, when not available


class ToolRegistry:
    """Registry of agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"Unknown tool '{name}'") from None

    def list(self) -> list[AgentTool]:
        return list(self._tools.values())

    def to_tool_specs(self, *, only_available: bool = True) -> list[dict[str, Any]]:
        """OpenAI-style function-calling specs for the registered tools."""
        specs = []
        for tool in self._tools.values():
            if only_available and not tool.available:
                continue
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        return specs

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a tool by name; unavailable tools raise ToolUnavailableError."""
        tool = self.get(name)
        if not tool.available or tool.handler is None:
            raise ToolUnavailableError(
                f"Tool '{name}' is not available: {tool.reason or 'not implemented yet'}"
            )
        return tool.handler(**(arguments or {}))


def _current_position_handler(game_provider: Callable[[], Game | None]):
    def handler() -> dict[str, Any]:
        game = game_provider()
        if game is None:
            raise NotFoundError("No game is currently loaded")
        turn = "white" if chess.Board(game.final_position).turn == chess.WHITE else "black"
        return {
            "fen": game.final_position,
            "turn": turn,
            "move_count": game.move_count,
            "result": game.result.value,
        }

    return handler


def _analyze_position_handler(engine: ChessEngine):
    def handler(fen: str, depth: int | None = None, multipv: int | None = None) -> dict[str, Any]:
        return engine.analyze_position(fen, depth=depth, multipv=multipv).model_dump()

    return handler


def _analyze_move_handler(engine: ChessEngine):
    def handler(fen: str, move_uci: str, depth: int | None = None) -> dict[str, Any]:
        comparisons = engine.compare_moves(fen, [move_uci], depth=depth)
        return {"comparisons": [comparison.model_dump() for comparison in comparisons]}

    return handler


def _game_analysis_handler(game_provider: Callable[[], Game | None], analyzer: GameAnalyzer):
    def handler(depth: int | None = None) -> dict[str, Any]:
        game = game_provider()
        if game is None:
            raise NotFoundError("No game is currently loaded")
        return analyzer.analyze(game, depth=depth).model_dump()

    return handler


_UNAVAILABLE_TOOLS = (
    AgentTool(
        name="get_player_history",
        description="Return a player's game history",
        parameters={"type": "object", "properties": {}, "required": []},
        available=False,
        reason="requires the persistence layer (planned Phase 2)",
    ),
    AgentTool(
        name="get_player_statistics",
        description="Return aggregated player statistics",
        parameters={"type": "object", "properties": {}, "required": []},
        available=False,
        reason="requires the persistence layer (planned Phase 2)",
    ),
    AgentTool(
        name="search_chess_knowledge",
        description="Search a curated chess knowledge base (RAG)",
        parameters={"type": "object", "properties": {}, "required": []},
        available=False,
        reason="requires the RAG knowledge base (planned Phase 2+)",
    ),
    AgentTool(
        name="generate_training_position",
        description="Generate a personalized training position",
        parameters={"type": "object", "properties": {}, "required": []},
        available=False,
        reason="requires the personalized training generator (planned Phase 2+)",
    ),
)


def build_default_registry(
    engine: ChessEngine,
    analyzer: GameAnalyzer,
    *,
    game_provider: Callable[[], Game | None] | None = None,
) -> ToolRegistry:
    """Build the tool registry wired to the services that exist today.

    ``game_provider`` returns the currently loaded game (or ``None``); when it
    is not provided, the game-context tools are registered as unavailable.
    """
    registry = ToolRegistry()
    provider = game_provider or (lambda: None)
    game_context_ready = game_provider is not None

    registry.register(
        AgentTool(
            name="get_current_position",
            description="Get the position of the currently loaded game (FEN, turn, result)",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_current_position_handler(provider) if game_context_ready else None,
            available=game_context_ready,
            reason=None if game_context_ready else "no game is currently loaded",
        )
    )
    registry.register(
        AgentTool(
            name="analyze_position",
            description="Run the chess engine on a FEN and return evaluations, best move, PV",
            parameters={
                "type": "object",
                "properties": {
                    "fen": {"type": "string"},
                    "depth": {"type": "integer"},
                    "multipv": {"type": "integer"},
                },
                "required": ["fen"],
            },
            handler=_analyze_position_handler(engine),
        )
    )
    registry.register(
        AgentTool(
            name="analyze_move",
            description="Compare a played move against the engine's best in a position",
            parameters={
                "type": "object",
                "properties": {
                    "fen": {"type": "string"},
                    "move_uci": {"type": "string"},
                    "depth": {"type": "integer"},
                },
                "required": ["fen", "move_uci"],
            },
            handler=_analyze_move_handler(engine),
        )
    )
    registry.register(
        AgentTool(
            name="get_game_analysis",
            description="Get the full deterministic analysis of the currently loaded game",
            parameters={
                "type": "object",
                "properties": {"depth": {"type": "integer"}},
                "required": [],
            },
            handler=_game_analysis_handler(provider, analyzer) if game_context_ready else None,
            available=game_context_ready,
            reason=None if game_context_ready else "no game is currently loaded",
        )
    )
    for tool in _UNAVAILABLE_TOOLS:
        registry.register(tool)
    return registry


__all__ = [
    "AgentTool",
    "ToolRegistry",
    "build_default_registry",
]
