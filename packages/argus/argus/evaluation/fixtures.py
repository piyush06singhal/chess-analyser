"""Versioned evaluation fixtures (§47/§48).

Everything here is a real, well-known chess record — the Opera Game, the Ruy
Lopez Breyer line, classic mate and stalemate positions — chosen so the expected
result can be checked against a reference (python-chess) rather than asserted
from memory. Fixtures are versioned; when a legitimate methodology change moves
an expected value, the fixture is updated deliberately and the reason recorded.

Nothing here is randomly generated. A benchmark that fails because its input
changed is worse than useless.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Bumped whenever a fixture's content or its expected value changes.
FIXTURE_VERSION = "14.0"

STARTPOS_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

# Real, well-documented games (public domain / standard game record).
OPERA_GAME_PGN = """[Event "Paris Opera House"]
[Site "Paris FRA"]
[Date "1858.11.02"]
[Round "?"]
[Result "1-0"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[ECO "C41"]
[TimeControl "600+5"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
"""

RUY_BREYER_PGN = """[Event "Ruy Lopez Breyer (theory line)"]
[Result "*"]
[ECO "C95"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6
8. c3 O-O 9. h3 Nb8 10. d4 Nbd7 11. Nbd2 Bb7 12. Bc2 Re8 13. Nf1 Bf8
14. Ng3 g6 15. b3 Bg7 *
"""

SCHOLARS_MATE_PGN = """[Event "Scholar's mate"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""

CASTLING_PGN = """[Event "Castling test"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O Nf6 5. d3 O-O *
"""

EN_PASSANT_PGN = """[Event "En passant test"]
[Result "*"]

1. e4 Nf6 2. e5 d5 3. exd6 *
"""

PROMOTION_PGN = """[Event "Promotion test"]
[Result "*"]

1. a4 b5 2. axb5 a6 3. bxa6 Bb7 4. axb7 Nc6 5. bxa8=Q *
"""

CHECK_PGN = """[Event "Check test"]
[Result "*"]

1. e4 d5 2. Bb5+ c6 *
"""

ANNOTATED_PGN = """[Event "Annotated short game"]
[Result "0-1"]
[White "Annotator"]
[Black "Annotator"]

1. e4 {the king's pawn} e5 $1 2. Nf3 Nc6 3. Bc4 (3. Bb5 a6 {Morphy}) 3... Nf6
4. Ng5 d5 5. exd5 Nxd5?? {a blunder} 6. Nxf7 Kxf7 0-1
"""

DRAW_PGN = """[Event "Drawn test"]
[Result "1/2-1/2"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1/2-1/2
"""

# Threefold repetition: the knights shuffle and the start position recurs.
REPETITION_PGN = """[Event "Repetition test"]
[Result "1/2-1/2"]

1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 Nf6 4. Ng1 Ng8 1/2-1/2
"""

# 3. Nf6 is illegal: a knight on f3 cannot reach f6.
ILLEGAL_MOVE_PGN = """[Event "Illegal move"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Nf6 *
"""

HEADERS_ONLY_PGN = """[Event "Headers only"]
[White "Nobody"]
[Black "Nobody"]
[Result "*"]
"""

CORRUPTED_PGN = "this is not a chess game at all"

MULTI_GAME_PGN = SCHOLARS_MATE_PGN + "\n\n" + CASTLING_PGN


# --- FEN validation (§6) ------------------------------------------------------


@dataclass(frozen=True)
class FenCase:
    """A FEN with the expected validation outcome."""

    name: str
    fen: str
    valid: bool
    #: A substring expected in at least one error, when ``valid`` is False.
    expect_error_contains: str | None = None
    category: str = "general"


FEN_CASES: tuple[FenCase, ...] = (
    FenCase("startpos", STARTPOS_FEN, True, category="valid"),
    FenCase(
        "after_e4",
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        True,
        category="valid",
    ),
    FenCase(
        "endgame_kings",
        "8/8/8/4k3/8/8/8/4K3 w - - 0 1",
        True,
        category="valid",
    ),
    FenCase("empty_fen", "", False, "FEN is empty", category="malformed"),
    FenCase("garbage", "not a fen", False, "Invalid FEN", category="malformed"),
    FenCase("too_few_fields", "8/8/8/8/8/8/8/8 w - -", False, "empty", category="malformed"),
    FenCase("empty_board", "8/8/8/8/8/8/8/8 w - - 0 1", False, "empty", category="impossible"),
    FenCase(
        "missing_black_king",
        "8/8/8/8/8/8/8/4K3 w - - 0 1",
        False,
        "king",
        category="impossible",
    ),
    FenCase(
        "too_many_white_pawns",
        "4k3/pppppppp/pppppppp/8/8/8/8/4K3 w - - 0 1",
        False,
        "pawn",
        category="impossible",
    ),
    FenCase(
        "pawn_on_back_rank",
        "4k3/8/8/8/8/8/8/P3K3 w - - 0 1",
        False,
        "back rank",
        category="impossible",
    ),
    # White to move, but the *black* king is in check — the side not to move
    # cannot be in check.
    FenCase(
        "side_not_to_move_in_check",
        "4k3/8/8/8/8/8/4R3/4K3 w - - 0 1",
        False,
        "check",
        category="impossible",
    ),
)


# --- Position properties (§5) -------------------------------------------------


@dataclass(frozen=True)
class PositionCase:
    """A position with checkable expected properties.

    ``expected`` keys the suite understands:

    * ``side_to_move``: ``"w"`` / ``"b"``
    * ``legal_move_count``: exact number of legal moves
    * ``is_check`` / ``is_checkmate`` / ``is_stalemate`` / ``is_game_over``: bool
    * ``is_insufficient_material``: bool
    * ``can_claim_fifty_moves``: bool
    * ``legal_san_includes``: SANs that must validate
    * ``validate_san_refuses``: SANs that must be refused
    * ``no_legal_moves_from``: squares with zero legal moves (e.g. a pinned knight)
    """

    name: str
    fen: str
    expected: dict = field(default_factory=dict)
    category: str = "general"


POSITION_CASES: tuple[PositionCase, ...] = (
    PositionCase(
        "startpos",
        STARTPOS_FEN,
        {
            "side_to_move": "w",
            "legal_move_count": 20,
            "is_check": False,
            "is_game_over": False,
        },
        category="opening",
    ),
    PositionCase(
        "stalemate",
        "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1",
        {"is_stalemate": True, "legal_move_count": 0, "is_game_over": True, "is_check": False},
        category="endgame",
    ),
    PositionCase(
        "checkmate",
        "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1",
        {"is_checkmate": True, "legal_move_count": 0, "is_game_over": True, "is_check": True},
        category="endgame",
    ),
    # Black knight d7 is pinned to the king on e8 by the bishop on b5, with the
    # diagonal b5–c6–d7–e8 otherwise empty, so the knight has no legal move.
    PositionCase(
        "pinned_knight",
        "4k3/3n4/8/1B6/8/8/8/4K3 b - - 0 1",
        {"no_legal_moves_from": ["d7"]},
        category="pin",
    ),
    PositionCase(
        "castling_both_sides",
        "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
        {"legal_san_includes": ["O-O", "O-O-O"]},
        category="castling",
    ),
    PositionCase(
        "castling_rights_absent",
        "r3k2r/8/8/8/8/8/8/R3K2R w - - 0 1",
        {"validate_san_refuses": ["O-O", "O-O-O"]},
        category="castling",
    ),
    PositionCase(
        "en_passant_available",
        "rnbqkbnr/ppp1p1pp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3",
        {"legal_san_includes": ["exd6"]},
        category="en_passant",
    ),
    PositionCase(
        "promotion_available",
        "8/4P3/8/8/8/8/8/4K1k1 w - - 0 1",
        {"legal_san_includes": ["e8=Q", "e8=R", "e8=B", "e8=N"]},
        category="promotion",
    ),
    PositionCase(
        "insufficient_material",
        "8/8/8/4k3/8/8/8/4K3 w - - 0 1",
        {"is_insufficient_material": True},
        category="endgame",
    ),
    PositionCase(
        "fifty_move_claimable",
        "8/8/8/4k3/8/8/8/4K3 w - - 100 200",
        {"can_claim_fifty_moves": True},
        category="endgame",
    ),
    PositionCase(
        "back_rank_mate_in_one",
        "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1",
        {"legal_san_includes": ["Ra8#"]},
        category="tactics",
    ),
)


__all__ = [
    "ANNOTATED_PGN",
    "CASTLING_PGN",
    "CHECK_PGN",
    "CORRUPTED_PGN",
    "DRAW_PGN",
    "EN_PASSANT_PGN",
    "FEN_CASES",
    "FIXTURE_VERSION",
    "HEADERS_ONLY_PGN",
    "ILLEGAL_MOVE_PGN",
    "MULTI_GAME_PGN",
    "OPERA_GAME_PGN",
    "POSITION_CASES",
    "PROMOTION_PGN",
    "REPETITION_PGN",
    "RUY_BREYER_PGN",
    "SCHOLARS_MATE_PGN",
    "STARTPOS_FEN",
    "FenCase",
    "PositionCase",
]
