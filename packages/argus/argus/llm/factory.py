"""LLM client factory: configuration → provider instance.

The provider is selected purely from configuration; no API key is hardcoded
anywhere. Unknown or misconfigured providers raise ``LLMNotConfiguredError``
so the API can answer with an honest 501 instead of failing obscurely.
"""

from __future__ import annotations

from argus.llm.base import LLMClient, LLMNotConfiguredError, LLMSettings
from argus.shared.logging import get_logger

logger = get_logger(__name__)


def build_llm_client(settings: LLMSettings) -> LLMClient:
    """Build the configured LLM client.

    Raises:
        LLMNotConfiguredError: when no provider is configured or a real
            provider is missing its API key.
    """
    if not settings.provider:
        raise LLMNotConfiguredError(
            "No LLM provider configured; set ARGUS_LLM_PROVIDER "
            "(openai | anthropic | groq | echo) and ARGUS_LLM_API_KEY"
        )
    if settings.provider == "echo":
        from argus.llm.echo_client import EchoLLMClient

        logger.info("Using the echo LLM provider (development only)")
        return EchoLLMClient()
    if not settings.api_key:
        raise LLMNotConfiguredError(
            f"LLM provider '{settings.provider}' is selected but ARGUS_LLM_API_KEY is not set"
        )
    if settings.provider in ("openai", "groq"):
        from argus.llm.openai_client import OpenAIClient

        # Groq exposes an OpenAI-compatible chat-completions API, so it uses the
        # same client with a different base URL and a Groq default model. The
        # provider name is validated by the factory, never guessed.
        is_groq = settings.provider == "groq"
        return OpenAIClient(
            api_key=settings.api_key,
            model=settings.model or ("openai/gpt-oss-120b" if is_groq else ""),
            base_url=settings.base_url
            or ("https://api.groq.com/openai/v1" if is_groq else ""),
            temperature=settings.temperature,
            max_output_tokens=settings.max_output_tokens,
            timeout_seconds=settings.timeout_seconds,
            provider_name="Groq" if is_groq else "OpenAI",
        )
    if settings.provider == "anthropic":
        from argus.llm.anthropic_client import AnthropicClient

        return AnthropicClient(
            api_key=settings.api_key,
            model=settings.model,
            base_url=settings.base_url,
            temperature=settings.temperature,
            max_output_tokens=settings.max_output_tokens,
            timeout_seconds=settings.timeout_seconds,
        )
    raise LLMNotConfiguredError(
        f"Unknown LLM provider '{settings.provider}' "
        "(supported: openai, anthropic, groq, echo)"
    )
