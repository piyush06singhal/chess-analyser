"""Graph tools for the AI agent (§31): evidence-driven multi-hop reasoning.

The agent never gets a database handle and never writes a graph query. It gets
nine narrow tools, each with a strict schema, each returning *structured evidence*
with provenance already attached. That is what lets the coach answer "why do I
keep making this mistake?" by traversing

    player → pattern → evidence → games → training → retention

without the model ever seeing the database. Every tool is read-only and every
result is already authorized by the API layer that supplied the provider.

The tools return ``found: false`` (or empty lists with a note) when the graph has
nothing — the honest refusal §52 requires — rather than a plausible-looking
relationship the model must not invent.
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders

#: The contract for these tools: they read the stored graph, never the engine.
_GRAPH_TAGS = ("graph", "evidence")

_SIMILARITY_LEVELS = [
    "exact",
    "equivalent",
    "structurally_similar",
    "opening_similar",
    "tactically_similar",
]


def _call(provider, **kwargs) -> dict[str, Any]:
    result = provider(**kwargs) if provider is not None else None
    if result is None:
        return {
            "found": False,
            "note": "Caissa has no verified graph evidence for that request.",
        }
    return dict(result)


def build_graph_tools(providers: AgentProviders) -> list[Tool]:
    """The graph tool family (§31). Each tool maps 1:1 to a traversal."""

    def find_related_games(
        context: AgentContext,
        game_id: str | None = None,
        player_id: str | None = None,
        opening: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return _call(
            providers.graph_related_games,
            game_id=game_id or context.active_game_id,
            player_id=player_id or context.player_id,
            opening=opening,
            limit=limit,
        )

    def find_related_positions(
        _context: AgentContext,
        fen: str,
        minimum_similarity: str = "structurally_similar",
        limit: int = 10,
    ) -> dict[str, Any]:
        return _call(
            providers.graph_related_positions,
            fen=fen,
            minimum_similarity=minimum_similarity,
            limit=limit,
        )

    def find_player_patterns(
        context: AgentContext,
        player_id: str | None = None,
        pattern_type: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return _call(
            providers.graph_player_patterns,
            player_id=player_id or context.player_id,
            pattern_type=pattern_type,
            limit=limit,
        )

    def find_pattern_evidence(
        _context: AgentContext,
        pattern_id: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        return _call(providers.graph_pattern_evidence, pattern_id=pattern_id, limit=limit)

    def find_training_history(
        context: AgentContext,
        player_id: str | None = None,
        pattern_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return _call(
            providers.graph_training_history,
            player_id=player_id or context.player_id,
            pattern_id=pattern_id,
            limit=limit,
        )

    def find_opponent_connections(
        _context: AgentContext,
        opponent_id: int,
        fen: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return _call(
            providers.graph_opponent_connections,
            opponent_id=opponent_id,
            fen=fen,
            limit=limit,
        )

    def find_opening_connections(
        _context: AgentContext,
        opening: str | None = None,
        player_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return _call(
            providers.graph_opening_connections,
            opening=opening,
            player_id=player_id,
            limit=limit,
        )

    def find_knowledge_for_position(
        _context: AgentContext,
        fen: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        return _call(providers.graph_knowledge_for_position, fen=fen, limit=limit)

    def trace_insight_evidence(
        _context: AgentContext,
        node_type: str,
        node_key: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        return _call(
            providers.graph_trace_evidence,
            node_type=node_type,
            node_key=node_key,
            limit=limit,
        )

    return [
        Tool(
            name="find_related_games",
            description=(
                "Games connected to a game or player in the intelligence graph — same "
                "opening, shared patterns. Returns stored evidence, never a guess."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "game_id": {"type": "string"},
                        "player_id": {"type": "string"},
                        "opening": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": [],
                },
                outputs=("found", "games", "relationship", "note"),
            ),
            permission=ToolPermission.ANY,
            handler=find_related_games,
            tags=_GRAPH_TAGS,
        ),
        Tool(
            name="find_related_positions",
            description=(
                "Stored positions related to a FEN, with the similarity level "
                "(exact/equivalent/structurally similar/…). Exact means exact."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "fen": {"type": "string", "minLength": 1},
                        "minimum_similarity": {"type": "string", "enum": _SIMILARITY_LEVELS},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": ["fen"],
                },
                outputs=("found", "positions", "levels", "note"),
            ),
            permission=ToolPermission.ANY,
            handler=find_related_positions,
            tags=_GRAPH_TAGS,
        ),
        Tool(
            name="find_player_patterns",
            description=(
                "The player's stored recurring patterns, with the sample size behind "
                "each. Empty when no pattern has enough evidence."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "player_id": {"type": "string"},
                        "pattern_type": {
                            "type": "string",
                            "enum": [
                                "tactical",
                                "positional",
                                "king_safety",
                                "material",
                                "opening",
                                "calculation",
                                "conversion",
                                "recovery",
                                "endgame",
                                "time_management",
                            ],
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": [],
                },
                outputs=("found", "patterns", "note"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=find_player_patterns,
            tags=_GRAPH_TAGS,
        ),
        Tool(
            name="find_pattern_evidence",
            description=(
                "The games and positions behind one stored pattern: the exact evidence "
                "for a claim the coach is about to make."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern_id": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": ["pattern_id"],
                },
                outputs=("found", "evidence", "games", "sample_size", "note"),
            ),
            permission=ToolPermission.ANY,
            handler=find_pattern_evidence,
            tags=_GRAPH_TAGS,
        ),
        Tool(
            name="find_training_history",
            description=(
                "The player's training attempts, optionally for one pattern. Reports "
                "results descriptively — Caissa does not infer causation from them."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "player_id": {"type": "string"},
                        "pattern_id": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": [],
                },
                outputs=("found", "attempts", "summary", "note"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=find_training_history,
            tags=_GRAPH_TAGS,
        ),
        Tool(
            name="find_opponent_connections",
            description=(
                "What an opponent's stored games say about their repertoire and how "
                "they answered a position. Reuses the Phase 9 opponent layer."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "opponent_id": {"type": "integer", "minimum": 1},
                        "fen": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": ["opponent_id"],
                },
                outputs=("found", "repertoire", "responses", "note"),
            ),
            permission=ToolPermission.ANY,
            handler=find_opponent_connections,
            tags=_GRAPH_TAGS,
        ),
        Tool(
            name="find_opening_connections",
            description=(
                "The games and players connected to an opening line, with frequency "
                "and results from stored games."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "opening": {"type": "string"},
                        "player_id": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": [],
                },
                outputs=("found", "openings", "games", "note"),
            ),
            permission=ToolPermission.ANY,
            handler=find_opening_connections,
            tags=_GRAPH_TAGS,
        ),
        Tool(
            name="find_knowledge_for_position",
            description=(
                "The sourced chess concepts a position actually exhibits, with the "
                "board fact that proves each one and its source."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "fen": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": ["fen"],
                },
                outputs=("found", "concepts", "note"),
            ),
            permission=ToolPermission.ANY,
            handler=find_knowledge_for_position,
            tags=_GRAPH_TAGS + ("knowledge",),
        ),
        Tool(
            name="trace_insight_evidence",
            description=(
                "Follow one graph node to the stored objects behind it: relationships, "
                "evidence references, sample size, methodology and any gaps."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "node_type": {"type": "string", "minLength": 1},
                        "node_key": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    },
                    "required": ["node_type", "node_key"],
                },
                outputs=("found", "relationships", "evidence", "gaps"),
            ),
            permission=ToolPermission.ANY,
            handler=trace_insight_evidence,
            tags=_GRAPH_TAGS,
        ),
    ]


__all__ = ["build_graph_tools"]
