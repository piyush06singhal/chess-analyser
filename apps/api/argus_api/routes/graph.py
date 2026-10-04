"""Intelligence-graph routes (Phase 13).

These endpoints expose the graph the AI coach reasons over: the Position, Game and
Player Explorers (§40–§42), the "Why?" trace (§43), the health check (§36), the
structured graph search (§38), and the incremental-update entry points (§34).

Nothing here runs an engine or invents a relationship: every route reads stored
evidence through the graph service, which applies authorization. A route that
finds nothing answers with an honest empty result and a note.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from argus.intelligence_graph.knowledge import (
    ARGUS_SOURCE,
    KNOWLEDGE_GRAPH_VERSION,
    build_curated_concepts,
    retrieve_concepts,
)
from argus.intelligence_graph.taxonomy import (
    GRAPH_METHODOLOGY_VERSION,
    GRAPH_SCHEMA_VERSION,
    EdgeType,
    NodeType,
)
from argus.shared.errors import ValidationError

from argus_api.db.models import KnowledgeConceptRecord
from argus_api.deps import get_db
from argus_api.services import graph_explorers
from argus_api.services.graph_intelligence import (
    link_position_concepts,
    rebuild,
    seed_knowledge,
    update_opponents,
    update_player_patterns,
    update_player_training,
    update_scenarios,
)
from argus_api.services.graph_service import update_game

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/method")
def graph_method() -> dict:
    """Publish the graph's vocabulary and versions (§37) so it can be inspected."""
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "methodology_version": GRAPH_METHODOLOGY_VERSION,
        "knowledge_version": KNOWLEDGE_GRAPH_VERSION,
        "storage": "postgresql-relational-tables-with-json-evidence",
        "node_types": [node.value for node in NodeType],
        "edge_types": [edge.value for edge in EdgeType],
        "note": (
            "Caissa stores the graph in relational tables and keeps evidence in a JSON "
            "column. A graph database was evaluated and rejected: the access patterns "
            "are bounded, indexable traversals, not arbitrary paths. See "
            "docs/intelligence-graph/README.md."
        ),
    }


@router.get("/health")
def graph_health(db: Session = Depends(get_db)) -> dict:
    """The Caissa Graph Health Check (§36)."""
    return graph_explorers.graph_health(db)


@router.post("/snapshot")
def graph_snapshot(db: Session = Depends(get_db)) -> dict:
    """Persist a reproducible snapshot of the graph's versions and counts (§8)."""
    return graph_explorers.graph_snapshot(db, persist=True)


