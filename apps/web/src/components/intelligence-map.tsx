"use client";

import { useMemo, useState } from "react";

import type { GraphNeighborhood, GraphNeighborhoodNode } from "@/lib/api";
import {
  forceLayout,
  globalKey,
  type LayoutInputEdge,
  type LayoutInputNode,
} from "@/lib/force-layout";

// The Intelligence Map (§39): a force-directed view of one node's neighbourhood.
//
// The point of the map is what a table cannot show — that a pattern sits on a
// position inside a game played by a player, and that those connections continue
// outward. It is deliberately not a second source of truth: every node and edge
// drawn here came back from the graph's authorized traversal, an edge is labelled
// with its own relationship name, and the map states plainly when the walk was
// truncated or when privacy withheld nodes.
//
// Layout is deterministic (no Math.random), so the same neighbourhood always
// draws the same way. Colour is a cue, never the only signal: the node type is
// always written out beside the node and repeated in the legend and the list.

const WIDTH = 900;
const HEIGHT = 560;

//: Node colour by kind, drawn straight from the theme tokens so the map is
//: correct in both light and dark themes. These are cues; the label carries the
//: meaning for anyone who cannot rely on colour.
const NODE_COLOR: Record<string, string> = {
  player: "var(--color-violet-primary)",
  game: "var(--color-emerald-primary)",
  position: "var(--color-sky-primary)",
  opening: "var(--color-amber-primary)",
  opening_node: "var(--color-amber-primary)",
  game_phase: "var(--color-mist-500)",
  pattern: "var(--color-rose-primary)",
  tactical_pattern: "var(--color-rose-primary)",
  positional_pattern: "var(--color-amber-primary)",
  king_safety_pattern: "var(--color-rose-primary)",
  material_pattern: "var(--color-amber-primary)",
  insight: "var(--color-violet-primary)",
  training_position: "var(--color-emerald-primary)",
  training_attempt: "var(--color-emerald-primary)",
  training_session: "var(--color-emerald-primary)",
  scenario: "var(--color-violet-primary)",
  prediction: "var(--color-violet-primary)",
  opponent_profile: "var(--color-violet-primary)",
  preparation_report: "var(--color-violet-primary)",
  knowledge_concept: "var(--color-sky-primary)",
  knowledge_document: "var(--color-sky-primary)",
  study_item: "var(--color-mist-500)",
};

function nodeColor(nodeType: string): string {
  return NODE_COLOR[nodeType] ?? "var(--color-mist-500)";
}

function readableType(nodeType: string): string {
  return nodeType.replace(/_/g, " ");
}

function shortLabel(node: GraphNeighborhoodNode): string {
  const label = node.label?.trim();
  if (label) return label.length > 24 ? `${label.slice(0, 23)}…` : label;
  const key = node.node_key;
  return key.length > 14 ? `${key.slice(0, 13)}…` : key;
}

/**
 * Draw a neighbourhood returned by ``GET /api/graph/neighborhood``.
 *
 * The component is presentational: it reports a click through ``onSelect`` and
 * leaves fetching and the "Why?" trace to the page. When there is nothing to
 * draw it says so with the reason the API gave rather than rendering an empty
 * canvas.
 */
