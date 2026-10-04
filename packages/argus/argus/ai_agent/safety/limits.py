"""Resource limits: the budget a turn must fit inside.

An agent loop is a place where cost can run away quietly. A model that keeps
calling tools, an engine search at depth 40, a prompt that grows every iteration —
each is cheap alone and ruinous in a loop. So every turn runs against an explicit
budget, and the budget is enforced by the loop, not requested in the prompt (spec
§25, §37).

Three kinds of limit, for three different reasons:

* **Iteration and tool-call caps** bound the *loop*. Without them a model that
  keeps "checking one more thing" never terminates.
* **Engine caps** bound *compute*. Depth and MultiPV are hard-clamped at the tool
  boundary; this module counts how many engine calls a turn may make at all.
* **Context caps** bound *tokens*. Evidence and history are rendered into a
  character budget, so the prompt cannot grow without limit across a conversation.

Exceeding a limit is not an error: the loop stops, records ``limit_reached`` in the
trace, and answers with what it has. A partial answer that says what it did not get
to is better than a turn that runs until the request times out.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentLimits:
    """The per-turn budget."""

    #: Model↔tool round trips. Four is generous for grounded question answering and
    #: stops "one more check" loops.
    max_iterations: int = 4
    #: Total tool executions across the whole turn.
    max_tool_calls: int = 10
    #: Engine invocations. Searches are the slowest and dearest thing here.
    max_engine_calls: int = 3
    #: Ceiling for any single engine search, mirrored by the tool clamp.
    max_depth: int = 24
    max_multipv: int = 5

    #: Characters of evidence handed to the model.
    max_evidence_chars: int = 6000
    #: Characters of conversation history handed to the model.
    max_history_chars: int = 4000
    #: Characters streamed back into the conversation as tool results.
    max_tool_result_chars: int = 2500
    #: Ceiling on the answer text returned to the UI.
    max_response_chars: int = 6000

    #: Wall-clock budget for the whole turn, when the caller can enforce it.
    timeout_seconds: float = 90.0


@dataclass
class Budget:
    """A live counter against an :class:`AgentLimits`."""

    limits: AgentLimits = field(default_factory=AgentLimits)
    iterations: int = 0
    tool_calls: int = 0
    engine_calls: int = 0
    reached: list[str] = field(default_factory=list)

    # --- checks --------------------------------------------------------------

    def can_iterate(self) -> bool:
        return self.iterations < self.limits.max_iterations

    def can_call_tool(self, tool_name: str, *, uses_engine: bool = False) -> tuple[bool, str]:
        """Whether a further tool call is within budget."""
        if self.tool_calls >= self.limits.max_tool_calls:
            return False, (
                f"tool-call budget reached ({self.limits.max_tool_calls}); answering "
                f"with the evidence already gathered"
            )
        if uses_engine and self.engine_calls >= self.limits.max_engine_calls:
            return False, (
                f"engine budget reached ({self.limits.max_engine_calls} searches); no "
                f"further engine evaluation this turn"
            )
        return True, ""

    # --- recording -----------------------------------------------------------

    def note_iteration(self) -> None:
        self.iterations += 1

    def note_tool_call(self, *, uses_engine: bool = False) -> None:
        self.tool_calls += 1
        if uses_engine:
            self.engine_calls += 1

    def note_reached(self, reason: str) -> None:
        if reason not in self.reached:
            self.reached.append(reason)

    @property
    def exhausted(self) -> bool:
        return bool(self.reached)

    def to_dict(self) -> dict[str, object]:
        return {
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "engine_calls": self.engine_calls,
            "limits": {
                "max_iterations": self.limits.max_iterations,
                "max_tool_calls": self.limits.max_tool_calls,
                "max_engine_calls": self.limits.max_engine_calls,
                "max_depth": self.limits.max_depth,
                "max_multipv": self.limits.max_multipv,
                "max_evidence_chars": self.limits.max_evidence_chars,
                "max_history_chars": self.limits.max_history_chars,
                "max_response_chars": self.limits.max_response_chars,
                "timeout_seconds": self.limits.timeout_seconds,
            },
            "reached": list(self.reached),
        }


@dataclass
class TurnClock:
    """A monotonic wall-clock budget."""

    timeout_seconds: float

    def __post_init__(self) -> None:
        import time

        self._start = time.monotonic()

    def elapsed(self) -> float:
        import time

        return time.monotonic() - self._start

    def expired(self) -> bool:
        return self.elapsed() > self.timeout_seconds

    def remaining(self) -> float:
        return max(0.0, self.timeout_seconds - self.elapsed())


__all__ = ["AgentLimits", "Budget", "TurnClock"]
