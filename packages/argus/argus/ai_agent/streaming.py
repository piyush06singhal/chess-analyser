"""Streaming preparation: the agent's lifecycle as an event stream (spec §27).

An agent turn is naturally staged — plan, tool calls, evidence, answer, validation —
and those stages are already observable: the trace records every one of them. The
problem a user feels is that all of it arrives at once, after the slowest tool
returns. This module turns the observation into an ordered event stream so a
transport can forward the stages *while the turn is still running*: "consulting
Stockfish…", "3 tools", "verifying claims…".

What is honestly streamable today
---------------------------------
**Lifecycle events, because the loop emits them where they happen.** The plan is
known before any tool runs; each tool call completes on its own; validation runs
before the answer is returned. Those are emitted live.

**Not token-level deltas of the final prose.** That needs the provider client to
expose a streaming completion API, and the configured providers (including the
deterministic ``echo`` client used in tests) do not. ``stream_turn`` therefore ends
with a single ``answer`` event carrying the complete message. The
``answer_delta`` kind is *reserved* for the day a provider streams tokens, so the
event vocabulary a client codes against will not change when it arrives.

The bridge
----------
The core is synchronous, a response body is asynchronous, and the transport runs the
blocking turn on a worker thread anyway (``asyncio.to_thread`` in the API). So
:func:`stream_turn` runs the turn on a thread of its own and yields events from a
queue as they arrive. That is a real stream, not a buffered replay, and it needs no
async dependency inside the agent package.

A subscriber must never be able to break an answer, so the sink is called inside a
guard: a raising sink is logged and the turn continues (see
:meth:`argus.ai_agent.core.loop.CoachingAgent._emit`).
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from argus.shared.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a runtime cycle
    from argus.ai_agent.core.context import AgentContext
    from argus.ai_agent.core.loop import CoachingAgent
    from argus.ai_agent.memory.conversation import ConversationMemory

logger = get_logger(__name__)


class TurnEventKind(str, Enum):
    """The ordered vocabulary of a turn. Stable: add, do not rename."""

    PLAN = "plan"
    TOOL_CALL = "tool_call"
    MISSING = "missing"
    EVIDENCE = "evidence"
    ANSWER = "answer"
    #: Reserved for token-level streaming; never emitted by the current loop.
    ANSWER_DELTA = "answer_delta"
    VALIDATION = "validation"
    DONE = "done"
    #: Terminal frame: the complete, serialised answer (mirrors the non-streaming
    #: response), so a consumer can treat the stream and the POST identically.
    RESULT = "result"
    #: Terminal frame for a failed turn: the error, in the API's own shape.
    ERROR = "error"


@dataclass
class TurnEvent:
    """One frame of the stream."""

    kind: TurnEventKind
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """A flat, JSON-ready frame: ``{"event": "...", ...fields}``."""
        return {"event": self.kind.value, **self.data}


def turn_event_stream(
    agent: CoachingAgent,
    question: str,
    *,
    context: AgentContext | None = None,
    memory: ConversationMemory | None = None,
    include_answer: bool = True,
) -> Iterator[TurnEvent]:
    """Yield a turn's events as they happen, ending with a terminal frame.

    Exactly one terminal frame is produced: :attr:`TurnEventKind.RESULT` on success,
    :attr:`TurnEventKind.ERROR` on failure. After it, the generator stops. A
    consumer can therefore consume frames until the terminal one and still get the
    same answer the non-streaming endpoint would have returned.
    """
    frames: queue.Queue[TurnEvent | None] = queue.Queue()

    def sink(event: dict[str, Any]) -> None:
        payload = dict(event)
        kind = TurnEventKind(str(payload.pop("event")))
        frames.put(TurnEvent(kind=kind, data=payload))

    def run() -> None:
        try:
            answer = agent.ask(question, context=context, memory=memory, on_event=sink)
        except Exception as exc:  # noqa: BLE001 — a turn failure is a frame, not a crash
            logger.exception("Agent turn failed while streaming")
            frames.put(
                TurnEvent(
                    TurnEventKind.ERROR,
                    {"code": getattr(exc, "code", "agent_turn_failed"), "message": str(exc)},
                )
            )
        else:
            if include_answer:
                frames.put(
                    TurnEvent(
                        TurnEventKind.RESULT,
                        {"answer": answer.model_dump(mode="json")},
                    )
                )
        finally:
            frames.put(None)

    threading.Thread(target=run, name="argus-agent-turn", daemon=True).start()

    while True:
        frame = frames.get()
        if frame is None:
            return
        yield frame


__all__ = ["TurnEvent", "TurnEventKind", "turn_event_stream"]
