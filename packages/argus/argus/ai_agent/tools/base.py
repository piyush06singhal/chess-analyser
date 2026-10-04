"""Tool contract: schema, permission, validation, authorization.

A tool is the only way the agent can learn a chess fact, which makes the tool
boundary the place where three promises are kept.

**Schema.** Every tool declares a JSON Schema for its arguments and an explicit
list of the output fields it returns. Arguments are validated *before* the handler
runs, so an LLM emitting ``{"depth": "deep"}`` or forgetting ``ply`` gets a
structured refusal instead of a stack trace — or worse, a handler that silently
does something else.

**Permission.** Each tool declares what it needs before it may run:

* :attr:`ToolPermission.ANY` — no context (engine analysis of a given FEN).
* :attr:`ToolPermission.GAME_CONTEXT` — an active game, and *authorized*.
* :attr:`ToolPermission.PLAYER_CONTEXT` — an active player.
* :attr:`ToolPermission.PRODUCTION_MODEL` — a production-gated model exists.
* :attr:`ToolPermission.VALIDATED_POSITION` — the position must come from a
  stored, analysed game rather than from the model's imagination.

**Authorization.** A context carries the game ids the caller may read. A tool
refuses an id outside that set, and it does so here, in the backend — never by
asking the model to be well-behaved (spec §39/§40).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.shared.errors import ArgusError, ValidationError


class ToolPermission(str, Enum):
    """What a tool requires before it may run."""

    ANY = "any"
    GAME_CONTEXT = "game_context"
    #: Requires an active *live* game the caller is authorized for.
    LIVE_CONTEXT = "live_context"
    PLAYER_CONTEXT = "player_context"
    PRODUCTION_MODEL = "production_model"
    VALIDATED_POSITION = "validated_position"


class ToolNotPermittedError(ArgusError):
    """Raised when a tool is called without the context it requires."""

    code = "tool_not_permitted"


class ToolArgumentError(ValidationError):
    """Raised when tool arguments fail schema validation."""

    code = "tool_argument_error"


@dataclass(frozen=True)
class ToolSchema:
    """The declared contract of a tool."""

    parameters: dict[str, Any]
    #: Documented output fields. Not enforced at runtime (the handler is ours),
    #: but emitted in the catalogue so the contract is visible and testable.
    outputs: tuple[str, ...] = ()
    #: Dollar-free cost hints used by the resource limiter.
    uses_engine: bool = False
    uses_llm_tokens: bool = False


#: Handlers receive the turn's context as a keyword argument plus the validated
#: tool arguments, and return a payload dict. Passing the context is what makes
#: board awareness possible ("the position the user is looking at"), and it keeps
#: the context out of the model's hands: the LLM never supplies a game id that the
#: backend has not already authorized.
ToolHandler = Callable[..., dict[str, Any]]


@dataclass
class Tool:
    """A single agent tool: schema + permission + handler + availability."""

    name: str
    description: str
    schema: ToolSchema
    permission: ToolPermission = ToolPermission.ANY
    handler: ToolHandler | None = None
    #: When False, the tool is declared but cannot run: the loop must not offer it
    #: to the model, and calling it raises with the reason.
    available: bool = True
    reason: str | None = None
    #: Free-form tags for the catalogue and the evaluator.
    tags: tuple[str, ...] = ()

    def is_usable(self, context: AgentContext) -> tuple[bool, str | None]:
        """Can this tool run for this context? Returns ``(ok, reason)``.

        The *context* checks run before the *deployment* check on purpose. When a
        tool is both unavailable here and starved of context, the reason the user can
        act on is the missing context — "no active player in this conversation" tells
        them what to do, while "this deployment has no player layer" does not. The
        deployment gap surfaces once the context is supplied.
        """
        if self.permission is ToolPermission.GAME_CONTEXT and not context.has_game():
            return False, "no active game in this conversation"
        if self.permission is ToolPermission.LIVE_CONTEXT and not context.has_live_game():
            return False, "no active live game in this conversation"
        if self.permission is ToolPermission.PLAYER_CONTEXT and not context.player_id:
            return False, "no active player in this conversation"
        if not self.available or self.handler is None:
            return False, self.reason or "not implemented yet"
        # Fair play (spec §20/§54): during a competitive live game no engine
        # analysis may be produced at all. This is enforced here, in the backend,
        # for *every* engine-backed tool — not by asking the model to be
        # well-behaved and not by trusting the prompt.
        if context.live_analysis_forbidden and self.schema.uses_engine:
            return False, (
                "engine analysis is disabled during a competitive live game; "
                "use the in-game coach, which offers non-engine guidance"
            )
        return True, None


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate ``arguments`` against a JSON Schema subset; return them cleaned.

    Deliberately a small, explicit subset — objects, strings, integers, numbers,
    booleans, arrays and enums — because that is what these tools use and a
    dependency-free validator we fully understand beats a permissive one we do
    not. Unknown keys are rejected rather than ignored: a typo in a parameter name
    should be a loud failure, not a silent default.
    """
    if not isinstance(arguments, dict):
        raise ToolArgumentError("Tool arguments must be an object")
    properties: dict[str, Any] = schema.get("properties") or {}
    required: list[str] = list(schema.get("required") or [])

    missing = [name for name in required if name not in arguments]
    if missing:
        raise ToolArgumentError(
            f"Missing required argument(s): {', '.join(sorted(missing))}"
        )
    unknown = [name for name in arguments if name not in properties]
    if unknown:
        raise ToolArgumentError(
            f"Unknown argument(s): {', '.join(sorted(unknown))}. "
            f"Accepted: {', '.join(sorted(properties)) or '(none)'}"
        )

    cleaned: dict[str, Any] = {}
    for name, value in arguments.items():
        spec = properties[name]
        if value is None:
            if name in required:
                raise ToolArgumentError(f"Argument '{name}' may not be null")
            continue
        cleaned[name] = _coerce(name, value, spec)
    return cleaned


