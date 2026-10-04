"""\"Show me why\": every claim resolved to the evidence that produced it.

Phase 11 §24/§25 ask for a reusable *Show me why* surface and an EvidencePanel
component. The rule behind both is the platform's oldest one: a claim is only as
good as the reference under it, and an unresolvable reference must be shown as a
gap rather than quietly dropped.

This module is deliberately engine-free and storage-free. It takes a claim and a
list of already-stored references, classifies each reference by *what kind of
knowledge it is* (an engine fact, a Caissa-derived feature, an interpretation, or
a prediction), and reports whether the reference can actually be followed —
which is what lets the UI render ``linked`` versus ``unavailable`` honestly.

The four knowledge kinds are the same ones the agent's evidence packet already
uses, so a claim explained here and a claim explained to the agent cannot diverge
in how they are labelled.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

EVIDENCE_METHODOLOGY_VERSION = "11.0"


class KnowledgeKind(str, Enum):
    """Where a piece of evidence came from — the axis that keeps them apart."""

    ENGINE_FACT = "engine_fact"
    """Stockfish measured it (an evaluation, a best move, a mate distance)."""

    ARGUS_FEATURE = "argus_feature"
    """Caissa measured it from the board or the stored analysis (material, phase)."""

    INTERPRETATION = "interpretation"
    """Caissa's reading of measured facts — candidate, never a measurement."""

    PREDICTION = "prediction"
    """A gated model's output; present only when a production model exists."""

    REFUSAL = "refusal"
    """An explicit statement that Caissa cannot answer, with the reason."""


class EvidenceItem(BaseModel):
    """One reference under a claim, classified and (when possible) followable."""

    kind: KnowledgeKind
    label: str
    statement: str
    #: Where the reader lands if they follow it. ``None`` when Caissa cannot open
    #: it — the UI must render that as unavailable, not as an empty link.
    href: str | None = None
    game_id: str | None = None
    ply: int | None = None
    fen: str | None = None
    value: float | None = None
    unit: str | None = None
    certainty: str | None = Field(default=None, description="confirmed | candidate")
    source_system: str | None = None
    followable: bool = False
    unavailable_reason: str | None = None


class EvidencePacket(BaseModel):
    """A claim with everything behind it, and everything Caissa could not find."""

    claim: str
    claim_level: str | None = None
    answer_type: str = Field(
        default="fact",
        description="fact | interpretation | recommendation | prediction | refusal",
    )
    items: list[EvidenceItem] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    certainty: str | None = None
    methodology_version: str = EVIDENCE_METHODOLOGY_VERSION

    @property
    def has_engine_fact(self) -> bool:
        return any(item.kind is KnowledgeKind.ENGINE_FACT for item in self.items)

    @property
    def has_prediction(self) -> bool:
        return any(item.kind is KnowledgeKind.PREDICTION for item in self.items)

    def counts_by_kind(self) -> dict[str, int]:
        counts = {kind.value: 0 for kind in KnowledgeKind}
        for item in self.items:
            counts[item.kind.value] += 1
        return counts

    def to_payload(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "claim_level": self.claim_level,
            "answer_type": self.answer_type,
            "certainty": self.certainty,
            "items": [item.model_dump(mode="json") for item in self.items],
            "counts_by_kind": self.counts_by_kind(),
            "gaps": list(self.gaps),
            "methodology_version": self.methodology_version,
        }


#: Which reference shapes Caissa knows how to resolve, and what kind they are.
#: A reference naming a kind Caissa does not recognise is refused, not guessed.
_KIND_BY_REFERENCE: dict[str, KnowledgeKind] = {
    "engine": KnowledgeKind.ENGINE_FACT,
    "engine_fact": KnowledgeKind.ENGINE_FACT,
    "evaluation": KnowledgeKind.ENGINE_FACT,
    "best_move": KnowledgeKind.ENGINE_FACT,
    "argus_derived_feature": KnowledgeKind.ARGUS_FEATURE,
    "feature": KnowledgeKind.ARGUS_FEATURE,
    "material": KnowledgeKind.ARGUS_FEATURE,
    "phase": KnowledgeKind.ARGUS_FEATURE,
    "interpretation": KnowledgeKind.INTERPRETATION,
    "pattern": KnowledgeKind.INTERPRETATION,
    "prediction": KnowledgeKind.PREDICTION,
    "model": KnowledgeKind.PREDICTION,
    "refusal": KnowledgeKind.REFUSAL,
}


def _classify(kind: str | None) -> KnowledgeKind | None:
    if not kind:
        return None
    return _KIND_BY_REFERENCE.get(str(kind).strip().lower())


