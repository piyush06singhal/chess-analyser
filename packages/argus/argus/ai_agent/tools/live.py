"""Live-game tools: what the agent may read about a game in progress (§53).

A live game is a different thing from a stored one, and the tools reflect that.
They answer *about the board* — where the pieces are, whose clock is running, what
the game's status is — and never about the engine's opinion of it.

The fair-play rule lives outside these tools, in
:mod:`argus.ai_agent.tools.base`: when the active context is a competitive live
game, every engine-backed tool becomes unavailable, so ``get_live_position`` can
safely hand the agent a FEN without a best move being one tool call away.
:func:`get_training_coach_state` is the tool that makes the rule legible: it
returns the game's own permissions and the refusal the agent must relay, so the
agent can say *why* it will not suggest a move rather than merely declining.

All six tools require an active live game (:attr:`ToolPermission.LIVE_CONTEXT`) and
resolve it from the context when no id is named, exactly as the stored-game tools
do — a user in a live game should never have to paste an id.
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders
from argus.shared.errors import NotFoundError

#: How many plies of history an answer may carry before it stops being an answer.
MAX_HISTORY = 200


def _require(provider: Any, capability: str) -> Any:
    if provider is None:
        raise NotFoundError(
            f"Caissa cannot reach {capability} in this deployment, so it will not "
            f"guess at it."
        )
    return provider


def _resolve_live_game_id(context: AgentContext, live_game_id: str | None) -> str:
    resolved = live_game_id or context.active_live_game_id
    if not resolved:
        raise NotFoundError(
            "No live game is active in this conversation. Open a live game, or name "
            "its id."
        )
    return resolved


def build_live_tools(providers: AgentProviders) -> list[Tool]:
    """The live-game tool family."""

    def _state(context: AgentContext, live_game_id: str | None) -> dict[str, Any]:
        provider = _require(providers.live_game, "the live game layer")
        resolved = _resolve_live_game_id(context, live_game_id)
        payload = provider(resolved)
        if not payload:
            raise NotFoundError(
                f"Caissa has no live game with id '{resolved}', or you may not read it."
            )
        return payload

    def get_live_game(
        context: AgentContext,
        live_game_id: str | None = None,
        include_legal_moves: bool | None = None,
    ) -> dict[str, Any]:
        """The whole live game: board, seats, clocks, status and permissions."""
        payload = _state(context, live_game_id)
        if include_legal_moves is False:
            payload = {**payload, "legal_moves": []}
        return payload

    def get_live_position(
        context: AgentContext, live_game_id: str | None = None
    ) -> dict[str, Any]:
        """The board as it stands: FEN, side to move, move number, last move."""
        payload = _state(context, live_game_id)
        moves = payload.get("moves") or []
        return {
            "live_game_id": payload.get("game_id"),
            "fen": payload.get("current_fen"),
            "side_to_move": payload.get("side_to_move"),
            "move_number": payload.get("move_number"),
            "last_move": moves[-1] if moves else None,
            "status": payload.get("status"),
            "legal_moves": payload.get("legal_moves") or [],
            "version": payload.get("version"),
        }

    def get_live_game_history(
        context: AgentContext,
        live_game_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """The moves played so far, in order, with the clock charged for each."""
        provider = _require(providers.live_history, "the live game history layer")
        resolved = _resolve_live_game_id(context, live_game_id)
        capped = MAX_HISTORY if limit is None else max(1, min(limit, MAX_HISTORY))
        payload = provider(resolved, limit=capped)
        if not payload:
            raise NotFoundError(f"Caissa has no history for live game '{resolved}'.")
        return payload

    def get_live_game_status(
        context: AgentContext, live_game_id: str | None = None
    ) -> dict[str, Any]:
        """The game's status, result and draw state — never a prediction of it."""
        payload = _state(context, live_game_id)
        return {
            "live_game_id": payload.get("game_id"),
            "status": payload.get("status"),
            "result": payload.get("result"),
            "result_reason": payload.get("result_reason"),
            "draw_offer": payload.get("draw_offer"),
            "mode": payload.get("mode"),
            "visibility": payload.get("visibility"),
            "rated": payload.get("rated"),
            "version": payload.get("version"),
            "sequence": payload.get("sequence"),
            "players": payload.get("players"),
            "seats": payload.get("seats"),
            "viewer": payload.get("viewer"),
        }

    def get_live_clock(
        context: AgentContext, live_game_id: str | None = None
    ) -> dict[str, Any]:
        """The server-authoritative clock, with the time it was read at."""
        payload = _state(context, live_game_id)
        return {
            "live_game_id": payload.get("game_id"),
            "status": payload.get("status"),
            "clock": payload.get("clock"),
            "clock_config": payload.get("clock_config"),
            "side_to_move": payload.get("side_to_move"),
        }

    def get_training_coach_state(
        context: AgentContext,
        live_game_id: str | None = None,
        question: str | None = None,
    ) -> dict[str, Any]:
        """What the in-game coach may say, and whether engine output is permitted.

        In a competitive game this returns the refusal and the permitted
        non-engine guidance. The agent must relay it rather than answering from
        its own chess knowledge.
        """
        provider = _require(providers.live_coach_state, "the live coach layer")
        resolved = _resolve_live_game_id(context, live_game_id)
        payload = provider(resolved, question=question)
        if not payload:
            raise NotFoundError(
                f"Caissa has no coach state for live game '{resolved}'."
            )
        return payload

    return [
        Tool(
            name="get_live_game",
            description=(
                "Read the live game in progress: the board, the seats, the clocks, "
                "the game status and the coach's permissions. Use this before "
                "answering anything about a live game."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "live_game_id": {"type": "string", "minLength": 1},
                        "include_legal_moves": {"type": "boolean"},
                    },
                    "required": [],
                },
                outputs=("game_id", "status", "current_fen", "side_to_move", "clock", "permissions"),
            ),
            permission=ToolPermission.LIVE_CONTEXT,
            handler=get_live_game,
            tags=("live",),
        ),
        Tool(
            name="get_live_position",
            description=(
                "Read the position on the live board: FEN, side to move, move "
                "number, last move and the legal moves. It does not include an "
                "engine evaluation."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"live_game_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=("fen", "side_to_move", "move_number", "last_move", "legal_moves"),
            ),
            permission=ToolPermission.LIVE_CONTEXT,
            handler=get_live_position,
            tags=("live",),
        ),
        Tool(
            name="get_live_game_history",
            description=(
                "Read every move played in the live game so far, in order, with the "
                "clock after each move."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "live_game_id": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_HISTORY},
                    },
                    "required": [],
                },
                outputs=("live_game_id", "move_count", "moves"),
            ),
            permission=ToolPermission.LIVE_CONTEXT,
            handler=get_live_game_history,
            tags=("live",),
        ),
        Tool(
            name="get_live_game_status",
            description=(
                "Read the live game's status: active, finished, resigned, timeout, "
                "draw agreed; its result, and any pending draw offer."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"live_game_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=("status", "result", "result_reason", "draw_offer"),
            ),
            permission=ToolPermission.LIVE_CONTEXT,
            handler=get_live_game_status,
            tags=("live",),
        ),
        Tool(
            name="get_live_clock",
            description=(
                "Read the server-authoritative clock for the live game: both sides' "
                "remaining time, which side is to move, and whether it is running."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"live_game_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=("clock", "clock_config", "side_to_move"),
            ),
            permission=ToolPermission.LIVE_CONTEXT,
            handler=get_live_clock,
            tags=("live",),
        ),
        Tool(
            name="get_training_coach_state",
            description=(
                "Ask what the in-game coach is allowed to say right now. Returns "
                "the coach's permissions, whether engine output is permitted, "
                "and — in a competitive game — the refusal to relay. Consult this "
                "before answering any chess question about a live game."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "live_game_id": {"type": "string", "minLength": 1},
                        "question": {"type": "string", "maxLength": 500},
                    },
                    "required": [],
                },
                outputs=("kind", "permissions", "message", "hint"),
            ),
            permission=ToolPermission.LIVE_CONTEXT,
            handler=get_training_coach_state,
            tags=("live", "coach", "fairplay"),
        ),
    ]


__all__ = ["MAX_HISTORY", "build_live_tools"]
