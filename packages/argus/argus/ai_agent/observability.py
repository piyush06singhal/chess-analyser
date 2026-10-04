"""Agent observability: what happened during a turn, safely recorded.

An agent has more failure modes than a normal endpoint — a tool that silently
returns nothing, a loop that runs long, a model that answers without consulting
anything — and none of them raise an error. They are only visible if the turn is
recorded. So every turn produces an :class:`AgentTrace` (spec §34).

Two rules make the trace safe to keep:

**Never log credentials or private payloads.** API keys are never in a trace: keys
live in the provider, and the trace only ever records a provider *name*. Tool
arguments are recorded with ids and small scalars, and large payloads are reduced
to sizes. The redaction pass runs over the finished trace as a backstop, because a
provider error message is exactly the sort of string that can carry a key.

**Never trust the trace to be complete.** A trace records what the agent did; it is
not proof that the answer is correct. That distinction is why validation is a
separate layer with its own result.

The trace is returned with the answer, so the UI can show "3 tools · 1.2s · no LLM"
instead of asking the user to take the turn on faith.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Patterns redacted from any string that enters a trace.
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
    re.compile(r"\b(api[_-]?key|apikey|token|password|secret)\b\s*[:=]\s*\S+", re.IGNORECASE),
)
_REDACTED = "[redacted]"

#: Argument keys whose values must never be recorded verbatim.
_SENSITIVE_ARG_KEYS = {"api_key", "token", "password", "secret", "authorization"}


def scrub(text: str) -> str:
    """Redact anything that looks like a credential."""
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(_REDACTED, cleaned)
    return cleaned


def safe_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """A trace-safe view of tool arguments."""
    if not arguments:
        return {}
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if key.lower() in _SENSITIVE_ARG_KEYS:
            safe[key] = _REDACTED
            continue
        if isinstance(value, str):
            safe[key] = scrub(value if len(value) <= 200 else value[:197] + "…")
        elif isinstance(value, (int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, dict):
            safe[key] = f"<object: {len(value)} key(s)>"
        elif isinstance(value, (list, tuple)):
            safe[key] = f"<list: {len(value)} item(s)>"
        else:
            safe[key] = f"<{type(value).__name__}>"
    return safe


@dataclass
class ToolCallRecord:
    """One tool execution."""

    name: str
    ok: bool
    duration_ms: float = 0.0
    error_code: str | None = None
    error_message: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    result_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.name,
            "ok": self.ok,
            "duration_ms": round(self.duration_ms, 1),
            "error_code": self.error_code,
            "error_message": scrub(self.error_message) if self.error_message else None,
            "arguments": safe_arguments(self.arguments),
            "result_bytes": self.result_bytes,
        }


@dataclass
class AgentTrace:
    """Everything recorded about one agent turn."""

    request_id: str
    question: str = ""
    mode: str = "coach"
    context: dict[str, Any] = field(default_factory=dict)
    intents: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    deterministic: bool = False
    iterations: int = 0
    validation_passed: bool | None = None
    validation_checked: int = 0
    #: Free-form stages with timings, e.g. {"plan": 0.4, "llm": 900.1}.
    timings_ms: dict[str, float] = field(default_factory=dict)
    status: str = "ok"
    notes: list[str] = field(default_factory=list)

    # --- recording -----------------------------------------------------------

    def add_tool_call(self, record: ToolCallRecord) -> None:
        self.tool_calls.append(record)

    def time(self, stage: str, duration_ms: float) -> None:
        self.timings_ms[stage] = round(self.timings_ms.get(stage, 0.0) + duration_ms, 1)

    def note(self, text: str) -> None:
        self.notes.append(scrub(text))

    # --- reading -------------------------------------------------------------

    @property
    def total_ms(self) -> float:
        return round(sum(self.timings_ms.values()), 1)

    @property
    def tool_count(self) -> int:
        return len(self.tool_calls)

    @property
    def failed_tools(self) -> list[ToolCallRecord]:
        return [record for record in self.tool_calls if not record.ok]

    @property
    def tools_used(self) -> list[str]:
        return [record.name for record in self.tool_calls]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "mode": self.mode,
            # Context values keep their native type (a ply stays an int, not "17"):
            # the trace is read by machines as well as people, and a caller asking
            # "which ply?" should not have to parse a string. Strings are still
            # scrubbed, because a context can carry a caller-supplied FEN or id.
            "context": {
                key: scrub(value) if isinstance(value, str) else value
                for key, value in self.context.items()
            },
            "intents": list(self.intents),
            "tool_calls": [record.to_dict() for record in self.tool_calls],
            "tool_count": self.tool_count,
            "failed_tool_count": len(self.failed_tools),
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "deterministic": self.deterministic,
            "iterations": self.iterations,
            "validation": {
                "passed": self.validation_passed,
                "checked": self.validation_checked,
            },
            "timings_ms": dict(self.timings_ms),
            "total_ms": self.total_ms,
            "status": self.status,
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        parts = [f"{self.tool_count} tool call(s)"]
        if self.deterministic:
            parts.append("no LLM")
        elif self.provider:
            parts.append(f"{self.provider}{'/' + self.model if self.model else ''}")
        parts.append(f"{int(self.total_ms)}ms")
        if self.status != "ok":
            parts.append(self.status)
        return " · ".join(parts)


__all__ = ["AgentTrace", "ToolCallRecord", "safe_arguments", "scrub"]
