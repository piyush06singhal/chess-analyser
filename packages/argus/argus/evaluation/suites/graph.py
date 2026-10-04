"""Intelligence graph evaluation (§28/§29).

Checks the graph's promises directly, on an in-memory store so the suite is
deterministic and needs no database: a derived edge needs evidence, a reversed
edge is refused, a traversal is bounded, an unauthorized node is hidden, and
similarity is never upgraded to an exact match.
"""

from __future__ import annotations

from argus.evaluation.results import SuiteResult, check
from argus.intelligence_graph.access import GraphAccessPolicy
from argus.intelligence_graph.evidence import EvidenceKind, EvidenceReference
from argus.intelligence_graph.fingerprint import fingerprint, position_hash
from argus.intelligence_graph.health import check_graph
from argus.intelligence_graph.models import GraphEdge, GraphNode
from argus.intelligence_graph.service import (
    GraphEvidenceError,
    GraphSchemaError,
    IntelligenceGraphService,
)
from argus.intelligence_graph.similarity import SimilarityLevel, classify
from argus.intelligence_graph.store import InMemoryGraphStore
from argus.intelligence_graph.taxonomy import EdgeType, NodeType


def _service(policy: GraphAccessPolicy | None = None) -> IntelligenceGraphService:
    return IntelligenceGraphService(InMemoryGraphStore(), policy=policy)


def _game_node(game_id: str = "g1") -> GraphNode:
    return GraphNode(
        node_type=NodeType.GAME,
        node_key=game_id,
        label=f"game {game_id}",
        attributes={"game_id": game_id},
    )


def graph_suite(context) -> SuiteResult:
    """Node/edge correctness, evidence, traversal, authorization, similarity."""
    checks = []
    service = _service()

    # --- structural edge: allowed with no evidence ---------------------------
    service.upsert_node(_game_node())
    service.upsert_node(
        GraphNode(
            node_type=NodeType.POSITION,
            node_key="p1",
            label="position p1",
            attributes={"game_id": "g1"},
        )
    )
    service.write_edge(
        GraphEdge(
            edge_type=EdgeType.CONTAINS_POSITION,
            from_type=NodeType.GAME,
            from_key="g1",
            to_type=NodeType.POSITION,
            to_key="p1",
        )
    )
    checks.append(
        check(
            "a structural edge needs no evidence",
            bool(service.store.out_edges(NodeType.GAME, "g1")),
        )
    )

    # --- derived edge: refused without evidence ------------------------------
    service.upsert_node(
        GraphNode(node_type=NodeType.TACTICAL_PATTERN, node_key="pat1", label="fork")
    )
    refused = False
    try:
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.HAS_PATTERN,
                from_type=NodeType.PLAYER,
                from_key="1",
                to_type=NodeType.TACTICAL_PATTERN,
                to_key="pat1",
            )
        )
    except GraphEvidenceError:
        refused = True
    checks.append(
        check(
            "a derived edge without evidence is refused",
            refused,
            detail="has_pattern requires an EvidenceReference",
            critical=True,
        )
    )

    # --- an evidenced derived edge is written and traceable ------------------
    service.upsert_node(
        GraphNode(
            node_type=NodeType.PLAYER,
            node_key="1",
            label="player 1",
            attributes={"player_id": 1},
        )
    )
    service.write_edge(
        GraphEdge(
            edge_type=EdgeType.HAS_PATTERN,
            from_type=NodeType.PLAYER,
            from_key="1",
            to_type=NodeType.TACTICAL_PATTERN,
            to_key="pat1",
            evidence=[
                EvidenceReference(kind=EvidenceKind.GAME, game_id="g1", ply=12)
            ],
        )
    )
    trace = service.trace_evidence(NodeType.TACTICAL_PATTERN, "pat1")
    checks.append(
        check(
            "an evidenced edge traces to its reference",
            trace["evidence_count"] >= 1 and not trace["gaps"],
            detail=f"evidence={trace['evidence_count']}, gaps={len(trace['gaps'])}",
            critical=True,
        )
    )

    # --- shape violation refused --------------------------------------------
    reversed_refused = False
    try:
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.PLAYED,
                from_type=NodeType.GAME,
                from_key="g1",
                to_type=NodeType.PLAYER,
                to_key="1",
            )
        )
    except GraphSchemaError:
        reversed_refused = True
    checks.append(
        check(
            "a reversed edge shape is refused",
            reversed_refused,
            detail="played is player -> game",
            critical=True,
        )
    )

    # --- traversal is bounded ------------------------------------------------
    result = service.traverse(NodeType.GAME, "g1", limit=1)
    checks.append(
        check(
            "a limited traversal reports truncation",
            len(result.nodes) <= 1 and result.truncated,
            detail=f"nodes={len(result.nodes)}, truncated={result.truncated}",
        )
    )

    # --- authorization -------------------------------------------------------
    restricted = GraphAccessPolicy(allowed_game_ids={"other-game"})
    restricted_service = _service(restricted)
    restricted_service.store.upsert_node(_game_node("g1"))
    hidden = restricted_service.get_node(NodeType.GAME, "g1")
    checks.append(
        check(
            "an unauthorized node is hidden",
            hidden is None and restricted.denials == 1,
            detail="denied and counted, never returned",
            critical=True,
        )
    )

    # --- health --------------------------------------------------------------
    report = check_graph(
        service.store,
        schema_version=service.schema_version,
        methodology_version=service.methodology_version,
    )
    checks.append(
        check(
            "a consistent graph is healthy",
            report.healthy,
            detail=f"orphans={len(report.orphan_nodes)}, invalid={len(report.invalid_edges)}",
        )
    )

    # --- similarity / fingerprint -------------------------------------------
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    start_bookkeeping = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 9"
    checks.append(
        check(
            "bookkeeping differences are the same position",
            position_hash(start) == position_hash(start_bookkeeping),
            detail="halfmove/fullmove excluded from identity",
        )
    )
    checks.append(
        check(
            "a different side to move is a different position",
            position_hash(start) != position_hash(start.replace(" w ", " b ")),
            critical=True,
        )
    )
    checks.append(
        check(
            "identical positions classify as exact",
            classify(start, start_bookkeeping) is SimilarityLevel.EXACT,
        )
    )
    checks.append(
        check(
            "a different position is never called exact",
            classify(start, "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1")
            is not SimilarityLevel.EXACT,
            critical=True,
        )
    )
    checks.append(
        check(
            "an unrelated position yields no verified similarity",
            classify(
                start,
                "8/8/8/4k3/8/8/8/4K3 w - - 0 1",
            )
            is None,
            detail="nothing holds -> None, never a weaker label",
        )
    )
    checks.append(
        check(
            "an invalid FEN has no fingerprint",
            fingerprint("not a fen").valid is False,
        )
    )

    return SuiteResult(
        suite="graph",
        title="Intelligence graph correctness and provenance",
        checks=checks,
        methodology_version="13.0",
    )


__all__ = ["graph_suite"]
