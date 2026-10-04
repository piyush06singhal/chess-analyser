"""The SQL implementation of :class:`argus.intelligence_graph.store.GraphStore`.

PostgreSQL relational tables carry the graph (see
``docs/intelligence-graph/README.md`` for why that beats a graph database
here). This class is the only place SQL meets the graph; the traversal,
authorization, evidence and versioning rules stay in the core service, so they
are identical whether the store is in-memory (tests) or SQL (production).

Two performance choices are deliberate:

* **Idempotent upserts.** Node writes look up ``(node_type, node_key)`` and edge
  writes look up the full edge identity, so the incremental updater can re-run
  without creating duplicates.
* **Selective reads.** ``out_edges`` / ``in_edges`` filter in SQL on the indexed
  ``(from_type, from_key)`` / ``(to_type, to_key)`` columns, so a traversal never
  loads the whole graph.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from argus.intelligence_graph.evidence import EvidenceReference
from argus.intelligence_graph.models import GraphEdge, GraphNode
from argus.intelligence_graph.taxonomy import EdgeType, NodeType
from argus.shared.errors import RepositoryError
from argus.shared.time import utcnow_or as _utc

from argus_api.db.models import GraphEdgeRecord, GraphNodeRecord

#: Cap on the per-position game-id list kept for authorization. A position seen in
#: more than this many games is rare; the cap exists so a single hot position
#: cannot make a node row unbounded. The containment edges remain exact.
_MAX_GAME_IDS_PER_NODE = 200



def _node_from_record(record: GraphNodeRecord) -> GraphNode:
    return GraphNode(
        node_type=NodeType(record.node_type),
        node_key=record.node_key,
        label=record.label or "",
        attributes=dict(record.attributes or {}),
        methodology_version=record.methodology_version or "",
        data_cutoff=record.data_cutoff,
        created_at=_utc(record.created_at),
        updated_at=_utc(record.updated_at),
    )


def _edge_from_record(record: GraphEdgeRecord) -> GraphEdge:
    evidence = [
        EvidenceReference(**ref)
        for ref in (record.evidence or [])
        if isinstance(ref, dict)
    ]
    return GraphEdge(
        edge_type=EdgeType(record.edge_type),
        from_type=NodeType(record.from_type),
        from_key=record.from_key,
        to_type=NodeType(record.to_type),
        to_key=record.to_key,
        evidence=evidence,
        sample_size=record.sample_size,
        methodology_version=record.methodology_version or "",
        data_cutoff=record.data_cutoff,
        created_at=_utc(record.created_at),
        updated_at=_utc(record.updated_at),
    )


class SqlGraphStore:
    """A :class:`GraphStore` over the ``graph_nodes`` / ``graph_edges`` tables."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @contextmanager
    def _guard(self):
        try:
            yield
        except RepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            raise RepositoryError(f"Graph store failure: {exc}") from exc

    # --- nodes ----------------------------------------------------------------

    def upsert_node(self, node: GraphNode) -> GraphNode:
        with self._guard():
            record = self.session.execute(
                select(GraphNodeRecord).where(
                    GraphNodeRecord.node_type == node.node_type.value,
                    GraphNodeRecord.node_key == node.node_key,
                )
            ).scalar_one_or_none()
            if record is None:
                record = GraphNodeRecord(
                    node_type=node.node_type.value,
                    node_key=node.node_key,
                    label=node.label,
                    attributes=dict(node.attributes),
                    methodology_version=node.methodology_version,
                    data_cutoff=node.data_cutoff,
                )
                self.session.add(record)
            else:
                record.label = node.label or record.label
                record.attributes = _merge_attributes(record.attributes, node.attributes)
                record.methodology_version = node.methodology_version
                record.data_cutoff = node.data_cutoff or record.data_cutoff
                record.updated_at = _utc()
            self.session.commit()
            self.session.refresh(record)
            return _node_from_record(record)

    def get_node(self, node_type: NodeType, node_key: str) -> GraphNode | None:
        with self._guard():
            record = self.session.execute(
                select(GraphNodeRecord).where(
                    GraphNodeRecord.node_type == node_type.value,
                    GraphNodeRecord.node_key == node_key,
                )
            ).scalar_one_or_none()
            return _node_from_record(record) if record is not None else None

    def delete_node(self, node_type: NodeType, node_key: str) -> bool:
        with self._guard():
            result = self.session.execute(
                delete(GraphNodeRecord).where(
                    GraphNodeRecord.node_type == node_type.value,
                    GraphNodeRecord.node_key == node_key,
                )
            )
            self.session.execute(
                delete(GraphEdgeRecord).where(
                    ((GraphEdgeRecord.from_type == node_type.value)
                     & (GraphEdgeRecord.from_key == node_key))
                    | ((GraphEdgeRecord.to_type == node_type.value)
                       & (GraphEdgeRecord.to_key == node_key))
                )
            )
            self.session.commit()
            return (result.rowcount or 0) > 0

    # --- edges ----------------------------------------------------------------

    def upsert_edge(self, edge: GraphEdge) -> GraphEdge:
        with self._guard():
            record = self.session.execute(
                select(GraphEdgeRecord).where(
                    GraphEdgeRecord.edge_type == edge.edge_type.value,
                    GraphEdgeRecord.from_type == edge.from_type.value,
                    GraphEdgeRecord.from_key == edge.from_key,
                    GraphEdgeRecord.to_type == edge.to_type.value,
                    GraphEdgeRecord.to_key == edge.to_key,
                )
            ).scalar_one_or_none()
            payload = [ref.to_payload() for ref in edge.evidence]
            if record is None:
                record = GraphEdgeRecord(
                    edge_type=edge.edge_type.value,
                    from_type=edge.from_type.value,
                    from_key=edge.from_key,
                    to_type=edge.to_type.value,
                    to_key=edge.to_key,
                    evidence=payload,
                    sample_size=edge.sample_size,
                    methodology_version=edge.methodology_version,
                    data_cutoff=edge.data_cutoff,
                )
                self.session.add(record)
            else:
                record.evidence = payload
                record.sample_size = edge.sample_size
                record.methodology_version = edge.methodology_version
                record.data_cutoff = edge.data_cutoff or record.data_cutoff
                record.updated_at = _utc()
            self.session.commit()
            self.session.refresh(record)
            return _edge_from_record(record)

    def out_edges(
        self,
        node_type: NodeType,
        node_key: str,
        edge_types: frozenset[EdgeType] | None = None,
    ) -> list[GraphEdge]:
        with self._guard():
            query = select(GraphEdgeRecord).where(
                GraphEdgeRecord.from_type == node_type.value,
                GraphEdgeRecord.from_key == node_key,
            )
            if edge_types:
                query = query.where(
                    GraphEdgeRecord.edge_type.in_([edge.value for edge in edge_types])
                )
            rows = self.session.execute(query.order_by(GraphEdgeRecord.id)).scalars()
            return [_edge_from_record(row) for row in rows]

    def in_edges(
        self,
        node_type: NodeType,
        node_key: str,
        edge_types: frozenset[EdgeType] | None = None,
    ) -> list[GraphEdge]:
        with self._guard():
            query = select(GraphEdgeRecord).where(
                GraphEdgeRecord.to_type == node_type.value,
                GraphEdgeRecord.to_key == node_key,
            )
            if edge_types:
                query = query.where(
                    GraphEdgeRecord.edge_type.in_([edge.value for edge in edge_types])
                )
            rows = self.session.execute(query.order_by(GraphEdgeRecord.id)).scalars()
            return [_edge_from_record(row) for row in rows]

    def delete_edges(self, edge: GraphEdge) -> int:
        with self._guard():
            result = self.session.execute(
                delete(GraphEdgeRecord).where(
                    GraphEdgeRecord.edge_type == edge.edge_type.value,
                    GraphEdgeRecord.from_type == edge.from_type.value,
                    GraphEdgeRecord.from_key == edge.from_key,
                    GraphEdgeRecord.to_type == edge.to_type.value,
                    GraphEdgeRecord.to_key == edge.to_key,
                )
            )
            self.session.commit()
            return result.rowcount or 0

    def delete_edges_for_game(self, game_id: str) -> int:
        """Drop every edge whose evidence points at a game (invalidation, §35)."""
        with self._guard():
            removed = 0
            for row in self.session.execute(select(GraphEdgeRecord)).scalars():
                references = any(
                    isinstance(ref, dict) and ref.get("game_id") == game_id
                    for ref in (row.evidence or [])
                )
                if references or (row.from_type == "game" and row.from_key == game_id):
                    self.session.delete(row)
                    removed += 1
            self.session.commit()
            return removed

    # --- queries --------------------------------------------------------------

    def find_nodes(
        self,
        node_type: NodeType,
        *,
        attributes: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[GraphNode]:
        with self._guard():
            query = select(GraphNodeRecord).where(GraphNodeRecord.node_type == node_type.value)
            rows = self.session.execute(query.order_by(GraphNodeRecord.id)).scalars()
            matches: list[GraphNode] = []
            for row in rows:
                node = _node_from_record(row)
                if attributes and not all(
                    node.attributes.get(name) == value for name, value in attributes.items()
                ):
                    continue
                matches.append(node)
                if len(matches) >= limit:
                    break
            return matches

    def iter_nodes(self) -> list[GraphNode]:
        with self._guard():
            rows = self.session.execute(select(GraphNodeRecord)).scalars()
            return [_node_from_record(row) for row in rows]

    def iter_edges(self) -> list[GraphEdge]:
        with self._guard():
            rows = self.session.execute(select(GraphEdgeRecord)).scalars()
            return [_edge_from_record(row) for row in rows]

    def counts(self) -> dict[str, int]:
        """Node and edge totals, cheap enough for a health endpoint."""
        from sqlalchemy import func

        with self._guard():
            nodes = self.session.execute(
                select(func.count()).select_from(GraphNodeRecord)
            ).scalar_one()
            edges = self.session.execute(
                select(func.count()).select_from(GraphEdgeRecord)
            ).scalar_one()
            return {"nodes": int(nodes), "edges": int(edges)}


def _merge_attributes(existing: dict | None, incoming: dict | None) -> dict:
    """Merge attributes, union-ing ``game_ids`` and letting incoming win elsewhere."""
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if key == "game_ids" and isinstance(value, (list, tuple)):
            current = list(merged.get("game_ids") or [])
            for item in value:
                if item not in current:
                    current.append(item)
            merged["game_ids"] = current[:_MAX_GAME_IDS_PER_NODE]
        else:
            merged[key] = value
    return merged


__all__ = ["SqlGraphStore"]
