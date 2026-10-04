"""Graph value objects: nodes, edges, snapshots, and traversal results.

These are plain data. They carry no database handles and no engine references, so
the same objects are produced by an in-memory store in a unit test and by the
SQL store in production. That is deliberate: the traversal rules (authorization,
evidence, versioning) are then tested once, against the same shapes the API
serves.

Node identity is a two-part key, ``(node_type, node_key)``:

* ``node_type`` is the controlled :class:`NodeType`;
* ``node_key`` is the identity of the underlying domain object in its own
  namespace — a game id, a player id, a position hash, a pattern id, a knowledge
  concept slug.

The pair is rendered as ``node_type:node_key`` (:attr:`GraphNode.global_key`) for
logging and edges, but it is *stored* as two columns so the graph can index and
join on each independently.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from argus.intelligence_graph.evidence import EvidenceReference
from argus.intelligence_graph.taxonomy import EdgeType, NodeType
from argus.shared.time import utcnow


class GraphNode(BaseModel):
    """One node: a reference to an existing domain object, never a copy of it."""

    node_type: NodeType
    node_key: str
    label: str = ""
    #: Small, non-authoritative facts for display and filtering (colour, result,
    #: ECO code, occurrence count). The authoritative data stays in its own table.
    attributes: dict[str, Any] = Field(default_factory=dict)
    methodology_version: str = ""
    data_cutoff: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def global_key(self) -> str:
        return f"{self.node_type.value}:{self.node_key}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type.value,
            "node_key": self.node_key,
            "label": self.label,
            "attributes": dict(self.attributes),
            "methodology_version": self.methodology_version,
            "data_cutoff": self.data_cutoff.isoformat() if self.data_cutoff else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class GraphEdge(BaseModel):
    """One directed relationship, with the evidence that justifies it."""

    edge_type: EdgeType
    from_type: NodeType
    from_key: str
    to_type: NodeType
    to_key: str
    #: Derived edges carry evidence; structural edges may leave this empty.
    evidence: list[EvidenceReference] = Field(default_factory=list)
    #: The size of the sample behind a derived claim (§6), when there is one.
    sample_size: int | None = None
    methodology_version: str = ""
    data_cutoff: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def from_global_key(self) -> str:
        return f"{self.from_type.value}:{self.from_key}"

    @property
    def to_global_key(self) -> str:
        return f"{self.to_type.value}:{self.to_key}"

    @property
    def evidence_count(self) -> int:
        return len(self.evidence)

    def to_payload(self, *, include_evidence: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "edge_type": self.edge_type.value,
            "from": {"node_type": self.from_type.value, "node_key": self.from_key},
            "to": {"node_type": self.to_type.value, "node_key": self.to_key},
            "sample_size": self.sample_size,
            "methodology_version": self.methodology_version,
            "data_cutoff": self.data_cutoff.isoformat() if self.data_cutoff else None,
            "evidence_count": self.evidence_count,
        }
        if include_evidence:
            payload["evidence"] = [ref.to_payload() for ref in self.evidence]
        return payload


class GraphSnapshot(BaseModel):
    """A reproducible description of the graph at a point in time (§8/§37)."""

    graph_version: str
    schema_version: str
    methodology_version: str
    data_cutoff: datetime
    node_counts: dict[str, int] = Field(default_factory=dict)
    edge_counts: dict[str, int] = Field(default_factory=dict)
    total_nodes: int = 0
    total_edges: int = 0
    created_at: datetime = Field(default_factory=utcnow)

    def to_payload(self) -> dict[str, Any]:
        return {
            "graph_version": self.graph_version,
            "schema_version": self.schema_version,
            "methodology_version": self.methodology_version,
            "data_cutoff": self.data_cutoff.isoformat(),
            "node_counts": dict(self.node_counts),
            "edge_counts": dict(self.edge_counts),
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "created_at": self.created_at.isoformat(),
        }


class TraversalHop(BaseModel):
    """One hop of a traversal, kept so the path is explainable (§43)."""

    edge_type: EdgeType
    from_node: str
    to_node: str
    evidence_count: int = 0
    sample_size: int | None = None


class TraversalResult(BaseModel):
    """The outcome of a bounded traversal from a starting node."""

    root: str
    hops: list[TraversalHop] = Field(default_factory=list)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    truncated: bool = False
    limit: int = 0
    #: Nodes that were reached but filtered out because the caller may not see
    #: them; reported as a count, never as content, so privacy is never leaked.
    denied_count: int = 0


__all__ = [
    "GraphEdge",
    "GraphNode",
    "GraphSnapshot",
    "TraversalHop",
    "TraversalResult",
    "utcnow",
]
