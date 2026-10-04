"""Agent tools: the complete, permissioned surface the agent may call.

The tool families are assembled here, and this module is the only place that knows
every tool that exists. That matters for two reasons:

* **Auditability.** `catalogue()` is a machine-readable list of everything the agent
  can do, with each tool's permission and availability. A reviewer can read one
  function and know the agent's entire reach.
* **Least privilege.** Tools whose providers are missing are registered but
  *unavailable*, with a reason. The agent therefore cannot call a capability that
  does not exist in this deployment — and, importantly, it is told why, so it can
  explain the gap instead of improvising around it.

The families themselves:

======================  ==========================================================
``positions``           what the board contains (deterministic, no engine)
``engine``              evaluations, best moves, MultiPV, move comparison
``game``                the stored game, its move analysis, moments, report
``player``              Player Intelligence, insights and their evidence
``opening``             a curated, versioned opening base that can say "unknown"
``prediction``          the gated route to a validated probability
``training``            personalized exercises from stored analysis
``opponent``            what the stored games say about an opponent
``scenario``            what-if analysis: comparison, counterfactuals, refusals
======================  ==========================================================
"""

from __future__ import annotations

from argus.ai_agent.tools.base import (
    Tool,
    ToolArgumentError,
    ToolHandler,
    ToolNotPermittedError,
    ToolOutcome,
    ToolPermission,
    ToolSchema,
    Toolbox,
    validate_arguments,
)
from argus.ai_agent.tools.chess_engine import (
    MAX_DEPTH,
    MAX_MULTIPV,
    build_engine_tools,
    clamp_depth,
    clamp_multipv,
    normalize_move,
)
from argus.ai_agent.tools.game_analysis import build_game_tools
from argus.ai_agent.tools.graph import build_graph_tools
from argus.ai_agent.tools.knowledge import (
    KNOWLEDGE_VERSION,
    build_knowledge_tools,
    search_concepts,
)
from argus.ai_agent.tools.live import build_live_tools
from argus.ai_agent.tools.legacy import (
    AgentTool,
    ToolRegistry,
    build_default_registry,
)
from argus.ai_agent.tools.opening import OPENING_BASE_VERSION, build_opening_tools, match_opening
from argus.ai_agent.tools.opponent import OPPONENT_REASON, build_opponent_tools
from argus.ai_agent.tools.player_analysis import build_player_tools
from argus.ai_agent.tools.position import board_facts, build_position_tools, summarize_facts
from argus.ai_agent.tools.prediction import KNOWN_TASKS, build_prediction_tools
from argus.ai_agent.tools.providers import AgentProviders
from argus.ai_agent.tools.scenarios import (
    NO_ENGINE_REASON,
    SCENARIO_REQUIREMENTS,
    build_scenario_tools,
)
from argus.ai_agent.tools.training import TRAINING_REASON, build_training_tools


def build_agent_toolbox(providers: AgentProviders) -> Toolbox:
    """Assemble every tool family into one permissioned toolbox."""
    toolbox = Toolbox()
    families = (
        build_position_tools,
        build_engine_tools,
        build_game_tools,
        build_player_tools,
        build_opening_tools,
        build_knowledge_tools,
        build_prediction_tools,
        build_training_tools,
        build_opponent_tools,
        build_scenario_tools,
        build_live_tools,
        build_graph_tools,
    )
    for family in families:
        for tool in family(providers):
            toolbox.register(tool)
    _apply_provider_availability(toolbox, providers)
    return toolbox


