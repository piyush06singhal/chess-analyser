"""Knowledge / RAG evaluation (§27).

The knowledge layer must be sourced, deterministic and honest about absence:
a concept is returned only with its source and licence, a position is linked to a
concept only through a board rule that proves it, and an unsupported query
returns nothing rather than a written-from-memory definition.
"""

from __future__ import annotations

from argus.evaluation.results import SuiteResult, check
from argus.intelligence_graph.knowledge import (
    ARGUS_SOURCE,
    build_curated_concepts,
    exhibited_concepts,
    retrieve_concepts,
)


def knowledge_suite(context) -> SuiteResult:
    """Retrieval relevance, source correctness and position linkage."""
    checks = []
    concepts = build_curated_concepts()

    checks.append(
        check("the concept base is non-empty", len(concepts) > 0, detail=f"{len(concepts)} concepts")
    )
    checks.append(
        check(
            "every concept is sourced and licenced",
            all(c.source_id == ARGUS_SOURCE.id for c in concepts) and bool(ARGUS_SOURCE.licence),
            detail=f"source={ARGUS_SOURCE.id}, licence={ARGUS_SOURCE.licence!r}",
            critical=True,
        )
    )
    checks.append(
        check(
            "every concept has a definition",
            all(c.definition.strip() for c in concepts),
        )
    )

    # Retrieval: a known concept is found; a nonsense query returns nothing.
    fork = retrieve_concepts("fork")
    checks.append(check("a known concept is retrieved", any(c.slug == "fork" for c in fork)))
    checks.append(
        check(
            "an unsupported query returns nothing",
            retrieve_concepts("the-preposterous-gambit-of-nowhere") == [],
            detail="no match -> empty, never invented",
            critical=True,
        )
    )

    # Position linkage is deterministic and rule-bound. White pawns on a3 and a4
    # are doubled on the a-file.
    doubled_fen = "4k3/8/8/8/P7/P7/8/4K3 w - - 0 1"
    doubled = exhibited_concepts(doubled_fen)
    slugs = {exhibition.concept_slug for exhibition in doubled}
    checks.append(
        check(
            "doubled pawns are detected from the board",
            "doubled-pawns" in slugs,
            detail=", ".join(sorted(slugs)) or "none",
        )
    )
    start = exhibited_concepts("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    checks.append(
        check(
            "the start position exhibits nothing known",
            start == [],
            detail="no rule fires -> empty, never guessed",
        )
    )
    # Determinism: the same FEN produces the same result every time.
    again = exhibited_concepts(doubled_fen)
    checks.append(
        check(
            "position linkage is deterministic",
            [e.model_dump() for e in again] == [e.model_dump() for e in doubled],
        )
    )
    # An invalid FEN yields nothing, not an exception or a guess.
    checks.append(
        check(
            "an invalid FEN yields nothing",
            exhibited_concepts("not a fen") == [],
        )
    )

    return SuiteResult(
        suite="knowledge",
        title="Knowledge sourcing and position linkage",
        checks=checks,
    )


__all__ = ["knowledge_suite"]
