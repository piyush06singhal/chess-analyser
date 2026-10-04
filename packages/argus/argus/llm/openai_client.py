"""OpenAI tool-calling provider (chat completions API).

Uses ``httpx`` against the REST API directly — no vendor SDK dependency — so
the provider layer stays thin and easily auditable. The key comes from
configuration only.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from argus.llm.base import LLMRequestError, LLMResponseError
from argus.llm.retry import post_with_rate_limit_retry
from argus.shared.logging import get_logger

logger = get_logger(__name__)

DEFAULT_API_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIClient:
    """LLMClient implementation for OpenAI-compatible chat APIs."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = "",
        temperature: float = 0.3,
        max_output_tokens: int = 1200,
        timeout_seconds: float = 60.0,
        provider_name: str = "OpenAI",
    ) -> None:
        self._api_key = api_key
        self._model = model or DEFAULT_MODEL
        self._api_base = (base_url or DEFAULT_API_BASE).rstrip("/")
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._timeout = timeout_seconds
        # The provider label is used only in error messages, so a Groq failure
        # never reports itself as an OpenAI failure (an honest error, not a
        # misleading one).
        self._provider = provider_name

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Complete a conversation; returns ``message`` or ``tool_calls``.

        The agent loop speaks a provider-neutral message shape; this method
        serialises it to the OpenAI wire format. That is the provider boundary's
        job: without it, a real provider rejects the assistant ``tool_calls``
        message (no ``id``) and the tool result (no ``tool_call_id``, non-string
        content) with a 400.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _to_wire_messages(messages),
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        body = json.dumps(payload)
        logger.debug(
            "%s request [messages=%d tools=%d bytes=%d]",
            self._provider,
            len(payload["messages"]),
            len(payload.get("tools") or []),
            len(body),
        )
        try:
            # A 429 is retried briefly with backoff before it is reported, so a
            # free-tier throttle does not degrade the turn to evidence-only.
            response = post_with_rate_limit_retry(
                lambda: httpx.post(
                    f"{self._api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    content=body,
                    timeout=self._timeout,
                ),
                provider=self._provider,
            )
        except httpx.HTTPError as exc:
            raise LLMRequestError(f"{self._provider} request failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise LLMRequestError(
                f"{self._provider} rejected the API key (check ARGUS_LLM_API_KEY)",
                details={"status": response.status_code},
            )
        if response.status_code == 429:
            raise LLMRequestError(
                f"{self._provider} rate limit or quota exceeded (HTTP 429)"
            )
        if response.status_code >= 400:
            raise LLMRequestError(
                f"{self._provider} request failed with HTTP {response.status_code}",
                details={"body": response.text[:500]},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError(f"{self._provider} returned a non-JSON response") from exc

        return self._parse(body)

    def _parse(self, body: dict[str, Any]) -> dict[str, Any]:
        choices = body.get("choices") or []
        if not choices:
            raise LLMResponseError(f"{self._provider} response contains no choices")
        message = choices[0].get("message") or {}
        tool_calls = [
            {
                # Keep the provider's call id so the tool result can be matched
                # to its request on the next turn (required by the wire format).
                "id": call.get("id") or f"call_{index}",
                "name": call.get("function", {}).get("name", ""),
                "arguments": _parse_arguments(call.get("function", {}).get("arguments", "{}")),
            }
            for index, call in enumerate(message.get("tool_calls") or [])
        ]
        if tool_calls:
            return {"tool_calls": tool_calls, "raw_message": message}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError(f"{self._provider} returned an empty message")
        return {"message": content}


def _to_wire_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert the agent loop's neutral messages to the OpenAI wire shape.

    * an assistant message with tool calls gets ``id``/``type``/``function`` with
      a JSON-string ``arguments``;
    * a tool result gets a ``tool_call_id`` and a string ``content`` (a JSON
      object is serialised, because the API requires a string).
    """
    wire: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for index, call in enumerate(message["tool_calls"]):
                arguments = call.get("arguments") or {}
                calls.append(
                    {
                        "id": call.get("id") or f"call_{index}",
                        "type": "function",
                        "function": {
                            "name": call.get("name", ""),
                            "arguments": arguments
                            if isinstance(arguments, str)
                            else json.dumps(arguments),
                        },
                    }
                )
            wire.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or None,
                    "tool_calls": calls,
                }
            )
            continue
        if role == "tool":
            content = message.get("content")
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id") or "call_0",
                    "content": content if isinstance(content, str) else json.dumps(content),
                }
            )
            continue
        wire.append(message)
    return wire


def _parse_arguments(raw: str) -> dict[str, Any]:
    """Parse a tool-call argument JSON string (empty string means no args)."""
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise LLMResponseError("tool call arguments are not valid JSON") from None
    if not isinstance(parsed, dict):
        raise LLMResponseError("tool call arguments must be a JSON object")
    return parsed
