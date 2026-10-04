"""Phase 7 streaming preparation (spec §27).

These tests pin two things the roadmap depends on:

* the event vocabulary is emitted **in order, live**, so a transport can forward
  "consulting Stockfish…" while the turn is still running;
* the stream is *equivalent* to the non-streaming endpoint — the terminal frame
  carries the same answer object the POST returns, so a client can be written once.

The tests drive a real turn (no mocking of the loop) so the ordering assertions are
about the loop's actual emission points, not about a stub.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.ai_agent.core.context import AgentContext  # noqa: E402
from argus.ai_agent.core.loop import CoachingAgent  # noqa: E402
from argus.ai_agent.streaming import TurnEventKind, turn_event_stream  # noqa: E402
from argus.ai_agent.tools import AgentProviders, build_agent_toolbox  # noqa: E402

from tests.conftest import START_FEN  # noqa: E402

GAME_ID = "189d51ba-7404-4e6c-91b2-39119c103132"


class _Provider:
    """A provider callable that records calls and returns a fixed payload."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.payload


def _agent(providers: AgentProviders) -> CoachingAgent:
    return CoachingAgent(build_agent_toolbox(providers))


def test_the_plan_arrives_before_any_tool_and_done_is_last():
    """Ordering is the whole value of the stream: stages arrive as they happen."""
    agent = _agent(AgentProviders())
    frames = list(turn_event_stream(agent, "What is my biggest weakness?"))
    kinds = [frame.kind for frame in frames]

    assert kinds[0] is TurnEventKind.PLAN
    assert kinds[-1] is TurnEventKind.RESULT
    # The plan names what will be attempted, before anything was attempted.
    assert frames[0].data["intents"] == ["player_weakness"]
    # A recorded absence for a tool the plan wanted, streamed as it was discovered.
    assert any(frame.kind is TurnEventKind.MISSING for frame in frames)
    # Validation and DONE precede the terminal result frame.
    assert TurnEventKind.VALIDATION in kinds
    assert kinds.index(TurnEventKind.DONE) < kinds.index(TurnEventKind.RESULT)


def test_tool_calls_are_streamed_with_their_outcomes():
    """Each tool call is its own frame, with success and duration."""
    providers = AgentProviders(move_analysis=_Provider({"ply": 17, "san": "fxg5", "classification": "blunder"}))
    agent = _agent(providers)
    frames = list(
        turn_event_stream(
            agent,
            "Why was this move bad?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
    )
    calls = [frame for frame in frames if frame.kind is TurnEventKind.TOOL_CALL]
    assert calls, "no tool call was streamed"
    assert calls[0].data["tool"] == "get_move_analysis"
    assert calls[0].data["ok"] is True
    assert isinstance(calls[0].data["duration_ms"], float)


def test_the_terminal_frame_carries_the_same_answer_as_the_post():
    """A stream consumer sees exactly what the non-streaming endpoint returns."""
    providers = AgentProviders(
        game_moves=_Provider([{"uci": "e2e4"}, {"uci": "c7c5"}]),
    )
    agent = _agent(providers)
    question = "What opening is this?"
    context = AgentContext(active_game_id=GAME_ID)

    streamed = list(turn_event_stream(agent, question, context=context))[-1]
    assert streamed.kind is TurnEventKind.RESULT
    payload = streamed.data["answer"]

    direct = _agent(providers).ask(question, context=context)
    assert payload["message"] == direct.message
    assert payload["deterministic"] == direct.deterministic
    assert payload["validation"]["passed"] == direct.validation.passed


def test_a_raising_subscriber_never_breaks_the_turn():
    """A streaming consumer is a UI concern; a bad one must not cost the answer."""
    seen: list[str] = []

    def bad_sink(event: dict) -> None:
        seen.append(event["event"])
        raise RuntimeError("the subscriber exploded")

    agent = _agent(AgentProviders())
    answer = agent.ask(
        "What is my biggest weakness?",
        context=AgentContext(),
        on_event=bad_sink,
    )
    # The frames were attempted (so streaming works) and the answer still arrived.
    assert "plan" in seen
    assert answer.message
    assert answer.evidence is not None


def test_the_sink_is_scoped_to_one_turn():
    """A later turn must not leak frames into an earlier sink."""
    first: list[dict] = []
    agent = _agent(AgentProviders())
    agent.ask("What is my biggest weakness?", context=AgentContext(), on_event=first.append)
    count_after_first = len(first)

    agent.ask("Can you predict my win probability?", context=AgentContext())
    assert len(first) == count_after_first


def test_streaming_a_position_question_emits_evidence_and_actions():
    """The frames a UI binds to: evidence count and the navigation actions."""
    providers = AgentProviders(
        move_analysis=_Provider(
            {
                "ply": 17,
                "move_number": 9,
                "mover": "white",
                "san": "fxg5",
                "uci": "f4g5",
                "fen_before": START_FEN,
                "eval_before_cp": 531,
                "eval_after_cp": 23,
                "centipawn_loss": 508,
                "classification": "blunder",
                "best_move_uci": "d1h5",
                "best_move_san": "Qh5+",
                "principal_variation": ["d1h5", "e8d7"],
                # The real move-analysis provider stamps the game it came from, which
                # is what lets the answer offer a navigation back to the position.
                "game_id": GAME_ID,
            }
        )
    )
    agent = _agent(providers)
    frames = list(
        turn_event_stream(
            agent,
            "Why was this move bad?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
    )
    by_kind = {frame.kind: frame for frame in frames}
    assert by_kind[TurnEventKind.EVIDENCE].data["items"] >= 1
    assert "show_position" in by_kind[TurnEventKind.ANSWER].data["actions"]
    assert by_kind[TurnEventKind.RESULT].data["answer"]["deterministic"] is True


def test_include_answer_false_yields_only_lifecycle_frames():
    """A caller may want the progress events without the serialised answer."""
    agent = _agent(AgentProviders())
    frames = list(turn_event_stream(agent, "What is my biggest weakness?", include_answer=False))
    assert TurnEventKind.RESULT not in [frame.kind for frame in frames]
    assert TurnEventKind.DONE in [frame.kind for frame in frames]


def test_a_failed_turn_is_an_error_frame_not_an_exception():
    """The stream contract holds on failure: exactly one terminal frame."""
    class ExplodingAgent(CoachingAgent):
        def _turn(self, question, *, context=None, memory=None, request_id=None):  # type: ignore[override]
            raise RuntimeError("the provider exploded")

    agent = ExplodingAgent(build_agent_toolbox(AgentProviders()))
    frames = list(turn_event_stream(agent, "anything"))
    assert frames[-1].kind is TurnEventKind.ERROR
    assert frames[-1].data["message"]
    assert all(frame.kind is not TurnEventKind.RESULT for frame in frames)


def test_every_event_is_json_ready():
    """Frames must serialise without a custom encoder, because the transport is HTTP."""
    import json

    agent = _agent(AgentProviders())
    for frame in turn_event_stream(agent, "What is my biggest weakness?"):
        json.dumps(frame.to_dict())


@pytest.mark.parametrize("question", ["hi", "", "tell me everything"])
def test_an_unrecognised_question_still_streams_a_complete_turn(question: str):
    """No intent, no tools — the stream still opens with a plan and closes with a result."""
    agent = _agent(AgentProviders())
    frames = list(turn_event_stream(agent, question))
    kinds = [frame.kind for frame in frames]
    assert kinds[0] is TurnEventKind.PLAN
    assert kinds[-1] is TurnEventKind.RESULT
