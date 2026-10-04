"""Tests for the LLM provider layer and the coach API surface.

The coach is probabilistic AI kept behind a clean abstraction: these tests
verify the provider-neutral contract, the factory's configuration handling,
and that the API answers honestly (501) when no provider is configured.
"""

from __future__ import annotations

import httpx
import pytest

from argus.llm.base import LLMNotConfiguredError, LLMSettings
from argus.llm.echo_client import EchoLLMClient
from argus.llm.factory import build_llm_client
from argus.llm.openai_client import OpenAIClient


# --- settings / factory -------------------------------------------------------------


class TestLLMSettings:
    def test_empty_provider_is_unconfigured(self):
        settings = LLMSettings(provider="", model="", api_key="")
        assert settings.is_configured is False

    def test_real_provider_requires_key(self):
        settings = LLMSettings(provider="openai", model="gpt-4o-mini", api_key="")
        assert settings.is_configured is False
        keyed = LLMSettings(provider="openai", model="gpt-4o-mini", api_key="sk-test")
        assert keyed.is_configured is True

    def test_echo_provider_needs_no_key(self):
        settings = LLMSettings(provider="echo", model="", api_key="")
        assert settings.is_configured is True

    def test_describe_never_leaks_key(self):
        settings = LLMSettings(provider="openai", model="gpt-4o-mini", api_key="sk-secret")
        snapshot = settings.describe()
        assert snapshot["api_key_set"] is True
        assert "sk-secret" not in str(snapshot)


class TestLLMFactory:
    def test_missing_provider_raises_not_configured(self):
        with pytest.raises(LLMNotConfiguredError):
            build_llm_client(LLMSettings(provider="", model="", api_key=""))

    def test_missing_key_raises_not_configured(self):
        with pytest.raises(LLMNotConfiguredError):
            build_llm_client(LLMSettings(provider="openai", model="", api_key=""))

    def test_unknown_provider_raises_not_configured(self):
        with pytest.raises(LLMNotConfiguredError):
            build_llm_client(LLMSettings(provider="hal9000", model="", api_key="k"))

    def test_echo_provider_builds(self):
        client = build_llm_client(LLMSettings(provider="echo", model="", api_key=""))
        assert isinstance(client, EchoLLMClient)

    def test_openai_provider_builds(self):
        client = build_llm_client(LLMSettings(provider="openai", model="gpt-4o-mini", api_key="sk-test"))
        assert isinstance(client, OpenAIClient)

    def test_groq_provider_builds_with_groq_defaults(self):
        """Groq is an OpenAI-compatible provider with its own base URL and model."""
        client = build_llm_client(LLMSettings(provider="groq", model="", api_key="gsk-test"))
        assert isinstance(client, OpenAIClient)
        # The default base URL is Groq's, not OpenAI's — checked via the real
        # attribute so a mis-wired default is caught.
        assert "groq.com" in client._api_base
        assert client._model == "openai/gpt-oss-120b"

    def test_groq_requires_a_key(self):
        with pytest.raises(LLMNotConfiguredError):
            build_llm_client(LLMSettings(provider="groq", model="", api_key=""))

    def test_unknown_provider_error_names_the_supported_set(self):
        with pytest.raises(LLMNotConfiguredError) as excinfo:
            build_llm_client(LLMSettings(provider="hal9000", model="", api_key="k"))
        assert "groq" in str(excinfo.value)


# --- echo provider behavior -----------------------------------------------------------


class TestEchoClient:
    def test_first_turn_requests_a_tool(self):
        client = EchoLLMClient()
        response = client.complete(
            [{"role": "user", "content": "hi"}],
            tools=[{"function": {"name": "get_current_position", "parameters": {}}}],
        )
        assert response["tool_calls"][0]["name"] == "get_current_position"

    def test_identifies_itself_plainly(self):
        client = EchoLLMClient()
        client.complete([{"role": "user", "content": "hi"}])
        response = client.complete([{"role": "user", "content": "hi"}])
        assert "echo provider" in response["message"]


# --- coach API ------------------------------------------------------------------------


class TestCoachAPI:
    def test_status_reports_unconfigured_without_key(self, client, monkeypatch):
        monkeypatch.setenv("ARGUS_LLM_PROVIDER", "")
        from argus_api.config import get_settings

        get_settings.cache_clear()
        client.app.state.settings = get_settings()
        body = client.get("/api/coach/status").json()
        assert body["configured"] is False
        assert body["provider"] is None

    def test_chat_without_provider_is_honest_501(self, client, monkeypatch):
        monkeypatch.setenv("ARGUS_LLM_PROVIDER", "")
        from argus_api.config import get_settings

        get_settings.cache_clear()
        client.app.state.settings = get_settings()
        response = client.post("/api/coach/chat", json={"message": "hello"})
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "llm_not_configured"

    def test_chat_with_echo_provider_works(self, client, monkeypatch):
        monkeypatch.setenv("ARGUS_LLM_PROVIDER", "echo")
        from argus_api.config import get_settings

        get_settings.cache_clear()
        client.app.state.settings = get_settings()
        response = client.post("/api/coach/chat", json={"message": "How is my position?"})
        assert response.status_code == 200
        body = response.json()
        assert body["message"]
        assert "tool_trace" in body and "tool_calls_used" in body

    def test_empty_message_is_rejected(self, client):
        response = client.post("/api/coach/chat", json={"message": ""})
        assert response.status_code == 422


# --- provider wire format -------------------------------------------------------------


