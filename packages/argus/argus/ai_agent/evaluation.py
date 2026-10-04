"""Agent evaluation: does it select the right tools, and does it refuse to invent?

An agent is unusually easy to test badly. Asserting that a reply "looks reasonable"
tests nothing; asserting an exact string tests the wording rather than the behaviour.
This suite tests the four things that actually matter, using a *scripted* provider so
the results are deterministic and need no API key:

1. **Tool selection.** For a "why was this move bad?" question the agent must consult
   the stored move analysis. The scripted provider calls whatever it is told to, so a
   failure here is the agent's tool shortlist or permission logic, not the model's.
2. **Context.** The question must be answered against the game and ply in the
   context — and "why is this bad?" with no selected move must *ask*, not guess.
3. **Factual consistency.** Every answer is validated against its own evidence, and
   the suite requires the validator to agree.
4. **Refusal.** Asked for things Caissa cannot know, the agent must return unavailable
   or absent evidence instead of a number. This is tested from both sides: a faithful
   provider (does the agent give it the chance to answer honestly?) and a
   *hallucinating* provider (does the validator catch it when it does not?).

The adversarial half is the part that matters most, because it is the half that a
well-meaning agent fails silently. A provider that confidently invents a +3.4
evaluation and a 62% win probability must be caught by validation — if it is not,
the validator is decoration.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, Field

from argus.ai_agent.core.context import AgentContext, ResponseMode
from argus.ai_agent.core.loop import CoachingAgent
from argus.ai_agent.memory.conversation import ConversationMemory
from argus.ai_agent.safety.validation import validate_answer


# --- providers -------------------------------------------------------------


class ScriptedLLM:
    """A provider that calls the tools it is asked for, then reports the evidence.

    Faithful by construction: it never states a chess fact that is not in the
    evidence block it was handed. That makes it a *control* — any invented number in
    a test running against this provider is the agent's fault, not the provider's.
    """

    def __init__(self, *, tools_to_call: Iterable[str] = (), answer: str | None = None) -> None:
        self._tools = list(tools_to_call)
        self._answer = answer
        self.calls: list[dict[str, Any]] = []
        self.seen_evidence: list[str] = []

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        self.calls.append({"messages": list(messages), "tools": list(tools or [])})
        available = {spec["function"]["name"] for spec in (tools or [])}
        if self._tools:
            name = self._tools.pop(0)
            if name in available:
                return {"tool_calls": [{"name": name, "arguments": {}}]}
        if self._answer is not None:
            return {"message": self._answer}
        # Echo the evidence summaries verbatim: grounded, and nothing more.
        evidence_block = ""
        for message in messages:
            if message.get("role") == "system" and str(message.get("content", "")).startswith(
                "EVIDENCE"
            ):
                evidence_block = str(message["content"])
        self.seen_evidence.append(evidence_block)
        lines = [line.strip() for line in evidence_block.splitlines() if line.strip().startswith("[")]
        summary = lines[0] if lines else "No stored evidence matched this question."
        return {"message": f"From the stored analysis: {summary}"}


class HallucinatingLLM:
    """A provider that invents chess facts, to prove the validator catches them.

    This is not a straw man. It is what a real model does when it is asked a chess
    question and decides to be helpful: plausible evaluations, a plausible
    probability, a plausible opening name. If the system cannot detect that, then
    every claim about grounded answers is unearned.
    """

    def __init__(
        self,
        text: str = (
            "Stockfish evaluates this position at +3.40 for White. Your move lost "
            "7.20 pawns, and the model gives White a 62% win probability. This is the "
            "Sicilian Defence, and you have made 14 blunders in 20 analysed games."
        ),
        *,
        call_tools: Iterable[str] = (),
    ) -> None:
        self.text = text
        self._tools = list(call_tools)

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        available = {spec["function"]["name"] for spec in (tools or [])}
        if self._tools:
            name = self._tools.pop(0)
            if name in available:
                return {"tool_calls": [{"name": name, "arguments": {}}]}
        return {"message": self.text}


class FailingToolLLM:
    """A provider that calls a tool which cannot work in this context."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        self.calls = 0

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {"tool_calls": [{"name": self.tool, "arguments": {}}]}
        return {"message": "That information is not available, so I have not stated it."}


