"""The controlled vocabulary of the Caissa Intelligence Graph.

A graph is only as trustworthy as its vocabulary. If any code path can invent a
node kind or a relationship name, the graph becomes a second, uncontrolled data
store that disagrees with the domain models it claims to describe. So this module
is the *only* place node kinds and edge kinds are declared, and everything else
imports from here.

Two axes are defined:

* :class:`NodeType` — the kinds of thing the graph knows about. Every one maps to
  an existing domain entity (``Player``, ``Game``, ``TrainingPosition`` …) or to a
  derived entity that already has a home (``Insight``, ``Scenario``). The graph
  introduces **no new domain concepts**; it stores identity and relationships.
* :class:`EdgeType` — the kinds of relationship. Every edge kind is directional
  and names a *verifiable* connection between two stored things. There is no
  generic ``RELATED_TO`` by default: it exists, but it is explicitly marked as the
  weakest kind and every use must carry evidence, because "related" is the word a
  graph uses when it has nothing concrete to say.

Pattern categories (§16) are their own enum because a pattern is classified by
*what kind of chess problem it is*, which is independent of the node kind used to
store it. :func:`pattern_node_type` maps a pattern category to its node kind.
"""

from __future__ import annotations

from enum import Enum

#: Bumped whenever node/edge semantics change in a way that invalidates a stored
#: edge. A stored edge whose ``schema_version`` is older is reported as stale
#: rather than silently read.
GRAPH_SCHEMA_VERSION = "13.0"

#: Bumped whenever the *derivation* changes (how a pattern is detected, how a
#: similarity is computed). Evidence records the methodology that produced it, so
#: an insight can always name the method that is responsible for it.
GRAPH_METHODOLOGY_VERSION = "13.0"


class NodeType(str, Enum):
    """A controlled node kind. Values are stable strings, safe to persist."""

    PLAYER = "player"
    GAME = "game"
    MOVE = "move"
    POSITION = "position"
    OPENING = "opening"
    OPENING_NODE = "opening_node"
    GAME_PHASE = "game_phase"

    # Pattern family. The four specialised kinds carry a dedicated detector in
    # existing intelligence; the generic PATTERN kind covers categories (opening,
    # calculation, conversion, recovery, endgame, time management) that are
    # derived but have no separate node class.
    PATTERN = "pattern"
    TACTICAL_PATTERN = "tactical_pattern"
    POSITIONAL_PATTERN = "positional_pattern"
    KING_SAFETY_PATTERN = "king_safety_pattern"
    MATERIAL_PATTERN = "material_pattern"

    INSIGHT = "insight"
    TRAINING_POSITION = "training_position"
    TRAINING_ATTEMPT = "training_attempt"
    TRAINING_SESSION = "training_session"
    SCENARIO = "scenario"
    PREDICTION = "prediction"
    OPPONENT_PROFILE = "opponent_profile"
    PREPARATION_REPORT = "preparation_report"
    KNOWLEDGE_DOCUMENT = "knowledge_document"
    KNOWLEDGE_CONCEPT = "knowledge_concept"
    STUDY_ITEM = "study_item"

    @property
    def is_pattern(self) -> bool:
        return self in _PATTERN_NODE_TYPES


_PATTERN_NODE_TYPES = frozenset(
    {
        NodeType.PATTERN,
        NodeType.TACTICAL_PATTERN,
        NodeType.POSITIONAL_PATTERN,
        NodeType.KING_SAFETY_PATTERN,
        NodeType.MATERIAL_PATTERN,
    }
)


class PatternType(str, Enum):
    """§16 pattern categories. Only categories Caissa can actually detect exist."""

    TACTICAL = "tactical"
    POSITIONAL = "positional"
    KING_SAFETY = "king_safety"
    MATERIAL = "material"
    OPENING = "opening"
    CALCULATION = "calculation"
    CONVERSION = "conversion"
    RECOVERY = "recovery"
    ENDGAME = "endgame"
    TIME_MANAGEMENT = "time_management"