export function IntelligenceMap({
  graph,
  selectedKey,
  onSelect,
  height = 420,
}: {
  graph: GraphNeighborhood;
  selectedKey?: string;
  onSelect?: (node: GraphNeighborhoodNode) => void;
  height?: number;
}) {
  const nodes = useMemo(() => graph.nodes ?? [], [graph.nodes]);
  const edges = useMemo(() => graph.edges ?? [], [graph.edges]);
  const [showEdgeLabels, setShowEdgeLabels] = useState(true);

  const ids = useMemo(
    () => new Set(nodes.map((node) => globalKey(node.node_type, node.node_key))),
    [nodes],
  );

  const layout = useMemo(() => {
    const inputNodes: LayoutInputNode[] = nodes.map((node) => ({
      id: globalKey(node.node_type, node.node_key),
      weight: globalKey(node.node_type, node.node_key) === graph.node ? 1.9 : 1,
    }));
    const inputEdges: LayoutInputEdge[] = edges
      .map((edge) => ({
        from: globalKey(edge.from.node_type, edge.from.node_key),
        to: globalKey(edge.to.node_type, edge.to.node_key),
      }))
      .filter((edge) => ids.has(edge.from) && ids.has(edge.to));
    return forceLayout(inputNodes, inputEdges, { width: WIDTH, height: HEIGHT });
  }, [nodes, edges, ids, graph.node]);

  const positioned = useMemo(
    () =>
      nodes
        .map((node) => {
          const id = globalKey(node.node_type, node.node_key);
          const point = layout.get(id);
          return point ? { node, id, ...point } : null;
        })
        .filter((entry): entry is { node: GraphNeighborhoodNode; id: string; x: number; y: number } =>
          entry !== null,
        ),
    [nodes, layout],
  );

  const positionById = useMemo(
    () => new Map(positioned.map((entry) => [entry.id, entry])),
    [positioned],
  );

  const typesPresent = useMemo(() => {
    const seen = new Map<string, number>();
    for (const node of nodes) {
      seen.set(node.node_type, (seen.get(node.node_type) ?? 0) + 1);
    }
    return [...seen.entries()].sort((a, b) => b[1] - a[1]);
  }, [nodes]);

  if (!graph.found) {
    return (
      <div className="rounded-lg border border-dashed border-ink-600 bg-ink-900/40 p-4">
        <p className="text-small leading-relaxed text-mist-500">
          {graph.note ?? "Caissa has no such node available to this caller."}
        </p>
      </div>
    );
  }

  if (nodes.length <= 1) {
    return (
      <div className="rounded-lg border border-dashed border-ink-600 bg-ink-900/40 p-4">
        <p className="text-small leading-relaxed text-mist-500">
          This node is stored, but Caissa has no stored relationship from it within{" "}
          {graph.depth ?? 1} hop{(graph.depth ?? 1) === 1 ? "" : "s"}. A node with nothing
          connected is shown as nothing connected — the map does not invent an edge.
        </p>
      </div>
    );
  }

  const edgeCount = edges.length;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="mono text-small text-mist-600">
          {nodes.length} node{nodes.length === 1 ? "" : "s"} · {edgeCount} edge
          {edgeCount === 1 ? "" : "s"} · depth {graph.depth ?? 1}
        </span>
        <button
          type="button"
          className="btn btn-ghost text-xs"
          onClick={() => setShowEdgeLabels((value) => !value)}
          aria-pressed={showEdgeLabels}
        >
          {showEdgeLabels ? "Hide" : "Show"} edge labels
        </button>
      </div>

      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ height }}
        className="w-full rounded-lg bg-ink-900/70 ring-1 ring-ink-700"
        role="img"
        aria-label={`Intelligence map: ${nodes.length} nodes and ${edgeCount} relationships around ${graph.label ?? graph.node}`}
      >
        <defs>
          <marker
            id="argus-map-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-ink-500)" />
          </marker>
        </defs>

        {edges.map((edge, index) => {
          const from = positionById.get(globalKey(edge.from.node_type, edge.from.node_key));
          const to = positionById.get(globalKey(edge.to.node_type, edge.to.node_key));
          if (!from || !to) return null;
          const x1 = from.x;
          const y1 = from.y;
          const x2 = to.x;
          const y2 = to.y;
          const midX = (x1 + x2) / 2;
          const midY = (y1 + y2) / 2;
          const label = edge.edge_type.replace(/_/g, " ");
          return (
            <g key={`edge-${index}`}>
              <line
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                stroke="var(--color-ink-500)"
                strokeWidth={1}
                strokeOpacity={0.55}
                markerEnd="url(#argus-map-arrow)"
              >
                <title>
                  {label}
                  {edge.evidence_count ? ` · ${edge.evidence_count} evidence refs` : ""}
                  {edge.sample_size ? ` · sample ${edge.sample_size}` : ""}
                </title>
              </line>
              {showEdgeLabels && edgeCount <= 40 ? (
                <text
                  x={midX}
                  y={midY - 3}
                  textAnchor="middle"
                  className="mono"
                  fontSize={9}
                  fill="var(--color-mist-600)"
                >
                  {label}
                </text>
              ) : null}
            </g>
          );
        })}

        {positioned.map((entry) => {
          const isRoot = entry.id === graph.node;
          const isSelected = entry.id === selectedKey;
          const radius = isRoot ? 13 : 9;
          return (
            <g
              key={entry.id}
              transform={`translate(${entry.x} ${entry.y})`}
              className={onSelect ? "cursor-pointer" : undefined}
              onClick={() => onSelect?.(entry.node)}
            >
              <title>
                {readableType(entry.node.node_type)} · {entry.node.label || entry.node.node_key}
              </title>
              {isSelected ? (
                <circle r={radius + 5} fill="none" stroke="var(--color-mist-50)" strokeWidth={1.5} />
              ) : null}
              <circle
                r={radius}
                fill={nodeColor(entry.node.node_type)}
                stroke="var(--color-ink-850)"
                strokeWidth={2}
              />
              {isRoot ? (
                <circle r={radius + 3} fill="none" stroke={nodeColor(entry.node.node_type)} strokeWidth={1} strokeOpacity={0.5} />
              ) : null}
              <text
                y={radius + 12}
                textAnchor="middle"
                fontSize={11}
                fill="var(--color-mist-300)"
              >
                {shortLabel(entry.node)}
              </text>
              <text
                y={radius + 23}
                textAnchor="middle"
                className="mono"
                fontSize={9}
                fill="var(--color-mist-600)"
              >
                {readableType(entry.node.node_type)}
              </text>
            </g>
          );
        })}
      </svg>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {typesPresent.map(([type, count]) => (
          <span key={type} className="flex items-center gap-1.5 text-meta text-mist-500">
            <span
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: nodeColor(type) }}
            />
            {readableType(type)}
            <span className="mono text-mist-600">· {count}</span>
          </span>
        ))}
      </div>

      {graph.truncated ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2">
          <p className="text-meta text-amber-300">
            The walk hit its node limit ({graph.limit}), so this map is partial. Narrow the depth
            or widen the limit to see the rest — Caissa does not imply the neighbourhood ends here.
          </p>
        </div>
      ) : null}
      {graph.denied_count ? (
        <div className="rounded-lg border border-rose-500/25 bg-rose-500/5 px-3 py-2">
          <p className="text-meta text-rose-300">
            {graph.denied_count} connected node{graph.denied_count === 1 ? "" : "s"} withheld by
            authorization and not drawn.
          </p>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The same neighbourhood as a keyboard- and screen-reader-navigable list.
 *
 * The SVG is one image to assistive technology, so the connections are repeated
 * here as a real list of edges with buttons for each node. This is the surface
 * that is reached by Tab; the drawing is the visual shortcut to the same facts.
 */
export function NeighborhoodList({
  graph,
  selectedKey,
  onSelect,
}: {
  graph: GraphNeighborhood;
  selectedKey?: string;
  onSelect?: (node: GraphNeighborhoodNode) => void;
}) {
  const nodes = useMemo(() => graph.nodes ?? [], [graph.nodes]);
  const edges = graph.edges ?? [];
  const byId = useMemo(
    () => new Map(nodes.map((node) => [globalKey(node.node_type, node.node_key), node])),
    [nodes],
  );

  if (!graph.found || edges.length === 0) return null;

  return (
    <div className="card p-4">
      <h3 className="label mb-3 flex items-center justify-between">
        <span>Connections</span>
        <span className="mono text-mist-600">{edges.length}</span>
      </h3>
      <ul className="space-y-1.5">
        {edges.map((edge, index) => {
          const from = byId.get(globalKey(edge.from.node_type, edge.from.node_key));
          const to = byId.get(globalKey(edge.to.node_type, edge.to.node_key));
          if (!from || !to) return null;
          const fromId = globalKey(from.node_type, from.node_key);
          const toId = globalKey(to.node_type, to.node_key);
          return (
            <li
              key={`hop-${index}`}
              className="flex flex-wrap items-center gap-x-1.5 text-small text-mist-500"
            >
              <button
                type="button"
                className={`mono ${fromId === selectedKey ? "text-mist-50" : "text-mist-200 hover:text-mist-50"}`}
                onClick={() => onSelect?.(from)}
              >
                {from.label || from.node_key}
              </button>
              <span className="mono text-meta text-mist-600">{edge.edge_type.replace(/_/g, " ")}</span>
              <span className="text-mist-600">→</span>
              <button
                type="button"
                className={`mono ${toId === selectedKey ? "text-mist-50" : "text-mist-200 hover:text-mist-50"}`}
                onClick={() => onSelect?.(to)}
              >
                {to.label || to.node_key}
              </button>
              {edge.evidence_count ? (
                <span className="badge badge-source">{edge.evidence_count} refs</span>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
