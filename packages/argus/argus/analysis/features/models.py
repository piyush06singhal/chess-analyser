"""Raw position features extracted from the board.

Raw features come straight from board state — no engine calls, no ML, no
derived aggregation. Derived ML features live in ``argus.ml.features``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RawPositionFeatures(BaseModel):
    """Raw, deterministic features of a single position."""

    # Material
    material_balance: int = Field(description="White material minus black material (centipawns)")
    material_white: int
    material_black: int

    # Mobility
    mobility_white: int = Field(description="Number of legal moves for white")
    mobility_black: int

    # King safety
    king_safety_white: int = Field(description="Pawn-shield pawn count in front of the white king")
    king_safety_black: int = Field(description="Pawn-shield pawn count in front of the black king")
    white_in_check: bool
    black_in_check: bool
    white_castled: bool
    black_castled: bool

    # Pawn structure (counts)
    isolated_pawns_white: int
    isolated_pawns_black: int
    doubled_pawns_white: int
    doubled_pawns_black: int
    passed_pawns_white: int
    passed_pawns_black: int
    backward_pawns_white: int
    backward_pawns_black: int

    # Center control
    center_occupied_white: int = Field(description="Center squares (d4,e4,d5,e5) occupied by white")
    center_occupied_black: int
    center_attacked_white: int = Field(description="Center squares attacked by white")
    center_attacked_black: int

    # Development
    undeveloped_pieces_white: int = Field(
        description="Knights/bishops/rooks/queen still on their original squares"
    )
    undeveloped_pieces_black: int

    # Tactical indicators
    hanging_pieces_white: int = Field(description="White pieces attacked and not defended")
    hanging_pieces_black: int = Field(description="Black pieces attacked and not defended")
    checkers: int = Field(description="Number of pieces giving check")

    # Phase inputs (raw characteristics consumed by the phase classifier)
    queens_on_board: bool
    minor_pieces_on_board: int
    major_pieces_on_board: int
    total_pieces: int