#: Pattern categories that map to a dedicated node kind; everything else is a
#: generic PATTERN node carrying its ``pattern_type`` attribute.
_SPECIALISED_PATTERN_NODES: dict[PatternType, NodeType] = {
    PatternType.TACTICAL: NodeType.TACTICAL_PATTERN,
    PatternType.POSITIONAL: NodeType.POSITIONAL_PATTERN,
    PatternType.KING_SAFETY: NodeType.KING_SAFETY_PATTERN,
    PatternType.MATERIAL: NodeType.MATERIAL_PATTERN,
}


def pattern_node_type(pattern_type: PatternType | str) -> NodeType:
    """The node kind that stores a given pattern category."""
    resolved = (
        pattern_type if isinstance(pattern_type, PatternType) else PatternType(str(pattern_type))
    )
    return _SPECIALISED_PATTERN_NODES.get(resolved, NodeType.PATTERN)


class EdgeType(str, Enum):
    """A controlled, directional relationship kind.

    Each value documents its ``(from → to)`` shape so a writer cannot accidentally
    reverse it. :func:`edge_shape` exposes that documentation programmatically.
    """

    # --- player ↔ game --------------------------------------------------------
    PLAYED = "played"  # player → game
    PARTICIPATED_IN = "participated_in"  # game → player (inverse of PLAYED)

    # --- game → position / move ----------------------------------------------
    CONTAINS_MOVE = "contains_move"  # game → move
    CONTAINS_POSITION = "contains_position"  # game → position
    OCCURS_IN = "occurs_in"  # move/position → game
    REACHES = "reaches"  # position → position (one ply on)
    DEVIATES_FROM = "deviates_from"  # position → opening_node

    # --- openings -------------------------------------------------------------
    BELONGS_TO_OPENING = "belongs_to_opening"  # game/position → opening
    PLAYS_OPENING = "plays_opening"  # player → opening
    FOLLOWS = "follows"  # opening_node → opening_node

    # --- patterns -------------------------------------------------------------
    HAS_PATTERN = "has_pattern"  # player → pattern
    PATTERN_EVIDENCE = "pattern_evidence"  # pattern → position/game
    HAS_INSIGHT = "has_insight"  # player → insight

    # --- training -------------------------------------------------------------
    GENERATED_TRAINING = "generated_training"  # pattern/game → training_position
    TRAINED_WITH = "trained_with"  # player/training_position → training_position
    ATTEMPTED = "attempted"  # player → training_attempt
    ATTEMPT_OF = "attempt_of"  # training_attempt → training_position
    IN_SESSION = "in_session"  # training_attempt → training_session
    IMPROVED_AFTER = "improved_after"  # pattern → training_position

    # --- similarity -----------------------------------------------------------
    SIMILAR_TO = "similar_to"  # position → position (carries a similarity level)

    # --- opponent -------------------------------------------------------------
    RESPONDED_WITH = "responded_with"  # position/player → move
    PREPARED_FOR = "prepared_for"  # preparation_report → opponent_profile

    # --- scenarios / predictions ---------------------------------------------
    DERIVED_FROM = "derived_from"  # scenario/prediction → game/position
    PREDICTED = "predicted"  # position → prediction

    # --- knowledge ------------------------------------------------------------
    EXHIBITS = "exhibits"  # position/pattern → knowledge_concept
    EXPLAINS = "explains"  # knowledge_concept/document → knowledge_concept
    SUPPORTED_BY = "supported_by"  # any node → evidence node
    DERIVED_FROM_KNOWLEDGE = "derived_from_knowledge"  # knowledge_chunk → document

    # --- collections ----------------------------------------------------------
    COLLECTED = "collected"  # study_item → entity

    #: The weakest kind. Permitted only with explicit evidence attached, because
    #: "related" without evidence is exactly the fabricated connection §52 forbids.
    RELATED_TO = "related_to"

    @property
    def is_evidence_bearing(self) -> bool:
        """Whether an edge of this kind is required to carry at least one source ref."""
        return self in _EVIDENCE_REQUIRED_EDGES


