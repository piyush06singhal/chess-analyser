"""Opening knowledge: a small, versioned, curated base — and honest ignorance.

Opening names are a place where language models fail in a very specific way: they
produce a confident, plausible, wrong name ("the Sicilian Defence" for 1.d4 d5).
The fix is not a better prompt; it is to make the name come from a stored table and
to return ``unknown`` when the table has no match (spec §20).

The base is deliberately tiny and entirely factual — the ECO codes each opening
begins with, its defining move sequence, and the standard name. It is versioned so
that a renamed or extended entry is a new version rather than a silent change. It is
not a theory database, and it does not contain evaluation claims: "this is the
Sicilian" is a lookup, while "the Sicilian is good for Black" is a chess opinion
that needs its own evidence.

Values come from the standard public ECO naming conventions. Nothing here is
scraped from copyrighted material (spec §19).
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders
from argus.shared.errors import NotFoundError

#: Bumped whenever the table changes, so an answer can be traced to a version.
OPENING_BASE_VERSION = "7.0"

#: (name, eco, first moves in UCI). Matched as the longest prefix of the game's
#: moves, so the most specific entry wins.
_OPENINGS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Sicilian Defence, Najdorf Variation", "B90", ("e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4", "f3d4", "g8f6", "b1c3", "a7a6")),
    ("Sicilian Defence, Dragon Variation", "B70", ("e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4", "f3d4", "g8f6", "b1c3", "g7g6")),
    ("Sicilian Defence, Accelerated Dragon", "B34", ("e2e4", "c7c5", "g1f3", "b8c6", "d2d4", "c5d4", "f3d4", "g7g6")),
    ("Sicilian Defence, Sveshnikov Variation", "B33", ("e2e4", "c7c5", "g1f3", "b8c6", "d2d4", "c5d4", "f3d4", "g8f6", "b1c3", "e7e5")),
    ("Sicilian Defence, Closed", "B23", ("e2e4", "c7c5", "b1c3",)),
    ("Sicilian Defence", "B20", ("e2e4", "c7c5",)),
    ("French Defence, Winawer Variation", "C15", ("e2e4", "e7e6", "d2d4", "d7d5", "b1c3", "f8b4")),
    ("French Defence, Tarrasch Variation", "C03", ("e2e4", "e7e6", "d2d4", "d7d5", "b1d2",)),
    ("French Defence, Advance Variation", "C02", ("e2e4", "e7e6", "d2d4", "d7d5", "e4e5",)),
    ("French Defence, Exchange Variation", "C01", ("e2e4", "e7e6", "d2d4", "d7d5", "e4d5",)),
    ("French Defence", "C00", ("e2e4", "e7e6",)),
    ("Caro-Kann Defence, Advance Variation", "B12", ("e2e4", "c7c6", "d2d4", "d7d5", "e4e5",)),
    ("Caro-Kann Defence, Classical Variation", "B18", ("e2e4", "c7c6", "d2d4", "d7d5", "b1c3", "d5e4", "c3e4", "c8f5")),
    ("Caro-Kann Defence, Exchange Variation", "B13", ("e2e4", "c7c6", "d2d4", "d7d5", "e4d5",)),
    ("Caro-Kann Defence", "B10", ("e2e4", "c7c6",)),
    ("Scandinavian Defence", "B01", ("e2e4", "d7d5",)),
    ("Alekhine Defence", "B02", ("e2e4", "g8f6",)),
    ("Pirc Defence", "B07", ("e2e4", "d7d6", "d2d4", "g8f6", "b1c3",)),
    ("Modern Defence", "B06", ("e2e4", "g7g6",)),
    ("King's Gambit", "C30", ("e2e4", "e7e5", "f2f4",)),
    ("Vienna Game", "C25", ("e2e4", "e7e5", "b1c3",)),
    ("Italian Game, Two Knights Defence", "C55", ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6")),
    ("Italian Game, Giuoco Piano", "C50", ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5")),
    ("Italian Game", "C50", ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4",)),
    ("Ruy Lopez, Berlin Defence", "C65", ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "g8f6")),
    ("Ruy Lopez, Morphy Defence", "C78", ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6")),
    ("Ruy Lopez, Exchange Variation", "C68", ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5c6")),
    ("Ruy Lopez", "C60", ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5",)),
    ("Petrov Defence", "C42", ("e2e4", "e7e5", "g1f3", "g8f6")),
    ("Scotch Game", "C45", ("e2e4", "e7e5", "g1f3", "b8c6", "d2d4",)),
    ("Four Knights Game", "C47", ("e2e4", "e7e5", "g1f3", "b8c6", "b1c3", "g8f6")),
    ("King's Knight Opening", "C40", ("e2e4", "e7e5", "g1f3",)),
    ("Open Game", "C20", ("e2e4", "e7e5",)),
    ("Queen's Gambit Declined, Slav Defence", "D10", ("d2d4", "d7d5", "c2c4", "c7c6")),
    ("Queen's Gambit Declined, Exchange Variation", "D35", ("d2d4", "d7d5", "c2c4", "e7e6", "b1c3", "g8f6", "c4d5")),
    ("Queen's Gambit Declined", "D30", ("d2d4", "d7d5", "c2c4", "e7e6")),
    ("Queen's Gambit Accepted", "D20", ("d2d4", "d7d5", "c2c4", "d5c4")),
    ("Queen's Gambit", "D06", ("d2d4", "d7d5", "c2c4",)),
    ("Slav Defence", "D10", ("d2d4", "d7d5", "c2c4", "c7c6")),
    ("London System", "D02", ("d2d4", "d7d5", "g1f3", "g8f6", "c1f4",)),
    ("Nimzo-Indian Defence", "E20", ("d2d4", "g8f6", "c2c4", "e7e6", "b1c3", "f8b4")),
    ("Queen's Indian Defence", "E12", ("d2d4", "g8f6", "c2c4", "e7e6", "g1f3", "b7b6")),
    ("King's Indian Defence, Classical", "E90", ("d2d4", "g8f6", "c2c4", "g7g6", "b1c3", "f8g7", "e2e4", "d7d6", "g1f3")),
    ("King's Indian Defence", "E60", ("d2d4", "g8f6", "c2c4", "g7g6",)),
    ("Grünfeld Defence", "D80", ("d2d4", "g8f6", "c2c4", "g7g6", "b1c3", "d7d5")),
    ("Catalan Opening", "E00", ("d2d4", "g8f6", "c2c4", "e7e6", "g2g3",)),
    ("Dutch Defence", "A80", ("d2d4", "f7f5",)),
    ("English Opening", "A10", ("c2c4",)),
    ("Réti Opening", "A04", ("g1f3",)),
    ("Bird Opening", "A02", ("f2f4",)),
    ("King's Pawn Game", "C20", ("e2e4",)),
    ("Queen's Pawn Game", "A40", ("d2d4",)),
)


def _normalize(sequence: list[str] | None, moves_text: str | None) -> list[str]:
    if sequence:
        return [str(move).strip().lower() for move in sequence]
    if moves_text:
        return [token.strip().lower() for token in moves_text.split() if token.strip()]
    return []


def match_opening(moves: list[str]) -> dict[str, Any]:
    """Longest-prefix match against the curated base."""
    best: tuple[int, str, str, tuple[str, ...]] | None = None
    for name, eco, line in _OPENINGS:
        if len(line) > len(moves):
            continue
        if moves[: len(line)] != list(line):
            continue
        if best is None or len(line) > best[0]:
            best = (len(line), name, eco, line)
    if best is None:
        return {
            "matched": False,
            "name": None,
            "eco": None,
            "matched_plies": 0,
            "base_version": OPENING_BASE_VERSION,
        }
    length, name, eco, line = best
    return {
        "matched": True,
        "name": name,
        "eco": eco,
        "line_uci": list(line),
        "line_plies": length,
        "matched_plies": length,
        "base_version": OPENING_BASE_VERSION,
    }


def build_opening_tools(providers: AgentProviders) -> list[Tool]:
    """The opening knowledge tool family."""

    def _game_opening(context: AgentContext) -> dict[str, Any] | None:
        if providers.game_lookup is not None and context.active_game_id:
            game = providers.game_lookup(context.active_game_id)
            if game:
                return {
                    "name": game.get("opening_name"),
                    "eco": game.get("eco_code"),
                    "source": "stored game record",
                }
        return None

    def get_opening_information(
        context: AgentContext,
        moves: str | None = None,
        moves_uci: list[str] | None = None,
        game_id: str | None = None,
    ) -> dict[str, Any]:
        sequence = _normalize(moves_uci, moves)
        if not sequence and (game_id or context.active_game_id):
            resolved = game_id or context.active_game_id
            if providers.game_moves is not None and resolved:
                rows = providers.game_moves(resolved)
                if rows:
                    sequence = [
                        str(row.get("uci")).lower()
                        for row in rows
                        if row.get("uci")
                    ]
        if not sequence:
            stored = _game_opening(context)
            if stored and stored.get("name"):
                return {
                    "matched": True,
                    "name": stored["name"],
                    "eco": stored["eco"],
                    "source": stored["source"],
                    "base_version": OPENING_BASE_VERSION,
                    "note": "Taken from the stored game record, not re-derived.",
                }
            raise NotFoundError(
                "No move sequence or opening was supplied, and Caissa stores no opening "
                "name for the active game. Caissa will not guess an opening name."
            )
        matched = match_opening(sequence)
        stored = _game_opening(context)
        matched["source"] = f"curated opening base v{OPENING_BASE_VERSION}"
        if stored and stored.get("name"):
            matched["stored_game_record"] = {
                "name": stored["name"],
                "eco": stored["eco"],
                "agrees": stored["name"] == matched.get("name"),
            }
        if not matched["matched"]:
            matched["note"] = (
                "No entry in the Caissa opening base matches this move sequence. The "
                "opening is unknown to Caissa; do not name it."
            )
        return matched

    return [
        Tool(
            name="get_opening_information",
            description=(
                "Look up the opening for a move sequence (UCI list or space-separated "
                "string) or for the active game. Returns the name and ECO code from a "
                "curated, versioned table, or matched=false when the base has no entry. "
                "Never state an opening name this tool did not return."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "moves": {"type": "string", "minLength": 1},
                        "moves_uci": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "game_id": {"type": "string", "minLength": 1},
                    },
                    "required": [],
                },
                outputs=("matched", "name", "eco", "line_uci", "base_version"),
            ),
            permission=ToolPermission.ANY,
            handler=get_opening_information,
            tags=("opening", "knowledge"),
        )
    ]


__all__ = [
    "OPENING_BASE_VERSION",
    "build_opening_tools",
    "match_opening",
]
