"""Opponent-preparation training: practise against what the opponent actually plays.

The personalized engine builds exercises from the player's **own** mistakes. This
module builds a different kind of exercise from the **opponent's** games, and it
is deliberately strict about what it may claim:

* A position qualifies only when the opponent played the *same move* there often
  enough to be characteristic (the occurrence gate). A one-off move is not a
  habit and must not become preparation material.
* The exercise is the position **after** that move — the position the preparing
  player will actually face — and its solution is the engine's stored best reply
  at the very next ply of the same game. It is engine-verified analysis Caissa
  already holds, never a constructed line.
* Every exercise records why it exists: the opponent, the move they play there,
  and how many of their games show it. That provenance survives into the stored
  exercise and is rendered verbatim.

Nothing here predicts what the opponent will do. It prepares a response to a
move they have demonstrably played before.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from argus.opponent_intelligence.common import normalize_fen
from argus.training.models import OPPONENT_PREPARATION_SOURCE

#: A move seen in fewer of the opponent's games than this is not characteristic
#: and never becomes preparation material.
DEFAULT_MIN_OCCURRENCES = 2


@dataclass(frozen=True)
class PreparationRow:
    """One candidate exercise row, plus the provenance that justifies it."""

    row: dict[str, Any]
    game_id: str
    opponent_name: str
    opponent_move_san: str
    occurrences: int
    opponent_color: str

    def reason(self) -> str:
        return (
            f"Opponent preparation: {self.opponent_name} played {self.opponent_move_san} "
            f"here in {self.occurrences} stored game(s); this is the position you then face."
        )


def characteristic_moves(
    opponent_rows: list[dict[str, Any]],
    *,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
) -> dict[tuple[str, str], int]:
    """Count how many games show the opponent playing each (position, move).

    Keyed by (normalized FEN before the move, UCI move). The count is games, not
    plies, so the same move repeated inside one game cannot inflate it.
    """
    seen: dict[tuple[str, str], set[str]] = {}
    for game in opponent_rows:
        color = game.get("color")
        for move in game.get("moves") or []:
            if move.get("color") != color:
                continue
            key = (normalize_fen(str(move.get("fen_before") or "")), str(move.get("uci") or ""))
            if not key[0] or not key[1]:
                continue
            seen.setdefault(key, set()).add(str(game.get("game_id")))
    return {
        key: len(game_ids)
        for key, game_ids in seen.items()
        if len(game_ids) >= max(1, int(min_occurrences))
    }


def select_preparation_rows(
    opponent_rows: list[dict[str, Any]],
    *,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
    max_exercises: int = 20,
) -> list[PreparationRow]:
    """The reply positions that follow the opponent's characteristic moves.

    Deterministic: candidates are ordered by the opponent's move count (desc),
    then game id, then ply, and at most ``max_exercises`` are returned. A move
    with no stored reply ply is skipped rather than padded.
    """
    counts = characteristic_moves(opponent_rows, min_occurrences=min_occurrences)
    candidates: list[tuple[int, str, int, PreparationRow]] = []

    for game in opponent_rows:
        game_id = str(game.get("game_id") or "")
        color = game.get("color")
        name = str(game.get("opponent_name") or "the opponent")
        moves = sorted(game.get("moves") or [], key=lambda move: move.get("ply") or 0)
        by_ply = {move.get("ply"): move for move in moves}
        for move in moves:
            if move.get("color") != color:
                continue
            key = (normalize_fen(str(move.get("fen_before") or "")), str(move.get("uci") or ""))
            occurrences = counts.get(key)
            if not occurrences:
                continue
            reply = by_ply.get((move.get("ply") or 0) + 1)
            if not reply or not reply.get("best_move_uci") or not reply.get("fen_before"):
                continue
            candidates.append(
                (
                    occurrences,
                    game_id,
                    int(move.get("ply") or 0),
                    PreparationRow(
                        row=reply,
                        game_id=game_id,
                        opponent_name=name,
                        opponent_move_san=str(move.get("san") or ""),
                        occurrences=occurrences,
                        opponent_color=str(color or ""),
                    ),
                )
            )

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected: list[PreparationRow] = []
    dedup: set[str] = set()
    for _occurrences, _game_id, _ply, candidate in candidates:
        normalized = normalize_fen(str(candidate.row.get("fen_before") or ""))
        if normalized in dedup:
            continue
        dedup.add(normalized)
        selected.append(candidate)
        if len(selected) >= max_exercises:
            break
    return selected


#: The data_source every exercise built here carries.
SOURCE = OPPONENT_PREPARATION_SOURCE

__all__ = [
    "DEFAULT_MIN_OCCURRENCES",
    "PreparationRow",
    "characteristic_moves",
    "select_preparation_rows",
]