#: Edge kinds that are *derived* claims and therefore may never be stored without
#: an :class:`~argus.intelligence_graph.evidence.EvidenceReference`.
_EVIDENCE_REQUIRED_EDGES = frozenset(
    {
        EdgeType.HAS_PATTERN,
        EdgeType.PATTERN_EVIDENCE,
        EdgeType.HAS_INSIGHT,
        EdgeType.SIMILAR_TO,
        EdgeType.IMPROVED_AFTER,
        EdgeType.RESPONDED_WITH,
        EdgeType.DERIVED_FROM,
        EdgeType.PREDICTED,
        EdgeType.EXHIBITS,
        EdgeType.RELATED_TO,
        EdgeType.GENERATED_TRAINING,
        EdgeType.DEVIATES_FROM,
    }
)


#: The declared ``(from, to)`` shape of each edge kind. A writer that reverses an
#: edge is refused rather than trusted to have meant it.
_EDGE_SHAPES: dict[EdgeType, tuple[frozenset[NodeType], frozenset[NodeType]]] = {
    EdgeType.PLAYED: (frozenset({NodeType.PLAYER}), frozenset({NodeType.GAME})),
    EdgeType.PARTICIPATED_IN: (frozenset({NodeType.GAME}), frozenset({NodeType.PLAYER})),
    EdgeType.CONTAINS_MOVE: (frozenset({NodeType.GAME}), frozenset({NodeType.MOVE})),
    EdgeType.CONTAINS_POSITION: (
        frozenset({NodeType.GAME}),
        frozenset({NodeType.POSITION}),
    ),
    EdgeType.OCCURS_IN: (
        frozenset({NodeType.MOVE, NodeType.POSITION}),
        frozenset({NodeType.GAME}),
    ),
    EdgeType.REACHES: (frozenset({NodeType.POSITION}), frozenset({NodeType.POSITION})),
    EdgeType.DEVIATES_FROM: (
        frozenset({NodeType.POSITION, NodeType.GAME}),
        frozenset({NodeType.OPENING_NODE, NodeType.OPENING}),
    ),
    EdgeType.BELONGS_TO_OPENING: (
        frozenset({NodeType.GAME, NodeType.POSITION, NodeType.OPENING_NODE}),
        frozenset({NodeType.OPENING}),
    ),
    EdgeType.PLAYS_OPENING: (frozenset({NodeType.PLAYER}), frozenset({NodeType.OPENING})),
    EdgeType.FOLLOWS: (
        frozenset({NodeType.OPENING_NODE}),
        frozenset({NodeType.OPENING_NODE}),
    ),
    EdgeType.HAS_PATTERN: (frozenset({NodeType.PLAYER}), frozenset(_PATTERN_NODE_TYPES)),
    EdgeType.PATTERN_EVIDENCE: (
        frozenset(_PATTERN_NODE_TYPES),
        frozenset({NodeType.POSITION, NodeType.GAME, NodeType.MOVE}),
    ),
    EdgeType.HAS_INSIGHT: (frozenset({NodeType.PLAYER}), frozenset({NodeType.INSIGHT})),
    EdgeType.GENERATED_TRAINING: (
        frozenset(_PATTERN_NODE_TYPES | {NodeType.GAME, NodeType.SCENARIO, NodeType.INSIGHT}),
        frozenset({NodeType.TRAINING_POSITION}),
    ),
    EdgeType.TRAINED_WITH: (
        frozenset({NodeType.PLAYER, NodeType.TRAINING_POSITION, NodeType.PATTERN}),
        frozenset({NodeType.TRAINING_POSITION}),
    ),
    EdgeType.ATTEMPTED: (
        frozenset({NodeType.PLAYER}),
        frozenset({NodeType.TRAINING_ATTEMPT}),
    ),
    EdgeType.ATTEMPT_OF: (
        frozenset({NodeType.TRAINING_ATTEMPT}),
        frozenset({NodeType.TRAINING_POSITION}),
    ),
    EdgeType.IN_SESSION: (
        frozenset({NodeType.TRAINING_ATTEMPT}),
        frozenset({NodeType.TRAINING_SESSION}),
    ),
    EdgeType.IMPROVED_AFTER: (
        frozenset(_PATTERN_NODE_TYPES),
        frozenset({NodeType.TRAINING_POSITION}),
    ),
    EdgeType.SIMILAR_TO: (frozenset({NodeType.POSITION}), frozenset({NodeType.POSITION})),
    EdgeType.RESPONDED_WITH: (
        frozenset({NodeType.POSITION, NodeType.PLAYER}),
        frozenset({NodeType.MOVE}),
    ),
    EdgeType.PREPARED_FOR: (
        frozenset({NodeType.PREPARATION_REPORT}),
        frozenset({NodeType.OPPONENT_PROFILE, NodeType.PLAYER}),
    ),
    EdgeType.DERIVED_FROM: (
        frozenset({NodeType.SCENARIO, NodeType.PREDICTION, NodeType.TRAINING_POSITION}),
        frozenset({NodeType.GAME, NodeType.POSITION, NodeType.MOVE}),
    ),
    EdgeType.PREDICTED: (
        frozenset({NodeType.POSITION}),
        frozenset({NodeType.PREDICTION}),
    ),
    EdgeType.EXHIBITS: (
        frozenset(_PATTERN_NODE_TYPES | {NodeType.POSITION}),
        frozenset({NodeType.KNOWLEDGE_CONCEPT}),
    ),
    EdgeType.EXPLAINS: (
        frozenset({NodeType.KNOWLEDGE_CONCEPT, NodeType.KNOWLEDGE_DOCUMENT}),
        frozenset({NodeType.KNOWLEDGE_CONCEPT}),
    ),
    EdgeType.SUPPORTED_BY: (
        frozenset(NodeType),
        frozenset({NodeType.KNOWLEDGE_DOCUMENT, NodeType.KNOWLEDGE_CONCEPT}),
    ),
    EdgeType.DERIVED_FROM_KNOWLEDGE: (
        frozenset({NodeType.KNOWLEDGE_CONCEPT}),
        frozenset({NodeType.KNOWLEDGE_DOCUMENT}),
    ),
    EdgeType.COLLECTED: (
        frozenset({NodeType.STUDY_ITEM}),
        frozenset(NodeType),
    ),
    EdgeType.RELATED_TO: (frozenset(NodeType), frozenset(NodeType)),
}


