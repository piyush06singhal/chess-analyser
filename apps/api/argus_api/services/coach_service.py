"""Coach service: wires configuration → LLM client → agent loop.

The agent is provider-neutral; this module resolves the active LLM settings
(including the ``echo`` development provider), builds the client via the
factory, and produces honest status snapshots for the UI. The engine is
injected by the route layer (``app.state.engine``) so tools always operate on
the same engine instance the rest of the API uses. LLM errors propagate as
domain errors and are mapped centrally to HTTP responses.
"""

from __future__ import annotations

from argus.ai_agent.agent import ChessCoachAgent
from argus.ai_agent.tools import ToolRegistry, build_default_registry
from argus.analysis.engine.base import ChessEngine
from argus.analysis.game_analyzer import GameAnalyzer
from argus.llm.base import LLMClient, LLMNotConfiguredError, LLMSettings
from argus.llm.factory import build_llm_client
from argus.shared.logging import get_logger

logger = get_logger(__name__)


def llm_settings_from(api_settings) -> LLMSettings:  # noqa: ANN001 — argus_api.config.Settings
    """Map API settings to the LLM configuration snapshot."""
    return LLMSettings(
        provider=api_settings.llm_provider,
        model=api_settings.llm_model,
        api_key=api_settings.llm_api_key,
        base_url=api_settings.llm_base_url,
        temperature=api_settings.llm_temperature,
        max_output_tokens=api_settings.llm_max_output_tokens,
        timeout_seconds=api_settings.llm_timeout_seconds,
    )


def coach_status(api_settings) -> dict:  # noqa: ANN001
    """Status snapshot for the UI (never includes the API key)."""
    settings = llm_settings_from(api_settings)
    return settings.describe()


def build_coach(api_settings, engine: ChessEngine) -> ChessCoachAgent:  # noqa: ANN001
    """Build the coaching agent for one request.

    Raises:
        LLMNotConfiguredError: when no usable provider is configured.
    """
    settings = llm_settings_from(api_settings)
    if not settings.is_configured:
        raise LLMNotConfiguredError(
            "No LLM provider configured; set ARGUS_LLM_PROVIDER "
            "(openai | anthropic | groq | echo) and ARGUS_LLM_API_KEY"
        )
    client: LLMClient = build_llm_client(settings)
    registry = build_registry(engine)
    return ChessCoachAgent(registry, client)


def build_registry(engine: ChessEngine) -> ToolRegistry:
    """Build the tool registry bound to the API's engine instance.

    Game-context tools (``get_current_position``, ``get_game_analysis``)
    report themselves unavailable until a game is loaded into the session in a
    later phase — the registry stays honest about what it can do.
    """
    return build_default_registry(engine, GameAnalyzer(engine))
