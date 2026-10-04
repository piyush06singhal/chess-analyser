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

#: The shell has no evidence-packet validator (the Phase 7 agent owns that), so
#: its primary guard is the same explicit prohibition the Phase 7 prompt carries:
#: a number may be repeated only when a tool produced it, and a fact the user
#: supplied is never confirmed as one of ours. Without this the model agrees with
#: a leading premise ("assume my blunder rate is 42%") and restates it as fact.
SYSTEM_PROMPT = (
    "You are Caissa, a chess coaching assistant. Every chess fact you state must "
    "come from a tool result in this conversation. Never invent an evaluation, a "
    "percentage, a count, an opening name or a position. Never confirm a number, "
    "rate or statistic the user supplied as if Caissa had measured it: if it is "
    "not in a tool result, say plainly that you do not have it. If a tool is "
    "unavailable, say so rather than answering from assumption. State a sample "
    "size before making any claim about a player's tendency."
)


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
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *(history or []),
        ]
        messages.append({"role": "user", "content": user_message})
        specs = self.tool_specs
        tool_trace: list[dict[str, Any]] = []
        tool_calls_used = 0

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self._llm_client.complete(messages, tools=specs)
            tool_calls = response.get("tool_calls") or []
            if not tool_calls:
                return {
                    "message": response.get("message", ""),
                    "tool_calls_used": tool_calls_used,
                    "tool_trace": tool_trace,
                }
            # The assistant's tool request must be appended *before* its results.
            # A real provider (OpenAI, Groq) renders the conversation as a
            # template: a `tool` message with no preceding assistant `tool_calls`
            # entry, or one whose call carries an empty function name, is rejected
            # ("Tools should have a name!"). The neutral shape is the same one
            # `_to_wire_messages` expects, so the provider can serialise it.
            normalized = [
                {
                    "id": call.get("id") or f"call_{index}",
                    "name": call.get("name", ""),
                    "arguments": call.get("arguments") or {},
                }
                for index, call in enumerate(tool_calls)
            ]
            messages.append(
                {"role": "assistant", "content": None, "tool_calls": normalized}
            )
            for call in normalized:
                name = call["name"]
                tool_calls_used += 1
                try:
                    result = self._registry.call(name, call["arguments"])
                    payload: dict[str, Any] = {"status": "ok", "result": result}
                except ArgusError as exc:
                    payload = {"status": "error", "error": exc.to_dict()}
                tool_trace.append({"name": name, "status": payload["status"]})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": name,
                        "content": payload,
                    }
                )
        logger.warning("Agent hit the tool iteration guard (%d)", MAX_TOOL_ITERATIONS)
        return {
            "message": "",
            "tool_calls_used": tool_calls_used,
            "tool_trace": tool_trace,
            "truncated": True,
        }
