"""Evaluation perspective normalization.

Caissa uses exactly ONE internal convention for stored, user-facing evaluations:

    POSITIVE = WHITE IS BETTER.   NEGATIVE = BLACK IS BETTER.

Stockfish (UCI) reports scores from the **side-to-move** perspective, which
changes every ply. Mixing the two conventions is the classic source of
sign-flipped analysis bugs, so every conversion goes through this module and
is unit-tested.

Two perspectives exist in the codebase:

* **mover perspective** — what the engine returns natively ("is this good for
  the player who is about to move?"). Used for centipawn loss, because a loss
  is always the mover's loss.
* **white perspective** — the single stored/displayed convention. Used for
  evaluation bars, eval graphs, and reports.

Use :func:`to_white_perspective` to normalize engine output, and
:func:`from_white_perspective` (or :func:`for_color`) to present it for a
specific player.
"""

from __future__ import annotations

import chess

from argus.chess_core.models import Color


def side_to_move(fen: str) -> Color:
    """Return the side to move encoded in a FEN string."""
    return Color.WHITE if chess.Board(fen).turn == chess.WHITE else Color.BLACK


def flip(cp: int | None, mate: int | None) -> tuple[int | None, int | None]:
    """Negate a score (centipawns and mate distance)."""
    return (None if cp is None else -cp, None if mate is None else -mate)


def to_white_perspective(
    cp: int | None, mate: int | None, *, side: Color | str
) -> tuple[int | None, int | None]:
    """Convert a side-to-move-perspective score to the White-perspective convention.

    A score that is positive for Black-to-move means Black is better, which is
    negative for White — so the sign is flipped exactly when it is Black's turn.
    """
    color = Color(side)
    if color is Color.WHITE:
        return (cp, mate)
    return flip(cp, mate)


def to_mover_perspective(
    cp: int | None, mate: int | None, *, side: Color | str
) -> tuple[int | None, int | None]:
    """Inverse of :func:`to_white_perspective`: White convention → mover convention."""
    color = Color(side)
    if color is Color.WHITE:
        return (cp, mate)
    return flip(cp, mate)


def from_white_perspective(
    cp: int | None, mate: int | None, *, color: Color | str
) -> tuple[int | None, int | None]:
    """Present a White-perspective score for a specific player.

    ``color=WHITE`` returns it unchanged; ``color=BLACK`` negates it, so the
    same position reads ``+2.0`` for White and ``-2.0`` for Black.
    """
    return for_color(cp, mate, color)


def for_color(
    cp: int | None, mate: int | None, color: Color | str
) -> tuple[int | None, int | None]:
    """Alias of the presentation conversion (White convention → player view)."""
    player = Color(color)
    if player is Color.WHITE:
        return (cp, mate)
    return flip(cp, mate)


def format_evaluation(cp: int | None, mate: int | None) -> str:
    """Human-readable, non-misleading evaluation string.

    Mate scores are always rendered as mate distances (``#3`` / ``#-3``),
    never as an ordinary centipawn number. When both are missing the
    evaluation is honestly reported as unavailable.
    """
    if mate is not None:
        return f"#{mate}" if mate > 0 else f"#-{abs(mate)}"
    if cp is None:
        return "—"
    return f"{cp / 100:+.2f}"
