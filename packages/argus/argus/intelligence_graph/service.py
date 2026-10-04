"""``IntelligenceGraphService`` (§30): the only way to read or write the graph.

The service is where the graph's promises are kept, once, for every caller:

* **Validated writes.** A node/edge write checks the taxonomy: an unknown node
  kind, an undeclared edge shape, or a derived edge with no evidence is refused
  with a reason. The graph cannot be written into an inconsistent state.
* **Authorized reads.** Every node and edge is filtered through the
  :class:`~argus.intelligence_graph.access.GraphAccessPolicy` *before* it is
  returned. A traversal reports how many nodes it withheld, never their content.
* **Bounded traversal.** Depth and node limits are mandatory, so no query can ask
  the graph to walk itself into memory (§47).
* **Evidence tracing.** Any node's justification is resolvable to the stored
  references that produced it (§43), with unavailable references reported as gaps.
* **Versioning.** Writes stamp a methodology version and data cutoff, and a
  snapshot records the whole graph's versions (§8/§37).
* **Observability.** Counters for updates, traversals, latency, denials and
  evidence failures are kept here so the health endpoint can publish them (§54).

The LLM never calls this class. It calls the *graph tools* (§31), which call this
service with a schema-validated, authorized request.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from argus.intelligence_graph.access import GraphAccessPolicy, unrestricted
from argus.intelligence_graph.evidence import (
    EvidenceReference,
    merge_references,
    require_evidence,
)
from argus.intelligence_graph.models import (
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    TraversalHop,
    TraversalResult,
)
from argus.intelligence_graph.store import GraphStore
from argus.intelligence_graph.taxonomy import (
    GRAPH_METHODOLOGY_VERSION,
    GRAPH_SCHEMA_VERSION,
    EdgeType,
    NodeType,
    edge_shape_error,
    edge_shape_valid,
)
from argus.shared.errors import NotFoundError, ValidationError
from argus.shared.time import utcnow_or as _utc

DEFAULT_MAX_DEPTH = 3
DEFAULT_NODE_LIMIT = 200
MAX_NODE_LIMIT = 500


class GraphSchemaError(ValidationError):
    """Raised when a write would violate the graph taxonomy."""

    code = "graph_schema_error"


class GraphEvidenceError(ValidationError):
    """Raised when a derived edge is written without evidence (§6/§52)."""

    code = "graph_evidence_required"




class IntelligenceGraphService:
    """Node/edge writes, bounded traversal, evidence and versioning."""

    def __init__(
        self,
        store: GraphStore,
        *,
        policy: GraphAccessPolicy | None = None,
        methodology_version: str = GRAPH_METHODOLOGY_VERSION,
        schema_version: str = GRAPH_SCHEMA_VERSION,
    ) -> None:
        self.store = store
        self.policy = policy or unrestricted()
        self.methodology_version = methodology_version
        self.schema_version = schema_version
        self._metrics: dict[str, float] = {
            "graph_updates": 0,
            "graph_traversals": 0,
            "graph_rebuilds": 0,
            "traversal_ms_total": 0.0,
            "authorization_denials": 0,
            "evidence_validation_failures": 0,
            "orphan_nodes": 0,
            "invalid_edges": 0,
            "knowledge_retrievals": 0,
            "agent_graph_tool_calls": 0,
        }

    # --- metrics --------------------------------------------------------------

    def bump(self, name: str, value: float = 1.0) -> None:
        self._metrics[name] = self._metrics.get(name, 0.0) + value

    def metrics(self) -> dict[str, Any]:
        traversals = self._metrics.get("graph_traversals", 0.0)
        average = (
            self._metrics.get("traversal_ms_total", 0.0) / traversals if traversals else 0.0
        )
        return {
            **{key: int(value) for key, value in self._metrics.items() if key != "traversal_ms_total"},
            "average_traversal_latency_ms": round(average, 3),
        }

    # --- writes ---------------------------------------------------------------

    def upsert_node(self, node: GraphNode) -> GraphNode:
        """Create or update a node, stamping the current methodology version."""
        if not node.node_key:
            raise GraphSchemaError("A graph node requires a non-empty node_key.")
        if not node.methodology_version:
            node.methodology_version = self.methodology_version
        node.updated_at = _utc()
        stored = self.store.upsert_node(node)
        self.bump("graph_updates")
        return stored

    def write_edge(self, edge: GraphEdge) -> GraphEdge:
        """Create or update an edge, validating its declared shape and evidence."""
        if not edge_shape_valid(edge.edge_type, edge.from_type, edge.to_type):
            self.bump("invalid_edges")
            raise GraphSchemaError(
                edge_shape_error(edge.edge_type, edge.from_type, edge.to_type)
            )
        try:
            usable = require_evidence(edge.edge_type, edge.evidence)
        except Exception as exc:  # MissingEvidenceError
            self.bump("evidence_validation_failures")
            raise GraphEvidenceError(str(exc)) from exc
        edge.evidence = usable
        if edge.sample_size is None and usable:
            # The sample size defaults to the number of distinct sources, which is
            # the honest lower bound — never an invented number.
            edge.sample_size = len(usable)
        if not edge.methodology_version:
            edge.methodology_version = self.methodology_version
        edge.updated_at = _utc()
        stored = self.store.upsert_edge(edge)
        self.bump("graph_updates")
        return stored

    # --- reads ----------------------------------------------------------------

    def get_node(self, node_type: NodeType, node_key: str) -> GraphNode | None:
        node = self.store.get_node(node_type, node_key)
        if node is None:
            return None
        allowed, _ = self.policy.can_read_node(node)
        if not allowed:
            self.bump("authorization_denials")
            return None
        return node

    def require_node(self, node_type: NodeType, node_key: str) -> GraphNode:
        node = self.get_node(node_type, node_key)
        if node is None:
            raise NotFoundError(
                f"No {node_type.value} node '{node_key}' is available to this caller."
            )
        return node

    def nodes_of_type(
        self,
        node_type: NodeType,
        *,
        attributes: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[GraphNode]:
        found = self.store.find_nodes(node_type, attributes=attributes, limit=limit)
        return [node for node in found if self.policy.can_read_node(node)[0]]

    def neighbors(
        self,
        node_type: NodeType,
        node_key: str,
        *,
        edge_types: Iterable[EdgeType] | None = None,
        direction: str = "out",
    ) -> list[tuple[GraphEdge, GraphNode | None]]:
        """Immediate neighbors, authorized and with dangling endpoints retained."""
        types = frozenset(edge_types) if edge_types else None
        if direction == "in":
            edges = self.store.in_edges(node_type, node_key, types)
        elif direction == "out":
            edges = self.store.out_edges(node_type, node_key, types)
        else:
            edges = self.store.out_edges(node_type, node_key, types) + self.store.in_edges(
                node_type, node_key, types
            )
        out: list[tuple[GraphEdge, GraphNode | None]] = []
        for edge in edges:
            if direction == "in" or (
                direction == "both" and edge.to_global_key == f"{node_type.value}:{node_key}"
            ):
                other_type, other_key = edge.from_type, edge.from_key
            else:
                other_type, other_key = edge.to_type, edge.to_key
            other = self.store.get_node(other_type, other_key)
            if other is not None and not self.policy.can_read_node(other)[0]:
                self.bump("authorization_denials")
                continue
            out.append((edge, other))
        return out

    def traverse(
        self,
        node_type: NodeType,
        node_key: str,
        *,
        edge_types: Iterable[EdgeType] | None = None,
        direction: str = "out",
        max_depth: int = DEFAULT_MAX_DEPTH,
        limit: int = DEFAULT_NODE_LIMIT,
        include_root: bool = True,
    ) -> TraversalResult:
        """Bounded breadth-first traversal from one node.

        The result carries the hops taken (so an answer can be explained) and a
        count of nodes withheld by authorization (so privacy is never leaked).
        """
        started = time.perf_counter()
        self.bump("graph_traversals")
        limit = max(1, min(int(limit), MAX_NODE_LIMIT))
        root = self.get_node(node_type, node_key)
        if root is None:
            raise NotFoundError(
                f"No {node_type.value} node '{node_key}' is available to this caller."
            )
        types = frozenset(edge_types) if edge_types else None
        result = TraversalResult(root=root.global_key, limit=limit)
        if include_root:
            result.nodes.append(root)
        seen: set[str] = {root.global_key}
        frontier: list[GraphNode] = [root]
        for _depth in range(max(0, int(max_depth))):
            if not frontier or len(result.nodes) >= limit:
                if len(result.nodes) >= limit and frontier:
                    result.truncated = True
                break
            next_frontier: list[GraphNode] = []
            for current in frontier:
                for edge, other in self.neighbors(
                    current.node_type,
                    current.node_key,
                    edge_types=types,
                    direction=direction,
                ):
                    if other is None:
                        # Dangling endpoint: recorded in health, skipped here.
                        continue
                    if other.global_key in seen:
                        continue
                    seen.add(other.global_key)
                    result.edges.append(edge)
                    result.hops.append(
                        TraversalHop(
                            edge_type=edge.edge_type,
                            from_node=edge.from_global_key,
                            to_node=edge.to_global_key,
                            evidence_count=edge.evidence_count,
                            sample_size=edge.sample_size,
                        )
                    )
                    result.nodes.append(other)
                    next_frontier.append(other)
                    if len(result.nodes) >= limit:
                        result.truncated = True
                        break
                if result.truncated:
                    break
            frontier = next_frontier
        result.denied_count = (
            self.policy.denials if self.policy.allowed_game_ids is not None else 0
        )
        elapsed = (time.perf_counter() - started) * 1000
        self.bump("traversal_ms_total", elapsed)
        return result

    def evidence_for(self, node_type: NodeType, node_key: str) -> list[EvidenceReference]:
        """Every evidence reference attached to any edge touching this node."""
        refs: list[EvidenceReference] = []
        for edge in self.store.out_edges(node_type, node_key) + self.store.in_edges(
            node_type, node_key
        ):
            refs.extend(edge.evidence)
        return merge_references(refs)

    def trace_evidence(
        self,
        node_type: NodeType,
        node_key: str,
        *,
        max_depth: int = 2,
        limit: int = DEFAULT_NODE_LIMIT,
    ) -> dict[str, Any]:
        """Follow a node's justification to the stored objects behind it (§43).

        Returns the resolved references grouped by kind, plus the *gaps*: edges
        that are derived but whose referenced objects can no longer be found. A
        gap is reported, never hidden.
        """
        traversal = self.traverse(
            node_type,
            node_key,
            direction="both",
            max_depth=max_depth,
            limit=limit,
        )
        references = merge_references(
            *[edge.evidence for edge in traversal.edges],
            self.evidence_for(node_type, node_key),
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for ref in references:
            grouped.setdefault(ref.kind.value, []).append(ref.to_payload())
        gaps: list[str] = []
        for edge in traversal.edges:
            if edge.edge_type.is_evidence_bearing and not edge.evidence:
                gaps.append(
                    f"Edge '{edge.edge_type.value}' has no stored evidence reference."
                )
        return {
            "node": f"{node_type.value}:{node_key}",
            "evidence": grouped,
            "evidence_count": len(references),
            "hops": [hop.model_dump(mode="json") for hop in traversal.hops],
            "gaps": gaps,
        }

    # --- versioning -----------------------------------------------------------

    def snapshot(self, *, data_cutoff: datetime | None = None) -> GraphSnapshot:
        """A reproducible description of the graph's current state (§8)."""
        nodes = self.store.iter_nodes()
        edges = self.store.iter_edges()
        node_counts: dict[str, int] = {}
        for node in nodes:
            node_counts[node.node_type.value] = node_counts.get(node.node_type.value, 0) + 1
        edge_counts: dict[str, int] = {}
        for edge in edges:
            edge_counts[edge.edge_type.value] = edge_counts.get(edge.edge_type.value, 0) + 1
        cutoff = _utc(data_cutoff)
        return GraphSnapshot(
            graph_version=f"{self.schema_version}+{cutoff.strftime('%Y%m%dT%H%M%SZ')}",
            schema_version=self.schema_version,
            methodology_version=self.methodology_version,
            data_cutoff=cutoff,
            node_counts=node_counts,
            edge_counts=edge_counts,
            total_nodes=len(nodes),
            total_edges=len(edges),
        )

    def health(self) -> dict[str, Any]:
        """Graph consistency report (§36)."""
        from argus.intelligence_graph.health import check_graph

        report = check_graph(
            self.store,
            schema_version=self.schema_version,
            methodology_version=self.methodology_version,
        )
        self._metrics["orphan_nodes"] = len(report.orphan_nodes)
        self._metrics["invalid_edges"] = len(report.invalid_edges)
        return {
            "healthy": report.healthy,
            "schema_version": self.schema_version,
            "methodology_version": self.methodology_version,
            "report": report.to_payload(),
            "metrics": self.metrics(),
        }

    def invalidate(
        self,
        *,
        game_id: str | None = None,
        node_type: NodeType | None = None,
        node_key: str | None = None,
        edge_types: Iterable[EdgeType] | None = None,
    ) -> int:
        """Drop derived edges affected by a change (§35).

        Returns the number of edges removed. Structural edges (game → position)
        are re-materialised by the next incremental update; derived edges must be
        recomputed from the new data rather than left stale.
        """
        removed = 0
        types = frozenset(edge_types) if edge_types else None
        if node_type is not None and node_key is not None:
            for edge in self.store.out_edges(node_type, node_key, types):
                removed += self.store.delete_edges(edge)
            for edge in self.store.in_edges(node_type, node_key, types):
                removed += self.store.delete_edges(edge)
        elif game_id is not None:
            for edge in self.store.iter_edges():
                references_game = any(
                    ref.game_id == game_id for ref in edge.evidence
                ) or edge.from_key == game_id or edge.to_key == game_id
                if references_game and (types is None or edge.edge_type in types):
                    removed += self.store.delete_edges(edge)
        self.bump("graph_rebuilds")
        return removed


__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_NODE_LIMIT",
    "MAX_NODE_LIMIT",
    "GraphEvidenceError",
    "GraphSchemaError",
    "IntelligenceGraphService",
]
