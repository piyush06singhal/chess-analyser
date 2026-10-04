"""Position similarity (§12): controlled, non-interchangeable categories.

The single most damaging thing a "knowledge graph" can do is present a
*similar* position as an *exact historical match*. Caissa's whole trust model
rests on never doing that, so similarity is not a score here — it is a small,
ordered set of named categories, and the name is the claim.

:class:`SimilarityLevel` is ordered from strongest to weakest. Each level has a
precise, checkable definition, computed with python-chess (already a dependency;
no chess logic is re-implemented):

``EXACT``
    Same position identity: placement, side to move, castling rights and
    en-passant target all equal (the fingerprint from :mod:`.fingerprint`).
``EQUIVALENT``
    Different FEN, *identical legal move set*. This is the honest case of a
    transposition where only bookkeeping (an irrelevant en-passant square, or
    castling rights that can no longer be exercised) differs. It is still not
    EXACT, and the API never labels it EXACT.
``STRUCTURALLY_SIMILAR``
    Same side to move, same material signature and same pawn structure. This is
    a real, checkable structural resemblance — and nothing more.
``OPENING_SIMILAR``
    Same opening node / opening line, verified by the caller against stored
    opening data. It is *not* a board claim.
``TACTICALLY_SIMILAR``
    Same tactical motif, verified by the caller against stored tactical events.

Order matters: :func:`classify` returns the *strongest* level that holds, so a
pair that is EXACT is never also reported as STRUCTURALLY_SIMILAR. A caller that
needs a weaker relationship must ask for exactly that level with
:func:`qualifies` rather than assuming a stronger one implies it.
"""

from __future__ import annotations

from enum import Enum

import chess

from argus.intelligence_graph.fingerprint import normalize_fen, same_position

PieceMap = dict[int, int]


class SimilarityLevel(str, Enum):
    """Ordered from strongest (EXACT) to weakest (TACTICALLY_SIMILAR)."""

    EXACT = "exact"
    EQUIVALENT = "equivalent"
    STRUCTURALLY_SIMILAR = "structurally_similar"
    OPENING_SIMILAR = "opening_similar"
    TACTICALLY_SIMILAR = "tactically_similar"

    @property
    def rank(self) -> int:
        return _ORDER.index(self)

    @property
    def is_board_claim(self) -> bool:
        """Whether the level is decided from the board alone (EXACT..STRUCTURAL)."""
        return self in _BOARD_LEVELS


_ORDER: list[SimilarityLevel] = [
    SimilarityLevel.EXACT,
    SimilarityLevel.EQUIVALENT,
    SimilarityLevel.STRUCTURALLY_SIMILAR,
    SimilarityLevel.OPENING_SIMILAR,
    SimilarityLevel.TACTICALLY_SIMILAR,
]

_BOARD_LEVELS = frozenset(
    {
        SimilarityLevel.EXACT,
        SimilarityLevel.EQUIVALENT,
        SimilarityLevel.STRUCTURALLY_SIMILAR,
    }
)

#: Human-readable definitions, published verbatim so the UI can never overstate.
DEFINITIONS: dict[SimilarityLevel, str] = {
    SimilarityLevel.EXACT: (
        "The same position: identical placement, side to move, castling rights and "
        "en-passant target."
    ),
    SimilarityLevel.EQUIVALENT: (
        "A different FEN with an identical legal move set — a transposition where "
        "only bookkeeping differs. Not an exact match."
    ),
    SimilarityLevel.STRUCTURALLY_SIMILAR: (
        "Same side to move, same material and same pawn structure. A structural "
        "resemblance, not an exact match."
    ),
    SimilarityLevel.OPENING_SIMILAR: (
        "The same stored opening line. Says nothing about the rest of the board."
    ),
    SimilarityLevel.TACTICALLY_SIMILAR: (
        "The same stored tactical motif. Says nothing about the rest of the board."
    ),
}


def _board(fen: str) -> chess.Board | None:
    try:
        return chess.Board(fen)
    except ValueError:
        return None


