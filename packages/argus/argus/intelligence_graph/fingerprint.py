"""Position fingerprinting (§11): a stable identity for a chess position.

Two positions are the *same* only if the same player to move has the same legal
move set. Piece placement alone is not enough: two positions with identical
pieces but different castling rights, a different side to move, or a different
en-passant target are different positions and must not collide. So the
fingerprint covers, in FEN order:

1. piece placement,
2. side to move,
3. castling rights,
4. en-passant target.

The halfmove clock and fullmove number are deliberately dropped: they are
bookkeeping for the fifty-move rule and move numbering, not part of the position.
Dropping them is what lets a transposition reached by two move orders compare
equal, which is exactly what retrieval needs.

The project already stores positions in FEN and already has a light
four-field normaliser (:func:`argus.opponent_intelligence.common.normalize_fen`).
This module does not duplicate chess logic: it *validates* with python-chess
(already a dependency) and adds the hash and the explicit field breakdown the
graph indexes on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import chess

#: A canonical starting position, used as the root of the opening graph.
START_FEN = chess.STARTING_FEN


@dataclass(frozen=True)
class PositionFingerprint:
    """The decomposed identity of a position."""

    fen: str
    normalized_fen: str
    position_hash: str
    placement: str
    side_to_move: str
    castling: str
    en_passant: str
    valid: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "fen": self.fen,
            "normalized_fen": self.normalized_fen,
            "position_hash": self.position_hash,
            "placement": self.placement,
            "side_to_move": self.side_to_move,
            "castling": self.castling,
            "en_passant": self.en_passant,
            "valid": self.valid,
        }


def normalize_fen(fen: str) -> str:
    """The four identifying FEN fields: placement, side, castling, en passant."""
    return " ".join((fen or "").split()[:4])


def piece_placement(fen: str) -> str:
    """Just the placement field — the loosest useful structural key."""
    parts = (fen or "").split()
    return parts[0] if parts else ""


def position_hash(fen: str) -> str:
    """A stable 32-hex-character hash of the position identity.

    Deterministic across processes and runs (sha256 of the normalised FEN), so a
    stored hash is comparable to a freshly computed one. Returns the empty string
    for an empty/invalid input rather than hashing garbage.
    """
    normalized = normalize_fen(fen)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def fingerprint(fen: str) -> PositionFingerprint:
    """Decompose a FEN into its identity. Never raises on bad input."""
    raw = (fen or "").strip()
    normalized = normalize_fen(raw)
    placement = piece_placement(raw)
    fields = raw.split()
    side = fields[1] if len(fields) > 1 else ""
    castling = fields[2] if len(fields) > 2 else ""
    ep = fields[3] if len(fields) > 3 else ""
    if not raw:
        return PositionFingerprint(
            fen=raw,
            normalized_fen=normalized,
            position_hash="",
            placement=placement,
            side_to_move=side,
            castling=castling,
            en_passant=ep,
            valid=False,
        )
    try:
        board = chess.Board(raw)
        valid = board.status() == chess.STATUS_VALID
        if valid:
            normalized = " ".join(board.fen().split()[:4])
            placement = board.board_fen()
            side = "white" if board.turn == chess.WHITE else "black"
            castling = board.castling_xfen() if hasattr(board, "castling_xfen") else castling
            ep = chess.square_name(board.ep_square) if board.ep_square is not None else "-"
    except ValueError:
        valid = False
    return PositionFingerprint(
        fen=raw,
        normalized_fen=normalized,
        position_hash=position_hash(normalized) if normalized else "",
        placement=placement,
        side_to_move=side,
        castling=castling,
        en_passant=ep,
        valid=valid,
    )


def same_position(fen_a: str, fen_b: str) -> bool:
    """Whether two FENs denote the same position (identity, not similarity)."""
    a = normalize_fen(fen_a)
    b = normalize_fen(fen_b)
    return bool(a) and a == b


__all__ = [
    "START_FEN",
    "PositionFingerprint",
    "fingerprint",
    "normalize_fen",
    "piece_placement",
    "position_hash",
    "same_position",
]
