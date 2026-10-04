"""Training sessions (spec §21-23): resumable, honest, replayable.

A session is a *plan* of exercise ids plus progress counters. Plans are built
deterministically from the player's exercise library and current review queue —
no engine calls, no randomness; two calls with the same inputs produce the same
plan and the same order.

Kinds (all seven, per spec):

=====================  =====================================================
``quick``              five positions, whatever is most due — a coffee break.
``daily``              the review queue first, then fresh exercises, up to
                       ``daily_size``.
``weakness``           the recommendation engine's weakest categories first
                       (caller passes the prioritized category order).
``game_review``        exercises generated from one specific game.
``endgame``            endgame-category exercises only; when the library has
                       none, the plan is honestly empty — no substitutes.
``tactical``           tactical-category exercises only, same rule.
``custom``             the caller supplies explicit position ids.
=====================  =====================================================

A plan never invents content: if a category has too few exercises, the plan is
short and says so. Sessions are resumable because the plan is stored as ids —
reopening a session replays exactly the remaining ids, whatever happened in
between (including the source game being deleted, which does not invalidate the
stored exercise).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from argus.training.models import Category, TrainingPosition

#: The seven session kinds (mirrors the DB check constraint).
SESSION_KINDS = (
    "quick",
    "daily",
    "weakness",
    "game_review",
    "endgame",
    "tactical",
    "custom",
)

#: Sizes documented and fixed — a "quick" session is five positions everywhere.
QUICK_SIZE = 5
DAILY_SIZE = 10

#: Session kinds restricted to a single category, and that category.
CATEGORY_KINDS: dict[str, Category] = {
    "endgame": Category.ENDGAME,
    "tactical": Category.TACTICAL,
}


@dataclass
class SessionPlan:
    """A deterministic, resumable plan: ordered exercise ids + counters."""

    kind: str
    planned_position_ids: list[int] = field(default_factory=list)
    completed_position_ids: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def remaining_position_ids(self) -> list[int]:
        return [pid for pid in self.planned_position_ids if pid not in set(self.completed_position_ids)]

    @property
    def progress(self) -> float:
        """Completion fraction; 0.0 for an empty plan (never a fake number)."""
        if not self.planned_position_ids:
            return 0.0
        return len(self.completed_position_ids) / len(self.planned_position_ids)

    @property
    def is_complete(self) -> bool:
        return bool(self.planned_position_ids) and not self.remaining_position_ids

    def mark_completed(self, position_id: int) -> None:
        """Record one finished exercise (idempotent; order preserved)."""
        if position_id in self.planned_position_ids and position_id not in self.completed_position_ids:
            self.completed_position_ids.append(position_id)

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "planned": len(self.planned_position_ids),
            "completed": len(self.completed_position_ids),
            "remaining": len(self.remaining_position_ids),
            "progress": round(self.progress, 3),
            "notes": list(self.notes),
        }


def plan_session(
    kind: str,
    positions: list[TrainingPosition],
    *,
    now: datetime | None = None,
    due_position_ids: list[int] | None = None,
    weakness_category_order: list[str] | None = None,
    game_id: str | None = None,
    custom_position_ids: list[int] | None = None,
    max_positions: int | None = None,
) -> SessionPlan:
    """Build the plan for one session kind from the player's exercise library.

    ``positions`` is the caller's already-authorized view of the library (the
    service layer filters by player); ordering decisions here are deterministic
    functions of that list.
    """
    if kind not in SESSION_KINDS:
        raise ValueError(f"unknown session kind '{kind}' (known: {', '.join(SESSION_KINDS)})")

    notes: list[str] = []
    planned: list[TrainingPosition] = []

    if kind == "custom":
        available = {p.id: p for p in positions if p.id is not None}
        missing: list[int] = []
        for pid in custom_position_ids or []:
            position = available.get(pid)
            if position is not None:
                planned.append(position)
            else:
                missing.append(pid)
        if missing:
            notes.append(
                f"{len(missing)} requested exercise(s) are not in this player's library: "
                f"{missing}"
            )

    elif kind == "quick":
        planned = _by_due_order(positions, due_position_ids)[:QUICK_SIZE]
        if len(planned) < QUICK_SIZE:
            notes.append(
                f"only {len(planned)} exercise(s) available for a quick session"
            )

    elif kind == "daily":
        due_first = _by_due_order(positions, due_position_ids)
        planned = due_first[:DAILY_SIZE]
        if len(planned) < DAILY_SIZE:
            # Top up with NEW exercises (no schedule yet), deterministic order.
            fresh = sorted(
                (p for p in positions if p.state.value == "new" and p not in planned),
                key=_library_order,
            )
            planned = planned + fresh[: DAILY_SIZE - len(planned)]
        if not planned:
            notes.append("nothing is due and no fresh exercises exist yet")

    elif kind == "weakness":
        order = weakness_category_order or []
        if not order:
            notes.append("no recommendation order supplied; falling back to due order")
        by_category: dict[str, list[TrainingPosition]] = {}
        for position in positions:
            by_category.setdefault(position.category.value, []).append(position)
        seen_ids: set[int] = set()
        for category_value in order:
            bucket = by_category.get(category_value, [])
            bucket = _by_due_order(bucket, due_position_ids)
            for position in bucket:
                if position.id not in seen_ids:
                    planned.append(position)
                    seen_ids.add(position.id)
        # Anything left over follows due order, capped later.
        for position in _by_due_order(positions, due_position_ids):
            if position.id not in seen_ids:
                planned.append(position)
                seen_ids.add(position.id)

    elif kind in CATEGORY_KINDS:
        wanted = CATEGORY_KINDS[kind].value
        bucket = [p for p in positions if p.category.value == wanted]
        planned = _by_due_order(bucket, due_position_ids)[: (max_positions or DAILY_SIZE)]
        if not planned:
            notes.append(
                f"no '{wanted}' exercises exist in this library yet; the plan is empty "
                "rather than padded with substitutes"
            )

    elif kind == "game_review":
        if not game_id:
            raise ValueError("game_review sessions require the source game id")
        bucket = [p for p in positions if p.source_game_id == game_id]
        planned = sorted(bucket, key=lambda p: (p.source_ply if p.source_ply is not None else 0, p.id or 0))
        if not planned:
            notes.append(f"no exercises are sourced from game '{game_id}'")

    if max_positions is not None and kind not in CATEGORY_KINDS:
        planned = planned[:max_positions]

    return SessionPlan(
        kind=kind,
        planned_position_ids=[p.id for p in planned if p.id is not None],
        notes=notes,
    )


def _by_due_order(
    positions: list[TrainingPosition],
    due_position_ids: list[int] | None,
) -> list[TrainingPosition]:
    """Due exercises first (in the caller's queue order), then the rest."""
    due_rank = {pid: index for index, pid in enumerate(due_position_ids or [])}
    due = sorted(
        (p for p in positions if p.id in due_rank),
        key=lambda p: due_rank[p.id],
    )
    rest = sorted((p for p in positions if p.id not in due_rank), key=_library_order)
    return due + rest


def _library_order(position: TrainingPosition) -> tuple:
    """Deterministic library order: difficulty (hardest last is a choice —
    easiest first builds confidence), then id for stability."""
    difficulty_rank = {"beginner": 0, "easy": 1, "intermediate": 2, "advanced": 3, "expert": 4}
    return (difficulty_rank.get(position.difficulty.value, 5), position.id if position.id is not None else 0)


__all__ = [
    "CATEGORY_KINDS",
    "DAILY_SIZE",
    "QUICK_SIZE",
    "SESSION_KINDS",
    "SessionPlan",
    "plan_session",
]
