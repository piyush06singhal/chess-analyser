"""Player tools: retrieve Player Intelligence, never estimate it.

Every player statistic an agent states must come from here. Conversational
"facts" about the user are explicitly not authoritative (spec §18): if a previous
turn said "you are weak at tactics", that sentence is not data. The player tools
read the same versioned profile the Phase 5 dashboard reads, so the chat and the
dashboard can never disagree.

The other half of this module is *sample honesty*. Phase 5 established claim levels
(``observation`` → ``pattern`` → ``tendency``) and coverage bands
(``insufficient`` → ``limited`` → ``moderate`` → ``robust``), and it says "not
enough data" instead of guessing. These tools surface those fields verbatim, so
the agent's prose can be held to the same standard — and when the sample is too
small, the tool says so in the payload rather than leaving the model to notice.
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders
from argus.shared.errors import NotFoundError

#: Matches Phase 5: below this many analysed games, a recurring statement is not
#: supportable. Kept here so the agent's phrasing can be checked against it.
MIN_GAMES_FOR_PATTERN = 2
MIN_GAMES_FOR_TENDENCY = 20


def _resolve_player_id(context: AgentContext, player_id: str | None) -> str:
    resolved = player_id or context.player_id
    if not resolved:
        raise NotFoundError(
            "No player profile is active in this conversation. Open a player profile, "
            "or name the player id."
        )
    return resolved


def build_player_tools(providers: AgentProviders) -> list[Tool]:
    """The player intelligence tool family."""

    def _profile(context: AgentContext, player_id: str | None) -> dict[str, Any]:
        provider = providers.player_profile
        if provider is None:
            raise NotFoundError(
                "Caissa cannot reach the player profile layer in this deployment."
            )
        resolved = _resolve_player_id(context, player_id)
        profile = provider(resolved)
        if not profile:
            raise NotFoundError(f"Caissa has no player with id '{resolved}'.")
        return profile

    def get_player_profile(
        context: AgentContext, player_id: str | None = None
    ) -> dict[str, Any]:
        profile = _profile(context, player_id)
        games = int(profile.get("analyzed_games") or 0)
        payload = {
            "player_id": str(profile.get("player_id")),
            "display_name": profile.get("display_name"),
            "profile_version": profile.get("profile_version"),
            "methodology_version": profile.get("methodology_version"),
            "coverage": profile.get("coverage"),
            "sufficient_data": profile.get("sufficient_data"),
            "imported_games": profile.get("imported_games"),
            "analyzed_games": games,
            "games": profile.get("games"),
            "by_color": profile.get("by_color"),
            "openings": profile.get("openings"),
            "phases": profile.get("phases"),
            "tactical": profile.get("tactical"),
            "positional": profile.get("positional"),
            "king_safety": profile.get("king_safety"),
            "material": profile.get("material"),
            "conversion": profile.get("conversion"),
            "recovery": profile.get("recovery"),
            "time_controls": profile.get("time_controls"),
            "opponents": profile.get("opponents"),
            "trends": profile.get("trends"),
            "practicable": (
                "A recurring weakness may be stated only at 'tendency' level, which "
                f"needs {MIN_GAMES_FOR_TENDENCY} analysed games; below that, report "
                "observations with their sample size and say the sample is small."
            ),
            "sample_note": _sample_note(games, profile.get("coverage")),
        }
        return payload

    def get_player_statistics(
        context: AgentContext, player_id: str | None = None
    ) -> dict[str, Any]:
        provider = providers.player_statistics
        if provider is None:
            # Fall back to the profile: same numbers, one source of truth.
            profile = _profile(context, player_id)
            return {
                "player_id": str(profile.get("player_id")),
                "analyzed_games": profile.get("analyzed_games"),
                "games": profile.get("games"),
                "by_color": profile.get("by_color"),
                "accuracy": (profile.get("games") or {}).get("accuracy"),
                "centipawn_loss": (profile.get("games") or {}).get("centipawn_loss"),
                "source": "player profile snapshot",
            }
        resolved = _resolve_player_id(context, player_id)
        payload = provider(resolved)
        if not payload:
            raise NotFoundError(f"Caissa has no stored statistics for player '{resolved}'.")
        return payload

    def get_player_insights(
        context: AgentContext,
        player_id: str | None = None,
        category: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        provider = providers.player_insights
        if provider is None:
            raise NotFoundError(
                "Caissa cannot reach the player insight layer in this deployment."
            )
        resolved = _resolve_player_id(context, player_id)
        payload = provider(resolved)
        if not payload:
            raise NotFoundError(f"Caissa has no insights for player '{resolved}'.")
        insights = list(payload.get("insights") or [])
        if category:
            wanted = category.lower()
            insights = [
                insight
                for insight in insights
                if wanted in str(insight.get("category", "")).lower()
                or wanted == str(insight.get("metric", "")).lower()
            ]
        return {
            "player_id": str(payload.get("player_id", resolved)),
            "coverage": payload.get("coverage"),
            "sufficient_data": payload.get("sufficient_data"),
            "total_insights": len(payload.get("insights") or []),
            "insights": insights[: (limit or 12)],
            "claim_levels": {
                "observation": "a measured fact about this player's games",
                "pattern": "recurs across several games",
                "tendency": "established across a large sample",
            },
        }

    def get_player_evidence(
        context: AgentContext,
        insight_id: str | None = None,
        player_id: str | None = None,
    ) -> dict[str, Any]:
        provider = providers.player_evidence
        if provider is None:
            raise NotFoundError(
                "Caissa cannot reach the player evidence layer in this deployment."
            )
        resolved = _resolve_player_id(context, player_id)
        payload = provider(resolved, insight_id) if insight_id else provider(resolved)
        if not payload:
            raise NotFoundError(
                f"Caissa has no evidence for player '{resolved}'"
                + (f" and insight '{insight_id}'." if insight_id else ".")
            )
        return payload

    return [
        Tool(
            name="get_player_profile",
            description=(
                "The player's full intelligence profile: record, colour split, openings, "
                "phase performance, tactical and positional tendencies, trends, plus the "
                "coverage band and analysed-game count. The ONLY source for claims about "
                "a player's play."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"player_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=(
                    "player_id",
                    "analyzed_games",
                    "coverage",
                    "sufficient_data",
                    "games",
                    "by_color",
                    "openings",
                    "phases",
                    "tactical",
                    "trends",
                ),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_player_profile,
            tags=("player", "profile"),
        ),
        Tool(
            name="get_player_statistics",
            description=(
                "Aggregate statistics for a player across analysed games, with sample "
                "sizes. Use for countable questions rather than reading the whole profile."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"player_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=("analyzed_games", "games", "by_color"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_player_statistics,
            tags=("player",),
        ),
        Tool(
            name="get_player_insights",
            description=(
                "The player's evidenced insights with claim level, metric, value and "
                "sample size. Use for 'what is my biggest weakness?'. Filter by category "
                "such as 'tactical', 'phase', 'opening', 'time_control'."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "player_id": {"type": "string", "minLength": 1},
                        "category": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": [],
                },
                outputs=("player_id", "coverage", "sufficient_data", "insights"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_player_insights,
            tags=("player", "insight", "weakness"),
        ),
        Tool(
            name="get_player_evidence",
            description=(
                "The games, plies and moves behind a player insight, so a claim can be "
                "traced to real positions."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "insight_id": {"type": "string", "minLength": 1},
                        "player_id": {"type": "string", "minLength": 1},
                    },
                    "required": [],
                },
                outputs=("player_id", "insights"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_player_evidence,
            tags=("player", "evidence"),
        ),
    ]


def _sample_note(analyzed_games: int, coverage: Any) -> str:
    """The sentence that keeps a small sample from sounding like a pattern."""
    if analyzed_games < MIN_GAMES_FOR_PATTERN:
        return (
            f"Only {analyzed_games} analysed game(s): below the minimum for even a "
            "single-game-level observation. Report what exists and state the shortfall."
        )
    if analyzed_games < MIN_GAMES_FOR_TENDENCY:
        return (
            f"{analyzed_games} analysed game(s) (coverage: {coverage}). Enough for "
            "observations, NOT enough to call anything a recurring weakness. Never say "
            "'you always' or 'you keep'."
        )
    return (
        f"{analyzed_games} analysed game(s) (coverage: {coverage}) — a tendency can be "
        "stated, with its sample size and the number of games it appeared in."
    )


__all__ = [
    "MIN_GAMES_FOR_PATTERN",
    "MIN_GAMES_FOR_TENDENCY",
    "build_player_tools",
]
