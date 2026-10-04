"""Anthropic tool-calling provider (messages API).

Uses ``httpx`` against the REST API directly. The agent's OpenAI-style tool
specs are translated to Anthropic's tool format, and Anthropic's
``tool_use``/``tool_result`` blocks are translated back — the agent loop stays
provider-neutral.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from argus.llm.base import LLMRequestError, LLMResponseError
from argus.llm.retry import post_with_rate_limit_retry
from argus.shared.logging import get_logger

logger = get_logger(__name__)

DEFAULT_API_BASE = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-4-20250514"
API_VERSION = "2023-06-01"


class AnthropicClient:
    """LLMClient implementation for the Anthropic messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = "",
        temperature: float = 0.3,
        max_output_tokens: int = 1200,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._model = model or DEFAULT_MODEL
        self._api_base = (base_url or DEFAULT_API_BASE).rstrip("/")
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._timeout = timeout_seconds

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Complete a conversation; returns ``message`` or ``tool_calls``."""
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_output_tokens,
            "temperature": self._temperature,
            "messages": self._to_anthropic_messages(messages),
        }
        if tools:
            payload["tools"] = [
                {
                    "name": spec["function"]["name"],
                    "description": spec["function"].get("description", ""),
                    "input_schema": spec["function"].get("parameters", {"type": "object"}),
                }
                for spec in tools
            ]
            payload["system"] = self._extract_system(messages)

        try:
            # A 429 is retried briefly with backoff before it is reported, so a
            # free-tier throttle does not degrade the turn to evidence-only.
            response = post_with_rate_limit_retry(
                lambda: httpx.post(
                    f"{self._api_base}/v1/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": API_VERSION,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self._timeout,
                ),
                provider="Anthropic",
            )
        except httpx.HTTPError as exc:
            raise LLMRequestError(f"Anthropic request failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise LLMRequestError(
                "Anthropic rejected the API key (check ARGUS_LLM_API_KEY)",
                details={"status": response.status_code},
            )
        if response.status_code == 429:
            raise LLMRequestError("Anthropic rate limit or quota exceeded (HTTP 429)")
        if response.status_code >= 400:
            raise LLMRequestError(
                f"Anthropic request failed with HTTP {response.status_code}",
                details={"body": response.text[:500]},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError("Anthropic returned a non-JSON response") from exc
        return self._parse(body)

    @staticmethod
    def _extract_system(messages: list[dict[str, Any]]) -> str | None:
        system_parts = [
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "system"
        ]
        return "\n\n".join(system_parts) if system_parts else None

    def _to_anthropic_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate the neutral history (including tool results) to Anthropic format."""
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                continue  # moved to the top-level system parameter
            if role == "tool":
                # A tool result must directly follow the requesting assistant turn.
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": str(message.get("tool_call_id", "")),
                                "content": json.dumps(message.get("content", {}), default=str),
                            }
                        ],
                    }
                )
                continue
            content = message.get("content", "")
            if role == "assistant" and message.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": str(content)})
                for call in message["tool_calls"]:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(call.get("id", call.get("name", "call"))),
                            "name": call.get("name", ""),
                            "input": call.get("arguments") or {},
                        }
                    )
                converted.append({"role": "assistant", "content": blocks})
                continue
            converted.append({"role": role or "user", "content": str(content)})
        return converted

    def _parse(self, body: dict[str, Any]) -> dict[str, Any]:
        content = body.get("content") or []
        if not content:
            raise LLMResponseError("Anthropic response contains no content blocks")
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": block.get("input") or {},
                    }
                )
        if tool_calls:
            return {"tool_calls": tool_calls, "message": "\n".join(text_parts)}
        message = "\n".join(part for part in text_parts if part.strip())
        if not message.strip():
            raise LLMResponseError("Anthropic returned an empty message")
        return {"message": message}