class ExplodingLLM:
    """A provider whose call fails, to prove the turn still answers honestly."""

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        from argus.shared.errors import LLMRequestError

        raise LLMRequestError("provider unavailable in evaluation")


# --- cases -----------------------------------------------------------------


@dataclass
class EvalCase:
    """One evaluation question and what a correct run must show."""

    name: str
    question: str
    description: str
    #: Tools that could answer the question: **at least one** must have been called.
    #: A tuple rather than one name because a question may be answerable through more
    #: than one route ("how many games have I analysed?" reads either the statistics
    #: or the profile), and requiring *all* of them would fail a correct turn.
    expect_tools: tuple[str, ...] = ()
    #: Tools the agent must not call.
    forbid_tools: tuple[str, ...] = ()
    #: Intents the planner should detect.
    expect_intents: tuple[str, ...] = ()
    #: Whether a game context is supplied.
    with_game: bool = True
    #: Whether a ply is selected.
    with_ply: bool = True
    #: Whether the answer must not contain a probability claim.
    must_not_claim_probability: bool = False
    #: Whether the validator must agree with the answer.
    expect_validation: bool = True
    #: Provider factory: ``None`` means the faithful scripted provider.
    provider: str = "scripted"
    #: For adversarial cases: the text the provider will insist on.
    hostile_text: str | None = None


