"""Opening identification and deviation tracking.

The detector walks the real move sequence against the curated Caissa opening
table (:mod:`argus.intelligence.opening_book`) and reports:

* the **family**, **variation** and **ECO code** of the deepest known line the
  game actually played,
* the **deviation point** — the ply where the game left the table,
* the table's **expected continuation** from the last matched position.

Two honesty rules are enforced in code:

1. A name is only produced from a real move-sequence match. If nothing matches,
   the result is ``Unknown / Unclassified`` with the reason recorded; PGN header
   metadata (when present) is reported separately as header information.
2. **Opening deviation is not a chess error.** The deviation model carries no
   severity, no classification and no judgement — leaving a known line is a
   factual event, and whether it was good or bad is measured elsewhere (by the
   engine evaluation), never inferred here.
"""

from __future__ import annotations

from typing import Literal

import chess
from pydantic import BaseModel, Field

from argus.chess_core.models import Color
from argus.intelligence.base import GameContext, MoveFact
from argus.intelligence.opening_book import BookNode, OPENING_LINES, TRIE

MAX_EXPECTED_CONTINUATION = 4


class OpeningIdentification(BaseModel):
    """What (if anything) Caissa can say about the opening played."""

    source: Literal["move_sequence", "pgn_header", "unclassified"] = "unclassified"
    eco: str | None = None
    family: str | None = None
    variation: str | None = None
    name: str | None = Field(default=None, description="Display name, e.g. 'Italian Game'")
    matched_plies: int = 0
    matched_san: list[str] = Field(default_factory=list)
    header_eco: str | None = None
    header_name: str | None = None
    header_agreement: bool | None = Field(
        default=None,
        description="True/False when both a move-sequence match and a header ECO exist",
    )
    table_lines: int = 0
    note: str | None = None


class OpeningDeviation(BaseModel):
    """Where the game left the known opening table (a fact, not a judgement)."""

    deviated: bool = False
    ply: int | None = None
    move_number: int | None = None
    side: Color | None = None
    played_san: str | None = None
    last_book_ply: int = 0
    last_book_fen: str | None = None
    expected_continuation_san: list[str] = Field(default_factory=list)
    expected_continuation_uci: list[str] = Field(default_factory=list)
    note: str = ""


class OpeningAnalysis(BaseModel):
    """Structured opening section of the game report."""

    identification: OpeningIdentification
    deviation: OpeningDeviation
    book_lines: int = 0


def _display_name(family: str, variation: str | None) -> str:
    return family if not variation else f"{family}: {variation}"


def _uci_to_san(fen: str, uci_moves: list[str], limit: int = MAX_EXPECTED_CONTINUATION) -> list[str]:
    """Convert UCI continuation moves to SAN from a position (truncating safely)."""
    board = chess.Board(fen)
    san: list[str] = []
    for uci in uci_moves[:limit]:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        san.append(board.san(move))
        board.push(move)
    return san


def detect_opening(moves: list[MoveFact], context: GameContext) -> OpeningAnalysis:
    """Identify the opening and the deviation point from a game's move sequence."""
    header_eco = context.eco_code
    header_name = context.opening_name

    def unclassified(note: str) -> OpeningAnalysis:
        return OpeningAnalysis(
            identification=OpeningIdentification(
                source="pgn_header" if header_eco or header_name else "unclassified",
                eco=header_eco,
                name=header_name,
                header_eco=header_eco,
                header_name=header_name,
                table_lines=len(OPENING_LINES),
                note=note,
            ),
            deviation=OpeningDeviation(
                deviated=bool(moves),
                ply=1 if moves else None,
                move_number=moves[0].move_number if moves else None,
                side=moves[0].mover if moves else None,
                played_san=moves[0].san if moves else None,
                note=(
                    "The opening could not be identified from the move sequence. "
                    "An unclassified opening is reported instead of a guess."
                ),
            ),
            book_lines=len(OPENING_LINES),
        )

    if context.initial_position and context.initial_position != chess.STARTING_FEN:
        return unclassified(
            "The game does not start from the standard initial position, so the "
            "opening table (which is defined from the start position) does not apply."
        )
    if not moves:
        return unclassified("The game has no moves to match against the opening table.")

    node: BookNode = TRIE
    last_line_node: BookNode | None = None
    matched_plies = 0
    matched_san: list[str] = []
    deviation_fact: MoveFact | None = None

    for move in moves:
        child = node.children.get(move.uci)
        if child is None:
            deviation_fact = move
            break
        node = child
        matched_plies += 1
        matched_san.append(move.san)
        if node.line is not None:
            last_line_node = node

    last_book_ply = matched_plies
    last_book_fen = (
        moves[last_book_ply - 1].fen_after if last_book_ply > 0 else context.initial_position
    )
    expected_uci = list(last_line_node.book_continuation) if last_line_node else []
    expected_san = (
        _uci_to_san(last_book_fen, expected_uci) if expected_uci and last_book_fen else []
    )

    if last_line_node is None or last_line_node.line is None:
        if matched_plies == 0:
            return unclassified(
                f"{moves[0].san} is not part of any line in the Caissa opening table, so no "
                "opening is claimed. An unclassified opening is reported instead of a guess."
            )
        # Some moves matched a prefix but no named line terminated there.
        return unclassified(
            f"The first {matched_plies} move(s) matched the Caissa table but did not reach a "
            "named line, so no opening name is reported. An unclassified opening is preferred "
            "over a guess."
        )

    line = last_line_node.line
    identification = OpeningIdentification(
        source="move_sequence",
        eco=line.eco,
        family=line.family,
        variation=line.variation,
        name=_display_name(line.family, line.variation),
        matched_plies=matched_plies,
        matched_san=matched_san,
        header_eco=header_eco,
        header_name=header_name,
        header_agreement=(
            None if not header_eco else header_eco.strip().upper().startswith(line.eco[:3])
        ),
        table_lines=len(OPENING_LINES),
    )

    if deviation_fact is None:
        deviation = OpeningDeviation(
            deviated=False,
            last_book_ply=last_book_ply,
            last_book_fen=last_book_fen,
            expected_continuation_san=expected_san,
            expected_continuation_uci=expected_uci[:MAX_EXPECTED_CONTINUATION],
            note=(
                "The game stayed inside the Caissa opening table for its whole length; "
                "no deviation from a known line was observed."
            ),
        )
    else:
        deviation = OpeningDeviation(
            deviated=True,
            ply=deviation_fact.ply,
            move_number=deviation_fact.move_number,
            side=deviation_fact.mover,
            played_san=deviation_fact.san,
            last_book_ply=last_book_ply,
            last_book_fen=last_book_fen,
            expected_continuation_san=expected_san,
            expected_continuation_uci=expected_uci[:MAX_EXPECTED_CONTINUATION],
            note="Leaving a known line is a factual event; it is not by itself a mistake.",
        )

    return OpeningAnalysis(
        identification=identification, deviation=deviation, book_lines=len(OPENING_LINES)
    )
