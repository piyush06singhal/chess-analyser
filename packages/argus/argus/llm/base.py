"""LLM client contract and shared errors.

The contract matches the tool-calling loop in :mod:`argus.ai_agent.agent`:
``complete`` receives provider-neutral messages and OpenAI-style tool specs
and returns either a final assistant ``message`` or ``tool_calls`` to execute.
"""

from __future__ import annotations

from typing import Any, Protocol

from argus.shared.errors import ArgusError


class LLMError(ArgusError):
    """Base class for LLM provider failures."""

    code = "llm_error"


class LLMNotConfiguredError(LLMError):
    """Raised when no provider/key is configured."""

    code = "llm_not_configured"


class LLMRequestError(LLMError):
    """Raised when the provider request itself fails (network, auth, quota)."""

    code = "llm_request_error"


class LLMResponseError(LLMError):
    """Raised when the provider returns a malformed/empty response."""

    code = "llm_response_error"


class LLMClient(Protocol):
    """Provider-neutral LLM client (see :mod:`argus.ai_agent.agent`)."""

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]: ...


class LLMSettings:
    """Configuration snapshot for building an LLM client (no secrets hardcoded).

    Attributes:
        provider: ``"openai"``, ``"anthropic"``, ``"groq"``, ``"echo"`` or empty/None.
        model: provider model identifier.
        api_key: credential from the environment; empty means unconfigured.
        base_url: optional API base URL override (proxies, gateways).
        temperature: sampling temperature.
        max_output_tokens: response budget per completion.
        timeout_seconds: request timeout.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        base_url: str = "",
        temperature: float = 0.3,
        max_output_tokens: int = 1200,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.provider = (provider or "").strip().lower()
        self.model = (model or "").strip()
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").strip()
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds

    @property
    def is_configured(self) -> bool:
        """True when a real provider has everything it needs."""
        if self.provider == "echo":
            return True
        return bool(self.provider) and bool(self.api_key)

    def describe(self) -> dict[str, Any]:
        """Status snapshot safe to expose over the API (key never included)."""
        return {
            "provider": self.provider or None,
            "model": self.model or None,
            "configured": self.is_configured,
            "api_key_set": bool(self.api_key),
        }