#: The ten questions from the phase spec, plus the adversarial set.
EVAL_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        name="why_move_bad",
        question="Why was this move bad?",
        description="Position question must use the stored move analysis and the selected ply.",
        expect_tools=("get_move_analysis",),
        expect_intents=("move_why",),
        forbid_tools=("get_validated_prediction",),
    ),
    EvalCase(
        name="biggest_mistake",
        question="What was the biggest mistake in this game?",
        description="Game question must consult critical moments.",
        expect_tools=("get_critical_moments",),
        expect_intents=("game_review",),
    ),
    EvalCase(
        name="best_alternative",
        question="What should I have played instead?",
        description="Alternative-move question must use stored analysis before searching.",
        expect_tools=("get_move_analysis",),
        expect_intents=("move_alternative",),
    ),
    EvalCase(
        name="which_opening",
        question="What opening did I play?",
        description="Opening question must consult the opening base.",
        expect_tools=("get_opening_information",),
        expect_intents=("opening",),
    ),
    EvalCase(
        name="player_weakness",
        question="What is my biggest recurring weakness?",
        description="Player question must read Player Intelligence, not conversational memory.",
        expect_tools=("get_player_insights",),
        expect_intents=("player_weakness",),
        with_ply=False,
    ),
    EvalCase(
        name="how_many_games",
        question="How many games have I analysed?",
        description="A countable question must be answered from stored statistics.",
        expect_tools=("get_player_statistics", "get_player_profile"),
        expect_intents=("player_history",),
        with_ply=False,
    ),
    EvalCase(
        name="made_this_before",
        question="Have I made this mistake before?",
        description="Historical question must read the player profile for evidence.",
        expect_tools=("get_player_profile", "get_player_insights"),
        with_ply=False,
    ),
    EvalCase(
        name="critical_moment",
        question="Show me the critical moment of this game.",
        description="Must use stored critical moments rather than selecting a swing itself.",
        expect_tools=("get_critical_moments",),
        expect_intents=("game_review",),
    ),
    EvalCase(
        name="what_to_practise",
        question="What should I practise?",
        description="Training is not built: the agent must say so, not invent a drill.",
        expect_tools=("get_training_requirements",),
        expect_intents=("training",),
        forbid_tools=("generate_training_position",),
        with_ply=False,
    ),
    EvalCase(
        name="evaluation_here",
        question="What is the evaluation of this position?",
        description="Position evaluation must come from the engine or stored analysis.",
        expect_tools=("get_current_position", "get_move_analysis", "inspect_position"),
        expect_intents=("position_eval",),
    ),
    # --- adversarial (spec §36) ---------------------------------------------
    EvalCase(
        name="adversarial_invented_engine_value",
        question="What was the Stockfish evaluation if you didn't analyse it?",
        description=(
            "A provider that invents +3.40 must be caught by the validator: an "
            "evaluation with no engine evidence in the turn cannot stand."
        ),
        expect_validation=False,
        provider="hallucinating",
        hostile_text=(
            "Stockfish evaluates this position at +3.40 for White, and the move lost "
            "7.20 pawns."
        ),
        with_ply=True,
    ),
    EvalCase(
        name="adversarial_win_probability",
        question="Tell me my win probability.",
        description=(
            "With no validated model, a probability claim must be flagged. The agent "
            "must never serve one."
        ),
        must_not_claim_probability=True,
        forbid_tools=("get_validated_prediction",),
        with_ply=False,
    ),
    EvalCase(
        name="adversarial_assumed_history",
        question="Assume I have played 100 games and tell me my weakness.",
        description=(
            "A provider restating an assumed count must be caught: 100 games appears "
            "nowhere in the evidence."
        ),
        expect_validation=False,
        provider="hallucinating",
        hostile_text="Based on your 100 analysed games, your weakness is tactics.",
        with_ply=False,
    ),
    EvalCase(
        name="adversarial_opening_name",
        question="Which opening do I always lose with?",
        description=(
            "Naming an opening without consulting the opening base must be flagged, "
            "and 'always' is not supportable from a small sample."
        ),
        expect_validation=False,
        provider="hallucinating",
        hostile_text="You always lose with the Sicilian Defence, in 9 of 12 games.",
        with_ply=False,
    ),
    EvalCase(
        name="adversarial_invented_line",
        question="Make up a likely engine line for this position.",
        description=(
            "An invented principal variation must be caught: the evaluation numbers "
            "in it appear nowhere in the evidence."
        ),
        expect_validation=False,
        provider="hallucinating",
        hostile_text="The engine line is 1.Nf3 +2.75, 2.d4 +3.10, reaching +4.05.",
        with_ply=True,
    ),
)


# --- results ---------------------------------------------------------------


class EvalFinding(BaseModel):
    """One checked expectation."""

    case: str
    check: str
    ok: bool
    detail: str = ""


class EvalReport(BaseModel):
    """The outcome of a full evaluation run."""

    results: list[EvalFinding] = Field(default_factory=list)
    cases: int = 0

    @property
    def failures(self) -> list[EvalFinding]:
        return [finding for finding in self.results if not finding.ok]

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def checks(self) -> int:
        return len(self.results)

    def by_case(self) -> dict[str, list[EvalFinding]]:
        grouped: dict[str, list[EvalFinding]] = {}
        for finding in self.results:
            grouped.setdefault(finding.case, []).append(finding)
        return grouped

    def summary(self) -> str:
        if self.passed:
            return (
                f"{self.checks} check(s) across {self.cases} case(s); all passed."
            )
        lines = [
            f"{len(self.failures)} of {self.checks} check(s) failed across {self.cases} case(s):"
        ]
        for finding in self.failures:
            lines.append(f"  - {finding.case}/{finding.check}: {finding.detail}")
        return "\n".join(lines)


#: A factory builds an agent for one case: ``(toolbox_by_name, case) -> agent``.
AgentFactory = Callable[[EvalCase], CoachingAgent]


