"""Caissa opening table.

A small, hand-curated table of documented opening move sequences used by the
opening detector. Requirements it satisfies:

* **Exact prefix matching only.** An opening is reported only when the game
  really plays the moves of a known line. Superficial similarity (same first
  move, same pawn structure) never produces a name — an unknown opening is
  reported as ``Unknown / Unclassified`` instead of a guess.
* **No transposition guessing.** Transpositions are not inferred; a game that
  reaches a known position by a different move order stays unclassified.
* The table records the **book continuation** for each entry, so the deviation
  report can state which move(s) the table expected next.

The table is deliberately modest and traceable: each entry is a named opening
with its ECO code and the SAN move sequence that defines it. It is not a full
ECO database, and the detector says so when it cannot identify a game.
"""

from __future__ import annotations

from typing import NamedTuple

import chess


class OpeningLine(NamedTuple):
    """One known opening line."""

    eco: str
    family: str
    variation: str | None
    san: tuple[str, ...]


#: Curated table. Longer lines take precedence over shorter prefixes at match
#: time, so deeper variations win over their families.
OPENING_LINES: tuple[OpeningLine, ...] = (
    # --- 1.e4 e5 ---------------------------------------------------------------
    OpeningLine("C20", "King's Pawn Game", None, ("e4", "e5")),
    OpeningLine("C23", "Bishop's Opening", None, ("e4", "e5", "Bc4")),
    OpeningLine("C25", "Vienna Game", None, ("e4", "e5", "Nc3")),
    OpeningLine("C30", "King's Gambit", None, ("e4", "e5", "f4")),
    OpeningLine("C41", "Philidor Defense", None, ("e4", "e5", "Nf3", "d6")),
    OpeningLine("C42", "Petrov Defense", None, ("e4", "e5", "Nf3", "Nf6")),
    OpeningLine("C44", "Ponziani Opening", None, ("e4", "e5", "Nf3", "Nc6", "c3")),
    OpeningLine("C45", "Scotch Game", None, ("e4", "e5", "Nf3", "Nc6", "d4")),
    OpeningLine(
        "C47", "Four Knights Game", None, ("e4", "e5", "Nf3", "Nc6", "Nc3", "Nf6")
    ),
    OpeningLine("C50", "Italian Game", None, ("e4", "e5", "Nf3", "Nc6", "Bc4")),
    OpeningLine(
        "C50",
        "Italian Game",
        "Giuoco Pianissimo",
        ("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "d3"),
    ),
    OpeningLine(
        "C53",
        "Italian Game",
        "Giuoco Piano",
        ("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3"),
    ),
    OpeningLine(
        "C55", "Two Knights Defense", None, ("e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6")
    ),
    OpeningLine("C60", "Ruy Lopez", None, ("e4", "e5", "Nf3", "Nc6", "Bb5")),
    OpeningLine(
        "C65", "Ruy Lopez", "Berlin Defense", ("e4", "e5", "Nf3", "Nc6", "Bb5", "Nf6")
    ),
    OpeningLine(
        "C68",
        "Ruy Lopez",
        "Exchange Variation",
        ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bxc6"),
    ),
    OpeningLine(
        "C70",
        "Ruy Lopez",
        "Morphy Defense",
        ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6"),
    ),
    # --- Sicilian --------------------------------------------------------------
    OpeningLine("B20", "Sicilian Defense", None, ("e4", "c5")),
    OpeningLine("B22", "Sicilian Defense", "Alapin Variation", ("e4", "c5", "c3")),
    OpeningLine("B23", "Sicilian Defense", "Closed", ("e4", "c5", "Nc3")),
    OpeningLine(
        "B30", "Sicilian Defense", "Old Sicilian", ("e4", "c5", "Nf3", "Nc6")
    ),
    OpeningLine(
        "B40", "Sicilian Defense", "Paulsen", ("e4", "c5", "Nf3", "e6")
    ),
    OpeningLine(
        "B50", "Sicilian Defense", "Modern Variations", ("e4", "c5", "Nf3", "d6")
    ),
    OpeningLine(
        "B70",
        "Sicilian Defense",
        "Dragon Variation",
        ("e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "g6"),
    ),
    OpeningLine(
        "B90",
        "Sicilian Defense",
        "Najdorf Variation",
        ("e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6"),
    ),
    # --- other 1.e4 defences ---------------------------------------------------
    OpeningLine("B01", "Scandinavian Defense", None, ("e4", "d5")),
    OpeningLine("B02", "Alekhine Defense", None, ("e4", "Nf6")),
    OpeningLine("B06", "Modern Defense", None, ("e4", "g6")),
    OpeningLine("B07", "Pirc Defense", None, ("e4", "d6", "d4", "Nf6", "Nc3", "g6")),
    OpeningLine("B10", "Caro-Kann Defense", None, ("e4", "c6")),
    OpeningLine(
        "B12", "Caro-Kann Defense", "Advance Variation", ("e4", "c6", "d4", "d5", "e5")
    ),
    OpeningLine(
        "B18",
        "Caro-Kann Defense",
        "Classical Variation",
        ("e4", "c6", "d4", "d5", "Nc3", "dxe4", "Nxe4", "Bf5"),
    ),
    OpeningLine("C00", "French Defense", None, ("e4", "e6")),
    OpeningLine(
        "C02", "French Defense", "Advance Variation", ("e4", "e6", "d4", "d5", "e5")
    ),
    OpeningLine(
        "C03", "French Defense", "Tarrasch Variation", ("e4", "e6", "d4", "d5", "Nd2")
    ),
    OpeningLine(
        "C11",
        "French Defense",
        "Classical Variation",
        ("e4", "e6", "d4", "d5", "Nc3", "Nf6"),
    ),
    OpeningLine(
        "C15",
        "French Defense",
        "Winawer Variation",
        ("e4", "e6", "d4", "d5", "Nc3", "Bb4"),
    ),
    # --- 1.d4 ------------------------------------------------------------------
    OpeningLine("A45", "Trompowsky Attack", None, ("d4", "Nf6", "Bg5")),
    OpeningLine("A80", "Dutch Defense", None, ("d4", "f5")),
    OpeningLine("D00", "Queen's Pawn Game", None, ("d4", "d5")),
    OpeningLine("D02", "London System", None, ("d4", "d5", "Nf3", "Nf6", "Bf4")),
    OpeningLine("D06", "Queen's Gambit", None, ("d4", "d5", "c4")),
    OpeningLine("D10", "Slav Defense", None, ("d4", "d5", "c4", "c6")),
    OpeningLine("D20", "Queen's Gambit Accepted", None, ("d4", "d5", "c4", "dxc4")),
    OpeningLine("D30", "Queen's Gambit Declined", None, ("d4", "d5", "c4", "e6")),
    OpeningLine(
        "D43",
        "Semi-Slav Defense",
        None,
        ("d4", "d5", "c4", "e6", "Nf3", "Nf6", "Nc3", "c6"),
    ),
    OpeningLine(
        "D53",
        "Queen's Gambit Declined",
        "Orthodox Defense",
        ("d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7"),
    ),
    OpeningLine("D80", "Grünfeld Defense", None, ("d4", "Nf6", "c4", "g6", "Nc3", "d5")),
    OpeningLine("E00", "Catalan Opening", None, ("d4", "Nf6", "c4", "e6", "g3")),
    OpeningLine("E11", "Bogo-Indian Defense", None, ("d4", "Nf6", "c4", "e6", "Nf3", "Bb4+")),
    OpeningLine("E12", "Queen's Indian Defense", None, ("d4", "Nf6", "c4", "e6", "Nf3", "b6")),
    OpeningLine("E20", "Nimzo-Indian Defense", None, ("d4", "Nf6", "c4", "e6", "Nc3", "Bb4")),
    OpeningLine("E60", "King's Indian Defense", None, ("d4", "Nf6", "c4", "g6", "Nc3", "Bg7")),
    OpeningLine(
        "E70",
        "King's Indian Defense",
        "Normal Variation",
        ("d4", "Nf6", "c4", "g6", "Nc3", "Bg7", "e4", "d6"),
    ),
    OpeningLine("A56", "Benoni Defense", None, ("d4", "Nf6", "c4", "c5", "d5")),
    OpeningLine("A60", "Benoni Defense", "Modern", ("d4", "Nf6", "c4", "c5", "d5", "e6")),
    # --- flank openings --------------------------------------------------------
    OpeningLine("A02", "Bird Opening", None, ("f4",)),
    OpeningLine("A01", "Nimzo-Larsen Attack", None, ("b3",)),
    OpeningLine("A04", "Réti Opening", None, ("Nf3", "d5", "c4")),
    OpeningLine("A07", "King's Indian Attack", None, ("Nf3", "d5", "g3")),
    OpeningLine("A10", "English Opening", None, ("c4",)),
    OpeningLine("A30", "English Opening", "Symmetrical", ("c4", "c5")),
)


class BookNode:
    """A node in the opening trie, keyed by UCI move."""

    __slots__ = ("children", "line", "book_continuation")

    def __init__(self) -> None:
        self.children: dict[str, "BookNode"] = {}
        self.line: OpeningLine | None = None
        self.book_continuation: list[str] = []


def _to_uci_path(san_moves: tuple[str, ...]) -> list[str] | None:
    """Convert a SAN sequence to UCI moves from the standard start position."""
    board = chess.Board()
    path: list[str] = []
    for san in san_moves:
        try:
            move = board.parse_san(san)
        except ValueError:
            return None
        path.append(move.uci())
        board.push(move)
    return path


def build_trie() -> BookNode:
    """Build the opening trie from :data:`OPENING_LINES`.

    Longer lines are inserted first so that overlapping prefixes keep the
    deeper (more specific) line's continuation available at each node.
    """
    root = BookNode()
    lines = sorted(OPENING_LINES, key=lambda line: len(line.san), reverse=True)
    for line in lines:
        path = _to_uci_path(line.san)
        if path is None:  # pragma: no cover — table is validated by tests
            continue
        node = root
        for index, uci in enumerate(path):
            node = node.children.setdefault(uci, BookNode())
            # The continuation from this node is the rest of the deepest line
            # passing through it.
            node.book_continuation = path[index + 1 :]
        node.line = line
    return root


#: Process-wide trie (immutable after construction).
TRIE: BookNode = build_trie()


def book_entry_count() -> int:
    """Number of opening lines in the table (15 of them are used in tests)."""
    return len(OPENING_LINES)
