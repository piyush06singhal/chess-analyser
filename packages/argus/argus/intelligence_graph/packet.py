"""The LLM context packet and hallucination control (§44, §46).

Two problems are solved here.

**§44 — what the model is given.** The agent must receive *only* relevant,
structured evidence, ranked and bounded — never the database. :class:`CoachEvidencePacket`
is that vessel: named sections, each item carrying its provenance, its sample size
and its rank, plus a ``limitations`` section that states what Caissa does not know.
:meth:`CoachEvidencePacket.to_prompt_payload` bounds every section, so the packet
cannot grow without limit as a player's history grows.

**§46 — what the model is allowed to say.** A model can still write a number it was
never given, or attribute a claim to an engine that never ran. Before an answer is
returned, its claims are checked against the packet: a numerical claim needs an
item with a number, a historical claim needs a game-backed item, an engine claim
needs an engine-reliability item, a prediction claim needs a model version, and a
knowledge claim needs a sourced concept. A claim that fails is *removed* or
replaced with an explicit "evidence unavailable" statement — never returned as if
it were supported.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from argus.intelligence_graph.evidence import EvidenceReference
from argus.intelligence_graph.ranking import SourceReliability

PACKET_VERSION = "13.0"
DEFAULT_ITEMS_PER_SECTION = 12


class PacketSection(str, Enum):
    """The named sections of a coach evidence packet (§44)."""

    USER_CONTEXT = "user_context"
    CURRENT_POSITION = "current_position"
    RELEVANT_GAMES = "relevant_games"
    RELEVANT_PATTERNS = "relevant_patterns"
    TRAINING_HISTORY = "training_history"
    OPPONENT_DATA = "opponent_data"
    KNOWLEDGE = "knowledge"
    ENGINE_RESULTS = "engine_results"
    PREDICTION_RESULTS = "prediction_results"


class PacketItem(BaseModel):
    """One piece of evidence in the packet, with everything needed to trust it."""

    section: PacketSection
    label: str
    statement: str = ""
    node: str | None = None
    reliability: SourceReliability = SourceReliability.ARGUS_MEASURED
    sample_size: int | None = None
    rank_score: float | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    href: str | None = None
    value: float | None = None
    unit: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "section": self.section.value,
            "label": self.label,
            "statement": self.statement,
            "node": self.node,
            "reliability": self.reliability.value,
            "sample_size": self.sample_size,
            "rank_score": self.rank_score,
            "value": self.value,
            "unit": self.unit,
            "href": self.href,
            "evidence_count": len(self.evidence),
        }


class CoachEvidencePacket(BaseModel):
    """A bounded, ranked, provenance-carrying brief for one coach answer."""

    question: str = ""
    user_context: dict[str, Any] = Field(default_factory=dict)
    items: list[PacketItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    graph_versions: dict[str, Any] = Field(default_factory=dict)
    methodology_version: str = PACKET_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def add(self, item: PacketItem) -> None:
        self.items.append(item)

    def section(self, name: PacketSection) -> list[PacketItem]:
        return [item for item in self.items if item.section is name]

    def counts(self) -> dict[str, int]:
        return {name.value: len(self.section(name)) for name in PacketSection}

    def to_prompt_payload(
        self, *, max_items_per_section: int = DEFAULT_ITEMS_PER_SECTION
    ) -> dict[str, Any]:
        """The bounded structure handed to the model. Evidence rows never travel."""
        sections: dict[str, Any] = {}
        for name in PacketSection:
            rows = self.section(name)
            rows.sort(key=lambda item: (-(item.rank_score or 0.0), item.label))
            sections[name.value] = {
                "count": len(rows),
                "items": [
                    {
                        "label": item.label,
                        "statement": item.statement,
                        "sample_size": item.sample_size,
                        "reliability": item.reliability.value,
                        "has_evidence": bool(item.evidence),
                        "href": item.href,
                    }
                    for item in rows[: max(0, max_items_per_section)]
                ],
            }
        return {
            "question": self.question,
            "user_context": dict(self.user_context),
            "sections": sections,
            "limitations": list(self.limitations),
            "graph_versions": dict(self.graph_versions),
            "methodology_version": self.methodology_version,
        }


# --- hallucination control ----------------------------------------------------


class ClaimKind(str, Enum):
    """§46 claim categories, each with its own evidence requirement."""

    NUMERICAL = "numerical"
    HISTORICAL = "historical"
    ENGINE = "engine"
    PREDICTION = "prediction"
    KNOWLEDGE = "knowledge"


_REQUIRED_RELIABILITY: dict[ClaimKind, SourceReliability] = {
    ClaimKind.ENGINE: SourceReliability.ENGINE,
    ClaimKind.PREDICTION: SourceReliability.PREDICTION,
}


class Claim(BaseModel):
    """One statement in an answer that must be traceable to the packet."""

    text: str
    kind: ClaimKind
    #: The packet items (their ``node`` or ``label``) the claim cites.
    cites: list[str] = Field(default_factory=list)


class ClaimValidation(BaseModel):
    claim: str
    kind: ClaimKind
    supported: bool
    reason: str
    replacement: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "kind": self.kind.value,
            "supported": self.supported,
            "reason": self.reason,
            "replacement": self.replacement,
        }


class AnswerValidation(BaseModel):
    """The result of checking an answer's claims against its packet."""

    supported: list[ClaimValidation] = Field(default_factory=list)
    unsupported: list[ClaimValidation] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unsupported

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "supported": [item.to_payload() for item in self.supported],
            "unsupported": [item.to_payload() for item in self.unsupported],
            "rule": (
                "Every numerical claim has evidence, every historical claim has source "
                "data, every engine claim has engine output, every prediction has model "
                "metadata, and every knowledge claim has a source."
            ),
        }


