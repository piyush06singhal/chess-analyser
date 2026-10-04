"""The answer object: claims that are typed, referenced and checkable.

Spec §22 draws four kinds of sentence apart, and blending them is exactly how
chess commentary becomes untrustworthy:

``FACT``
    "Stockfish evaluates the position at +2.1." — an engine output.
``OBSERVATION``
    "Your move changed the evaluation by 5.1 pawns." — computed from stored
    values, no chess judgement involved.
``INTERPRETATION``
    "The move prioritises kingside pressure and overlooks the reply." — Caissa
    reasoning over features; a hypothesis, and marked as one.
``COACHING``
    "Before attacking, check your opponent's forcing moves." — advice, not a
    claim about this position at all.

Each claim is typed, and each carries the evidence references it rests on, so a
validator can check it and a reader can audit it. Answers also carry *actions*:
buttons that perform real application navigation rather than miming it (spec §29).
An action is only ever emitted when the data to back it exists.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from argus.ai_agent.core.context import ResponseMode
from argus.ai_agent.core.evidence import EvidencePacket


class ClaimKind(str, Enum):
    """Sentence-level claim types (spec §22)."""

    FACT = "fact"
    OBSERVATION = "observation"
    INTERPRETATION = "interpretation"
    COACHING = "coaching"

    @property
    def label(self) -> str:
        return _CLAIM_LABELS[self]


_CLAIM_LABELS: dict[ClaimKind, str] = {
    ClaimKind.FACT: "Engine fact",
    ClaimKind.OBSERVATION: "Observation",
    ClaimKind.INTERPRETATION: "Interpretation",
    ClaimKind.COACHING: "Coaching",
}


class Claim(BaseModel):
    """One typed statement, with the evidence references behind it."""

    kind: ClaimKind
    text: str
    #: ``EvidenceItem.ref()`` values this claim depends on.
    evidence_refs: list[str] = Field(default_factory=list)
    #: Set by the validator: was the claim checkable against the packet, and did
    #: it agree? ``None`` means it was not checkable (not that it is wrong).
    verified: bool | None = None
    note: str | None = None

    @property
    def is_disputed(self) -> bool:
        return self.verified is False


class AnswerAction(str, Enum):
    """Real, backed navigation the UI can perform (spec §29)."""

    SHOW_POSITION = "show_position"
    SHOW_BEST_LINE = "show_best_line"
    COMPARE_MOVES = "compare_moves"
    VIEW_CRITICAL_MOMENT = "view_critical_moment"
    VIEW_PLAYER_PATTERN = "view_player_pattern"
    CREATE_PUZZLE = "create_puzzle"
    OPEN_GAME = "open_game"
    OPEN_PLAYER = "open_player"


class AgentAction(BaseModel):
    """A button the UI can render — with the data needed to actually do it."""

    action: AnswerAction
    label: str
    #: Frontend route to navigate to, when the action is a navigation.
    href: str | None = None
    #: Extra parameters (e.g. the ply to focus, the alternative move to compare).
    params: dict[str, Any] = Field(default_factory=dict)
    #: Honest reason when the action cannot be performed yet.
    unavailable_reason: str | None = None

    @property
    def is_available(self) -> bool:
        """True when the UI can actually perform this action.

        Two kinds of action are performable: a **navigation** (an ``href`` to a real
        route) and an **agent-executable request** (the ``params`` needed to run the
        comparison, e.g. the played move and the engine's choice). A
        declared-but-unbuilt action carries an ``unavailable_reason`` and is never
        available, so the UI can show the gap instead of rendering a dead button that
        looks like it works.
        """
        if self.unavailable_reason is not None:
            return False
        return bool(self.href) or bool(self.params)


class ValidationFinding(BaseModel):
    """One high-value claim the validator checked."""

    claim: str
    kind: str
    ok: bool
    detail: str = ""


class ValidationReport(BaseModel):
    """Result of checking an answer against its own evidence (spec §23)."""

    checked: int = 0
    findings: list[ValidationFinding] = Field(default_factory=list)
    #: True when nothing was found to contradict the evidence. Note the
    #: asymmetry: this is *not* a promise that every sentence is true, only that
    #: the high-value, machine-checkable claims agreed with the packet.
    passed: bool = True

    def add(self, claim: str, kind: str, ok: bool, detail: str = "") -> None:
        self.checked += 1
        if not ok:
            self.passed = False
        self.findings.append(
            ValidationFinding(claim=claim, kind=kind, ok=ok, detail=detail)
        )

    @property
    def failures(self) -> list[ValidationFinding]:
        return [finding for finding in self.findings if not finding.ok]

    def summary(self) -> str:
        if not self.checked:
            return "No high-value claim was machine-checkable against the evidence."
        if self.passed:
            return f"{self.checked} high-value claim(s) checked against the evidence; all agreed."
        return (
            f"{self.checked} high-value claim(s) checked; "
            f"{len(self.failures)} disagreed with the evidence."
        )


class AgentAnswer(BaseModel):
    """The complete result of one agent turn."""

    message: str
    mode: ResponseMode = ResponseMode.COACH
    claims: list[Claim] = Field(default_factory=list)
    actions: list[AgentAction] = Field(default_factory=list)
    #: What the answer was allowed to rest on. Returned so the UI can show it.
    evidence: EvidencePacket | None = None
    validation: ValidationReport = Field(default_factory=ValidationReport)
    #: True when a deterministic backend path produced the answer and no LLM was
    #: consulted (spec §37) — cheaper, faster and exact.
    deterministic: bool = False
    #: Set when the LLM produced text: which provider/model/prompt version.
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    #: Plain-language statements about what could not be determined.
    limitations: list[str] = Field(default_factory=list)
    #: Per-turn observability (timings, tool calls, iterations).
    trace: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_dissent(self) -> bool:
        """True when the validator flagged a claim, or evidence was missing."""
        if not self.validation.passed:
            return True
        return bool(self.evidence and self.evidence.missing)

    def headline(self) -> str:
        """A short label describing how the answer was produced."""
        if self.deterministic:
            return "answered from stored Caissa data (no LLM call)"
        return f"answered by {self.provider or 'llm'}{f'/{self.model}' if self.model else ''}"

    def claims_of(self, kind: ClaimKind) -> list[Claim]:
        return [claim for claim in self.claims if claim.kind is kind]


__all__ = [
    "AgentAction",
    "AgentAnswer",
    "AnswerAction",
    "Claim",
    "ClaimKind",
    "ValidationFinding",
    "ValidationReport",
]
