"""Evidence references: the reason a derived relationship is allowed to exist.

Every relationship the graph stores that is not a plain structural fact (a game
*contains* its positions) is a **derived claim**. A player *has* a pattern; a
position is *similar* to another; training *improved* a result. §6 is explicit: a
derived relationship without evidence must not be presented as an established
fact.

This module makes that structurally impossible to get wrong in two ways:

* :class:`EvidenceReference` is the one shape every derived edge carries, whether
  the evidence is a game, a move, a position, an analysis, a training attempt, a
  dataset version, a model version or a knowledge source. One shape means one
  validator and one place to trace.
* :func:`require_evidence` refuses an edge kind that is derived but carries no
  references. The refusal names the edge and the reason, so the caller learns
  something instead of getting an opaque failure.

Nothing here invents a reference. A reference to an object that no longer exists
is stored, but marked ``unavailable`` when it is read (see the service layer):
deleting a source must not silently erase the record that an analysis once ran.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from argus.intelligence_graph.taxonomy import EdgeType


class EvidenceKind(str, Enum):
    """What kind of stored object a reference points at."""

    GAME = "game"
    MOVE = "move"
    POSITION = "position"
    ANALYSIS = "analysis"
    TRAINING_POSITION = "training_position"
    TRAINING_ATTEMPT = "training_attempt"
    TRAINING_SESSION = "training_session"
    SCENARIO = "scenario"
    INSIGHT = "insight"
    PLAYER = "player"
    OPPONENT = "opponent"
    PREDICTION = "prediction"
    DATASET = "dataset"
    MODEL = "model"
    KNOWLEDGE_SOURCE = "knowledge_source"
    ENGINE = "engine"


class EvidenceReference(BaseModel):
    """One traceable pointer behind a derived claim (§7).

    Exactly what a reference carries depends on the insight: a pattern claim
    points at games and positions; a training-effect claim points at attempts; a
    prediction claim points at a model and a dataset. The optional fields exist
    because the *kind* of evidence varies — the requirement is that at least one
    identifying field is present, which :meth:`has_anchor` enforces.
    """

    kind: EvidenceKind
    label: str = ""
    #: A human-readable statement of what this reference contributes.
    statement: str = ""

    game_id: str | None = None
    ply: int | None = None
    move_id: int | None = None
    position_id: int | None = None
    position_hash: str | None = None
    fen: str | None = None
    analysis_id: int | None = None
    analysis_version: str | None = None
    training_position_id: int | None = None
    training_attempt_id: int | None = None
    session_id: int | None = None
    scenario_id: int | None = None
    insight_id: str | None = None
    player_id: int | None = None
    opponent_id: int | None = None
    prediction_id: int | None = None
    dataset_version: str | None = None
    model_version: str | None = None
    knowledge_source_id: int | None = None
    engine: str | None = None
    engine_version: str | None = None

    #: The stored value the claim is about, when it is numeric (a centipawn loss,
    #: an attempt count). Kept beside the reference so evidence is checkable.
    value: float | None = None
    unit: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def has_anchor(self) -> bool:
        """Whether the reference identifies at least one stored object."""
        return any(
            value is not None
            for value in (
                self.game_id,
                self.move_id,
                self.position_id,
                self.position_hash,
                self.analysis_id,
                self.training_position_id,
                self.training_attempt_id,
                self.session_id,
                self.scenario_id,
                self.insight_id,
                self.prediction_id,
                self.dataset_version,
                self.model_version,
                self.knowledge_source_id,
            )
        )

    @property
    def anchor(self) -> str | None:
        """A stable one-line identifier for dedupe and display."""
        for name in (
            "analysis_id",
            "training_attempt_id",
            "move_id",
            "position_id",
            "training_position_id",
            "session_id",
            "scenario_id",
            "prediction_id",
            "insight_id",
            "knowledge_source_id",
            "game_id",
            "dataset_version",
            "model_version",
        ):
            value = getattr(self, name)
            if value is not None:
                return f"{name}:{value}"
        return None

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class MissingEvidenceError(ValueError):
    """Raised when a derived edge is written without any evidence reference."""

    def __init__(self, edge_type: EdgeType) -> None:
        self.edge_type = edge_type
        super().__init__(
            f"Edge '{edge_type.value}' is a derived claim and requires at least one "
            f"evidence reference. A relationship without evidence must not be "
            f"presented as an established fact."
        )


def require_evidence(
    edge_type: EdgeType, references: list[EvidenceReference] | None
) -> list[EvidenceReference]:
    """Validate the evidence on a derived edge, returning it cleaned.

    An evidence-bearing edge with no usable reference raises
    :class:`MissingEvidenceError`. A reference that names no anchor at all is
    dropped (it proves nothing) and the same check then applies to what remains.
    """
    usable = [ref for ref in (references or []) if ref.has_anchor()]
    if edge_type.is_evidence_bearing and not usable:
        raise MissingEvidenceError(edge_type)
    return usable


def reference_for_game(
    game_id: str, *, ply: int | None = None, label: str = "", statement: str = ""
) -> EvidenceReference:
    """A reference to a stored game (optionally at a specific ply)."""
    return EvidenceReference(
        kind=EvidenceKind.GAME,
        game_id=game_id,
        ply=ply,
        label=label or f"game {game_id}",
        statement=statement,
    )


def reference_for_attempt(
    attempt_id: int, *, position_id: int | None = None, label: str = "", statement: str = ""
) -> EvidenceReference:
    return EvidenceReference(
        kind=EvidenceKind.TRAINING_ATTEMPT,
        training_attempt_id=attempt_id,
        training_position_id=position_id,
        label=label or f"attempt {attempt_id}",
        statement=statement,
    )


def merge_references(
    *groups: list[EvidenceReference] | None,
) -> list[EvidenceReference]:
    """Union several evidence lists, dropping duplicates by :attr:`anchor`."""
    seen: set[str] = set()
    merged: list[EvidenceReference] = []
    for group in groups:
        for ref in group or []:
            anchor = ref.anchor
            if anchor is None:
                continue
            if anchor in seen:
                continue
            seen.add(anchor)
            merged.append(ref)
    return merged


__all__ = [
    "EvidenceKind",
    "EvidenceReference",
    "MissingEvidenceError",
    "merge_references",
    "reference_for_attempt",
    "reference_for_game",
    "require_evidence",
]