def _find_item(packet: CoachEvidencePacket, citation: str) -> PacketItem | None:
    for item in packet.items:
        if item.node == citation or item.label == citation:
            return item
    return None


def validate_claim(packet: CoachEvidencePacket, claim: Claim) -> ClaimValidation:
    """Check one claim against the packet; never raises."""
    resolved = [item for item in (_find_item(packet, cite) for cite in claim.cites) if item]
    if not resolved:
        return ClaimValidation(
            claim=claim.text,
            kind=claim.kind,
            supported=False,
            reason="The claim cites no evidence in the packet.",
            replacement="Caissa has no stored evidence for that statement.",
        )

    if claim.kind is ClaimKind.NUMERICAL:
        if not any(item.value is not None for item in resolved):
            return ClaimValidation(
                claim=claim.text,
                kind=claim.kind,
                supported=False,
                reason="A numerical claim must rest on an item carrying a stored value.",
                replacement="The exact number is not in Caissa's stored evidence.",
            )
    elif claim.kind is ClaimKind.HISTORICAL:
        if not any(item.evidence for item in resolved):
            return ClaimValidation(
                claim=claim.text,
                kind=claim.kind,
                supported=False,
                reason="A historical claim must rest on a stored game/position reference.",
                replacement="Caissa has no stored game supporting that history.",
            )
    elif claim.kind is ClaimKind.ENGINE:
        required = _REQUIRED_RELIABILITY[ClaimKind.ENGINE]
        if not any(item.reliability is required for item in resolved):
            return ClaimValidation(
                claim=claim.text,
                kind=claim.kind,
                supported=False,
                reason="An engine claim must rest on stored engine output.",
                replacement="No stored engine output supports that claim.",
            )
    elif claim.kind is ClaimKind.PREDICTION:
        if not any(
            item.reliability is SourceReliability.PREDICTION
            and any(ref.model_version or ref.dataset_version for ref in item.evidence)
            for item in resolved
        ):
            return ClaimValidation(
                claim=claim.text,
                kind=claim.kind,
                supported=False,
                reason="A prediction claim must carry model/dataset metadata.",
                replacement="Caissa has no validated model backing that prediction.",
            )
    elif claim.kind is ClaimKind.KNOWLEDGE:
        if not any(
            any(ref.knowledge_source_id or ref.kind.value == "knowledge_source" for ref in item.evidence)
            for item in resolved
        ):
            return ClaimValidation(
                claim=claim.text,
                kind=claim.kind,
                supported=False,
                reason="A knowledge claim must cite a stored, sourced concept.",
                replacement="Caissa has no stored, sourced explanation for that.",
            )

    return ClaimValidation(
        claim=claim.text,
        kind=claim.kind,
        supported=True,
        reason="Supported by packet evidence.",
    )


def validate_answer(
    packet: CoachEvidencePacket, claims: list[Claim]
) -> AnswerValidation:
    """Split claims into supported and unsupported, keeping the refusals visible."""
    validation = AnswerValidation()
    for claim in claims:
        outcome = validate_claim(packet, claim)
        (validation.supported if outcome.supported else validation.unsupported).append(outcome)
    return validation


def render_packet(packet: CoachEvidencePacket) -> dict[str, Any]:
    """The full packet for the UI's "Why?" panel (§43)."""
    return {
        "question": packet.question,
        "user_context": dict(packet.user_context),
        "items": [item.to_payload() for item in packet.items],
        "counts": packet.counts(),
        "limitations": list(packet.limitations),
        "graph_versions": dict(packet.graph_versions),
        "methodology_version": packet.methodology_version,
        "created_at": packet.created_at.isoformat(),
    }


__all__ = [
    "AnswerValidation",
    "Claim",
    "ClaimKind",
    "ClaimValidation",
    "CoachEvidencePacket",
    "PACKET_VERSION",
    "PacketItem",
    "PacketSection",
    "render_packet",
    "validate_answer",
    "validate_claim",
]
