"""Training tools (Phase 8): the agent's read-only view of the training engine.

These tools let the coach talk about practice without inventing it. Every answer
comes from exercises Caissa *already generated* from the player's own analysed
mistakes, each carrying an engine-verified solution and a traceable origin
(game → ply → classification → exercise).

Two rules shape the surface:

* **The agent never reveals a solution by accident.** The tools that hand back a
  puzzle strip the solution and its line; only ``generate_training_explanation``
  returns them, and it exists for the explicit "why is this the right move?"
  question.
* **The agent never writes training history.** ``evaluate_training_attempt``
  grades a submitted move against the stored solution but stores nothing —
  attempts are written by the training endpoints, in the product flow, not by a
  coaching answer.

When no exercise exists yet, the tools say so rather than improvising a position.
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders
from argus.shared.errors import NotFoundError

#: One shared sentence for "there is nothing to practise yet, and why".
TRAINING_REASON = (
    "This player has no generated training exercises yet. Generate them from an "
    "analysed game first — Caissa will not improvise a puzzle."
)


def _require(provider: Any, capability: str) -> Any:
    if provider is None:
        raise NotFoundError(
            f"Caissa cannot reach {capability} in this deployment, so it will not "
            f"guess at it."
        )
    return provider


def _resolve_player(context: AgentContext) -> str:
    if not context.player_id:
        raise NotFoundError(
            "No player is active in this conversation. Open a player, or name the "
            "player id, and Caissa will look at their training."
        )
    return context.player_id


def _resolve_game(context: AgentContext, game_id: str | None) -> str:
    resolved = game_id or context.active_game_id
    if not resolved:
        raise NotFoundError(
            "No game is active in this conversation. Open a game, or name the game id."
        )
    return resolved


def build_training_tools(providers: AgentProviders) -> list[Tool]:
    """The training tool family."""

    # -- handlers ------------------------------------------------------------

    def get_training_recommendations(context: AgentContext) -> dict[str, Any]:
        provider = _require(providers.training_recommendations, "the training engine")
        payload = provider(_resolve_player(context))
        if not payload:
            raise NotFoundError(TRAINING_REASON)
        return payload

    def generate_training_position(
        context: AgentContext, category: str | None = None, game_id: str | None = None
    ) -> dict[str, Any]:
        library = _require(providers.training_library, "the training library")
        player = _resolve_player(context)
        payload = library(player, game_id=game_id, category=category, state=None, limit=20)
        positions = (payload or {}).get("positions") or []
        if not positions:
            raise NotFoundError(
                TRAINING_REASON
                if not category
                else f"{TRAINING_REASON} None match the category '{category}'."
            )
        # Deterministic choice: the newest exercise. Never random, so the same
        # question yields the same puzzle and can be reasoned about consistently.
        return {"position": positions[0], "available": len(positions), "solution_withheld": True}

    def get_review_queue(context: AgentContext) -> dict[str, Any]:
        provider = _require(providers.training_review_queue, "the training review queue")
        return provider(_resolve_player(context))

    def get_training_progress(context: AgentContext) -> dict[str, Any]:
        provider = _require(providers.training_progress, "the training engine")
        return provider(_resolve_player(context))

    def get_training_from_game(context: AgentContext, game_id: str | None = None) -> dict[str, Any]:
        library = _require(providers.training_library, "the training library")
        resolved = _resolve_game(context, game_id)
        player = context.player_id
        payload = library(player, game_id=resolved, category=None, state=None, limit=100)
        return {
            "game_id": resolved,
            "count": (payload or {}).get("count", 0),
            "positions": (payload or {}).get("positions", []),
            "note": "Each exercise traces back to this game and a specific ply.",
        }

    def generate_training_explanation(
        context: AgentContext, position_id: int
    ) -> dict[str, Any]:
        provider = _require(providers.training_position, "the training engine")
        payload = provider(int(position_id), _resolve_player(context), True)
        if not payload:
            raise NotFoundError(f"Caissa has no training position {position_id} for this player.")
        return payload

    def evaluate_training_attempt(
        context: AgentContext, position_id: int, submitted_uci: str
    ) -> dict[str, Any]:
        provider = _require(providers.training_evaluate, "the training engine")
        return provider(int(position_id), _resolve_player(context), submitted_uci)

    requirements = {
        "position_source": "a stored game owned by the player, already analysed",
        "ply_source": "a ply with stored engine evidence of a better move",
        "answer_key": "the engine's best move and principal variation at that ply",
        "no_synthesis": "no position may be constructed by the model",
    }

    # -- tool declarations ---------------------------------------------------

    return [
        Tool(
            name="get_training_recommendations",
            description=(
                "What to practise, prioritised from the player's measured training "
                "performance. Every recommendation carries its evidence and sample "
                "size. Categories with too little data are never recommended."
            ),
            schema=ToolSchema(
                parameters={"type": "object", "properties": {}, "required": []},
                outputs=("player_id", "opportunities", "library_categories", "evidence_policy"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_training_recommendations,
            tags=("training",),
        ),
        Tool(
            name="generate_training_position",
            description=(
                "One training exercise generated from the player's own analysed "
                "mistakes. The engine-verified solution is WITHHELD — use "
                "generate_training_explanation when the user asks why a move is right."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "minLength": 1},
                        "game_id": {"type": "string", "minLength": 1},
                    },
                    "required": [],
                },
                outputs=("position", "available", "solution_withheld"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=generate_training_position,
            tags=("training",),
        ),
        Tool(
            name="get_review_queue",
            description=(
                "The exercises whose spaced-repetition review is due. Attempting an "
                "exercise is what schedules the next review, so an untried library "
                "has an empty queue."
            ),
            schema=ToolSchema(
                parameters={"type": "object", "properties": {}, "required": []},
                outputs=("player_id", "due", "count", "total_queued"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_review_queue,
            tags=("training", "review"),
        ),
        Tool(
            name="get_training_progress",
            description=(
                "Measured training progress: accuracy by category and difficulty, "
                "with the number of attempts behind every figure, hint usage and "
                "retention. Use it to answer 'am I improving?' with real numbers."
            ),
            schema=ToolSchema(
                parameters={"type": "object", "properties": {}, "required": []},
                outputs=("attempts_total", "by_category", "by_difficulty", "retention", "library_size"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_training_progress,
            tags=("training", "progress"),
        ),
        Tool(
            name="get_training_from_game",
            description=(
                "The training exercises derived from one game — the game → training "
                "link. Each carries its source ply, so the user can jump back to the "
                "original position."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"game_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=("game_id", "count", "positions"),
            ),
            permission=ToolPermission.GAME_CONTEXT,
            handler=get_training_from_game,
            tags=("training", "game"),
        ),
        Tool(
            name="generate_training_explanation",
            description=(
                "The full evidence of one training exercise, INCLUDING the "
                "engine-verified solution, principal variation and the move the user "
                "played. Use it to explain why the solution is right and what went "
                "wrong, grounded in the stored analysis."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "position_id": {"type": "integer", "minimum": 1},
                    },
                    "required": ["position_id"],
                },
                outputs=("id", "fen", "category", "solution", "principal_variation", "played_move", "source_reason"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=generate_training_explanation,
            tags=("training", "explain"),
        ),
        Tool(
            name="evaluate_training_attempt",
            description=(
                "Grade a move the user is considering against the exercise's stored "
                "solution (correct / near-best / incorrect) WITHOUT storing an "
                "attempt. For explaining a candidate move, not for recording one."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "position_id": {"type": "integer", "minimum": 1},
                        "submitted_uci": {"type": "string", "minLength": 2},
                    },
                    "required": ["position_id", "submitted_uci"],
                },
                outputs=("position_id", "outcome", "reason", "evaluation_delta_cp", "persisted"),
                uses_engine=True,
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=evaluate_training_attempt,
            tags=("training", "evaluate"),
        ),
        Tool(
            name="get_training_requirements",
            description=(
                "The contract a legitimate training position must satisfy: where it "
                "comes from, what evidence backs its solution, and why the model may "
                "never synthesise one."
            ),
            schema=ToolSchema(
                parameters={"type": "object", "properties": {}, "required": []},
                outputs=("requirements", "methodology_version"),
            ),
            permission=ToolPermission.ANY,
            handler=lambda _context: {
                "methodology_version": "8.0",
                "requirements": requirements,
                "note": TRAINING_REASON,
            },
            tags=("training", "meta"),
        ),
    ]


__all__ = ["TRAINING_REASON", "build_training_tools"]
