"""Chess knowledge retrieval: a small curated base, versioned and searchable.

Most "explain the concept" questions are about a fixed, small vocabulary: a fork, a
pin, an outpost, opposition, prophylaxis. That vocabulary is exactly where a
language model is *usually* right — and exactly where being subtly wrong is most
damaging, because the user learns the wrong idea and keeps it.

So the concepts live in a versioned table here, and `search_chess_knowledge`
returns them verbatim. The tool returns ``found: false`` when the base has no entry,
which forbids inventing a definition and forces the honest "Caissa has no stored
explanation for that".

Entries are short, plain, and factual, and each names what the concept looks like on
the board — the part that is actually checkable. There are no evaluation claims and
nothing is copied from copyrighted material (spec §19).
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders

#: Bump whenever an entry changes, so a stored answer stays traceable.
KNOWLEDGE_VERSION = "7.0"

#: name → (category, definition, how to spot it, typical mistake)
_CONCEPTS: dict[str, tuple[str, str, str, str]] = {
    "fork": (
        "tactics",
        "One piece attacks two or more enemy pieces at once, so the opponent cannot save them all.",
        "look for a knight or pawn that can reach a square attacking two valuable pieces at once",
        "playing the fork before checking whether the attacked pieces can move with tempo or defend each other",
    ),
    "pin": (
        "tactics",
        "A piece cannot move (absolute pin) or should not move (relative pin) because something more valuable stands behind it on the same line.",
        "check every line from a bishop, rook or queen through an enemy piece to its king or queen",
        "treating a relative pin as absolute, or assuming a pinned piece is harmless when it can still capture",
    ),
    "skewer": (
        "tactics",
        "A line attack where the more valuable piece is in front and must move, exposing the piece behind it.",
        "look for a check or capture that forces the front piece to move off a line",
        "missing that the front piece may move with a check of its own",
    ),
    "discovered attack": (
        "tactics",
        "Moving one piece unmasks an attack from another piece behind it.",
        "before moving a piece, ask what stands behind it on the same line",
        "moving the front piece without calculating the discovered line's full consequence",
    ),
    "double attack": (
        "tactics",
        "One move creates two separate threats, so the opponent can only answer one.",
        "count the threats the move creates, not just the piece moved",
        "seeing one threat and assuming it wins",
    ),
    "back-rank mate": (
        "tactics",
        "Mate on the back rank because the king is trapped behind its own unmoved pawns.",
        "check whether the king's escape squares are blocked by its own pawns",
        "forgetting to make luft (a pawn move giving the king an escape square) in a quiet position",
    ),
    "hanging piece": (
        "tactics",
        "An undefended piece that can simply be taken.",
        "count attackers and defenders on every piece after each move",
        "assuming a piece is defended because it looks defended",
    ),
    "overloading": (
        "tactics",
        "A defender has more duties than it can perform, so one threat must fail.",
        "find the piece doing two jobs, then attack one of the jobs",
        "attacking the defended square instead of the defender's duty",
    ),
    "passed pawn": (
        "pawn structure",
        "A pawn with no enemy pawns on its file or the adjacent files ahead of it.",
        "scan each file for the nearest pawn in front",
        "pushing a passed pawn before the position is ready, or blockading your own",
    ),
    "isolated pawn": (
        "pawn structure",
        "A pawn with no friendly pawns on the adjacent files, so it cannot be defended by a pawn.",
        "look for a pawn whose neighbouring files are empty of friendly pawns",
        "trading pieces into an endgame where the isolated pawn is a permanent target",
    ),
    "doubled pawns": (
        "pawn structure",
        "Two pawns of the same colour on one file.",
        "check for two same-colour pawns with no pawn between them on the file",
        "assuming doubled pawns are always bad — they often bring an open file and a half-open one",
    ),
    "outpost": (
        "position",
        "A square that cannot be attacked by an enemy pawn, occupied by a piece — usually a knight.",
        "find squares beyond the enemy pawn chain that no pawn can hit",
        "trading the outposted piece that gives the position its character",
    ),
    "weak square": (
        "position",
        "A square that no pawn can defend, so an enemy piece can settle there.",
        "after every pawn move, note the squares that can no longer be defended by a pawn",
        "moving a pawn without counting the squares it gives up",
    ),
    "open file": (
        "position",
        "A file with no pawns on it, which rooks can occupy without obstruction.",
        "count pawns per file",
        "occupying an open file without a way to use it",
    ),
    "tempo": (
        "concepts",
        "One unit of time: a single move. Losing a tempo means spending a move to reach a position you could have reached sooner.",
        "compare your piece placements with the opponent's move count",
        "making a useful-looking developing move that is answered with a threat, forcing you back",
    ),
    "prophylaxis": (
        "strategy",
        "Playing to prevent the opponent's plan before pursuing your own.",
        "ask what the opponent would play if it were their move",
        "improving your own position while ignoring an obvious plan forming against you",
    ),
    "initiative": (
        "strategy",
        "The side creating threats dictates play; the opponent is busy responding.",
        "count who is answering whose threats",
        "choosing a quiet equalising move when a forcing one keeps the initiative",
    ),
    "zugzwang": (
        "endgame",
        "A position where the side to move would prefer not to: every move worsens their position.",
        "in king and pawn endings, compare the reserve moves each side has",
        "counting pawn moves in the endgame without noticing that they run out",
    ),
    "opposition": (
        "endgame",
        "Two kings facing each other with one square between them; the side NOT to move has the opposition and can force their way through.",
        "place the two kings on the same file or rank with one square between",
        "marching the king forward when the opponent already holds the opposition",
    ),
    "square of the pawn": (
        "endgame",
        "A geometric rule for whether a lone king can catch a running pawn before it promotes.",
        "draw a square from the pawn to its promotion square toward the king",
        "concluding a pawn race by counting moves instead of using the square",
    ),
    "opposite-coloured bishops": (
        "endgame",
        "Bishops on different colours. Drawing tendencies are high because neither can attack the other's camp on the squares it controls.",
        "check the bishop square colours after a bishop trade",
        "believing an extra pawn wins when the defenders' bishop covers the right colour",
    ),
}

_CATEGORIES = sorted({entry[0] for entry in _CONCEPTS.values()})


def available_concept_names() -> list[str]:
    """The public list of concept names in the curated base."""
    return sorted(_CONCEPTS)


def get_concept(name: str) -> dict[str, Any] | None:
    """The stored definition of one concept, or ``None`` when it is not held.

    A public accessor so other systems (the Phase 13 knowledge graph) can cite the
    curated base rather than copying its text and letting the two drift.
    """
    entry = _CONCEPTS.get((name or "").strip().lower())
    if entry is None:
        return None
    category, definition, spot, mistake = entry
    return {
        "concept": (name or "").strip().lower(),
        "category": category,
        "definition": definition,
        "how_to_spot": spot,
        "typical_mistake": mistake,
        "version": KNOWLEDGE_VERSION,
    }


def search_concepts(query: str) -> dict[str, Any]:
    """Find a concept by name or by searching its description."""
    text = (query or "").strip().lower()
    if not text:
        return {"found": False, "query": query, "version": KNOWLEDGE_VERSION}
    direct = _CONCEPTS.get(text)
    if direct is None and text.endswith("s"):
        # Singular/plural tolerance: "forks" finds "fork", not the reverse.
        singular = text[:-1]
        if singular in _CONCEPTS:
            direct = _CONCEPTS[singular]
            text = singular
    if direct is not None:
        category, definition, spot, mistake = direct
        return {
            "found": True,
            "query": query,
            "concept": text,
            "category": category,
            "definition": definition,
            "how_to_spot": spot,
            "typical_mistake": mistake,
            "version": KNOWLEDGE_VERSION,
        }
    matches = [
        {
            "concept": name,
            "category": entry[0],
            "definition": entry[1],
        }
        for name, entry in _CONCEPTS.items()
        if text in name or text in entry[1].lower() or text in entry[2].lower()
    ]
    if matches:
        return {
            "found": True,
            "query": query,
            "matches": matches[:5],
            "note": "Several concepts match; use the one the question is about.",
            "version": KNOWLEDGE_VERSION,
        }
    return {
        "found": False,
        "query": query,
        "available_concepts": sorted(_CONCEPTS),
        "note": (
            "Caissa has no stored explanation for that concept. Say so; do not write a "
            "definition from memory."
        ),
        "version": KNOWLEDGE_VERSION,
    }


def build_knowledge_tools(providers: AgentProviders) -> list[Tool]:
    """The knowledge tool family."""

    def search_chess_knowledge(
        _context: AgentContext, query: str, category: str | None = None
    ) -> dict[str, Any]:
        result = search_concepts(query)
        if category and result.get("found"):
            if "matches" in result:
                result["matches"] = [
                    match for match in result["matches"] if match["category"] == category
                ]
            elif result.get("category") != category:
                return {
                    "found": False,
                    "query": query,
                    "note": (
                        f"'{result.get('concept')}' is a {result.get('category')} concept, "
                        f"not {category}."
                    ),
                    "version": KNOWLEDGE_VERSION,
                }
        return result

    return [
        Tool(
            name="search_chess_knowledge",
            description=(
                "Look up a chess concept from Caissa's versioned knowledge base "
                "(tactics, pawn structure, position, strategy, endgame). Returns "
                "found=false when there is no entry — in that case do not supply a "
                "definition of your own."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "category": {
                            "type": "string",
                            "enum": _CATEGORIES,
                        },
                    },
                    "required": ["query"],
                },
                outputs=("found", "concept", "category", "definition", "how_to_spot", "version"),
            ),
            permission=ToolPermission.ANY,
            handler=search_chess_knowledge,
            tags=("knowledge",),
        ),
        Tool(
            name="list_chess_concepts",
            description="Every chess concept Caissa has a stored explanation for.",
            schema=ToolSchema(
                parameters={"type": "object", "properties": {}, "required": []},
                outputs=("concepts", "categories", "version"),
            ),
            permission=ToolPermission.ANY,
            handler=lambda _context: {
                "concepts": sorted(_CONCEPTS),
                "categories": _CATEGORIES,
                "version": KNOWLEDGE_VERSION,
            },
            tags=("knowledge", "meta"),
        ),
    ]


__all__ = [
    "KNOWLEDGE_VERSION",
    "available_concept_names",
    "build_knowledge_tools",
    "get_concept",
    "search_concepts",
]
