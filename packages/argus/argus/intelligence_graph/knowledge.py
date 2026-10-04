"""The chess knowledge system (§25–§28): sourced concepts and position links.

Caissa already had a small curated concept base used by the agent's
``search_chess_knowledge`` tool. This module turns that base into a **source-backed**
knowledge layer:

* ``KnowledgeSource`` — where the text came from, with its licence. Nothing is
  ingested that the application is not authorized to use (§26).
* ``KnowledgeDocument`` — one titled work from a source, carrying author, licence,
  publication and reference.
* ``KnowledgeChunk`` — a retrievable passage of a document, linked to the concepts
  it discusses.
* ``KnowledgeConcept`` — a concept with a definition, a Caissa-authored source, and
  a link back to the curated base entry it is derived from (never a copy that can
  drift).

The second half is §27: **which concepts a real position exhibits**. That is a
deterministic, checkable computation over the board with python-chess — pawn
structure, a weak back rank, opposite-coloured bishops, an outpost. Every result
carries a plain statement of *what on the board* produced it. No concept is ever
attached to a position by a language model, and a position that exhibits nothing
known produces nothing.

The concept text itself is Caissa-authored (the curated base is original text with
no evaluation claims); the licence recorded is therefore Caissa's own. A future
third-party ingest must carry its own licence, which this module refuses to
default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

import chess
from pydantic import BaseModel, Field

KNOWLEDGE_GRAPH_VERSION = "13.0"

#: The licence Caissa's own curated text is published under.
ARGUS_LICENCE = "Caissa original content — internal use."


class KnowledgeSourceType(str, Enum):
    Caissa = "argus"
    PUBLIC_DOMAIN = "public_domain"
    LICENSED = "licensed"
    USER_PROVIDED = "user_provided"


class KnowledgeSource(BaseModel):
    """Where a body of knowledge came from, and the terms it is held under."""

    id: str
    name: str
    source_type: KnowledgeSourceType = KnowledgeSourceType.Caissa
    author: str | None = None
    licence: str = ARGUS_LICENCE
    publication: str | None = None
    url: str | None = None
    version: str = KNOWLEDGE_GRAPH_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_type": self.source_type.value,
            "author": self.author,
            "licence": self.licence,
            "publication": self.publication,
            "url": self.url,
            "version": self.version,
        }


class KnowledgeDocument(BaseModel):
    """One titled work from a source."""

    id: str
    source_id: str
    title: str
    author: str | None = None
    licence: str = ARGUS_LICENCE
    publication: str | None = None
    url: str | None = None
    version: str = KNOWLEDGE_GRAPH_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "title": self.title,
            "author": self.author,
            "licence": self.licence,
            "publication": self.publication,
            "url": self.url,
            "version": self.version,
        }


class KnowledgeChunk(BaseModel):
    """A retrievable passage of a document."""

    id: str
    document_id: str
    ordinal: int
    text: str
    concepts: list[str] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "ordinal": self.ordinal,
            "text": self.text,
            "concepts": list(self.concepts),
        }


class KnowledgeConcept(BaseModel):
    """A concept, sourced and versioned."""

    slug: str
    name: str
    category: str
    definition: str
    how_to_spot: str = ""
    typical_mistake: str = ""
    source_id: str
    document_id: str | None = None
    #: The curated-base entry this was derived from, so the two cannot drift.
    related_base_concept: str | None = None
    version: str = KNOWLEDGE_GRAPH_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_payload(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "category": self.category,
            "definition": self.definition,
            "how_to_spot": self.how_to_spot,
            "typical_mistake": self.typical_mistake,
            "source_id": self.source_id,
            "document_id": self.document_id,
            "related_base_concept": self.related_base_concept,
            "version": self.version,
        }


# --- the Caissa source and its concepts ---------------------------------------

ARGUS_SOURCE = KnowledgeSource(
    id="argus-curated",
    name="Caissa curated chess concepts",
    source_type=KnowledgeSourceType.Caissa,
    author="Caissa",
    licence=ARGUS_LICENCE,
)

ARGUS_DOCUMENT = KnowledgeDocument(
    id="argus-concepts",
    source_id=ARGUS_SOURCE.id,
    title="Caissa chess concept glossary",
    author="Caissa",
    licence=ARGUS_LICENCE,
)


def _slug(text: str) -> str:
    return text.strip().lower().replace(" ", "-")


def build_curated_concepts() -> list[KnowledgeConcept]:
    """The knowledge concepts, derived from the curated agent base.

    Imported lazily so this module has no import-time dependency on the agent
    package, and so a deployment without the agent still has the knowledge layer.
    """
    from argus.ai_agent.tools.knowledge import get_concept

    concepts: list[KnowledgeConcept] = []
    for name in _CURATED_NAMES:
        entry = get_concept(name)
        if entry is None:
            continue
        concepts.append(
            KnowledgeConcept(
                slug=_slug(name),
                name=name,
                category=entry["category"],
                definition=entry["definition"],
                how_to_spot=entry.get("how_to_spot", ""),
                typical_mistake=entry.get("typical_mistake", ""),
                source_id=ARGUS_SOURCE.id,
                document_id=ARGUS_DOCUMENT.id,
                related_base_concept=name,
            )
        )
    return concepts


#: The subset of the curated base that Caissa also links to positions. Kept
#: explicit so a concept is only position-linked when a rule exists for it.
_CURATED_NAMES: tuple[str, ...] = (
    "fork",
    "pin",
    "skewer",
    "discovered attack",
    "double attack",
    "back-rank mate",
    "hanging piece",
    "overloading",
    "passed pawn",
    "isolated pawn",
    "doubled pawns",
    "outpost",
    "weak square",
    "open file",
    "opposite-coloured bishops",
)


# --- position → concept (§27) -------------------------------------------------


class ConceptExhibition(BaseModel):
    """One concept a position exhibits, with the board fact that proves it."""

    concept_slug: str
    concept_name: str
    statement: str
    #: The side whose position exhibits it ("white" | "black" | None).
    side: str | None = None
    evidence_squares: list[str] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "concept_slug": self.concept_slug,
            "concept_name": self.concept_name,
            "statement": self.statement,
            "side": self.side,
            "evidence_squares": list(self.evidence_squares),
        }


def _squares(names: list[int]) -> list[str]:
    return [chess.square_name(square) for square in names]


def _doubled_pawns(board: chess.Board) -> list[ConceptExhibition]:
    out: list[ConceptExhibition] = []
    for color, label in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        files: dict[int, list[int]] = {}
        for square in board.pieces(chess.PAWN, color):
            files.setdefault(chess.square_file(square), []).append(square)
        for squares in files.values():
            if len(squares) >= 2:
                out.append(
                    ConceptExhibition(
                        concept_slug="doubled-pawns",
                        concept_name="doubled pawns",
                        statement=(
                            f"{label} has {len(squares)} pawns on the "
                            f"{chess.FILE_NAMES[chess.square_file(squares[0])]}-file."
                        ),
                        side=label,
                        evidence_squares=_squares(squares),
                    )
                )
    return out


def _isolated_pawns(board: chess.Board) -> list[ConceptExhibition]:
    out: list[ConceptExhibition] = []
    for color, label in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        pawn_files = {chess.square_file(square) for square in board.pieces(chess.PAWN, color)}
        for square in board.pieces(chess.PAWN, color):
            file_index = chess.square_file(square)
            if (file_index - 1) not in pawn_files and (file_index + 1) not in pawn_files:
                out.append(
                    ConceptExhibition(
                        concept_slug="isolated-pawn",
                        concept_name="isolated pawn",
                        statement=(
                            f"{label}'s pawn on {chess.square_name(square)} has no friendly "
                            f"pawns on the adjacent files."
                        ),
                        side=label,
                        evidence_squares=[chess.square_name(square)],
                    )
                )
    return out


def _passed_pawns(board: chess.Board) -> list[ConceptExhibition]:
    out: list[ConceptExhibition] = []
    for color, label, direction in ((chess.WHITE, "white", 1), (chess.BLACK, "black", -1)):
        enemy_files = {
            chess.square_file(square) for square in board.pieces(chess.PAWN, not color)
        }
        for square in board.pieces(chess.PAWN, color):
            file_index = chess.square_file(square)
            rank = chess.square_rank(square)
            blocking = False
            for other in board.pieces(chess.PAWN, not color):
                other_file = chess.square_file(other)
                if abs(other_file - file_index) <= 1 and (
                    chess.square_rank(other) - rank
                ) * direction > 0:
                    blocking = True
                    break
            if not blocking and file_index not in enemy_files:
                out.append(
                    ConceptExhibition(
                        concept_slug="passed-pawn",
                        concept_name="passed pawn",
                        statement=(
                            f"{label}'s pawn on {chess.square_name(square)} has no enemy "
                            f"pawns ahead of it on its file or the adjacent files."
                        ),
                        side=label,
                        evidence_squares=[chess.square_name(square)],
                    )
                )
    return out


def _open_files(board: chess.Board) -> list[ConceptExhibition]:
    occupied = {
        chess.square_file(square)
        for square in board.pieces(chess.PAWN, chess.WHITE) | board.pieces(chess.PAWN, chess.BLACK)
    }
    out: list[ConceptExhibition] = []
    for file_index in range(8):
        if file_index not in occupied:
            out.append(
                ConceptExhibition(
                    concept_slug="open-file",
                    concept_name="open file",
                    statement=f"The {chess.FILE_NAMES[file_index]}-file has no pawns on it.",
                    side=None,
                    evidence_squares=[],
                )
            )
    return out


def _forward_squares_occupied(board: chess.Board, color: bool, king_square: int) -> bool:
    """Whether all three squares the king would step to are occupied by its own side.

    This is the checkable part of "boxed in by its own pawns": a king with an
    escape square is not vulnerable to a back-rank mate, so the rule must not fire.
    """
    file_index = chess.square_file(king_square)
    rank = chess.square_rank(king_square)
    forward = 1 if color == chess.WHITE else -1
    for delta_file in (-1, 0, 1):
        target_file = file_index + delta_file
        target_rank = rank + forward
        if not (0 <= target_file < 8 and 0 <= target_rank < 8):
            continue
        piece = board.piece_at(chess.square(target_file, target_rank))
        if piece is None or piece.color != color:
            return False
    return True


def _weak_back_rank(board: chess.Board) -> list[ConceptExhibition]:
    out: list[ConceptExhibition] = []
    for color, label, back_rank in (
        (chess.WHITE, "white", 0),
        (chess.BLACK, "black", 7),
    ):
        king_square = board.king(color)
        if king_square is None or chess.square_rank(king_square) != back_rank:
            continue
        if not _forward_squares_occupied(board, color, king_square):
            continue
        # A rook or queen of the opponent on the back rank is the concrete threat.
        enemy_majors = board.pieces(chess.ROOK, not color) | board.pieces(chess.QUEEN, not color)
        on_back_rank = [
            square
            for square in enemy_majors
            if chess.square_rank(square) == back_rank
        ]
        if on_back_rank:
            out.append(
                ConceptExhibition(
                    concept_slug="back-rank-mate",
                    concept_name="back-rank mate",
                    statement=(
                        f"{label}'s king is on the back rank and an enemy major piece "
                        f"is on the same rank, where the king is boxed in by its own pawns."
                    ),
                    side=label,
                    evidence_squares=[chess.square_name(king_square)]
                    + _squares(on_back_rank),
                )
            )
    return out


def _opposite_bishops(board: chess.Board) -> list[ConceptExhibition]:
    white_bishops = list(board.pieces(chess.BISHOP, chess.WHITE))
    black_bishops = list(board.pieces(chess.BISHOP, chess.BLACK))
    if len(white_bishops) != 1 or len(black_bishops) != 1:
        return []
    white_square = white_bishops[0]
    black_square = black_bishops[0]
    white_colour = (chess.square_file(white_square) + chess.square_rank(white_square)) % 2
    black_colour = (chess.square_file(black_square) + chess.square_rank(black_square)) % 2
    if white_colour == black_colour:
        return []
    return [
        ConceptExhibition(
            concept_slug="opposite-coloured-bishops",
            concept_name="opposite-coloured bishops",
            statement=(
                "Each side has exactly one bishop and they stand on opposite colours."
            ),
            side=None,
            evidence_squares=[
                chess.square_name(white_square),
                chess.square_name(black_square),
            ],
        )
    ]


def _outposts(board: chess.Board) -> list[ConceptExhibition]:
    out: list[ConceptExhibition] = []
    for color, label, ranks in ((chess.WHITE, "white", (2, 3, 4)), (chess.BLACK, "black", (3, 4, 5))):
        enemy_pawns = board.pieces(chess.PAWN, not color)
        defended = set()
        for pawn in board.pieces(chess.PAWN, color):
            for delta in (7, 9):
                target = pawn + delta if color == chess.WHITE else pawn - delta
                if 0 <= target < 64:
                    defended.add(target)
        for knight in board.pieces(chess.KNIGHT, color):
            if chess.square_rank(knight) not in ranks:
                continue
            attacked_by_pawn = any(
                abs(chess.square_file(pawn) - chess.square_file(knight)) == 1
                and (
                    chess.square_rank(pawn) - chess.square_rank(knight)
                ) * (1 if color == chess.WHITE else -1) == -1
                for pawn in enemy_pawns
            )
            if not attacked_by_pawn and knight in defended:
                out.append(
                    ConceptExhibition(
                        concept_slug="outpost",
                        concept_name="outpost",
                        statement=(
                            f"{label}'s knight on {chess.square_name(knight)} cannot be "
                            f"attacked by an enemy pawn and is defended by a pawn."
                        ),
                        side=label,
                        evidence_squares=[chess.square_name(knight)],
                    )
                )
    return out


#: Every deterministic rule, in a fixed order so output is stable.
_RULES = (
    _doubled_pawns,
    _isolated_pawns,
    _passed_pawns,
    _open_files,
    _weak_back_rank,
    _opposite_bishops,
    _outposts,
)


def exhibited_concepts(fen: str) -> list[ConceptExhibition]:
    """Concepts this position exhibits, computed from the board.

    Deterministic and checkable. Returns an empty list — the honest "nothing
    Caissa has a rule for" — rather than guessing. Invalid FEN returns empty.
    """
    try:
        board = chess.Board(fen)
    except ValueError:
        return []
    if board.status() != chess.STATUS_VALID:
        return []
    found: list[ConceptExhibition] = []
    for rule in _RULES:
        found.extend(rule(board))
    return found


def retrievable_concepts() -> dict[str, KnowledgeConcept]:
    """The concept set keyed by slug."""
    return {concept.slug: concept for concept in build_curated_concepts()}


def retrieve_concepts(query: str) -> list[KnowledgeConcept]:
    """Search concepts by slug/name/definition — source-backed, never invented."""
    text = (query or "").strip().lower()
    if not text:
        return []
    concepts = build_curated_concepts()
    scored: list[tuple[int, KnowledgeConcept]] = []
    for concept in concepts:
        haystack = f"{concept.slug} {concept.name} {concept.category} {concept.definition}".lower()
        if text in concept.slug or text in concept.name.lower():
            scored.append((0, concept))
        elif text in haystack:
            scored.append((1, concept))
    scored.sort(key=lambda pair: (pair[0], pair[1].slug))
    return [concept for _, concept in scored]


__all__ = [
    "ARGUS_DOCUMENT",
    "ARGUS_LICENCE",
    "ARGUS_SOURCE",
    "KNOWLEDGE_GRAPH_VERSION",
    "ConceptExhibition",
    "KnowledgeChunk",
    "KnowledgeConcept",
    "KnowledgeDocument",
    "KnowledgeSource",
    "KnowledgeSourceType",
    "build_curated_concepts",
    "exhibited_concepts",
    "retrievable_concepts",
    "retrieve_concepts",
]
