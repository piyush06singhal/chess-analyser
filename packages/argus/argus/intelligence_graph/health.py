"""Caissa Graph Health Check (§36).

The graph is derived data. Derived data drifts: a game is deleted and its
positions are left pointing at nothing; a stored edge names a shape the taxonomy
no longer declares; a derived edge is written by an older methodology and never
recomputed. None of that should be discovered by a user noticing a strange
answer.

So this module runs the checks a reviewer would run by hand, and returns a
structured report rather than a boolean:

* **orphan nodes** — a node no edge references (usually a leaf that was never
  linked, or a leftover of an interrupted rebuild);
* **dangling edges** — an edge whose endpoint node does not exist;
* **invalid edges** — an edge whose shape the current taxonomy forbids;
* **missing evidence** — a derived edge with no evidence reference (§6);
* **stale edges** — an edge whose methodology version predates the current one;
* **duplicate edges** — the same ``(type, from, to)`` seen twice (only possible in
  a store that does not dedupe, which is worth knowing about).

Every list is capped, and the report always carries the true total, so a health
call on a large graph stays cheap and honest.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from argus.intelligence_graph.models import GraphEdge
from argus.intelligence_graph.store import GraphStore
from argus.intelligence_graph.taxonomy import edge_shape_valid

_SAMPLE_CAP = 20


class GraphHealthReport(BaseModel):
    """The outcome of the consistency checks, with totals and samples."""

    node_count: int = 0
    edge_count: int = 0
    orphan_nodes: list[str] = Field(default_factory=list)
    orphan_count: int = 0
    dangling_edges: list[str] = Field(default_factory=list)
    dangling_count: int = 0
    invalid_edges: list[str] = Field(default_factory=list)
    invalid_count: int = 0
    missing_evidence: list[str] = Field(default_factory=list)
    missing_evidence_count: int = 0
    stale_edges: list[str] = Field(default_factory=list)
    stale_count: int = 0
    duplicate_edges: list[str] = Field(default_factory=list)
    duplicate_count: int = 0
    note: str | None = None

    @property
    def healthy(self) -> bool:
        return all(
            count == 0
            for count in (
                self.dangling_count,
                self.invalid_count,
                self.missing_evidence_count,
                self.duplicate_count,
            )
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "orphan_nodes": {
                "count": self.orphan_count,
                "sample": self.orphan_nodes[:_SAMPLE_CAP],
                "note": (
                    "An orphan node is not an error by itself; it is reported so a "
                    "rebuild that stopped early is visible."
                ),
            },
            "dangling_edges": {"count": self.dangling_count, "sample": self.dangling_edges[:_SAMPLE_CAP]},
            "invalid_edges": {"count": self.invalid_count, "sample": self.invalid_edges[:_SAMPLE_CAP]},
            "missing_evidence": {
                "count": self.missing_evidence_count,
                "sample": self.missing_evidence[:_SAMPLE_CAP],
                "note": "Derived relationships without evidence must not be presented as fact.",
            },
            "stale_edges": {
                "count": self.stale_count,
                "sample": self.stale_edges[:_SAMPLE_CAP],
                "note": "Edges written by an older methodology; recompute rather than trust.",
            },
            "duplicate_edges": {
                "count": self.duplicate_count,
                "sample": self.duplicate_edges[:_SAMPLE_CAP],
            },
        }


def _edge_label(edge: GraphEdge) -> str:
    return f"{edge.edge_type.value}:{edge.from_global_key}->{edge.to_global_key}"


def check_graph(
    store: GraphStore,
    *,
    schema_version: str,
    methodology_version: str,
) -> GraphHealthReport:
    """Run every consistency check against a store."""
    nodes = store.iter_nodes()
    edges = store.iter_edges()
    report = GraphHealthReport(node_count=len(nodes), edge_count=len(edges))
    node_keys = {node.global_key for node in nodes}

    referenced: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in edges:
        label = _edge_label(edge)
        referenced.add(edge.from_global_key)
        referenced.add(edge.to_global_key)
        if edge.from_global_key not in node_keys or edge.to_global_key not in node_keys:
            report.dangling_count += 1
            if len(report.dangling_edges) < _SAMPLE_CAP:
                report.dangling_edges.append(label)
        if not edge_shape_valid(edge.edge_type, edge.from_type, edge.to_type):
            report.invalid_count += 1
            if len(report.invalid_edges) < _SAMPLE_CAP:
                report.invalid_edges.append(label)
        if edge.edge_type.is_evidence_bearing and not edge.evidence:
            report.missing_evidence_count += 1
            if len(report.missing_evidence) < _SAMPLE_CAP:
                report.missing_evidence.append(label)
        if edge.methodology_version and edge.methodology_version != methodology_version:
            report.stale_count += 1
            if len(report.stale_edges) < _SAMPLE_CAP:
                report.stale_edges.append(
                    f"{label} (methodology {edge.methodology_version})"
                )
        key = (edge.edge_type.value, edge.from_global_key, edge.to_global_key)
        if key in seen_edges:
            report.duplicate_count += 1
            if len(report.duplicate_edges) < _SAMPLE_CAP:
                report.duplicate_edges.append(label)
        seen_edges.add(key)

    for node in nodes:
        if node.global_key not in referenced:
            report.orphan_count += 1
            if len(report.orphan_nodes) < _SAMPLE_CAP:
                report.orphan_nodes.append(node.global_key)

    if report.orphan_count:
        report.note = (
            f"Caissa schema {schema_version} / methodology {methodology_version}: "
            f"{report.orphan_count} node(s) are not referenced by any relationship."
        )
    return report


__all__ = ["GraphHealthReport", "check_graph"]
