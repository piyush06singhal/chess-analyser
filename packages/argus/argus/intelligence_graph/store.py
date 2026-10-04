"""The storage seam: what the graph service needs from a store.

The service is written against :class:`GraphStore`, a narrow protocol. Two
implementations exist:

* :class:`InMemoryGraphStore` — exact, dependency-free, used by the unit tests
  and by small in-process traversals;
* the SQL-backed store in the API layer — indexed, incremental, the production
  implementation.

Keeping the protocol small is what makes the architectural decision in
``docs/intelligence-graph/README.md`` reversible: the traversal,
authorization, evidence and versioning rules live in the service, not in SQL, so
swapping the store never moves a security rule.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from argus.intelligence_graph.models import GraphEdge, GraphNode
from argus.intelligence_graph.taxonomy import EdgeType, NodeType


@runtime_checkable
class GraphStore(Protocol):
    """Everything the graph service may ask of storage."""

    def upsert_node(self, node: GraphNode) -> GraphNode: ...

    def get_node(self, node_type: NodeType, node_key: str) -> GraphNode | None: ...

    def upsert_edge(self, edge: GraphEdge) -> GraphEdge: ...

    def out_edges(
        self,
        node_type: NodeType,
        node_key: str,
        edge_types: frozenset[EdgeType] | None = None,
    ) -> list[GraphEdge]: ...

    def in_edges(
        self,
        node_type: NodeType,
        node_key: str,
        edge_types: frozenset[EdgeType] | None = None,
    ) -> list[GraphEdge]: ...

    def find_nodes(
        self,
        node_type: NodeType,
        *,
        attributes: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[GraphNode]: ...

    def delete_edges(self, edge: GraphEdge) -> int:
        """Delete edges matching the same ``(type, from, to)`` key as the given edge."""
        ...

    def delete_node(self, node_type: NodeType, node_key: str) -> bool: ...

    def iter_nodes(self) -> list[GraphNode]: ...

    def iter_edges(self) -> list[GraphEdge]: ...


class InMemoryGraphStore:
    """A correct, simple store. Not for production scale, ideal for tests."""

    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[tuple[str, str, str], GraphEdge] = {}

    @staticmethod
    def _nk(node_type: NodeType, node_key: str) -> str:
        return f"{node_type.value}:{node_key}"

    @staticmethod
    def _ek(edge: GraphEdge) -> tuple[str, str, str]:
        return (edge.edge_type.value, edge.from_global_key, edge.to_global_key)

    def upsert_node(self, node: GraphNode) -> GraphNode:
        key = self._nk(node.node_type, node.node_key)
        existing = self._nodes.get(key)
        if existing is not None:
            node.created_at = existing.created_at
        self._nodes[key] = node
        return node

    def get_node(self, node_type: NodeType, node_key: str) -> GraphNode | None:
        return self._nodes.get(self._nk(node_type, node_key))

    def upsert_edge(self, edge: GraphEdge) -> GraphEdge:
        key = self._ek(edge)
        existing = self._edges.get(key)
        if existing is not None:
            edge.created_at = existing.created_at
        self._edges[key] = edge
        return edge

    def out_edges(
        self,
        node_type: NodeType,
        node_key: str,
        edge_types: frozenset[EdgeType] | None = None,
    ) -> list[GraphEdge]:
        source = self._nk(node_type, node_key)
        return [
            edge
            for edge in self._edges.values()
            if edge.from_global_key == source
            and (edge_types is None or edge.edge_type in edge_types)
        ]

    def in_edges(
        self,
        node_type: NodeType,
        node_key: str,
        edge_types: frozenset[EdgeType] | None = None,
    ) -> list[GraphEdge]:
        target = self._nk(node_type, node_key)
        return [
            edge
            for edge in self._edges.values()
            if edge.to_global_key == target
            and (edge_types is None or edge.edge_type in edge_types)
        ]

    def find_nodes(
        self,
        node_type: NodeType,
        *,
        attributes: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[GraphNode]:
        matches: list[GraphNode] = []
        for node in self._nodes.values():
            if node.node_type is not node_type:
                continue
            if attributes and not all(
                node.attributes.get(name) == value for name, value in attributes.items()
            ):
                continue
            matches.append(node)
            if len(matches) >= limit:
                break
        return matches

    def delete_edges(self, edge: GraphEdge) -> int:
        key = self._ek(edge)
        if key in self._edges:
            del self._edges[key]
            return 1
        return 0

    def delete_node(self, node_type: NodeType, node_key: str) -> bool:
        key = self._nk(node_type, node_key)
        if key not in self._nodes:
            return False
        del self._nodes[key]
        for edge_key in [
            edge_key
            for edge_key, edge in self._edges.items()
            if edge.from_global_key == key or edge.to_global_key == key
        ]:
            del self._edges[edge_key]
        return True

    def iter_nodes(self) -> list[GraphNode]:
        return list(self._nodes.values())

    def iter_edges(self) -> list[GraphEdge]:
        return list(self._edges.values())

    # --- test conveniences ----------------------------------------------------

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return len(self._edges)


__all__ = ["GraphStore", "InMemoryGraphStore"]
