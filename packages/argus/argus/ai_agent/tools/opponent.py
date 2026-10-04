"""Opponent tools (Phase 9): the agent's read-only view of opponent intelligence.

These let the coach prepare a player *against a specific opponent* without
inventing anything. Every answer is an aggregate over games Caissa actually
stores, each carrying its sample size and claim level.

The rules the surface obeys:

* **Analytics, not psychology.** The tools report what was counted, never a
  mental state or a result prediction.
* **Sample sizes travel with every claim.** A tendency below its gate comes back
  marked ``insufficient`` rather than smoothed into a finding.
* **Preparation is about what to prepare, not what will happen.** The brief
  names openings and structures to prepare against, never outcomes.

When the opponent has no analysed games, the tools say so instead of producing
a confident-looking empty answer.
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders
from argus.shared.errors import NotFoundError

#: One shared sentence for "there is no opponent data to analyse, and why".
OPPONENT_REASON = (
    "This player has no stored games for Caissa to analyse, so there is nothing to "
    "say about their repertoire or tendencies yet. Import and analyse games first."
)


def _require(provider: Any, capability: str, reason: str | None = None) -> Any:
    if provider is None:
        raise NotFoundError(
            reason
            or f"Caissa cannot reach {capability} in this deployment, so it will not guess at it."
        )
    return provider


def _resolve_player(context: AgentContext, player_id: str | None) -> str:
    resolved = player_id or context.player_id
    if not resolved:
        raise NotFoundError(
            "No player is active in this conversation. Open a player, or name the player id, "
            "and Caissa will analyse that opponent."
        )
    return str(resolved)


def build_opponent_tools(providers: AgentProviders) -> list[Tool]:
    """The opponent tool family."""

    def get_opponent_profile(
        context: AgentContext, player_id: str | None = None
    ) -> dict[str, Any]:
        provider = _require(providers.opponent_profile, "the opponent intelligence layer")
        payload = provider(_resolve_player(context, player_id))
        if not payload:
            raise NotFoundError(OPPONENT_REASON)
        return payload

    def get_opponent_games(
        context: AgentContext, player_id: str | None = None, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        provider = _require(providers.opponent_games, "the opponent game history")
        payload = provider(_resolve_player(context, player_id), int(limit), int(offset))
        if not payload:
            raise NotFoundError(OPPONENT_REASON)
        return payload

    def get_opponent_repertoire(
        context: AgentContext, color: str = "white", player_id: str | None = None
    ) -> dict[str, Any]:
        provider = _require(providers.opponent_repertoire, "the opponent repertoire layer")
        return provider(_resolve_player(context, player_id), color, False)

    def get_opponent_recent_repertoire(
        context: AgentContext, color: str = "white", player_id: str | None = None
    ) -> dict[str, Any]:
        provider = _require(providers.opponent_repertoire, "the opponent repertoire layer")
        return provider(_resolve_player(context, player_id), color, True)

    def get_opponent_position_responses(
        context: AgentContext, fen: str, player_id: str | None = None
    ) -> dict[str, Any]:
        provider = _require(providers.opponent_position_responses, "the opponent response layer")
        return provider(_resolve_player(context, player_id), fen)

    def get_opponent_tendencies(
        context: AgentContext, player_id: str | None = None
    ) -> dict[str, Any]:
        provider = _require(providers.opponent_tendencies, "the opponent tendency layer")
        return provider(_resolve_player(context, player_id))

    def get_opponent_phase_statistics(
        context: AgentContext, player_id: str | None = None, color: str | None = None
    ) -> dict[str, Any]:
        provider = _require(providers.opponent_phase_statistics, "the opponent phase layer")
        return provider(_resolve_player(context, player_id), color)

    def get_opponent_preparation_report(
        context: AgentContext, player_id: str | None = None, color: str | None = None
    ) -> dict[str, Any]:
        provider = _require(providers.opponent_preparation_report, "the preparation report layer")
        return provider(_resolve_player(context, player_id), color)

    def generate_opponent_training(
        context: AgentContext,
        opponent_player_id: str,
        player_id: str | None = None,
        min_occurrences: int = 2,
        max_exercises: int = 20,
    ) -> dict[str, Any]:
        provider = _require(
            providers.opponent_preparation, "the opponent preparation training engine"
        )
        # The preparing player is the active one; the opponent is named explicitly.
        return provider(
            _resolve_player(context, player_id),
            str(opponent_player_id),
            int(min_occurrences),
            int(max_exercises),
        )

    def generate_opponent_brief(
        context: AgentContext, player_id: str | None = None, color: str | None = None
    ) -> dict[str, Any]:
        provider = _require(providers.opponent_preparation_report, "the preparation report layer")
        report = provider(_resolve_player(context, player_id), color)
        if not report:
            raise NotFoundError(OPPONENT_REASON)
        return _brief_from_report(report)

    requirements = {
        "source": "games owned by the analysed player, with stored engine analysis",
        "no_psychology": "the tools never infer mood, intent or a result prediction",
        "sample_sizes": "every claim is returned with the number of observations behind it",
        "gates": "findings below the sample-size gates are returned as 'insufficient'",
    }

    return [
        Tool(
            name="get_opponent_profile",
            description=(
                "The opponent's identity, game history, opening repertoire and measured "
                "statistics. Use it to answer 'what do we know about this opponent?' — "
                "every figure carries its sample size."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"player_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=(
                    "identity",
                    "statistics",
                    "coverage",
                    "repertoire",
                    "phase_statistics",
                    "tendencies",
                    "limitations",
                ),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_opponent_profile,
            tags=("opponent",),
        ),
        Tool(
            name="get_opponent_games",
            description=(
                "The opponent's game history, newest first: colour, opponent, result, "
                "opening and how many games are analysed vs imported."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "player_id": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                        "offset": {"type": "integer", "minimum": 0},
                    },
                    "required": [],
                },
                outputs=("player_id", "total", "statistics", "games"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_opponent_games,
            tags=("opponent", "history"),
        ),
        Tool(
            name="get_opponent_repertoire",
            description=(
                "What the opponent plays as a colour, and how often, from their stored "
                "games. A choice is only called characteristic when the sample-size gate "
                "is met; otherwise it is returned as a raw count."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "color": {"type": "string", "enum": ["white", "black"]},
                        "player_id": {"type": "string", "minLength": 1},
                    },
                    "required": [],
                },
                outputs=("color", "nodes", "top_lines", "analyzed_games", "coverage"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_opponent_repertoire,
            tags=("opponent", "repertoire"),
        ),
        Tool(
            name="get_opponent_recent_repertoire",
            description=(
                "The opponent's repertoire over their most recent games only — answers "
                "'what are they playing lately?', with its own, smaller sample size."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "color": {"type": "string", "enum": ["white", "black"]},
                        "player_id": {"type": "string", "minLength": 1},
                    },
                    "required": [],
                },
                outputs=("color", "nodes", "analyzed_games", "recent", "note"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_opponent_recent_repertoire,
            tags=("opponent", "repertoire", "recent"),
        ),
        Tool(
            name="get_opponent_position_responses",
            description=(
                "How the opponent answered one specific position (a FEN), with the move "
                "distribution and its sample size. Returns no match rather than a guess "
                "when they have never faced it."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "fen": {"type": "string", "minLength": 10},
                        "player_id": {"type": "string", "minLength": 1},
                    },
                    "required": ["fen"],
                },
                outputs=("query_fen", "match", "occurrences", "responses", "sample_note"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_opponent_position_responses,
            tags=("opponent", "positions"),
        ),
        Tool(
            name="get_opponent_tendencies",
            description=(
                "The opponent's measured, evidence-gated tendencies (castling habit, "
                "checking and capture rates, where their significant errors concentrate, "
                "time controls played). Each carries its observations count."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"player_id": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=("tendencies", "policy", "note"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_opponent_tendencies,
            tags=("opponent", "tendencies"),
        ),
        Tool(
            name="get_opponent_phase_statistics",
            description=(
                "The opponent's measured performance by phase (opening / middlegame / "
                "endgame): moves, significant errors and average centipawn loss, with "
                "the games behind each figure."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "color": {"type": "string", "enum": ["white", "black"]},
                        "player_id": {"type": "string", "minLength": 1},
                    },
                    "required": [],
                },
                outputs=("color", "phases", "analyzed_games", "sample_note"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_opponent_phase_statistics,
            tags=("opponent", "phases"),
        ),
        Tool(
            name="get_opponent_preparation_report",
            description=(
                "The composed preparation document: repertoire, tendencies, phase "
                "statistics and the evidence-gated insights, plus what to prepare."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "color": {"type": "string", "enum": ["white", "black"]},
                        "player_id": {"type": "string", "minLength": 1},
                    },
                    "required": [],
                },
                outputs=("identity", "coverage", "insights", "tendencies", "expected_lines", "limitations"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=get_opponent_preparation_report,
            tags=("opponent", "preparation"),
        ),
        Tool(
            name="generate_opponent_training",
            description=(
                "Generate preparation exercises against a specific opponent: the positions "
                "after moves that opponent demonstrably plays, with the engine's stored best "
                "reply as each solution. Exercises belong to the active player; nothing is "
                "predicted, and a move that is not yet characteristic yields no exercise."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "opponent_player_id": {"type": "string", "minLength": 1},
                        "player_id": {"type": "string", "minLength": 1},
                        "min_occurrences": {"type": "integer", "minimum": 1, "maximum": 50},
                        "max_exercises": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": ["opponent_player_id"],
                },
                outputs=("opponent_player_id", "player_id", "accepted", "rejected", "seen", "created"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=generate_opponent_training,
            tags=("opponent", "preparation", "training"),
        ),
        Tool(
            name="generate_opponent_brief",
            description=(
                "A short, readable preparation brief assembled deterministically from the "
                "preparation report: what to expect, what to prepare against, and the "
                "sample sizes and limitations. No model inference is involved."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "color": {"type": "string", "enum": ["white", "black"]},
                        "player_id": {"type": "string", "minLength": 1},
                    },
                    "required": [],
                },
                outputs=("player", "summary", "prepare", "expected", "sample", "limitations"),
            ),
            permission=ToolPermission.PLAYER_CONTEXT,
            handler=generate_opponent_brief,
            tags=("opponent", "preparation", "brief"),
        ),
        Tool(
            name="get_opponent_requirements",
            description=(
                "The contract a legitimate opponent claim must satisfy: where it comes "
                "from, why no psychology or prediction is allowed, and how sample sizes "
                "are enforced."
            ),
            schema=ToolSchema(
                parameters={"type": "object", "properties": {}, "required": []},
                outputs=("requirements", "methodology_version"),
            ),
            permission=ToolPermission.ANY,
            handler=lambda _context: {
                "methodology_version": "9.0",
                "requirements": requirements,
                "note": OPPONENT_REASON,
            },
            tags=("opponent", "meta"),
        ),
    ]


def _brief_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """Deterministically assemble a short brief from a preparation report."""
    identity = report.get("identity") or {}
    statistics = report.get("statistics") or {}
    insights = report.get("insights") or []
    repertoire = report.get("repertoire") or {}

    prepare: list[str] = []
    expected: list[str] = []
    if repertoire:
        color = repertoire.get("color")
        for node in (repertoire.get("nodes") or [])[:3]:
            expected.append(f"As {color}, they played {node.get('san')} in {node.get('occurrences')} game(s).")
    for insight in insights:
        if insight.get("preparation_hint"):
            prepare.append(str(insight["preparation_hint"]))
    summary = (
        f"{identity.get('name', 'This opponent')}: {statistics.get('analyzed_games', 0)} analysed "
        f"game(s) of {statistics.get('total_games', 0)} imported "
        f"(coverage: {report.get('coverage')})."
    )
    return {
        "player": identity.get("name"),
        "summary": summary,
        "prepare": prepare[:5],
        "expected": expected[:5],
        "sample": {
            "analyzed_games": statistics.get("analyzed_games", 0),
            "total_games": statistics.get("total_games", 0),
            "evidence_count": report.get("evidence_count", 0),
        },
        "limitations": report.get("limitations", []),
        "note": "Assembled deterministically from stored games; no prediction is made.",
    }


__all__ = ["OPPONENT_REASON", "build_opponent_tools"]