def legal_move_set(board: chess.Board) -> frozenset[str]:
    """The UCI moves legal in a position. The definition of EQUIVALENT."""
    return frozenset(move.uci() for move in board.legal_moves)


def material_signature(board: chess.Board) -> PieceMap:
    """Piece code → count for every piece, both colours."""
    counts: PieceMap = {}
    for piece in board.piece_map().values():
        counts[piece.piece_type] = counts.get(piece.piece_type, 0) + 1
    return counts


def pawn_structure_signature(board: chess.Board) -> frozenset[tuple[int, int]]:
    """(square index, colour) for every pawn — the pawn skeleton."""
    return frozenset(
        (square, piece.color)
        for square, piece in board.piece_map().items()
        if piece.piece_type == chess.PAWN
    )


def structural_signature(fen: str) -> tuple | None:
    """A hashable structural key: side to move + material + pawn structure."""
    board = _board(fen)
    if board is None:
        return None
    return (
        board.turn,
        tuple(sorted(material_signature(board).items())),
        pawn_structure_signature(board),
    )


def is_equivalent(fen_a: str, fen_b: str) -> bool:
    """Different FENs, identical legal move set."""
    board_a = _board(fen_a)
    board_b = _board(fen_b)
    if board_a is None or board_b is None:
        return False
    if normalize_fen(fen_a) == normalize_fen(fen_b):
        return False
    return legal_move_set(board_a) == legal_move_set(board_b)


def is_structurally_similar(fen_a: str, fen_b: str) -> bool:
    """Same side to move, material and pawn structure — and not the same position."""
    if same_position(fen_a, fen_b):
        return False
    left = structural_signature(fen_a)
    right = structural_signature(fen_b)
    if left is None or right is None:
        return False
    return left == right


def classify(
    fen_a: str,
    fen_b: str,
    *,
    same_opening: bool = False,
    same_motif: bool = False,
) -> SimilarityLevel | None:
    """The strongest similarity level that holds between two positions.

    Board levels are decided from the board. ``OPENING_SIMILAR`` and
    ``TACTICALLY_SIMILAR`` are decided by the caller, who has the stored opening
    line / tactical event to check against — this function never guesses them.

    Returns ``None`` when nothing holds: the honest "no verified similarity".
    """
    if not normalize_fen(fen_a) or not normalize_fen(fen_b):
        return None
    if same_position(fen_a, fen_b):
        return SimilarityLevel.EXACT
    if is_equivalent(fen_a, fen_b):
        return SimilarityLevel.EQUIVALENT
    if is_structurally_similar(fen_a, fen_b):
        return SimilarityLevel.STRUCTURALLY_SIMILAR
    if same_opening:
        return SimilarityLevel.OPENING_SIMILAR
    if same_motif:
        return SimilarityLevel.TACTICALLY_SIMILAR
    return None


def qualifies(level: SimilarityLevel | None, minimum: SimilarityLevel) -> bool:
    """Whether a level is at least as strong as ``minimum``.

    Used by callers that need a guarantee: an "exact match only" query passes
    ``minimum=SimilarityLevel.EXACT`` and get no false positives.
    """
    if level is None:
        return False
    return level.rank <= minimum.rank


def describe(level: SimilarityLevel | None) -> dict[str, object]:
    """The published definition of a level, for the UI and the evidence panel."""
    if level is None:
        return {
            "level": None,
            "definition": "No verified similarity was found between these positions.",
        }
    return {
        "level": level.value,
        "definition": DEFINITIONS[level],
        "is_board_claim": level.is_board_claim,
        "is_exact": level is SimilarityLevel.EXACT,
    }


__all__ = [
    "DEFINITIONS",
    "SimilarityLevel",
    "classify",
    "describe",
    "is_equivalent",
    "is_structurally_similar",
    "legal_move_set",
    "material_signature",
    "pawn_structure_signature",
    "qualifies",
    "structural_signature",
]