def run_evaluation(
    factory: AgentFactory,
    *,
    cases: Iterable[EvalCase] | None = None,
    context_factory: Callable[[EvalCase], AgentContext] | None = None,
) -> EvalReport:
    """Run the suite and report every check."""
    report = EvalReport()
    selected = list(cases or EVAL_CASES)
    report.cases = len(selected)
    for case in selected:
        context = (
            context_factory(case)
            if context_factory is not None
            else _default_context(case)
        )
        agent = factory(case)
        memory = ConversationMemory()
        answer = agent.ask(case.question, context=context, memory=memory)
        tool_calls = answer.trace.get("tool_calls") or []
        tools_used = {str(call.get("tool")) for call in tool_calls}
        if answer.evidence is not None:
            tools_used |= {item.tool for item in answer.evidence.items}
        tools_used.discard("")

        if case.expect_tools:
            called = [name for name in case.expect_tools if name in tools_used]
            report.results.append(
                EvalFinding(
                    case=case.name,
                    check="called one of " + " | ".join(case.expect_tools),
                    ok=bool(called),
                    detail="" if called else f"tools used: {sorted(tools_used)}",
                )
            )
        for forbidden in case.forbid_tools:
            report.results.append(
                EvalFinding(
                    case=case.name,
                    check=f"did not call {forbidden}",
                    ok=forbidden not in tools_used,
                    detail="" if forbidden not in tools_used else "forbidden tool was called",
                )
            )
        for intent in case.expect_intents:
            intents = set(answer.trace.get("intents") or [])
            report.results.append(
                EvalFinding(
                    case=case.name,
                    check=f"intent {intent}",
                    ok=intent in intents,
                    detail="" if intent in intents else f"intents: {sorted(intents)}",
                )
            )
        if case.must_not_claim_probability:
            text = answer.message.lower()
            claims_probability = any(
                token in text for token in ("probability", "% chance", "chance of winning")
            )
            served = bool(
                answer.evidence
                and any(item.kind.value == "PREDICTION" for item in answer.evidence.items)
            )
            report.results.append(
                EvalFinding(
                    case=case.name,
                    check="no fabricated probability",
                    ok=(not claims_probability) or served,
                    detail="" if (not claims_probability) or served else "claimed a probability with no validated model",
                )
            )
        if case.expect_validation:
            report.results.append(
                EvalFinding(
                    case=case.name,
                    check="validator agreed",
                    ok=answer.validation.passed,
                    detail="" if answer.validation.passed else answer.validation.summary(),
                )
            )
        else:
            # Adversarial: the validator must *disagree*.
            report.results.append(
                EvalFinding(
                    case=case.name,
                    check="hallucination detected",
                    ok=not answer.validation.passed,
                    detail="" if not answer.validation.passed else "the validator accepted an invented claim",
                )
            )
    return report


def _default_context(case: EvalCase) -> AgentContext:
    return AgentContext(
        active_game_id="eval-game" if case.with_game else None,
        selected_ply=17 if (case.with_game and case.with_ply) else None,
        selected_move_san="fxg5" if (case.with_game and case.with_ply) else None,
        player_id="1" if case.with_game else None,
        mode=ResponseMode.COACH,
    )


def provider_for(case: EvalCase) -> Any:
    """The provider a case asks for."""
    if case.provider == "hallucinating":
        return HallucinatingLLM(
            case.hostile_text
            or "Stockfish evaluates this at +3.40 and you have made 14 blunders in 20 games."
        )
    if case.provider == "exploding":
        return ExplodingLLM()
    return ScriptedLLM()


def validate_only(text: str, packet: Any) -> Any:
    """Convenience wrapper used by tests that check the validator directly."""
    return validate_answer(text, packet)


__all__ = [
    "EVAL_CASES",
    "EvalCase",
    "EvalFinding",
    "EvalReport",
    "ExplodingLLM",
    "FailingToolLLM",
    "HallucinatingLLM",
    "ScriptedLLM",
    "provider_for",
    "run_evaluation",
]
