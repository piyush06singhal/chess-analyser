"""AI chess coach agent shell.

Phase 1 foundation: the tool-calling contract. The agent does NOT calculate
chess positions — every chess fact comes from a registered tool. The LLM
provider is injected via :class:`LLMClient`; without one, ``run`` raises a
clear configuration error (an honest error state, not a fake conversation).
"""

from __future__ import annotations

from typing import Any, Protocol

from argus.ai_agent.tools import ToolRegistry
from argus.shared.errors import ArgusError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

MAX_TOOL_ITERATIONS = 8


class LLMClient(Protocol):
    """LLM provider abstraction (providers are implemented in a later phase).

    Implementations must support tool calling: given ``messages`` and tool
    specs, return a response dict containing either a ``message`` (assistant
    text) or ``tool_calls`` (name/arguments pairs to execute).
    """

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]: ...


class LLMNotConfiguredError(ArgusError):
    """Raised when the agent is used without an LLM provider."""

    code = "llm_not_configured"


class ChessCoachAgent:
    """Coaching agent that reasons over tool outputs.

    The ``run`` loop sends the conversation plus tool specs to the LLM client,
    executes requested tools through the registry, feeds results back, and
    stops at a final message or the iteration guard.
    """

    def __init__(self, registry: ToolRegistry, llm_client: LLMClient | None = None) -> None:
        self._registry = registry
        self._llm_client = llm_client

    @property
    def tool_specs(self) -> list[dict[str, Any]]:
        """Tool specs available to the LLM (only tools that actually work)."""
        return self._registry.to_tool_specs(only_available=True)

    def run(self, user_message: str, *, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Run one coaching turn.

        Raises:
            LLMNotConfiguredError: when no LLM provider is configured.
            ToolError subclasses: when a requested tool fails.
        """
        if self._llm_client is None:
            raise LLMNotConfiguredError(
                "No LLM provider configured; implement an LLMClient and pass it to the agent"
            )
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": user_message})
        specs = self.tool_specs

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self._llm_client.complete(messages, tools=specs)
            tool_calls = response.get("tool_calls") or []
            if not tool_calls:
                return {"message": response.get("message", ""), "tool_calls_used": 0}
            for call in tool_calls:
                name = call.get("name", "")
                try:
                    result = self._registry.call(name, call.get("arguments") or {})
                    payload: dict[str, Any] = {"status": "ok", "result": result}
                except ArgusError as exc:
                    payload = {"status": "error", "error": exc.to_dict()}
                messages.append({"role": "tool", "name": name, "content": payload})
        logger.warning("Agent hit the tool iteration guard (%d)", MAX_TOOL_ITERATIONS)
        return {"message": "", "tool_calls_used": MAX_TOOL_ITERATIONS, "truncated": True}