def _coerce(name: str, value: Any, spec: dict[str, Any]) -> Any:
    """Type-check and lightly coerce one argument against its spec."""
    expected = spec.get("type")
    if expected == "string":
        if not isinstance(value, str):
            raise ToolArgumentError(f"Argument '{name}' must be a string")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolArgumentError(
                f"Argument '{name}' must be one of {', '.join(map(str, spec['enum']))}"
            )
        if value == "" and spec.get("minLength", 0) > 0:
            raise ToolArgumentError(f"Argument '{name}' may not be empty")
        return value
    if expected == "integer":
        # Booleans are integers in Python; refuse them explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolArgumentError(f"Argument '{name}' must be an integer")
        if "minimum" in spec and value < spec["minimum"]:
            raise ToolArgumentError(f"Argument '{name}' must be >= {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise ToolArgumentError(f"Argument '{name}' must be <= {spec['maximum']}")
        return value
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolArgumentError(f"Argument '{name}' must be a number")
        return float(value)
    if expected == "boolean":
        if not isinstance(value, bool):
            raise ToolArgumentError(f"Argument '{name}' must be a boolean")
        return value
    if expected == "array":
        if not isinstance(value, list):
            raise ToolArgumentError(f"Argument '{name}' must be an array")
        item_spec = spec.get("items") or {}
        return [_coerce(name, item, item_spec) for item in value]
    if expected == "object":
        if not isinstance(value, dict):
            raise ToolArgumentError(f"Argument '{name}' must be an object")
        return value
    return value


@dataclass
class ToolOutcome:
    """The result of one tool call, including why it failed."""

    tool: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    duration_ms: float = 0.0

    @property
    def error_code(self) -> str:
        return str((self.error or {}).get("code", ""))

    @property
    def error_message(self) -> str:
        return str((self.error or {}).get("message", ""))


class Toolbox:
    """The set of tools the agent may call, with permissions enforced."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            from argus.shared.errors import ToolNotFoundError

            raise ToolNotFoundError(f"Unknown tool '{name}'") from None

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def callable_tools(self, context: AgentContext) -> list[Tool]:
        """Tools that may run for this context (availability + permission)."""
        usable: list[Tool] = []
        for tool in self._tools.values():
            ok, _ = tool.is_usable(context)
            if ok:
                usable.append(tool)
        return usable

    def names(self, context: AgentContext | None = None) -> set[str]:
        if context is None:
            return set(self._tools)
        return {tool.name for tool in self.callable_tools(context)}

    def to_tool_specs(self, context: AgentContext | None = None) -> list[dict[str, Any]]:
        """OpenAI-style function specs for the tools usable in this context."""
        tools = (
            self.callable_tools(context)
            if context is not None
            else [tool for tool in self._tools.values() if tool.available and tool.handler]
        )
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.schema.parameters,
                },
            }
            for tool in tools
        ]

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        context: AgentContext,
    ) -> ToolOutcome:
        """Validate, authorize and execute one tool call.

        Failures are returned as outcomes, never raised: a failed tool is
        information the agent needs in order to answer honestly, and the loop
        must keep going rather than abort the turn.
        """
        import time

        started = time.perf_counter()
        try:
            tool = self.get(name)
        except ArgusError as exc:
            return ToolOutcome(tool=name, ok=False, error=exc.to_dict())

        ok, reason = tool.is_usable(context)
        if not ok:
            error = ToolNotPermittedError(
                f"Tool '{name}' cannot run here: {reason}"
            ).to_dict()
            return ToolOutcome(tool=name, ok=False, error=error)

        try:
            cleaned = validate_arguments(tool.schema.parameters, arguments or {})
        except ArgusError as exc:
            return ToolOutcome(tool=name, ok=False, error=exc.to_dict())

        # Authorization: a handler may only touch a game the caller owns.
        target_game = str(cleaned.get("game_id") or context.active_game_id or "")
        if target_game and not context.is_authorized(target_game):
            error = ToolNotPermittedError(
                "That game does not belong to this caller; Caissa will not read it."
            ).to_dict()
            return ToolOutcome(tool=name, ok=False, error=error)
        # A live game is a separate namespace with its own allow-list, so it is
        # authorized separately and always restricted.
        target_live = str(cleaned.get("live_game_id") or context.active_live_game_id or "")
        if cleaned.get("live_game_id") is not None and not context.is_live_authorized(
            target_live
        ):
            error = ToolNotPermittedError(
                "That live game does not belong to this caller; Caissa will not read it."
            ).to_dict()
            return ToolOutcome(tool=name, ok=False, error=error)

        try:
            data = tool.handler(context, **cleaned) if tool.handler else {}
        except ArgusError as exc:
            return ToolOutcome(
                tool=name,
                ok=False,
                error=exc.to_dict(),
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 — a tool bug must not kill the turn
            from argus.shared.logging import get_logger

            get_logger(__name__).exception("Tool '%s' raised unexpectedly", name)
            error = ArgusError(f"Tool '{name}' failed: {exc}").to_dict()
            return ToolOutcome(
                tool=name,
                ok=False,
                error=error,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        return ToolOutcome(
            tool=name,
            ok=True,
            data=dict(data or {}),
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    def catalogue(self, context: AgentContext | None = None) -> list[dict[str, Any]]:
        """Machine-readable tool catalogue for the API and the evaluator."""
        entries = []
        for tool in self._tools.values():
            usable, reason = (
                tool.is_usable(context) if context is not None else (tool.available, tool.reason)
            )
            entries.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "permission": tool.permission.value,
                    "available": bool(usable),
                    "reason": reason,
                    "outputs": list(tool.schema.outputs),
                    "uses_engine": tool.schema.uses_engine,
                    "tags": list(tool.tags),
                    "parameters": tool.schema.parameters,
                }
            )
        return sorted(entries, key=lambda entry: entry["name"])


__all__ = [
    "Tool",
    "ToolArgumentError",
    "ToolHandler",
    "ToolNotPermittedError",
    "ToolOutcome",
    "ToolPermission",
    "ToolSchema",
    "Toolbox",
    "validate_arguments",
]
