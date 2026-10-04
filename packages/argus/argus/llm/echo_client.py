"""Echo LLM client for local development without external API calls.

Deliberately NOT a silent stand-in for a real provider: it identifies itself
in every reply and never invents chess facts. It is useful for developing the
agent loop, the API surface, and the UI against a controlled response.
"""

from __future__ import annotations

from typing import Any

from argus.shared.logging import get_logger

logger = get_logger(__name__)

IDENTIFIER = (
    "[echo provider — no LLM API is configured. "
    "Set ARGUS_LLM_PROVIDER and ARGUS_LLM_API_KEY for real coaching.]"
)


class EchoLLMClient:
    """Deterministic, self-identifying stand-in client.

    On the first turn it asks the model down to run one available tool so the
    tool-calling path can be exercised; afterwards it returns a plain message.
    """

    def __init__(self) -> None:
        self._turns = 0

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        self._turns += 1
        logger.debug("Echo LLM turn %d [tools=%d]", self._turns, len(tools or []))
        available_names = {t["function"]["name"] for t in (tools or [])}
        if self._turns == 1 and "get_current_position" in available_names:
            return {"tool_calls": [{"name": "get_current_position", "arguments": {}}]}
        return {
            "message": (
                "Caissa is running without a configured LLM provider, so I cannot "
                "produce a real coaching answer. The chess analysis itself is "
                "unaffected: engine reports and stored analyses remain fully "
                "available. " + IDENTIFIER
            )
        }
