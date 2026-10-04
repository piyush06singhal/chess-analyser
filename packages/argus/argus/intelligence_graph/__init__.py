"""Caissa Intelligence Graph (Phase 13): connecting existing evidence, honestly.

This package sits *above* the domain models. It introduces no new chess concepts
and stores no duplicate game data: it records identity and relationships between
the objects Caissa already has (players, games, positions, openings, patterns,
training, opponents, scenarios, knowledge), and it refuses to store a derived
relationship without evidence.

The public surface is small on purpose:

* :mod:`~argus.intelligence_graph.taxonomy` — the controlled node/edge vocabulary;
* :class:`~argus.intelligence_graph.evidence.EvidenceReference` — how a derived
  claim points back at the stored objects behind it;
* :class:`~argus.intelligence_graph.service.IntelligenceGraphService` — validated
  writes and bounded, authorized traversal;
* :mod:`~argus.intelligence_graph.similarity` — the controlled similarity levels;
* :mod:`~argus.intelligence_graph.knowledge` — source-backed concepts and the
  deterministic rules that link a position to them;
* :class:`~argus.intelligence_graph.packet.CoachEvidencePacket` — what the coach
  model is given, and the validation that keeps it honest.
"""

from argus.intelligence_graph.access import GraphAccessPolicy, unrestricted
from argus.intelligence_graph.evidence import (
    EvidenceKind,
    EvidenceReference,
    MissingEvidenceError,
    merge_references,
    require_evidence,
)
from argus.intelligence_graph.fingerprint import (
    PositionFingerprint,
    fingerprint,
    normalize_fen,
    position_hash,
    same_position,
)
from argus.intelligence_graph.health import GraphHealthReport, check_graph
from argus.intelligence_graph.knowledge import (
    ARGUS_DOCUMENT,
    ARGUS_SOURCE,
    ConceptExhibition,
    KnowledgeChunk,
    KnowledgeConcept,
    KnowledgeDocument,
    KnowledgeSource,
    build_curated_concepts,
    exhibited_concepts,
    retrieve_concepts,
)
from argus.intelligence_graph.models import (
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    TraversalHop,
    TraversalResult,
)
from argus.intelligence_graph.packet import (
    AnswerValidation,
    Claim,
    ClaimKind,
    ClaimValidation,
    CoachEvidencePacket,
    PacketItem,
    PacketSection,
    render_packet,
    validate_answer,
    validate_claim,
)
from argus.intelligence_graph.ranking import (
    WEIGHTS,
    EvidenceCandidate,
    RankedEvidence,
    SourceReliability,
    rank_evidence,
    score_candidate,
)
from argus.intelligence_graph.service import (
    GraphEvidenceError,
    GraphSchemaError,
    IntelligenceGraphService,
)
from argus.intelligence_graph.similarity import (
    DEFINITIONS as SIMILARITY_DEFINITIONS,
    SimilarityLevel,
    classify,
    describe as describe_similarity,
    qualifies,
)
from argus.intelligence_graph.store import GraphStore, InMemoryGraphStore
from argus.intelligence_graph.taxonomy import (
    GRAPH_METHODOLOGY_VERSION,
    GRAPH_SCHEMA_VERSION,
    EdgeType,
    NodeType,
    PatternType,
    edge_shape_valid,
    pattern_node_type,
)

__all__ = [
    "ARGUS_DOCUMENT",
    "ARGUS_SOURCE",
    "AnswerValidation",
    "Claim",
    "ClaimKind",
    "ClaimValidation",
    "CoachEvidencePacket",
    "ConceptExhibition",
    "EdgeType",
    "EvidenceCandidate",
    "EvidenceKind",
    "EvidenceReference",
    "GRAPH_METHODOLOGY_VERSION",
    "GRAPH_SCHEMA_VERSION",
    "GraphAccessPolicy",
    "GraphEdge",
    "GraphEvidenceError",
    "GraphHealthReport",
    "GraphNode",
    "GraphSchemaError",
    "GraphSnapshot",
    "GraphStore",
    "InMemoryGraphStore",
    "IntelligenceGraphService",
    "KnowledgeChunk",
    "KnowledgeConcept",
    "KnowledgeDocument",
    "KnowledgeSource",
    "MissingEvidenceError",
    "NodeType",
    "PacketItem",
    "PacketSection",
    "PatternType",
    "PositionFingerprint",
    "RankedEvidence",
    "SIMILARITY_DEFINITIONS",
    "SimilarityLevel",
    "SourceReliability",
    "TraversalHop",
    "TraversalResult",
    "WEIGHTS",
    "build_curated_concepts",
    "check_graph",
    "classify",
    "describe_similarity",
    "edge_shape_valid",
    "exhibited_concepts",
    "fingerprint",
    "merge_references",
    "normalize_fen",
    "pattern_node_type",
    "position_hash",
    "qualifies",
    "rank_evidence",
    "render_packet",
    "require_evidence",
    "retrieve_concepts",
    "same_position",
    "score_candidate",
    "unrestricted",
    "validate_answer",
    "validate_claim",
]