@router.post("/rebuild")
def graph_rebuild(
    limit_games: int | None = Query(default=None, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    """Rebuild the graph from the domain (bounded when ``limit_games`` is given)."""
    return rebuild(db, limit_games=limit_games)


@router.post("/games/{game_id}/update")
def graph_update_game(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Incremental update for one game (§34)."""
    return update_game(db, game_id)


@router.post("/players/{player_id}/update")
def graph_update_player(player_id: int, db: Session = Depends(get_db)) -> dict:
    """Incremental update for one player's patterns and training links (§34)."""
    return {
        "patterns": update_player_patterns(db, player_id),
        "training": update_player_training(db, player_id),
    }


@router.post("/opponents/update")
def graph_update_opponents(db: Session = Depends(get_db)) -> dict:
    return update_opponents(db)


@router.post("/scenarios/update")
def graph_update_scenarios(db: Session = Depends(get_db)) -> dict:
    return update_scenarios(db)


# --- explorers ----------------------------------------------------------------


@router.get("/position")
def position_explorer(fen: str = Query(min_length=1), db: Session = Depends(get_db)) -> dict:
    """Position Explorer (§40): exact matches, similarity, patterns, knowledge."""
    return graph_explorers.position_explorer(db, fen)


@router.get("/games/{game_id}")
def game_explorer(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Game Explorer (§41)."""
    return graph_explorers.game_explorer(db, game_id)


@router.get("/players/{player_id}")
def player_explorer(player_id: int, db: Session = Depends(get_db)) -> dict:
    """Player Explorer (§42), privacy-respecting."""
    return graph_explorers.player_explorer(db, player_id)


@router.get("/why/{node_type}/{node_key:path}")
def why(node_type: str, node_key: str, db: Session = Depends(get_db)) -> dict:
    """The "Why?" surface (§43): relationship, evidence, sample, methodology.

    ``node_key`` uses the ``:path`` converter because real node keys are not
    URL-safe: a player-profile insight id can contain a slash
    (``opening-unknown_/_unclassified``) and must still be addressable.
    """
    try:
        resolved = NodeType(node_type)
    except ValueError:
        raise ValidationError(
            f"Unknown node type '{node_type}'.",
            details={"node_types": [node.value for node in NodeType]},
        ) from None
    return graph_explorers.why(db, resolved, node_key)


@router.get("/neighborhood/{node_type}/{node_key:path}")
def graph_neighborhood(
    node_type: str,
    node_key: str,
    depth: int = Query(default=2, ge=1, le=3),
    limit: int = Query(default=60, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """The Intelligence Map (§39): a bounded, authorized neighbourhood subgraph.

    ``node_key`` uses the ``:path`` converter for the same reason the "Why?"
    route does: real node keys are not URL-safe.
    """
    try:
        resolved = NodeType(node_type)
    except ValueError:
        raise ValidationError(
            f"Unknown node type '{node_type}'.",
            details={"node_types": [node.value for node in NodeType]},
        ) from None
    return graph_explorers.neighborhood(
        db, resolved, node_key, depth=depth, limit=limit
    )


@router.get("/nodes/{node_type}")
def list_nodes(
    node_type: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """A page of nodes of one kind (authorized)."""
    try:
        resolved = NodeType(node_type)
    except ValueError:
        raise ValidationError(
            f"Unknown node type '{node_type}'.",
            details={"node_types": [node.value for node in NodeType]},
        ) from None
    from argus_api.services.graph_service import service_for

    nodes = service_for(db).nodes_of_type(resolved, limit=limit)
    return {
        "node_type": resolved.value,
        "count": len(nodes),
        "nodes": [node.to_payload() for node in nodes],
    }


@router.get("/search")
def graph_search(
    kind: str = Query(default="games"),
    eco_code: str | None = None,
    pattern_type: str | None = None,
    result: str | None = None,
    opponent_id: int | None = None,
    training_category: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Graph-backed structured search (§38)."""
    return graph_explorers.structured_search(
        db,
        kind=kind,
        eco_code=eco_code,
        pattern_type=pattern_type,
        result=result,
        opponent_id=opponent_id,
        training_category=training_category,
        limit=limit,
    )


# --- knowledge ----------------------------------------------------------------


@router.get("/concepts")
def list_concepts(
    query: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """The sourced chess concepts Caissa holds. Retrieved, never invented."""
    concepts = retrieve_concepts(query) if query else build_curated_concepts()
    return {
        "version": KNOWLEDGE_GRAPH_VERSION,
        "source": ARGUS_SOURCE.to_payload(),
        "count": len(concepts),
        "concepts": [concept.to_payload() for concept in concepts[:limit]],
        "note": (
            "Caissa returns only stored, sourced concepts. When a concept is absent it "
            "says so rather than writing a definition from memory."
        ),
    }


@router.post("/knowledge/seed")
def knowledge_seed(db: Session = Depends(get_db)) -> dict:
    """Seed the sourced concept base (idempotent)."""
    return seed_knowledge(db)


@router.post("/knowledge/link")
def knowledge_link(
    game_ids: list[str] | None = Body(default=None, embed=True),
    limit: int = Query(default=400, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    """Link stored positions to the concepts they actually exhibit (§27)."""
    return link_position_concepts(db, game_ids=game_ids, limit=limit)


@router.get("/concepts/{slug}")
def get_concept(slug: str, db: Session = Depends(get_db)) -> dict:
    """One sourced concept, with its licence."""
    row = db.get(KnowledgeConceptRecord, slug)
    if row is None:
        return {
            "slug": slug,
            "found": False,
            "note": "Caissa has no stored, sourced explanation for that concept.",
        }
    return {
        "slug": row.slug,
        "found": True,
        "name": row.name,
        "category": row.category,
        "definition": row.definition,
        "how_to_spot": row.how_to_spot,
        "typical_mistake": row.typical_mistake,
        "source_id": row.source_id,
        "related_base_concept": row.related_base_concept,
        "version": row.version,
    }