def _resolve_link(reference: dict) -> tuple[str | None, str | None]:
    """(href, unavailable_reason) — following the reference into a real page."""
    game_id = reference.get("game_id")
    ply = reference.get("ply")
    if game_id and ply is not None:
        return f"/game/{game_id}?ply={ply}", None
    if game_id:
        return f"/game/{game_id}", None
    if reference.get("fen"):
        return "/lab", None
    if reference.get("player_id"):
        return f"/players/{reference['player_id']}", None
    if reference.get("opponent_id"):
        return "/opponents", None
    return None, "Caissa has no stored location to open for this reference."


def build_reference(
    *,
    kind: str,
    label: str,
    statement: str,
    certainty: str | None = None,
    source_system: str | None = None,
    value: float | None = None,
    unit: str | None = None,
    href: str | None = None,
    game_id: str | None = None,
    ply: int | None = None,
    fen: str | None = None,
    **extra: Any,
) -> EvidenceItem:
    """Turn one stored reference into a classified, followable evidence item.

    Returns a REFUSAL-kind item when the reference names a kind Caissa does not
    recognise, so an unknown reference surfaces as a gap rather than being
    silently dropped or mislabelled.
    """
    classified = _classify(kind)
    if classified is None:
        return EvidenceItem(
            kind=KnowledgeKind.REFUSAL,
            label=label,
            statement=statement,
            certainty=certainty,
            source_system=source_system,
            followable=False,
            unavailable_reason=(
                f"Caissa does not recognise evidence kind '{kind}', so it cannot "
                f"present this reference as a fact."
            ),
        )
    reference = {
        "game_id": game_id,
        "ply": ply,
        "fen": fen,
        **{key: value for key, value in extra.items() if key in ("player_id", "opponent_id")},
    }
    resolved_href = href
    unavailable_reason: str | None = None
    if resolved_href is None and classified is not KnowledgeKind.REFUSAL:
        resolved_href, unavailable_reason = _resolve_link(reference)
    return EvidenceItem(
        kind=classified,
        label=label,
        statement=statement,
        href=resolved_href,
        game_id=game_id,
        ply=ply,
        fen=fen,
        value=value,
        unit=unit,
        certainty=certainty,
        source_system=source_system,
        followable=bool(resolved_href),
        unavailable_reason=unavailable_reason,
    )


def build_evidence_packet(
    *,
    claim: str,
    references: list[dict] | None = None,
    claim_level: str | None = None,
    answer_type: str = "fact",
    certainty: str | None = None,
    extra_gaps: list[str] | None = None,
) -> EvidencePacket:
    """Assemble the packet behind a claim.

    Every reference is classified; every reference that cannot be followed
    contributes a gap. A claim with no references is not hidden — it is returned
    with a stated gap, because "no evidence" is itself an answer.
    """
    items: list[EvidenceItem] = []
    gaps: list[str] = list(extra_gaps or [])
    for reference in references or []:
        if not isinstance(reference, dict):
            continue
        item = build_reference(
            kind=str(reference.get("kind") or ""),
            label=str(reference.get("label") or reference.get("kind") or "evidence"),
            statement=str(reference.get("statement") or reference.get("detail") or ""),
            certainty=reference.get("certainty"),
            source_system=reference.get("source_system") or reference.get("source"),
            value=reference.get("value") if isinstance(reference.get("value"), (int, float)) else None,
            unit=reference.get("unit"),
            href=reference.get("href"),
            game_id=reference.get("game_id"),
            ply=reference.get("ply"),
            fen=reference.get("fen"),
            player_id=reference.get("player_id"),
            opponent_id=reference.get("opponent_id"),
        )
        items.append(item)
        if not item.followable and item.unavailable_reason:
            gaps.append(f"{item.label}: {item.unavailable_reason}")
    if not references:
        gaps.append("This claim has no stored evidence behind it.")
    if answer_type == "prediction" and not any(
        item.kind is KnowledgeKind.PREDICTION for item in items
    ):
        gaps.append(
            "This is labelled a prediction but no production model backs it, so it "
            "is reported as unavailable."
        )
    return EvidencePacket(
        claim=claim,
        claim_level=claim_level,
        answer_type=answer_type,
        items=items,
        gaps=gaps,
        certainty=certainty,
    )


def evidence_method() -> dict[str, Any]:
    """Publish the evidence rules, so a panel can be argued with."""
    return {
        "methodology_version": EVIDENCE_METHODOLOGY_VERSION,
        "kinds": [kind.value for kind in KnowledgeKind],
        "rules": [
            "Engine facts, Caissa-derived features, interpretations and predictions are "
            "always labelled separately; they are never merged into one voice.",
            "A reference Caissa cannot follow is reported as unavailable with its reason.",
            "A claim with no evidence says so; it is never padded with a plausible one.",
            "A prediction is shown only when a production model produced it.",
        ],
    }


__all__ = [
    "EVIDENCE_METHODOLOGY_VERSION",
    "EvidenceItem",
    "EvidencePacket",
    "KnowledgeKind",
    "build_evidence_packet",
    "build_reference",
    "evidence_method",
]
