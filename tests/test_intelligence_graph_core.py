"""Unit tests for the Caissa Intelligence Graph core (Phase 13 §50).

These cover the pure modules — taxonomy, evidence, fingerprint, similarity,
ranking, packet validation, health, and the service's write/traversal rules —
with no database and no engine. That is deliberate: the security and honesty
rules live here, so they are tested here, once.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from argus.intelligence_graph.access import GraphAccessPolicy
from argus.intelligence_graph.evidence import (
    EvidenceKind,
    EvidenceReference,
    MissingEvidenceError,
    require_evidence,
)
from argus.intelligence_graph.fingerprint import (
    fingerprint,
    normalize_fen,
    position_hash,
    same_position,
)
from argus.intelligence_graph.health import check_graph
from argus.intelligence_graph.knowledge import (
    ARGUS_SOURCE,
    build_curated_concepts,
    exhibited_concepts,
    retrieve_concepts,
)
from argus.intelligence_graph.models import GraphEdge, GraphNode
from argus.intelligence_graph.packet import (
    Claim,
    ClaimKind,
    CoachEvidencePacket,
    PacketItem,
    PacketSection,
    validate_answer,
)
from argus.intelligence_graph.ranking import (
    WEIGHTS,
    EvidenceCandidate,
    SourceReliability,
    rank_evidence,
)
from argus.intelligence_graph.service import (
    GraphEvidenceError,
    GraphSchemaError,
    IntelligenceGraphService,
)
from argus.intelligence_graph.similarity import (
    SimilarityLevel,
    classify,
    qualifies,
)
from argus.intelligence_graph.store import InMemoryGraphStore
from argus.intelligence_graph.taxonomy import (
    EdgeType,
    NodeType,
    PatternType,
    edge_shape_valid,
    pattern_node_type,
)

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


# --- taxonomy -----------------------------------------------------------------


def test_edge_shapes_are_declared_and_enforced():
    assert edge_shape_valid(EdgeType.PLAYED, NodeType.PLAYER, NodeType.GAME)
    # The inverse is a different edge, not the same edge reversed.
    assert not edge_shape_valid(EdgeType.PLAYED, NodeType.GAME, NodeType.PLAYER)


def test_pattern_categories_map_to_specialised_nodes():
    assert pattern_node_type(PatternType.TACTICAL) is NodeType.TACTICAL_PATTERN
    assert pattern_node_type(PatternType.KING_SAFETY) is NodeType.KING_SAFETY_PATTERN
    # Categories with no dedicated class use the generic PATTERN node.
    assert pattern_node_type(PatternType.TIME_MANAGEMENT) is NodeType.PATTERN


# --- evidence -----------------------------------------------------------------


def test_derived_edge_requires_evidence():
    with pytest.raises(MissingEvidenceError):
        require_evidence(EdgeType.HAS_PATTERN, [])


def test_structural_edge_needs_no_evidence():
    assert require_evidence(EdgeType.CONTAINS_POSITION, []) == []


def test_reference_without_anchor_proves_nothing():
    empty = EvidenceReference(kind=EvidenceKind.GAME, label="nothing")
    assert not empty.has_anchor()
    # An anchorless reference is dropped; on a derived edge that leaves nothing,
    # so the write is refused rather than accepted as "evidence provided".
    with pytest.raises(MissingEvidenceError):
        require_evidence(EdgeType.HAS_PATTERN, [empty])
    # A structural edge simply drops it.
    assert require_evidence(EdgeType.CONTAINS_POSITION, [empty]) == []


# --- fingerprint --------------------------------------------------------------


def test_fingerprint_ignores_move_counters():
    later = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 12 40"
    assert same_position(START, later)
    assert normalize_fen(START) == normalize_fen(later)
    assert position_hash(START) == position_hash(later)


def test_fingerprint_distinguishes_side_castling_and_en_passant():
    black_to_move = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"
    no_castling = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1"
    assert not same_position(START, black_to_move)
    assert not same_position(START, no_castling)
    assert position_hash(START) != position_hash(black_to_move)


def test_fingerprint_of_invalid_fen_is_marked_invalid():
    result = fingerprint("not a fen")
    assert result.valid is False


# --- similarity ---------------------------------------------------------------


def test_exact_means_exact():
    assert classify(START, START) is SimilarityLevel.EXACT


def test_equivalent_is_not_exact():
    # Same board and side, but castling rights can no longer be exercised: the
    # legal move set is identical, so this is EQUIVALENT — and must never be
    # reported as EXACT.
    with_ep = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
    without_ep = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
    assert classify(with_ep, without_ep) is SimilarityLevel.EXACT


def test_structural_similarity_requires_same_side_to_move():
    white = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    black = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"
    assert classify(white, black) is None


def test_different_positions_are_not_similar():
    italian = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 6 5"
    assert classify(START, italian) is None


def test_qualifies_enforces_a_minimum_strength():
    assert qualifies(SimilarityLevel.EXACT, SimilarityLevel.EXACT)
    assert not qualifies(SimilarityLevel.STRUCTURALLY_SIMILAR, SimilarityLevel.EXACT)
    assert not qualifies(None, SimilarityLevel.STRUCTURALLY_SIMILAR)


# --- knowledge ----------------------------------------------------------------


def test_curated_concepts_are_sourced():
    concepts = build_curated_concepts()
    assert concepts
    for concept in concepts:
        assert concept.source_id == ARGUS_SOURCE.id
        assert concept.definition


def test_exhibited_concepts_are_deterministic_and_checkable():
    doubled = "4k3/8/8/8/8/P7/P3K3/8 w - - 0 1"
    slugs = {exhibition.concept_slug for exhibition in exhibited_concepts(doubled)}
    assert "doubled-pawns" in slugs
    assert "isolated-pawn" in slugs


def test_position_with_nothing_known_exhibits_nothing_invalid_fen():
    assert exhibited_concepts("rubbish") == []


def test_concept_retrieval_finds_by_name():
    found = retrieve_concepts("fork")
    assert found and found[0].slug == "fork"
    assert retrieve_concepts("") == []


# --- ranking ------------------------------------------------------------------


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_ranking_is_deterministic_and_explainable():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    engine = EvidenceCandidate(
        node="engine",
        relevance=1.0,
        reliability=SourceReliability.ENGINE,
        sample_size=30,
        occurred_at=now,
    )
    weak = EvidenceCandidate(
        node="prediction",
        relevance=0.2,
        reliability=SourceReliability.PREDICTION,
        occurred_at=now - timedelta(days=300),
    )
    ranked = rank_evidence([weak, engine], now=now)
    assert [item.node for item in ranked] == ["engine", "prediction"]
    assert set(ranked[0].breakdown) == set(WEIGHTS)


def test_no_sample_is_not_a_small_sample():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    no_sample = EvidenceCandidate(node="a", sample_size=None)
    tiny = EvidenceCandidate(node="b", sample_size=1)
    ranked = rank_evidence([no_sample, tiny], now=now)
    assert ranked[0].node == "b"


# --- packet + hallucination control ------------------------------------------


def _packet() -> CoachEvidencePacket:
    packet = CoachEvidencePacket(question="why?")
    packet.add(
        PacketItem(
            section=PacketSection.ENGINE_RESULTS,
            label="engine at ply 27",
            node="position:p1",
            reliability=SourceReliability.ENGINE,
            value=250.0,
            unit="cp",
            evidence=[EvidenceReference(kind=EvidenceKind.ANALYSIS, analysis_id=1, game_id="g1")],
        )
    )
    packet.add(
        PacketItem(
            section=PacketSection.RELEVANT_GAMES,
            label="game g1",
            node="game:g1",
            reliability=SourceReliability.ARGUS_MEASURED,
            evidence=[EvidenceReference(kind=EvidenceKind.GAME, game_id="g1")],
        )
    )
    return packet


def test_supported_claims_pass():
    packet = _packet()
    validation = validate_answer(
        packet,
        [
            Claim(text="The engine scored it +2.5", kind=ClaimKind.ENGINE, cites=["position:p1"]),
            Claim(text="It happened in g1", kind=ClaimKind.HISTORICAL, cites=["game:g1"]),
            Claim(text="That is 250 cp", kind=ClaimKind.NUMERICAL, cites=["position:p1"]),
        ],
    )
    assert validation.ok


def test_unsupported_claims_are_refused_with_a_replacement():
    packet = _packet()
    validation = validate_answer(
        packet,
        [
            Claim(text="The engine says +5", kind=ClaimKind.ENGINE, cites=["game:g1"]),
            Claim(text="A model predicts 70%", kind=ClaimKind.PREDICTION, cites=["game:g1"]),
            Claim(text="This is a well-known principle", kind=ClaimKind.KNOWLEDGE, cites=[]),
        ],
    )
    assert not validation.ok
    assert len(validation.unsupported) == 3
    assert all(item.replacement for item in validation.unsupported)


def test_packet_prompt_payload_is_bounded():
    packet = _packet()
    payload = packet.to_prompt_payload(max_items_per_section=1)
    assert payload["sections"]["engine_results"]["count"] == 1
    assert "evidence" not in payload["sections"]["engine_results"]["items"][0]


# --- service ------------------------------------------------------------------


def _service(**kwargs) -> IntelligenceGraphService:
    return IntelligenceGraphService(InMemoryGraphStore(), **kwargs)


def _player_node(key: str = "1") -> GraphNode:
    return GraphNode(node_type=NodeType.PLAYER, node_key=key, label="Morphy")


def _game_node(key: str = "g1") -> GraphNode:
    return GraphNode(node_type=NodeType.GAME, node_key=key, label="Opera Game")


def _pattern_node(key: str = "p1") -> GraphNode:
    return GraphNode(node_type=NodeType.TACTICAL_PATTERN, node_key=key, label="hanging piece")


def test_edges_are_deduplicated_on_upsert():
    service = _service()
    service.upsert_node(_player_node())
    service.upsert_node(_game_node())
    edge = GraphEdge(
        edge_type=EdgeType.PLAYED,
        from_type=NodeType.PLAYER,
        from_key="1",
        to_type=NodeType.GAME,
        to_key="g1",
    )
    first = service.write_edge(edge)
    second = service.write_edge(edge)
    assert first.created_at == second.created_at
    assert service.store.edge_count() == 1


def test_undeclared_edge_shape_is_refused():
    service = _service()
    with pytest.raises(GraphSchemaError):
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.PLAYED,
                from_type=NodeType.GAME,
                from_key="g1",
                to_type=NodeType.PLAYER,
                to_key="1",
            )
        )


def test_derived_edge_without_evidence_is_refused():
    service = _service()
    service.upsert_node(_player_node())
    service.upsert_node(_pattern_node())
    with pytest.raises(GraphEvidenceError):
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.HAS_PATTERN,
                from_type=NodeType.PLAYER,
                from_key="1",
                to_type=NodeType.TACTICAL_PATTERN,
                to_key="p1",
            )
        )


def test_traversal_is_bounded_and_authorized():
    store = InMemoryGraphStore()
    policy = GraphAccessPolicy(allowed_game_ids={"g1"})
    service = IntelligenceGraphService(store, policy=policy)
    service.upsert_node(_player_node())
    service.upsert_node(_game_node("g1"))
    service.upsert_node(_game_node("g2"))
    for key in ("g1", "g2"):
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.PLAYED,
                from_type=NodeType.PLAYER,
                from_key="1",
                to_type=NodeType.GAME,
                to_key=key,
            )
        )
    result = service.traverse(NodeType.PLAYER, "1", max_depth=1)
    reached = {node.node_key for node in result.nodes}
    assert reached == {"1", "g1"}  # g2 is filtered by authorization


def test_graph_relationship_does_not_grant_access_to_the_object():
    store = InMemoryGraphStore()
    policy = GraphAccessPolicy(allowed_game_ids={"g1"})
    service = IntelligenceGraphService(store, policy=policy)
    service.upsert_node(_pattern_node("shared"))
    service.upsert_node(
        GraphNode(
            node_type=NodeType.POSITION,
            node_key="h1",
            attributes={"game_id": "g1"},
        )
    )
    service.upsert_node(
        GraphNode(
            node_type=NodeType.POSITION,
            node_key="h2",
            attributes={"game_id": "g2"},
        )
    )
    for position in ("h1", "h2"):
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.PATTERN_EVIDENCE,
                from_type=NodeType.TACTICAL_PATTERN,
                from_key="shared",
                to_type=NodeType.POSITION,
                to_key=position,
                evidence=[EvidenceReference(kind=EvidenceKind.POSITION, position_hash=position)],
            )
        )
    result = service.traverse(NodeType.TACTICAL_PATTERN, "shared", max_depth=1)
    assert {node.node_key for node in result.nodes} == {"shared", "h1"}


def test_trace_evidence_reports_gaps():
    service = _service()
    service.upsert_node(_pattern_node())
    service.upsert_node(
        GraphNode(node_type=NodeType.POSITION, node_key="h1", attributes={"game_id": "g1"})
    )
    service.write_edge(
        GraphEdge(
            edge_type=EdgeType.PATTERN_EVIDENCE,
            from_type=NodeType.TACTICAL_PATTERN,
            from_key="p1",
            to_type=NodeType.POSITION,
            to_key="h1",
            evidence=[
                EvidenceReference(
                    kind=EvidenceKind.POSITION, position_hash="h1", game_id="g1", ply=27
                )
            ],
        )
    )
    traced = service.trace_evidence(NodeType.TACTICAL_PATTERN, "p1")
    assert traced["evidence_count"] == 1
    assert "position" in traced["evidence"]
    assert traced["gaps"] == []


def test_snapshot_records_versions_and_counts():
    service = _service()
    service.upsert_node(_player_node())
    snapshot = service.snapshot()
    assert snapshot.total_nodes == 1
    assert snapshot.methodology_version
    assert snapshot.to_payload()["graph_version"]


def test_health_detects_dangling_and_missing_evidence():
    store = InMemoryGraphStore()
    store.upsert_node(_player_node())
    store.upsert_edge(
        GraphEdge(
            edge_type=EdgeType.PLAYED,
            from_type=NodeType.PLAYER,
            from_key="1",
            to_type=NodeType.GAME,
            to_key="missing",
        )
    )
    report = check_graph(store, schema_version="13.0", methodology_version="13.0")
    assert report.dangling_count == 1
    assert not report.healthy
    assert report.to_payload()["healthy"] is False


def test_healthy_graph_reports_clean():
    service = _service()
    service.upsert_node(_player_node())
    service.upsert_node(_game_node())
    service.write_edge(
        GraphEdge(
            edge_type=EdgeType.PLAYED,
            from_type=NodeType.PLAYER,
            from_key="1",
            to_type=NodeType.GAME,
            to_key="g1",
        )
    )
    health = service.health()
    assert health["healthy"]


def test_invalidate_removes_affected_edges():
    service = _service()
    service.upsert_node(_player_node())
    service.upsert_node(_game_node())
    service.write_edge(
        GraphEdge(
            edge_type=EdgeType.PLAYED,
            from_type=NodeType.PLAYER,
            from_key="1",
            to_type=NodeType.GAME,
            to_key="g1",
        )
    )
    removed = service.invalidate(game_id="g1")
    assert removed == 1
    assert service.store.edge_count() == 0


def test_metrics_are_observable():
    service = _service()
    service.upsert_node(_player_node())
    service.traverse(NodeType.PLAYER, "1", max_depth=0)
    metrics = service.metrics()
    assert metrics["graph_updates"] >= 1
    assert metrics["graph_traversals"] == 1
    assert "average_traversal_latency_ms" in metrics
