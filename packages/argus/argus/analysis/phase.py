"""Game-phase classification (opening / middlegame / endgame).

Board-state based, not move-number based: the phase of each position follows
from material characteristics and development state. Thresholds are
configurable starting heuristics that can later be refined with data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import chess

from argus.analysis.features.extractor import extract_position_features, phase_piece_material
from argus.chess_core.models import GameMove


class GamePhase(str, Enum):
    """Phase of the game for a single position."""

    OPENING = "opening"
    MIDDLEGAME = "middlegame"
    ENDGAME = "endgame"


@dataclass(frozen=True)
class PhaseThresholds:
    """Board-state boundaries for phase classification (configurable).

    - ``endgame_max_phase_material``: combined (both sides) non-pawn/non-king
      material points (N/B=3, R=5, Q=9) at or below which the position is
      considered an endgame. The standard chess start is 62 points
      (31 per side: Q=9, R=5+5, N=3+3, B=3+3).
    - ``opening_min_undeveloped_total``: minimum combined undeveloped pieces
      (both sides) for a position to still count as opening.
    """

    endgame_max_phase_material: int = 14
    opening_min_undeveloped_total: int = 3


def classify_position(
    board: chess.Board, thresholds: PhaseThresholds | None = None
) -> GamePhase:
    """Classify the phase of a position from board characteristics."""
    limits = thresholds or PhaseThresholds()
    features = extract_position_features(board)
    phase_material = phase_piece_material(board)
    undeveloped_total = features.undeveloped_pieces_white + features.undeveloped_pieces_black

    if phase_material <= limits.endgame_max_phase_material:
        return GamePhase.ENDGAME
    if undeveloped_total >= limits.opening_min_undeveloped_total:
        return GamePhase.OPENING
    return GamePhase.MIDDLEGAME


def classify_position_fen(fen: str, thresholds: PhaseThresholds | None = None) -> GamePhase:
    """Classify the phase of a position given as a FEN string."""
    return classify_position(chess.Board(fen), thresholds)


def classify_game_moves(
    moves: list[GameMove], thresholds: PhaseThresholds | None = None
) -> list[GamePhase]:
    """Classify the phase after every move of a game (by ``fen_after``)."""
    return [classify_position_fen(move.fen_after, thresholds) for move in moves]
