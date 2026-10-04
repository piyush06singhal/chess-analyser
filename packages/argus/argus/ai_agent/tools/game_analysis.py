"""Game tools: retrieve the actual game, its analysis, and its moments.

These tools exist so the agent never has to *recall* a game. Every argument is an
identifier, and every answer comes from data Caissa stored when it analysed the
game. When a tool cannot find something it raises, and the loop records the
absence in the evidence packet — which is how "Caissa has no stored analysis for
that move" becomes possible to say truthfully (spec §8, §9, §24).

``get_move_analysis`` is the workhorse behind "why was my move bad?". It prefers
the stored analysis (exact, free, consistent with every other phase) and only
falls back to the engine when the stored analysis genuinely does not exist —
which is what keeps an agent turn from starting a 20-second search it did not
need to run.
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders
from argus.shared.errors import NotFoundError

#: How many moments a game-level answer may lean on before it stops being an
#: explanation and starts being a dump.
MAX_MOMENTS = 8


def _require(provider: Any, capability: str) -> Any:
    if provider is None:
        raise NotFoundError(
            f"Caissa cannot reach {capability} in this deployment, so it will not "
            f"guess at it."
        )
    return provider


def _resolve_game_id(context: AgentContext, game_id: str | None) -> str:
    resolved = game_id or context.active_game_id
    if not resolved:
        raise NotFoundError(
            "No game is active in this conversation. Open a game, or name the game id."
        )
    return resolved


def _resolve_ply(context: AgentContext, ply: int | None) -> int:
    """Work out which ply the user means, preferring the selected move."""
    if ply is not None:
        return ply
    if context.selected_ply is not None:
        return context.selected_ply
    raise NotFoundError(
        "No move is selected, so Caissa does not know which move you mean. Select a "
        "move on the board, or give me the ply number."
    )


def build_game_tools(providers: AgentProviders) -> list[Tool]:
    """The game and game-analysis tool family."""

    def get_game(context: AgentContext, game_id: str | None = None) -> dict[str, Any]:
        lookup = _require(providers.game_lookup, "the game library")
        resolved = _resolve_game_id(context, game_id)
        game = lookup(resolved)
        if not game:
            raise NotFoundError(f"Caissa has no game with id '{resolved}'.")
        return game

    def get_game_moves(
        context: AgentContext, game_id: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        rows = _require(providers.game_moves, "the stored move list")
        resolved = _resolve_game_id(context, game_id)
        moves = rows(resolved)
        if moves is None:
            raise NotFoundError(f"Caissa has no stored moves for game '{resolved}'.")
        payload = {"game_id": resolved, "move_count": len(moves), "moves": moves}
        if limit:
            payload["moves"] = moves[:limit]
            payload["truncated"] = len(moves) > limit
        return payload

    def get_move_analysis(
        context: AgentContext, ply: int | None = None, game_id: str | None = None
    ) -> dict[str, Any]:
        provider = _require(providers.move_analysis, "stored move analysis")
        resolved = _resolve_game_id(context, game_id)
        resolved_ply = _resolve_ply(context, ply)
        analysis = provider(resolved, resolved_ply)
        if not analysis:
            raise NotFoundError(
                f"Caissa has no stored move analysis for ply {resolved_ply} of game "
                f"'{resolved}'. The game may not be analysed yet."
            )
        return analysis

    def get_game_analysis(
        context: AgentContext, game_id: str | None = None
    ) -> dict[str, Any]:
        report = _require(providers.game_report, "the game report layer")
        resolved = _resolve_game_id(context, game_id)
        payload = report(resolved)
        if not payload:
            raise NotFoundError(
                f"Caissa has no GameReport for '{resolved}'. The game may not be analysed."
            )
        return payload

    def get_game_summary(
        context: AgentContext, game_id: str | None = None
    ) -> dict[str, Any]:
        summary = _require(providers.game_summary, "the game summary layer")
        resolved = _resolve_game_id(context, game_id)
        payload = summary(resolved)
        if not payload:
            raise NotFoundError(f"Caissa has no summary for game '{resolved}'.")
        return payload

    def get_critical_moments(
        context: AgentContext,
        game_id: str | None = None,
        side: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        provider = _require(providers.critical_moments, "stored critical moments")
        resolved = _resolve_game_id(context, game_id)
        payload = provider(resolved)
        if not payload:
            raise NotFoundError(
                f"Caissa has no critical moments for game '{resolved}'. The game may "
                f"not be analysed yet."
            )
        moments: list[dict[str, Any]] = []
        for key in ("engine_critical_moments", "argus_critical_moments", "moments"):
            moments.extend(payload.get(key) or [])
        if side:
            wanted = side.lower()
            moments = [m for m in moments if str(m.get("side", "")).lower() == wanted]
        moments.sort(key=lambda m: abs(float((m.get("evidence") or {}).get("swing_cp") or 0)), reverse=True)
        capped = moments[: (limit or MAX_MOMENTS)]
        return {
            "game_id": resolved,
            "total_moments": len(moments),
            "moments": capped,
            "returned": len(capped),
            "filtered_by_side": side,
            "selection": "ordered by absolute evaluation swing",
        }

    def get_game_trajectory(
        context: AgentContext, game_id: str | None = None
    ) -> dict[str, Any]:
        trajectory = _require(providers.game_report, "the game report layer")
        resolved = _resolve_game_id(context, game_id)
        payload = trajectory(resolved)
        if not payload:
            raise NotFoundError(f"Caissa has no report for game '{resolved}'.")
        report = payload.get("report") if isinstance(payload, dict) else None
        if isinstance(report, dict) and "trajectory" in report:
            return {"game_id": resolved, "trajectory": report["trajectory"]}
        return {"game_id": resolved, "trajectory": (payload or {}).get("trajectory", [])}

    return [
        Tool(
            name="get_game",
            description=(
                "The stored game record: players, ratings, result, date, opening and "
                "analysis status. Use it to confirm which game is being discussed."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"game_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=(
                    "id",
                    "white_player",
                    "black_player",
                    "white_rating",
                    "black_rating",
                    "result",
                    "date",
                    "analysis_status",
                    "move_count",
                ),
            ),
            permission=ToolPermission.GAME_CONTEXT,
            handler=get_game,
            tags=("game",),
        ),
        Tool(
            name="get_game_moves",
            description="The stored move list of a game, in ply order.",
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "game_id": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 400},
                    },
                    "required": [],
                },
                outputs=("game_id", "move_count", "moves"),
            ),
            permission=ToolPermission.GAME_CONTEXT,
            handler=get_game_moves,
            tags=("game",),
        ),
        Tool(
            name="get_move_analysis",
            description=(
                "The stored analysis of ONE move: the evaluation before and after, the "
                "centipawn loss, the classification, the engine's best move and the "
                "principal variation. This is the tool for 'why was this move bad?'. "
                "Defaults to the move the user has selected."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "ply": {"type": "integer", "minimum": 1, "maximum": 600},
                        "game_id": {"type": "string", "minLength": 1},
                    },
                    "required": [],
                },
                outputs=(
                    "ply",
                    "move_number",
                    "mover",
                    "san",
                    "uci",
                    "fen_before",
                    "fen_after",
                    "eval_before_cp",
                    "eval_after_cp",
                    "eval_change_cp",
                    "centipawn_loss",
                    "classification",
                    "best_move_uci",
                    "best_move_san",
                    "principal_variation",
                ),
            ),
            permission=ToolPermission.GAME_CONTEXT,
            handler=get_move_analysis,
            tags=("game", "move", "why"),
        ),
        Tool(
            name="get_game_analysis",
            description=(
                "The full structured GameReport for a game (Phase 4 game intelligence): "
                "phases, openings, accuracy, tactical and positional events, turning "
                "points. Large — use get_game_summary or get_critical_moments when a "
                "small answer is enough."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"game_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=("game_id", "report_version", "report"),
            ),
            permission=ToolPermission.GAME_CONTEXT,
            handler=get_game_analysis,
            tags=("game", "report"),
        ),
        Tool(
            name="get_game_summary",
            description=(
                "A compact factual summary of a game: players, result, opening, phase "
                "behaviour and the derived facts Caissa already computed."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"game_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=("white_player", "black_player", "result", "opening_name", "facts"),
            ),
            permission=ToolPermission.GAME_CONTEXT,
            handler=get_game_summary,
            tags=("game", "summary"),
        ),
        Tool(
            name="get_critical_moments",
            description=(
                "The game's critical moments, most significant first, each with the "
                "engine evidence behind it. Use for 'where did I lose this game?'. "
                "Filter by side ('white'/'black') to find the moments that hurt one player."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "game_id": {"type": "string", "minLength": 1},
                        "side": {"type": "string", "enum": ["white", "black"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": [],
                },
                outputs=("game_id", "total_moments", "moments", "selection"),
            ),
            permission=ToolPermission.GAME_CONTEXT,
            handler=get_critical_moments,
            tags=("game", "review"),
        ),
        Tool(
            name="get_game_trajectory",
            description="The evaluation trajectory of a game over plies (for describing how the game turned).",
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"game_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=("game_id", "trajectory"),
            ),
            permission=ToolPermission.GAME_CONTEXT,
            handler=get_game_trajectory,
            tags=("game", "review"),
        ),
    ]


def engine_fallback_notice(providers: AgentProviders) -> str:
    """The honest sentence when no engine is reachable."""
    if providers.engine is not None:
        return ""
    return (
        "No chess engine is configured, so Caissa cannot evaluate positions in this "
        "deployment. Only stored analyses can be discussed."
    )


__all__ = ["MAX_MOMENTS", "build_game_tools", "engine_fallback_notice"]
