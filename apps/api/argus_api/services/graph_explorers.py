"""Position / Game / Player Explorers, Why-tracing and graph search (§40–§43).

The explorers are read-only compositions of the graph and the domain: they answer
"what is connected to this?" by asking the graph service for authorized
neighbours, and never by running an engine or inventing a relationship. A section
with nothing behind it is returned empty with a note — the honest "no verified
connection found".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from argus.intelligence_graph.fingerprint import fingerprint
from argus.intelligence_graph.models import GraphSnapshot
from argus.intelligence_graph.ranking import methodology as ranking_methodology
from argus.intelligence_graph.service import IntelligenceGraphService
from argus.intelligence_graph.similarity import SimilarityLevel, classify
from argus.intelligence_graph.taxonomy import EdgeType, NodeType
from argus.shared.logging import get_logger

from argus_api.db.models import (
    Game,
    GamePosition,
    GraphSnapshotRecord,
    KnowledgeConceptRecord,
    Player,
    TrainingPosition,
)
from argus_api.observability import current_caller_id
from argus_api.services.graph_service import GRAPH_METHODOLOGY_VERSION, service_for


def _game_visible(db: Session, game_id: str) -> bool:
    """Whether the current caller may read ``game_id`` (graph reads must respect it)."""
    from argus_api.services.authorization import authorized_for_game

    return authorized_for_game(db, game_id)

logger = get_logger(__name__)

_SIMILARITY_SCAN_LIMIT = 400


def _node_payload(node: Any) -> dict[str, Any]:
    return node.to_payload() if node is not None else {}


def _concept(db: Session, slug: str) -> dict[str, Any]:
    row = db.get(KnowledgeConceptRecord, slug)
    if row is None:
        return {"slug": slug, "source": None}
    source = {
        "source_id": row.source_id,
        "related_base_concept": row.related_base_concept,
    }
    return {
        "slug": row.slug,
        "name": row.name,
        "category": row.category,
        "definition": row.definition,
        "how_to_spot": row.how_to_spot,
        "typical_mistake": row.typical_mistake,
        **source,
    }


# --- position explorer --------------------------------------------------------


def position_explorer(db: Session, fen: str, *, service: IntelligenceGraphService | None = None) -> dict[str, Any]:
    """Everything Caissa can say about a position, from stored evidence only."""
    service = service or service_for(db)
    fp = fingerprint(fen)
    if not fp.valid or not fp.position_hash:
        return {
            "fen": fen,
            "valid": False,
            "note": "Caissa cannot analyse that FEN: it is not a valid chess position.",
        }
    node = service.store.get_node(NodeType.POSITION, fp.position_hash)

    # Exact historical matches: games that contain this exact position.
    games: list[dict[str, Any]] = []
    for edge in service.store.in_edges(NodeType.POSITION, fp.position_hash, frozenset({EdgeType.CONTAINS_POSITION})):
        if edge.from_type is not NodeType.GAME:
            continue
        # Only games the caller may read: a shared position must not become a
        # backdoor to another caller's private game.
        if not _game_visible(db, edge.from_key):
            continue
        game = db.get(Game, edge.from_key)
        if game is None:
            continue
        games.append(
            {
                "game_id": game.id,
                "white": game.white_player_name,
                "black": game.black_player_name,
                "result": game.result,
                "date": game.date,
                "exact": True,
            }
        )

    # Similarity: classify stored positions against this one (bounded scan).
    similar: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    rows = db.execute(
        select(GamePosition.game_id, GamePosition.ply, GamePosition.fen)
        .order_by(GamePosition.id.desc())
        .limit(_SIMILARITY_SCAN_LIMIT)
    ).all()
    for game_id, ply, other_fen in rows:
        level = classify(fp.fen, other_fen)
        if level is None or level is SimilarityLevel.EXACT:
            continue
        key = f"{game_id}:{ply}"
        if key in seen:
            continue
        seen.add(key)
        similar.setdefault(level.value, []).append(
            {"game_id": game_id, "ply": ply, "level": level.value}
        )

    patterns = [
        _node_payload(other)
        for edge, other in service.neighbors(
            NodeType.POSITION, fp.position_hash, edge_types={EdgeType.PATTERN_EVIDENCE}, direction="in"
        )
    ]
    concepts = [
        _concept(db, other.node_key)
        for edge, other in service.neighbors(
            NodeType.POSITION, fp.position_hash, edge_types={EdgeType.EXHIBITS}, direction="out"
        )
        if other is not None
    ]
    training = [
        _node_payload(other)
        for edge, other in service.neighbors(
            NodeType.POSITION, fp.position_hash, edge_types={EdgeType.DERIVED_FROM}, direction="in"
        )
        if other is not None and other.node_type is NodeType.TRAINING_POSITION
    ]
    scenarios = [
        _node_payload(other)
        for edge, other in service.neighbors(
            NodeType.POSITION, fp.position_hash, edge_types={EdgeType.DERIVED_FROM}, direction="in"
        )
        if other is not None and other.node_type is NodeType.SCENARIO
    ]
    openings: list[str] = []
    for game in games:
        for edge in service.store.out_edges(NodeType.GAME, game["game_id"], frozenset({EdgeType.BELONGS_TO_OPENING})):
            if edge.to_key not in openings:
                openings.append(edge.to_key)

    return {
        "fen": fp.fen,
        "valid": True,
        "position": fp.to_payload(),
        "materialized": node is not None,
        "exact_matches": games,
        "exact_match_count": len(games),
        "similar": similar,
        "similar_note": (
            "Similarity levels are not interchangeable; only 'exact' is an exact match. "
            f"Scanned the {len(rows)} most recent stored positions."
        ),
        "openings": openings,
        "patterns": patterns,
        "knowledge_concepts": concepts,
        "training_positions": training,
        "scenarios": scenarios,
        "note": None if node is not None else "This position is not in the graph yet; run a graph update.",
    }


# --- game explorer ------------------------------------------------------------


def game_explorer(db: Session, game_id: str, *, service: IntelligenceGraphService | None = None) -> dict[str, Any]:
    """The graph around one game: opening, positions, patterns, training, relatives."""
    from argus_api.services.authorization import authorize_game

    authorize_game(db, game_id)  # a stranger's game is absent, not explored
    service = service or service_for(db)
    game = db.get(Game, game_id)
    if game is None:
        return {"game_id": game_id, "found": False, "note": "No such game is stored."}

    openings = [
        edge.to_key
        for edge in service.store.out_edges(NodeType.GAME, game_id, frozenset({EdgeType.BELONGS_TO_OPENING}))
    ]
    positions = [
        {"position_hash": edge.to_key, "edge": edge.edge_type.value}
        for edge in service.store.out_edges(NodeType.GAME, game_id, frozenset({EdgeType.CONTAINS_POSITION}))
    ]
    patterns: list[dict[str, Any]] = []
    training: list[dict[str, Any]] = []
    for position in positions:
        for edge, other in service.neighbors(
            NodeType.POSITION, position["position_hash"],
            edge_types={EdgeType.PATTERN_EVIDENCE}, direction="in",
        ):
            if other is not None:
                patterns.append(_node_payload(other))

    for edge, other in service.neighbors(
        NodeType.GAME, game_id, edge_types={EdgeType.DERIVED_FROM}, direction="in"
    ):
        if other is None:
            continue
        if other.node_type is NodeType.TRAINING_POSITION:
            training.append(_node_payload(other))

    related: list[dict[str, Any]] = []
    for opening in openings:
        for edge in service.store.in_edges(NodeType.OPENING, opening, frozenset({EdgeType.BELONGS_TO_OPENING})):
            if edge.from_key == game_id:
                continue
            related.append({"game_id": edge.from_key, "shared_opening": opening})
            if len(related) >= 10:
                break

    return {
        "game_id": game_id,
        "found": True,
        "game": {
            "white": game.white_player_name,
            "black": game.black_player_name,
            "result": game.result,
            "date": game.date,
            "eco_code": game.eco_code,
            "opening_name": game.opening_name,
            "analysis_status": game.analysis_status,
        },
        "openings": openings,
        "positions": positions,
        "patterns": patterns,
        "training": training,
        "related_games": related,
    }


# --- player explorer ----------------------------------------------------------


def player_explorer(db: Session, player_id: int, *, service: IntelligenceGraphService | None = None) -> dict[str, Any]:
    """The graph around one player, respecting privacy."""
    service = service or service_for(db)
    player = db.get(Player, player_id)
    if player is None:
        return {"player_id": player_id, "found": False, "note": "No such player is stored."}

    game_ids = [
        edge.to_key
        for edge in service.store.out_edges(NodeType.PLAYER, str(player_id), frozenset({EdgeType.PLAYED}))
    ]
    patterns = [
        _node_payload(other)
        for edge, other in service.neighbors(
            NodeType.PLAYER, str(player_id), edge_types={EdgeType.HAS_PATTERN}, direction="out"
        )
        if other is not None
    ]
    training_positions = [
        _node_payload(other)
        for edge, other in service.neighbors(
            NodeType.PLAYER, str(player_id), edge_types={EdgeType.TRAINED_WITH}, direction="out"
        )
        if other is not None
    ]
    attempts = [
        _node_payload(other)
        for edge, other in service.neighbors(
            NodeType.PLAYER, str(player_id), edge_types={EdgeType.ATTEMPTED}, direction="out"
        )
        if other is not None
    ]
    openings: list[str] = []
    opponents: list[int] = []
    for game_id in game_ids:
        if not _game_visible(db, game_id):
            continue
        game = db.get(Game, game_id)
        if game is None:
            continue
        for edge in service.store.out_edges(NodeType.GAME, game_id, frozenset({EdgeType.BELONGS_TO_OPENING})):
            if edge.to_key not in openings:
                openings.append(edge.to_key)
        other = game.black_player_id if game.white_player_id == player_id else game.white_player_id
        if other is not None and int(other) != player_id and int(other) not in opponents:
            opponents.append(int(other))

    correct = sum(1 for node in attempts if node.get("attributes", {}).get("correctness") == "correct")
    return {
        "player_id": player_id,
        "found": True,
        "player": {"name": player.name, "title": player.title, "platform": player.platform},
        "games": game_ids,
        "game_count": len(game_ids),
        "openings": openings,
        "patterns": patterns,
        "training_positions": training_positions,
        "training_attempts": len(attempts),
        "training_correct": correct,
        "opponents": opponents,
    }


# --- why / health / search ----------------------------------------------------


def why(db: Session, node_type: NodeType, node_key: str, *, service: IntelligenceGraphService | None = None) -> dict[str, Any]:
    """The "Why?" surface (§43): relationship, evidence, sample, methodology, dates."""
    service = service or service_for(db)
    node = service.get_node(node_type, node_key)
    if node is None:
        return {
            "node": f"{node_type.value}:{node_key}",
            "found": False,
            "note": "Caissa has no such node available to this caller.",
        }
    traced = service.trace_evidence(node_type, node_key)
    references = [
        ref
        for group in traced["evidence"].values()
        for ref in group
    ]
    date_range = _date_range(references)
    return {
        "node": traced["node"],
        "found": True,
        "label": node.label,
        "relationships": traced["hops"],
        "evidence": traced["evidence"],
        "evidence_count": traced["evidence_count"],
        "sample_size": max((hop.get("sample_size") or 0) for hop in traced["hops"]) if traced["hops"] else None,
        "methodology_version": node.methodology_version,
        "date_range": date_range,
        "gaps": traced["gaps"],
        "ranking_methodology": ranking_methodology(),
    }


def neighborhood(
    db: Session,
    node_type: NodeType,
    node_key: str,
    *,
    depth: int = 2,
    limit: int = 60,
    service: IntelligenceGraphService | None = None,
) -> dict[str, Any]:
    """The Intelligence Map (§39): the multi-hop neighbourhood around one node.

    A bounded, authorized breadth-first walk in both directions. The subgraph is
    returned exactly as the traversal found it — nodes, and edges carrying their
    controlled type plus evidence and sample counts — together with how many
    nodes were withheld by authorization and whether the walk was truncated by
    the node limit. A missing root is refused with a reason, never fabricated.

    Similarity keeps its own name: a ``similar_to`` edge is labelled with the
    level the classifier assigned and is never drawn as an exact match.
    """
    service = service or service_for(db)
    root = service.get_node(node_type, node_key)
    if root is None:
        return {
            "node": f"{node_type.value}:{node_key}",
            "found": False,
            "note": "Caissa has no such node available to this caller.",
        }
    traversal = service.traverse(
        node_type, node_key, direction="both", max_depth=depth, limit=limit
    )
    return {
        "node": traversal.root,
        "found": True,
        "label": root.label,
        "node_type": node_type.value,
        "depth": int(depth),
        "limit": traversal.limit,
        "truncated": traversal.truncated,
        "denied_count": traversal.denied_count,
        "nodes": [node.to_payload() for node in traversal.nodes],
        "edges": [edge.to_payload(include_evidence=False) for edge in traversal.edges],
        "hops": [hop.model_dump(mode="json") for hop in traversal.hops],
        "methodology_version": service.methodology_version,
        "note": (
            "Every node and edge here is stored and authorized. An edge keeps its "
            "own relationship name; a similarity level is never upgraded."
        ),
    }


def _date_range(references: list[dict[str, Any]]) -> dict[str, Any]:
    dates = sorted(
        str(ref["created_at"]) for ref in references if ref.get("created_at")
    )
    if not dates:
        return {"start": None, "end": None}
    return {"start": dates[0], "end": dates[-1]}


def graph_health(db: Session, *, service: IntelligenceGraphService | None = None) -> dict[str, Any]:
    service = service or service_for(db)
    return service.health()


def graph_snapshot(db: Session, *, persist: bool = True) -> dict[str, Any]:
    """Take (and optionally store) a reproducible graph snapshot (§8/§37)."""
    service = service_for(db)
    snapshot: GraphSnapshot = service.snapshot()
    if persist:
        db.add(
            GraphSnapshotRecord(
                graph_version=snapshot.graph_version,
                schema_version=snapshot.schema_version,
                methodology_version=snapshot.methodology_version,
                data_cutoff=snapshot.data_cutoff,
                node_counts=snapshot.node_counts,
                edge_counts=snapshot.edge_counts,
                total_nodes=snapshot.total_nodes,
                total_edges=snapshot.total_edges,
            )
        )
        db.commit()
    return snapshot.to_payload()


def structured_search(
    db: Session,
    *,
    kind: str,
    eco_code: str | None = None,
    pattern_type: str | None = None,
    result: str | None = None,
    opponent_id: int | None = None,
    training_category: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Graph-backed search (§38): resolve a structured query to stored objects.

    Only the filters Caissa can prove are accepted; an unknown filter is reported,
    not ignored. This is the structured half of unified search — free-text queries
    still go through the existing Phase 11 search.
    """
    service = service_for(db)
    results: list[dict[str, Any]] = []
    if kind == "games":
        query = select(Game)
        # Ownership: search returns only games the caller may read.
        caller = current_caller_id()
        if caller is not None:
            query = query.where(or_(Game.owner.is_(None), Game.owner == caller))
        if eco_code:
            query = query.where(Game.eco_code == eco_code.upper())
        if result:
            query = query.where(Game.result == result)
        if opponent_id is not None:
            query = query.where(
                (Game.white_player_id == opponent_id) | (Game.black_player_id == opponent_id)
            )
        for game in db.execute(query.order_by(Game.created_at.desc()).limit(limit)).scalars():
            if pattern_type:
                # Keep only games whose graph positions carry a pattern of this type.
                types = frozenset({EdgeType.PATTERN_EVIDENCE})
                has_pattern = False
                for edge in service.store.out_edges(NodeType.GAME, game.id, frozenset({EdgeType.CONTAINS_POSITION})):
                    for _e, node in service.neighbors(NodeType.POSITION, edge.to_key, edge_types=types, direction="in"):
                        if node is not None and node.attributes.get("pattern_type") == pattern_type:
                            has_pattern = True
                            break
                    if has_pattern:
                        break
                if not has_pattern:
                    continue
            results.append({"game_id": game.id, "white": game.white_player_name, "black": game.black_player_name, "result": game.result, "eco_code": game.eco_code})
    elif kind == "patterns":
        node_type = NodeType.TACTICAL_PATTERN if pattern_type == "tactical" else (
            NodeType.POSITIONAL_PATTERN if pattern_type == "positional" else NodeType.PATTERN
        )
        attrs = {"pattern_type": pattern_type} if pattern_type else None
        for node in service.nodes_of_type(node_type, attributes=attrs, limit=limit):
            results.append(_node_payload(node))
    elif kind == "training":
        query = select(TrainingPosition)
        if training_category:
            query = query.where(TrainingPosition.category == training_category)
        for row in db.execute(query.order_by(TrainingPosition.id.desc()).limit(limit)).scalars():
            results.append(
                {
                    "training_position_id": row.id,
                    "category": row.category,
                    "difficulty": row.difficulty,
                    "source_game_id": row.source_game_id,
                    "source_reason": row.source_reason,
                }
            )
    else:
        return {
            "kind": kind,
            "supported": False,
            "note": "Supported kinds: games, patterns, training.",
            "results": [],
        }
    return {
        "kind": kind,
        "supported": True,
        "count": len(results),
        "results": results,
        "methodology_version": GRAPH_METHODOLOGY_VERSION,
        "note": None if results else "No verified match was found for those filters.",
    }


__all__ = [
    "game_explorer",
    "graph_health",
    "graph_snapshot",
    "neighborhood",
    "player_explorer",
    "position_explorer",
    "structured_search",
    "why",
]
