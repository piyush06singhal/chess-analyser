"""Error categorisation.

Every move the engine flagged as inaccurate or worse is assigned a category
**from evidence that was actually detected at that ply**, never from the label
itself. A blunder is not automatically "tactical": the category is decided by
the events Caissa found, and if nothing fires the move is recorded as
``unclassified`` with the list of signals that were checked.

Priority order (highest first), documented and fixed:

1. ``tactical``      — a tactical event exists at this ply
2. ``material``      — the mover lost at least one pawn of net material
3. ``king_safety``   — a king-safety event is attached to this ply
4. ``opening``       — the ply is inside the identified opening phase
5. ``endgame``       — the position is an endgame
6. ``positional``    — a positional event (feature or candidate) is attached
7. ``unclassified``  — none of the above fired

Each error keeps ``basis`` (which signal decided it) and the raw evidence, so a
count of "7 tactical" can always be traced back to seven concrete positions.
"""

from __future__ import annotations

from enum import Enum

import chess
from pydantic import BaseModel, Field

from argus.analysis.phase import GamePhase
from argus.chess_core.models import Color
from argus.intelligence.base import (
    Certainty,
    EvidenceSource,
    MoveFact,
    color_label,
)
from argus.intelligence.king_safety import KingSafetyEventType, KingSafetyEvent
from argus.intelligence.material import material_points
from argus.intelligence.positional import PositionalEvent
from argus.intelligence.tactics import TacticType, TacticalEvent

#: Which tactical event types are treated as the decisive signal for a move.
_DECISIVE_TACTICS = {
    TacticType.FORK,
    TacticType.PIN,
    TacticType.SKEWER,
    TacticType.DISCOVERED_ATTACK,
    TacticType.HANGING_PIECE,
    TacticType.MATING_THREAT,
    TacticType.BACK_RANK_WEAKNESS,
    TacticType.OVERLOADED_DEFENDER,
}

#: King-safety events that hurt the side that played the move.
_OWN_KING_EVENTS = {
    KingSafetyEventType.PAWN_SHIELD_REDUCED,
    KingSafetyEventType.OPEN_FILE_NEAR_KING,
    KingSafetyEventType.KING_EXPOSED,
    KingSafetyEventType.KING_MOBILITY_LIMITED,
}

#: King-safety events that mean the opponent's king came under attack.
_OPPONENT_KING_EVENTS = {
    KingSafetyEventType.PIECE_PRESSURE_NEAR_KING,
    KingSafetyEventType.MATING_NET,
}


class ErrorCategory(str, Enum):
    """Caissa error categories (counts are evidence-derived)."""

    TACTICAL = "tactical"
    MATERIAL = "material"
    KING_SAFETY = "king_safety"
    OPENING = "opening"
    ENDGAME = "endgame"
    POSITIONAL = "positional"
    UNCLASSIFIED = "unclassified"


class CategorisedError(BaseModel):
    """One flagged move with the evidence behind its category."""

    category: ErrorCategory
    basis: str = Field(description="The signal that decided the category")
    ply: int
    move_number: int
    side: Color
    san: str
    classification: str | None = None
    centipawn_loss: int | None = None
    phase: str | None = None
    evidence: dict = Field(default_factory=dict)
    certainty: Certainty = Certainty.CONFIRMED
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE


class CategoryAnalysis(BaseModel):
    """Per-category error counts for both sides."""

    errors: list[CategorisedError] = Field(default_factory=list)
    by_category: dict[str, int] = Field(default_factory=dict)
    by_side_category: dict[str, dict[str, int]] = Field(default_factory=dict)
    unclassified_count: int = 0
    note: str = (
        "Categories come from events detected at that ply (tactics, king safety, material, "
        "phase, structure). Moves with no detected signal are reported as unclassified "
        "rather than filed under a guessed heading."
    )


def _net_material_loss(moves: list[MoveFact], index: int, mover: Color) -> int:
    """Pawns of net material the mover lost across the move and the reply."""
    fact = moves[index]
    board_before = chess.Board(fact.fen_before)
    color = chess.WHITE if mover == Color.WHITE else chess.BLACK
    before = material_points(board_before, color)
    reply = moves[index + 1] if index + 1 < len(moves) else None
    end_fen = reply.fen_after if reply is not None else fact.fen_after
    after = material_points(chess.Board(end_fen), color)
    return max(0, before - after)


