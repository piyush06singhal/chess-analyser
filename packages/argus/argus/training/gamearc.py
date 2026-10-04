"""Game-arc evidence: conversion and recovery, provable only across a whole game.

A single move analysis cannot say "this advantage was later lost" or "this worse
position was later held" — that needs the game's *evaluation trajectory and its
outcome*. This module supplies exactly that, from data Caissa already stores:

* the stored mover-perspective evaluations of the player's own moves, and
* the stored game result.

Two labels are produced, each with a plain-English reason that travels into the
exercise's provenance:

``conversion``
    The player reached a clearly winning evaluation (≥ ``winning_cp``) but the
    game did not end in a win, and a later one of their evaluations fell back to
    equality or worse. The instructive ply is the first winning one from which
    the win slipped.
``recovery``
    The player was clearly worse (≤ ``losing_cp``) but the game ended in a draw
    or a win, and a later evaluation recovered. The instructive ply is the point
    at which they were most lost and then held.

Everything is measured; nothing is assumed. When the trajectory does not show
the pattern, no label is produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from argus.training.models import Category

#: Evaluation (mover perspective, centipawns) at or above which a position is
#: counted as winning for the arc analysis.
WINNING_CP = 200
#: Evaluation at or below which a position is counted as losing.
LOSING_CP = -200
#: An evaluation at or above this after having been losing counts as "held".
HELD_CP = -100
#: A drop this large from a winning evaluation counts as "the win slipped".
SLIPPED_CP = 200


@dataclass(frozen=True)
class ArcLabel:
    ply: int
    category: Category
    reason: str


def _outcome(result: str, player_color: str) -> str:
    if result == "1-0":
        return "win" if player_color == "white" else "loss"
    if result == "0-1":
        return "win" if player_color == "black" else "loss"
    if result == "1/2-1/2":
        return "draw"
    return "unknown"


def classify_arc(
    rows: list[dict[str, Any]],
    *,
    player_color: str,
    result: str,
) -> dict[int, ArcLabel]:
    """Return ``ply -> ArcLabel`` for the game's conversion/recovery moments.

    ``rows`` are the stored move-analysis dicts (all colours) in the shape the
    generator consumes; only the player's own moves with a stored evaluation are
    considered.
    """
    outcome = _outcome(result, player_color)
    if outcome == "unknown":
        return {}

    own = [
        row
        for row in sorted(rows, key=lambda item: item.get("ply") or 0)
        if row.get("mover") == player_color and row.get("evaluation_before_cp") is not None
    ]
    if not own:
        return {}

    labels: dict[int, ArcLabel] = {}

    conversion = _conversion_ply(own) if outcome != "win" else None
    if conversion is not None:
        row = conversion
        labels[int(row["ply"])] = ArcLabel(
            ply=int(row["ply"]),
            category=Category.CONVERSION,
            reason=(
                f"Conversion: the game had a clearly winning evaluation "
                f"({row['evaluation_before_cp']}cp at ply {row['ply']}) but ended "
                f"{outcome}; this is where the win began to slip."
            ),
        )

    recovery = _recovery_ply(own) if outcome in ("win", "draw") else None
    if recovery is not None:
        row = recovery
        labels[int(row["ply"])] = ArcLabel(
            ply=int(row["ply"]),
            category=Category.RECOVERY,
            reason=(
                f"Recovery: the game was clearly worse "
                f"({row['evaluation_before_cp']}cp at ply {row['ply']}) but ended "
                f"{outcome}; this is the defensive turning point that held."
            ),
        )

    return labels


def _conversion_ply(own: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first winning ply from which the player's evaluation later fell."""
    for index, row in enumerate(own):
        value = row["evaluation_before_cp"]
        if value is None or value < WINNING_CP:
            continue
        later = [later_row["evaluation_before_cp"] for later_row in own[index + 1 :]]
        if later and min(later) < value - SLIPPED_CP and min(later) <= 0:
            return row
    return None


def _recovery_ply(own: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The point at which the player was most lost and then held."""
    losing = [row for row in own if (row["evaluation_before_cp"] or 0) <= LOSING_CP]
    if not losing:
        return None
    for row in losing:
        index = own.index(row)
        later = [later_row["evaluation_before_cp"] for later_row in own[index + 1 :]]
        if later and max(later) >= HELD_CP:
            return row
    return None


__all__ = ["ArcLabel", "classify_arc"]