#: Which provider each tool needs. A missing provider makes the tool unavailable
#: *with the reason the provider layer recorded*, rather than failing at call time.
_PROVIDER_REQUIREMENTS: dict[str, tuple[str, str]] = {
    "get_game": ("game_lookup", "the game library"),
    "get_game_moves": ("game_moves", "the stored move list"),
    "get_game_summary": ("game_summary", "the game summary layer"),
    "get_game_trajectory": ("game_report", "the game report layer"),
    "get_game_analysis": ("game_report", "the game report layer"),
    "get_critical_moments": ("critical_moments", "the critical-moment layer"),
    "get_move_analysis": ("move_analysis", "stored move analysis"),
    "get_player_profile": ("player_profile", "the player profile layer"),
    "get_player_insights": ("player_insights", "the player insight layer"),
    "get_player_evidence": ("player_evidence", "the player evidence layer"),
    "get_validated_prediction": ("prediction", "a validated prediction model"),
    "get_training_recommendations": ("training_recommendations", "the training engine"),
    "generate_training_position": ("training_library", "the training library"),
    "get_review_queue": ("training_review_queue", "the training review queue"),
    "get_training_progress": ("training_progress", "the training engine"),
    "get_training_from_game": ("training_library", "the training library"),
    "generate_training_explanation": ("training_position", "the training engine"),
    "evaluate_training_attempt": ("training_evaluate", "the training engine"),
    "get_opponent_profile": ("opponent_profile", "the opponent intelligence layer"),
    "get_opponent_games": ("opponent_games", "the opponent game history"),
    "get_opponent_repertoire": ("opponent_repertoire", "the opponent repertoire layer"),
    "get_opponent_recent_repertoire": ("opponent_repertoire", "the opponent repertoire layer"),
    "get_opponent_position_responses": (
        "opponent_position_responses",
        "the opponent response layer",
    ),
    "get_opponent_tendencies": ("opponent_tendencies", "the opponent tendency layer"),
    "get_opponent_phase_statistics": (
        "opponent_phase_statistics",
        "the opponent phase layer",
    ),
    "get_opponent_preparation_report": (
        "opponent_preparation_report",
        "the preparation report layer",
    ),
    "generate_opponent_brief": ("opponent_preparation_report", "the preparation report layer"),
    "generate_opponent_training": ("opponent_preparation", "the opponent preparation training engine"),
    "compare_candidate_moves": ("scenario_compare_moves", "a chess engine"),
    "compare_positions": ("scenario_compare_positions", "the position comparison engine"),
    "analyze_counterfactual": ("scenario_counterfactual", "a chess engine"),
    "explain_why_not_move": ("scenario_why_not", "a chess engine"),
    "explain_what_if": ("scenario_what_if", "a chess engine"),
    "explore_turning_points": ("scenario_explorer", "the turning-point explorer"),
    "create_training_from_scenario": ("scenario_training", "the training engine"),
    "get_opponent_response_scenario": (
        "scenario_opponent_response",
        "the opponent response layer",
    ),
    "get_live_game": ("live_game", "the live game layer"),
    "get_live_position": ("live_game", "the live game layer"),
    "get_live_game_status": ("live_game", "the live game layer"),
    "get_live_clock": ("live_game", "the live game layer"),
    "get_live_game_history": ("live_history", "the live game history layer"),
    "get_training_coach_state": ("live_coach_state", "the live coach layer"),
    "find_related_games": ("graph_related_games", "the intelligence graph"),
    "find_related_positions": ("graph_related_positions", "the intelligence graph"),
    "find_player_patterns": ("graph_player_patterns", "the intelligence graph"),
    "find_pattern_evidence": ("graph_pattern_evidence", "the intelligence graph"),
    "find_training_history": ("graph_training_history", "the intelligence graph"),
    "find_opponent_connections": ("graph_opponent_connections", "the intelligence graph"),
    "find_opening_connections": ("graph_opening_connections", "the intelligence graph"),
    "find_knowledge_for_position": ("graph_knowledge_for_position", "the intelligence graph"),
    "trace_insight_evidence": ("graph_trace_evidence", "the intelligence graph"),
}


def _apply_provider_availability(toolbox: Toolbox, providers: AgentProviders) -> None:
    for name, (attribute, description) in _PROVIDER_REQUIREMENTS.items():
        if getattr(providers, attribute, None) is not None:
            continue
        _mark_unavailable(toolbox, name, providers, attribute, description)
    # `get_player_statistics` falls back to the profile, so it is only genuinely
    # available when at least one of the two can answer. Declaring it available
    # otherwise would be a lie the tool would then have to admit at call time.
    if (
        providers.player_statistics is None
        and providers.player_profile is None
    ):
        _mark_unavailable(
            toolbox,
            "get_player_statistics",
            providers,
            "player_statistics",
            "the player statistics layer",
        )


def _mark_unavailable(
    toolbox: Toolbox,
    name: str,
    providers: AgentProviders,
    attribute: str,
    description: str,
) -> None:
    tool = toolbox.get(name)
    tool.available = False
    tool.reason = providers.reason_for(
        attribute, f"Caissa cannot reach {description} in this deployment."
    )


__all__ = [
    "KNOWLEDGE_VERSION",
    "KNOWN_TASKS",
    "MAX_DEPTH",
    "MAX_MULTIPV",
    "NO_ENGINE_REASON",
    "OPENING_BASE_VERSION",
    "OPPONENT_REASON",
    "SCENARIO_REQUIREMENTS",
    "TRAINING_REASON",
    "AgentProviders",
    "AgentTool",
    "Tool",
    "ToolRegistry",
    "ToolArgumentError",
    "ToolHandler",
    "ToolNotPermittedError",
    "ToolOutcome",
    "ToolPermission",
    "ToolSchema",
    "Toolbox",
    "board_facts",
    "build_agent_toolbox",
    "build_default_registry",
    "build_engine_tools",
    "build_game_tools",
    "build_graph_tools",
    "build_knowledge_tools",
    "build_live_tools",
    "build_opening_tools",
    "build_opponent_tools",
    "build_player_tools",
    "build_position_tools",
    "build_prediction_tools",
    "build_training_tools",
    "clamp_depth",
    "clamp_multipv",
    "match_opening",
    "normalize_move",
    "search_concepts",
    "summarize_facts",
    "validate_arguments",
]
