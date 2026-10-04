"""Scenario tools (Phase 10): the agent's questions about alternative futures.

These let the coach answer "what if I had played ...?", "why is this move worse?"
and "where did this game turn?" — and, critically, let it answer them *only* from
engine results.

The rules the surface obeys:

* **The engine decides.** Every evaluation and every continuation ply comes from a
  real search. A tool never estimates a score and never invents a line.
* **A refusal is an answer.** An illegal move comes back as ``illegal_move`` with
  "that move is not legal in this position"; a missing engine comes back as
  ``unavailable``. Neither is converted into a plausible number.
* **The comparison says what it is.** Scores from a separate search of a resulting
  position are marked ``resulting_position``; the tool result repeats that so the
  agent cannot present them as like-for-like.
* **The explorer is free.** Where a game could have gone differently is read from
  stored analysis, so the agent can answer it without triggering engine work.
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders
from argus.shared.errors import NotFoundError

#: One shared sentence for "there is no engine, so no counterfactual exists".
NO_ENGINE_REASON = (
    "No chess engine is available in this deployment, so Caissa cannot compute an "
    "alternative line. It will not estimate one."
)

#: What every scenario tool rests on, published so the contract is auditable.
SCENARIO_REQUIREMENTS = {
    "source": "the stored game (for game-based questions) or a position the user supplied",
    "engine": "Stockfish is the only source of evaluations and continuation lines",
    "refusals": "'illegal_move', 'unavailable' and 'insufficient_evidence' are results",
    "honesty": (
        "a score from a separate search of a resulting position is marked "
        "'resulting_position' and must not be presented as like-for-like"
    ),
    "immutability": "a counterfactual never modifies the stored game it came from",
}


def _require(provider: Any, capability: str, reason: str | None = None) -> Any:
    if provider is None:
        raise NotFoundError(reason or f"Caissa cannot reach {capability} in this deployment.")
    return provider


def _target(context: AgentContext, fen: str | None, game_id: str | None, ply: int | None) -> dict:
    """Resolve the position a tool should analyse: a FEN, or a stored game ply.

    Falls back to what the user is looking at, so "what if I had played ...?" works
    on the open game and the selected ply without the user repeating themselves.
    """
    resolved_fen = fen or context.current_fen
    if resolved_fen:
        return {"fen": resolved_fen, "game_id": None, "ply": None}
    resolved_game = game_id or context.active_game_id
    if not resolved_game:
        raise NotFoundError(
            "Name a position: pass a fen, or a game_id with the ply to branch from."
        )
    resolved_ply = ply if ply is not None else context.selected_ply
    if resolved_ply is None:
        raise NotFoundError("A game position needs a ply to branch from (1-based).")
    return {"fen": None, "game_id": str(resolved_game), "ply": int(resolved_ply)}


def build_scenario_tools(providers: AgentProviders) -> list[Tool]:
    """The scenario tool family."""

    def compare_candidate_moves(
        context: AgentContext,
        fen: str | None = None,
        game_id: str | None = None,
        ply: int | None = None,
        moves: list[str] | None = None,
        include_engine_top: int = 0,
        depth: int | None = None,
    ) -> dict[str, Any]:
        provider = _require(
            providers.scenario_compare_moves, "the candidate-move comparison engine", NO_ENGINE_REASON
        )
        target = _target(context, fen, game_id, ply)
        return provider(
            target["fen"],
            target["game_id"],
            target["ply"],
            list(moves or []),
            int(include_engine_top),
            depth,
        )

    def compare_positions(
        context: AgentContext,
        fen_a: str,
        fen_b: str,
        depth: int | None = None,
    ) -> dict[str, Any]:
        provider = _require(
            providers.scenario_compare_positions, "the position comparison engine"
        )
        return provider(fen_a, fen_b, depth)

    def analyze_counterfactual(
        context: AgentContext,
        alternative_move: str,
        fen: str | None = None,
        game_id: str | None = None,
        ply: int | None = None,
        actual_move: str | None = None,
        scenario_type: str = "counterfactual_move",
        plies_ahead: int | None = None,
        depth: int | None = None,
    ) -> dict[str, Any]:
        provider = _require(
            providers.scenario_counterfactual, "the counterfactual engine", NO_ENGINE_REASON
        )
        target = _target(context, fen, game_id, ply)
        return provider(
            target["fen"],
            target["game_id"],
            target["ply"],
            str(alternative_move),
            actual_move,
            str(scenario_type),
            plies_ahead,
            depth,
        )

    def explain_why_not_move(
        context: AgentContext,
        move: str,
        fen: str | None = None,
        game_id: str | None = None,
        ply: int | None = None,
        depth: int | None = None,
    ) -> dict[str, Any]:
        provider = _require(
            providers.scenario_why_not, "the move-review engine", NO_ENGINE_REASON
        )
        target = _target(context, fen, game_id, ply)
        return provider(target["fen"], target["game_id"], target["ply"], str(move), depth)

    def explain_what_if(
        context: AgentContext,
        move: str,
        fen: str | None = None,
        game_id: str | None = None,
        ply: int | None = None,
        actual_move: str | None = None,
        plies_ahead: int | None = None,
        depth: int | None = None,
    ) -> dict[str, Any]:
        provider = _require(
            providers.scenario_what_if, "the what-if engine", NO_ENGINE_REASON
        )
        target = _target(context, fen, game_id, ply)
        return provider(
            target["fen"],
            target["game_id"],
            target["ply"],
            str(move),
            actual_move,
            plies_ahead,
            depth,
        )

    def create_training_from_scenario(
        context: AgentContext,
        move: str,
        player_id: str | None = None,
        fen: str | None = None,
        game_id: str | None = None,
        ply: int | None = None,
        depth: int | None = None,
    ) -> dict[str, Any]:
        provider = _require(
            providers.scenario_training,
            "the training engine",
            "No training engine is wired in this deployment, so the counterfactual "
            "cannot be turned into an exercise.",
        )
        target = _target(context, fen, game_id, ply)
        resolved_player = player_id or context.player_id
        if not resolved_player:
            raise NotFoundError(
                "No player is active in this conversation, so there is nobody to own "
                "the exercise. Name the player id."
            )
        return provider(
            target["fen"],
            target["game_id"],
            target["ply"],
            str(move),
            str(resolved_player),
            depth,
        )

    def get_opponent_response_scenario(
        context: AgentContext,
        opponent_player_id: str,
        fen: str | None = None,
        game_id: str | None = None,
        ply: int | None = None,
        depth: int | None = None,
    ) -> dict[str, Any]:
        provider = _require(
            providers.scenario_opponent_response,
            "the opponent response layer",
            NO_ENGINE_REASON,
        )
        target = _target(context, fen, game_id, ply)
        return provider(
            target["fen"], target["game_id"], target["ply"], str(opponent_player_id), depth
        )

    def explore_turning_points(
        context: AgentContext, game_id: str | None = None, limit: int = 12
    ) -> dict[str, Any]:
        provider = _require(
            providers.scenario_explorer, "the turning-point explorer"
        )
        resolved_game = game_id or context.active_game_id
        if not resolved_game:
            raise NotFoundError("Name the game to explore (a game_id, or open one first).")
        return provider(str(resolved_game), int(limit))

    return [
        Tool(
            name="compare_candidate_moves",
            description=(
                "Compare several candidate moves in one position, all from a single "
                "engine search: score, centipawn loss, quality, principal variation, "
                "material and tactical consequences. Use it when the user asks which "
                "move is better, or to compare a played move with the engine's options."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "fen": {"type": "string", "minLength": 10},
                        "game_id": {"type": "string", "minLength": 1},
                        "ply": {"type": "integer", "minimum": 1, "maximum": 500},
                        "moves": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Moves in SAN or UCI; illegal ones are reported, not scored",
                        },
                        "include_engine_top": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 8,
                            "description": "Also include the engine's own top-N moves",
                        },
                        "depth": {"type": "integer", "minimum": 1, "maximum": 60},
                    },
                    "required": [],
                },
                outputs=("candidates", "best_move_uci", "engine_config", "notes", "evidence"),
            ),
            permission=ToolPermission.ANY,
            handler=compare_candidate_moves,
            tags=("scenario", "engine"),
        ),
        Tool(
            name="compare_positions",
            description=(
                "Compare two positions along two separate axes: the engine evaluation "
                "(a search result) and the board facts (material, pawn structure, "
                "activity, king safety, tactics, phase). The two are never merged."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "fen_a": {"type": "string", "minLength": 10},
                        "fen_b": {"type": "string", "minLength": 10},
                        "depth": {"type": "integer", "minimum": 1, "maximum": 60},
                    },
                    "required": ["fen_a", "fen_b"],
                },
                outputs=("engine_difference", "structural_differences", "phase_change", "notes"),
            ),
            permission=ToolPermission.ANY,
            handler=compare_positions,
            tags=("scenario", "comparison"),
        ),
        Tool(
            name="analyze_counterfactual",
            description=(
                "Build an alternative future for a position: the move actually played "
                "and an alternative move, each followed by the engine's own line, with "
                "the measured difference. Answers 'what if I had played ...?'."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "alternative_move": {"type": "string", "minLength": 1},
                        "fen": {"type": "string", "minLength": 10},
                        "game_id": {"type": "string", "minLength": 1},
                        "ply": {"type": "integer", "minimum": 1, "maximum": 500},
                        "actual_move": {"type": "string"},
                        "scenario_type": {
                            "type": "string",
                            "enum": [
                                "counterfactual_move",
                                "alternative_line",
                                "opening_deviation",
                                "tactical_variation",
                                "endgame_transition",
                                "opponent_response",
                                "user_hypothesis",
                            ],
                        },
                        "plies_ahead": {"type": "integer", "minimum": 1, "maximum": 20},
                        "depth": {"type": "integer", "minimum": 1, "maximum": 60},
                    },
                    "required": ["alternative_move"],
                },
                outputs=("status", "branch", "explanation", "source"),
            ),
            permission=ToolPermission.ANY,
            handler=analyze_counterfactual,
            tags=("scenario", "counterfactual"),
        ),
        Tool(
            name="explain_why_not_move",
            description=(
                "Everything measured about one move: what it does, the opponent's "
                "strongest reply the engine expects, the resulting evaluation, the most "
                "concrete problem, and the better alternatives. If the move is the "
                "engine's best, it says so instead of inventing a flaw."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "move": {"type": "string", "minLength": 1},
                        "fen": {"type": "string", "minLength": 10},
                        "game_id": {"type": "string", "minLength": 1},
                        "ply": {"type": "integer", "minimum": 1, "maximum": 500},
                        "depth": {"type": "integer", "minimum": 1, "maximum": 60},
                    },
                    "required": ["move"],
                },
                outputs=(
                    "status",
                    "move",
                    "best_response",
                    "critical_issue",
                    "better_alternatives",
                    "explanation",
                ),
            ),
            permission=ToolPermission.ANY,
            handler=explain_why_not_move,
            tags=("scenario", "explanation"),
        ),
        Tool(
            name="explain_what_if",
            description=(
                "Frame one alternative move as a hypothesis against what was actually "
                "played, with both continuations and the measured evaluation change."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "move": {"type": "string", "minLength": 1},
                        "fen": {"type": "string", "minLength": 10},
                        "game_id": {"type": "string", "minLength": 1},
                        "ply": {"type": "integer", "minimum": 1, "maximum": 500},
                        "actual_move": {"type": "string"},
                        "plies_ahead": {"type": "integer", "minimum": 1, "maximum": 20},
                        "depth": {"type": "integer", "minimum": 1, "maximum": 60},
                    },
                    "required": ["move"],
                },
                outputs=("status", "alternative_move", "evaluation_change_cp", "branch", "explanation"),
            ),
            permission=ToolPermission.ANY,
            handler=explain_what_if,
            tags=("scenario", "explanation"),
        ),
        Tool(
            name="create_training_from_scenario",
            description=(
                "Turn a counterfactual into a stored exercise: the position is real, "
                "the solution is the move the engine measured as best here. Refuses "
                "when the move is illegal, already played, or scored as inferior."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "move": {"type": "string", "minLength": 1},
                        "player_id": {"type": "string", "minLength": 1},
                        "fen": {"type": "string", "minLength": 10},
                        "game_id": {"type": "string", "minLength": 1},
                        "ply": {"type": "integer", "minimum": 1, "maximum": 500},
                        "depth": {"type": "integer", "minimum": 1, "maximum": 60},
                    },
                    "required": ["move"],
                },
                outputs=("status", "position_id", "position", "source", "note"),
            ),
            permission=ToolPermission.ANY,
            handler=create_training_from_scenario,
            tags=("scenario", "training"),
        ),
        Tool(
            name="get_opponent_response_scenario",
            description=(
                "What an opponent actually played in a position (from their stored "
                "games) beside what the engine recommends. The two are never merged "
                "and neither predicts their next move."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "opponent_player_id": {"type": "string", "minLength": 1},
                        "fen": {"type": "string", "minLength": 10},
                        "game_id": {"type": "string", "minLength": 1},
                        "ply": {"type": "integer", "minimum": 1, "maximum": 500},
                        "depth": {"type": "integer", "minimum": 1, "maximum": 60},
                    },
                    "required": ["opponent_player_id"],
                },
                outputs=(
                    "historically_observed",
                    "engine_recommended",
                    "distinction",
                ),
            ),
            permission=ToolPermission.ANY,
            handler=get_opponent_response_scenario,
            tags=("scenario", "opponent"),
        ),
        Tool(
            name="explore_turning_points",
            description=(
                "Where a game could have gone differently, from the stored analysis "
                "alone (no engine call): the decisive moments, their swings, and the "
                "alternative moves the analysis already recorded."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "game_id": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 40},
                    },
                    "required": [],
                },
                outputs=("game_id", "turning_points", "evaluation_series", "notes"),
            ),
            permission=ToolPermission.ANY,
            handler=explore_turning_points,
            tags=("scenario", "game"),
        ),
    ]


__all__ = ["NO_ENGINE_REASON", "SCENARIO_REQUIREMENTS", "build_scenario_tools"]