def edge_shape(edge_type: EdgeType) -> tuple[frozenset[NodeType], frozenset[NodeType]]:
    """The declared ``(from_types, to_types)`` for an edge kind."""
    return _EDGE_SHAPES[edge_type]


def edge_shape_valid(edge_type: EdgeType, from_type: NodeType, to_type: NodeType) -> bool:
    """Whether ``from_type --edge_type--> to_type`` is a legal, declared shape."""
    from_types, to_types = _EDGE_SHAPES[edge_type]
    return from_type in from_types and to_type in to_types


def edge_shape_error(edge_type: EdgeType, from_type: NodeType, to_type: NodeType) -> str:
    """A human explanation for why a shape was refused."""
    from_types, to_types = _EDGE_SHAPES[edge_type]
    return (
        f"'{edge_type.value}' may not connect {from_type.value} → {to_type.value}. "
        f"Declared shape: {'/'.join(sorted(t.value for t in from_types))} → "
        f"{'/'.join(sorted(t.value for t in to_types))}."
    )


__all__ = [
    "GRAPH_METHODOLOGY_VERSION",
    "GRAPH_SCHEMA_VERSION",
    "EdgeType",
    "NodeType",
    "PatternType",
    "edge_shape",
    "edge_shape_error",
    "edge_shape_valid",
    "pattern_node_type",
]
