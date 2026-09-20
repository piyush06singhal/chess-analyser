"""AI coaching agent foundation: tool abstraction + agent shell.

The agent NEVER calculates chess positions itself — all chess facts come from
tools that wrap the deterministic engine/analysis services and return
structured data. The LLM provider is injected via the :class:`LLMClient`
abstraction (implemented in a later phase).
"""

from argus.ai_agent.agent import ChessCoachAgent, LLMNotConfiguredError, LLMClient
from argus.ai_agent.tools import AgentTool, ToolRegistry, build_default_registry

__all__ = [
    "AgentTool",
    "ChessCoachAgent",
    "LLMClient",
    "LLMNotConfiguredError",
    "ToolRegistry",
    "build_default_registry",
]