class TestWireFormat:
    """The agent loop speaks a neutral shape; the provider serialises it.

    Without this translation a real provider (OpenAI, Groq) rejects the turn: an
    assistant ``tool_calls`` message with no ``id`` and a tool result with no
    ``tool_call_id`` and non-string content are a 400. The echo provider never
    noticed, so the bug only surfaced against a real API.
    """

    def test_tool_call_and_result_are_serialised_for_the_wire(self):
        from argus.llm.openai_client import _to_wire_messages

        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "why?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_abc", "name": "get_move_analysis", "arguments": {"ply": 17}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc",
                "name": "get_move_analysis",
                "content": {"status": "ok", "result": {"cp": 23}},
            },
        ]
        wire = _to_wire_messages(messages)
        assistant = wire[2]
        assert assistant["tool_calls"][0]["id"] == "call_abc"
        assert assistant["tool_calls"][0]["type"] == "function"
        # arguments must be a JSON string on the wire, not a dict
        assert isinstance(assistant["tool_calls"][0]["function"]["arguments"], str)
        tool_result = wire[3]
        assert tool_result["tool_call_id"] == "call_abc"
        # tool content must be a string on the wire, not a dict
        assert isinstance(tool_result["content"], str)

    def test_a_call_without_an_id_gets_a_stable_fallback(self):
        from argus.llm.openai_client import _to_wire_messages

        wire = _to_wire_messages(
            [
                {"role": "assistant", "content": "", "tool_calls": [{"name": "t", "arguments": {}}]},
                {"role": "tool", "name": "t", "content": {"ok": True}},
            ]
        )
        assert wire[0]["tool_calls"][0]["id"] == "call_0"
        assert wire[1]["tool_call_id"] == "call_0"


# --- rate-limit retry ------------------------------------------------------------


def _response(status_code: int, headers: dict[str, str] | None = None) -> httpx.Response:
    """A minimal response object: only the status and headers are read here."""
    return httpx.Response(status_code, headers=headers or {})


class TestRateLimitRetry:
    """A free-tier 429 clears with a short wait; other failures do not wait.

    Without this the first throttle on a request turns the whole turn into an
    evidence-only answer, which is honest but a worse product than the same
    answer one second later.
    """

    def test_retries_a_429_then_returns_the_success(self):
        from argus.llm.retry import post_with_rate_limit_retry

        replies = iter([_response(429), _response(429), _response(200)])
        slept: list[float] = []
        result = post_with_rate_limit_retry(
            lambda: next(replies), provider="Groq", sleep=slept.append
        )
        assert result.status_code == 200
        # Backoff is exponential, so the second wait is longer than the first.
        assert len(slept) == 2
        assert slept[1] > slept[0]

    def test_gives_up_after_the_cap_so_a_hard_limit_fails_fast(self):
        from argus.llm.retry import post_with_rate_limit_retry

        slept: list[float] = []
        result = post_with_rate_limit_retry(
            lambda: _response(429), provider="Groq", sleep=slept.append
        )
        # The 429 is handed back, so the client raises its usual honest error.
        assert result.status_code == 429
        assert len(slept) == 2  # two waits across three attempts, then stop

    def test_any_other_status_is_returned_without_waiting(self):
        from argus.llm.retry import post_with_rate_limit_retry

        slept: list[float] = []
        result = post_with_rate_limit_retry(
            lambda: _response(401), provider="Groq", sleep=slept.append
        )
        assert result.status_code == 401
        assert slept == []

    def test_a_short_retry_after_header_is_honoured(self):
        from argus.llm.retry import post_with_rate_limit_retry

        replies = iter([_response(429, {"retry-after": "0.5"}), _response(200)])
        slept: list[float] = []
        post_with_rate_limit_retry(
            lambda: next(replies), provider="Groq", sleep=slept.append
        )
        assert slept == [0.5]

    def test_an_absurd_retry_after_fails_fast_instead_of_stalling(self):
        from argus.llm.retry import post_with_rate_limit_retry

        replies = iter([_response(429, {"retry-after": "3600"}), _response(200)])
        slept: list[float] = []
        result = post_with_rate_limit_retry(
            lambda: next(replies), provider="Groq", sleep=slept.append
        )
        # The provider asked for an hour's wait; this request must not sit on it.
        assert result.status_code == 429
        assert slept == []

    def test_the_openai_client_retries_before_reporting_a_rate_limit(
        self, monkeypatch
    ):
        """End to end: a 429 then a good body means a real answer, not a fallback."""
        from argus.llm.openai_client import OpenAIClient

        monkeypatch.setattr("argus.llm.retry.time.sleep", lambda _seconds: None)
        replies = iter(
            [
                _response(429),
                _response(200),
            ]
        )

        def fake_post(*_args, **_kwargs):
            response = next(replies)
            if response.status_code == 200:
                response = httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": "Stockfish evaluates +1.20."}}
                        ]
                    },
                )
            return response

        monkeypatch.setattr("argus.llm.openai_client.httpx.post", fake_post)
        client = OpenAIClient(api_key="gsk-test", model="m", provider_name="Groq")
        result = client.complete([{"role": "user", "content": "how?"}])
        assert result["message"] == "Stockfish evaluates +1.20."

    def test_a_persistent_rate_limit_still_reports_honestly(self, monkeypatch):
        from argus.llm.base import LLMRequestError
        from argus.llm.openai_client import OpenAIClient

        monkeypatch.setattr("argus.llm.retry.time.sleep", lambda _seconds: None)
        monkeypatch.setattr(
            "argus.llm.openai_client.httpx.post", lambda *a, **k: _response(429)
        )
        client = OpenAIClient(api_key="gsk-test", model="m", provider_name="Groq")
        with pytest.raises(LLMRequestError) as excinfo:
            client.complete([{"role": "user", "content": "how?"}])
        assert "429" in str(excinfo.value)
