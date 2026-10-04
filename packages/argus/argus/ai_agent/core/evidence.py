"""The evidence packet: everything an agent answer is allowed to rest on.

A language model is a plausible-text generator. Left alone it will produce a
confident sentence about a move it has never seen. The only structural defence is
to make every chess fact arrive in one auditable object, and to require the answer
to be written *from* that object:

    tool call → EvidenceItem → EvidencePacket → prompt → answer → validation

Three properties matter:

**Provenance is not optional.** Every item carries the provenance vocabulary the
rest of Caissa already uses (:class:`argus.intelligence.base.EvidenceSource`:
``engine_fact`` / ``argus_derived_feature`` / ``argus_interpretation``) plus a
``certainty``. An agent may say "Stockfish evaluates this at +2.1" only when an
item of kind :attr:`EvidenceKind.ENGINE` says so — and the validator checks it
against the packet, not against the model's opinion.

**Absence is recorded, not implied.** When a tool is unavailable, fails, or
returns nothing, the packet gains a ``missing`` entry naming what could not be
retrieved and why. The response layer can then say "Caissa has no stored analysis
for that move" instead of inventing one, and the omission is visible in the trace
rather than silently absent.

**The packet is a budget, not a dump.** ``as_prompt_block`` renders a bounded,
compacted view. Putting a database into a prompt is how context windows and
invoices explode, and it makes the answer *less* grounded, not more.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from argus.intelligence.base import Certainty, EvidenceSource


class EvidenceKind(str, Enum):
    """What sort of thing a piece of evidence is — the agent's own vocabulary."""

    ENGINE = "ENGINE"
    POSITION = "POSITION"
    GAME = "GAME"
    GAME_ANALYSIS = "GAME_ANALYSIS"
    MOVE_ANALYSIS = "MOVE_ANALYSIS"
    CRITICAL_MOMENT = "CRITICAL_MOMENT"
    PLAYER_PROFILE = "PLAYER_PROFILE"
    PLAYER_INSIGHT = "PLAYER_INSIGHT"
    PLAYER_EVIDENCE = "PLAYER_EVIDENCE"
    OPENING = "OPENING"
    PREDICTION = "PREDICTION"
    KNOWLEDGE = "KNOWLEDGE"
    TRAINING = "TRAINING"


class EvidenceItem(BaseModel):
    """One retrieved fact, with where it came from and how strong it is."""

    kind: EvidenceKind
    tool: str
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE
    certainty: Certainty = Certainty.CONFIRMED
    #: Short machine-readable summary used in the prompt block and the UI.
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    #: Which game/ply the item is about, when it is about a position.
    game_id: str | None = None
    ply: int | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def ref(self) -> str:
        """A stable human-readable reference, e.g. ``game:71b2…@ply=17``."""
        if self.game_id and self.ply is not None:
            return f"game:{self.game_id}@ply={self.ply}"
        if self.game_id:
            return f"game:{self.game_id}"
        return f"{self.kind.value.lower()}:{self.tool}"


class MissingEvidence(BaseModel):
    """Something the agent wanted and could not get — recorded, never faked."""

    tool: str
    reason: str
    kind: EvidenceKind | None = None


class EvidencePacket(BaseModel):
    """The complete, bounded basis for one answer."""

    question: str
    items: list[EvidenceItem] = Field(default_factory=list)
    missing: list[MissingEvidence] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    #: Facts about the context the answer was produced under (game, ply, mode).
    context_summary: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- construction --------------------------------------------------------

    def add(self, item: EvidenceItem) -> EvidenceItem:
        self.items.append(item)
        return item

    def note_missing(self, tool: str, reason: str, kind: EvidenceKind | None = None) -> None:
        self.missing.append(MissingEvidence(tool=tool, reason=reason, kind=kind))

    def note_limitation(self, text: str) -> None:
        if text not in self.limitations:
            self.limitations.append(text)

    # --- reading -------------------------------------------------------------

    def of_kind(self, *kinds: EvidenceKind) -> list[EvidenceItem]:
        wanted = set(kinds)
        return [item for item in self.items if item.kind in wanted]

    def game_ids(self) -> list[str]:
        seen: list[str] = []
        for item in self.items:
            if item.game_id and item.game_id not in seen:
                seen.append(item.game_id)
        return seen

    @property
    def is_empty(self) -> bool:
        return not self.items

    def as_prompt_block(self, *, max_chars: int = 6000) -> str:
        """Render a bounded, compacted view for the model.

        Items are rendered newest-last in retrieval order; when the budget is hit
        the block says so rather than silently dropping evidence the model was
        told it had.
        """
        if not self.items:
            # A packet with no items is exactly when the absences matter most: an
            # empty evidence block that omitted `missing` would tell the model
            # "nothing was needed" when the truth is "nothing could be retrieved".
            head = "EVIDENCE: (none retrieved)"
            if not self.missing:
                return head
            lines = [head, "UNAVAILABLE:"]
            lines.extend(f"  - {entry.tool}: {entry.reason}" for entry in self.missing)
            return "\n".join(lines)
        lines: list[str] = []
        used = 0
        truncated = 0
        for index, item in enumerate(self.items, start=1):
            rendered = (
                f"[{index}] {item.kind.value} via {item.tool} "
                f"({item.source.value}/{item.certainty.value}, ref={item.ref()})\n"
                f"    {item.summary or '(no summary)'}\n"
                f"    data={_compact(item.data)}"
            )
            if used + len(rendered) > max_chars:
                truncated += 1
                continue
            lines.append(rendered)
            used += len(rendered)
        if truncated:
            lines.append(
                f"... {truncated} further evidence item(s) omitted: the prompt budget "
                f"({max_chars} chars) was reached."
            )
        if self.missing:
            lines.append("UNAVAILABLE:")
            lines.extend(f"  - {entry.tool}: {entry.reason}" for entry in self.missing)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _compact(data: dict[str, Any], *, max_chars: int = 700) -> str:
    """A short, stable rendering of a tool payload."""
    import json

    try:
        text = json.dumps(data, default=str, sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        text = str(data)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


__all__ = [
    "EvidenceItem",
    "EvidenceKind",
    "EvidencePacket",
    "MissingEvidence",
]