def categorise_errors(
    moves: list[MoveFact],
    *,
    tactical_by_ply: dict[int, list[TacticalEvent]],
    positional_by_ply: dict[int, list[PositionalEvent]],
    king_safety_by_ply: dict[int, list[KingSafetyEvent]],
    phase_by_ply: dict[int, GamePhase],
    opening_plies: int = 0,
) -> CategoryAnalysis:
    """Categorise every flagged move of a game from detected evidence."""
    errors: list[CategorisedError] = []

    for index, fact in enumerate(moves):
        if not fact.is_problem:
            continue
        tactical_events = [
            event
            for event in tactical_by_ply.get(fact.ply, [])
            if event.side == fact.mover
            and (event.type in _DECISIVE_TACTICS or event.engine_context.get("engine_supported"))
        ]
        positional_events = [
            event for event in positional_by_ply.get(fact.ply, []) if event.side == fact.mover
        ]
        king_events_own = [
            event
            for event in king_safety_by_ply.get(fact.ply, [])
            if event.type in _OWN_KING_EVENTS and event.side == fact.mover
        ]
        king_events_opponent = [
            event
            for event in king_safety_by_ply.get(fact.ply, [])
            if event.type in _OPPONENT_KING_EVENTS and event.side != fact.mover
        ]
        material_loss = _net_material_loss(moves, index, fact.mover)
        phase = phase_by_ply.get(fact.ply)

        checked = {
            "tactical_events": [event.type.value for event in tactical_events],
            "positional_events": [event.type.value for event in positional_events],
            "own_king_safety_events": [event.type.value for event in king_events_own],
            "opponent_king_safety_events": [event.type.value for event in king_events_opponent],
            "net_material_loss_pawns": material_loss,
            "phase": phase.value if phase else None,
            "inside_opening": fact.ply <= opening_plies if opening_plies else False,
        }

        category: ErrorCategory
        basis: str
        evidence = dict(checked)
        certainty = Certainty.CONFIRMED

        if tactical_events:
            worst = max(tactical_events, key=lambda event: event.severity)
            category, basis = ErrorCategory.TACTICAL, f"tactical_event:{worst.type.value}"
            evidence["tactical_detail"] = worst.statement
            certainty = worst.certainty
        elif material_loss >= 1:
            category, basis = ErrorCategory.MATERIAL, "net_material_loss"
            evidence["note"] = "Net material across the move and the opponent's reply"
        elif king_events_own or king_events_opponent:
            event = (king_events_own + king_events_opponent)[0]
            category = ErrorCategory.KING_SAFETY
            basis = f"king_safety_event:{event.type.value}"
            evidence["king_safety_detail"] = event.statement
        elif phase is GamePhase.OPENING:
            category, basis = ErrorCategory.OPENING, "phase_opening"
        elif phase is GamePhase.ENDGAME:
            category, basis = ErrorCategory.ENDGAME, "phase_endgame"
        elif positional_events:
            event = positional_events[0]
            category = ErrorCategory.POSITIONAL
            basis = f"positional_event:{event.type.value}"
            evidence["positional_detail"] = event.statement
            certainty = event.certainty
        else:
            category, basis = ErrorCategory.UNCLASSIFIED, "no_detected_signal"

        errors.append(
            CategorisedError(
                category=category,
                basis=basis,
                ply=fact.ply,
                move_number=fact.move_number,
                side=fact.mover,
                san=fact.san,
                classification=fact.classification.value if fact.classification else None,
                centipawn_loss=fact.centipawn_loss,
                phase=phase.value if phase else None,
                evidence=evidence,
                certainty=certainty,
                source=(
                    EvidenceSource.ARGUS_INTERPRETATION
                    if category
                    in {ErrorCategory.TACTICAL, ErrorCategory.POSITIONAL, ErrorCategory.UNCLASSIFIED}
                    else EvidenceSource.ARGUS_DERIVED_FEATURE
                ),
            )
        )

    by_category: dict[str, int] = {}
    by_side_category: dict[str, dict[str, int]] = {}
    for error in errors:
        by_category[error.category.value] = by_category.get(error.category.value, 0) + 1
        side_bucket = by_side_category.setdefault(error.side.value, {})
        side_bucket[error.category.value] = side_bucket.get(error.category.value, 0) + 1

    return CategoryAnalysis(
        errors=errors,
        by_category=by_category,
        by_side_category=by_side_category,
        unclassified_count=by_category.get(ErrorCategory.UNCLASSIFIED.value, 0),
    )


def category_statement(analysis: CategoryAnalysis, color: Color) -> str:
    """A factual sentence summarising one side's error categories."""
    bucket = analysis.by_side_category.get(color.value, {})
    if not bucket:
        return f"{color_label(color)} has no moves flagged by the engine."
    parts = ", ".join(f"{name} {count}" for name, count in sorted(bucket.items()))
    return f"{color_label(color)} flagged moves by category: {parts}."
