// Deterministic force-directed layout for the Intelligence Map (§39).
//
// A small spring/electrical simulation, written here rather than pulling in a
// graph library: the map is a bounded neighbourhood (a few dozen nodes), so an
// O(n²) pass is fine, and a *deterministic* layout matters more than a physics
// engine. Nothing here uses Math.random: initial positions come from a stable
// hash of each node's global key, so the same subgraph draws the same way every
// time — a screenshot from a bug report can be reproduced, and two runs of the
// same evaluation line up.
//
// The forces are the textbook three:
//   * repulsion between every pair, so nodes do not collapse onto each other;
//   * a spring along every edge, so related nodes pull together;
//   * a weak pull to the centre, so disconnected components do not fly away.

export interface LayoutInputNode {
  /** Stable identity (``"game:abc"``). Also what edges refer to. */
  id: string;
  /** A larger radius repels and separates more; used for the root node. */
  weight?: number;
}

export interface LayoutInputEdge {
  from: string;
  to: string;
}

export interface LayoutPoint {
  x: number;
  y: number;
}

export interface LayoutOptions {
  width?: number;
  height?: number;
  /** Physics steps. More is smoother and slower; the default settles quickly. */
  iterations?: number;
}

const DEFAULT_WIDTH = 900;
const DEFAULT_HEIGHT = 560;
const DEFAULT_ITERATIONS = 320;

/** A small, stable string hash (FNV-1a style), so layout is reproducible. */
export function hashString(value: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/**
 * Lay out ``nodes`` and ``edges`` in a bounded box.
 *
 * Returns a map from node id to position. The caller draws the edges as straight
 * lines between the two endpoints, so no routing is calculated here — the map
 * shows connectivity, not a chessboard.
 */
export function forceLayout(
  nodes: LayoutInputNode[],
  edges: LayoutInputEdge[],
  options: LayoutOptions = {},
): Map<string, LayoutPoint> {
  const width = options.width ?? DEFAULT_WIDTH;
  const height = options.height ?? DEFAULT_HEIGHT;
  const iterations = options.iterations ?? DEFAULT_ITERATIONS;
  const count = nodes.length;
  const positions = new Map<string, LayoutPoint>();
  if (count === 0) return positions;

  const radius = Math.min(width, height) * 0.38;
  const cx = width / 2;
  const cy = height / 2;

  // Seed on a circle, in a stable order. The hash jitters the angle a little so
  // sibling subgraphs (many nodes of the same type) do not overlap exactly, while
  // still being fully reproducible.
  const ordered = [...nodes].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const xs = new Float64Array(count);
  const ys = new Float64Array(count);
  const indexById = new Map<string, number>();
  ordered.forEach((node, index) => {
    indexById.set(node.id, index);
    const base = (index / count) * Math.PI * 2;
    const jitter = (hashString(node.id) % 1000) / 1000 / count;
    const angle = base + jitter;
    xs[index] = cx + Math.cos(angle) * radius;
    ys[index] = cy + Math.sin(angle) * radius;
  });

  const pairs: Array<[number, number]> = [];
  for (const edge of edges) {
    const from = indexById.get(edge.from);
    const to = indexById.get(edge.to);
    if (from === undefined || to === undefined || from === to) continue;
    pairs.push([from, to]);
  }

  const weights = ordered.map((node) => Math.max(0.6, node.weight ?? 1));

  // Tuned for the neighbourhood sizes the API returns (≤ 200 nodes). These are
  // deliberately gentle: a map that jitters is unreadable, and correctness of
  // the *connections* is what matters, not a perfect embedding.
  const repulsion = 6200;
  const springLength = 118;
  const springStrength = 0.02;
  const centrePull = 0.0045;
  const damping = 0.86;

  const dispX = new Float64Array(count);
  const dispY = new Float64Array(count);

  for (let step = 0; step < iterations; step += 1) {
    dispX.fill(0);
    dispY.fill(0);

    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        let dx = xs[i] - xs[j];
        let dy = ys[i] - ys[j];
        let distanceSq = dx * dx + dy * dy;
        if (distanceSq < 1) {
          // Perfectly coincident: nudge apart deterministically by index.
          dx = (i - j) * 0.5 + 0.5;
          dy = (i + j) * 0.5 + 0.5;
          distanceSq = dx * dx + dy * dy;
        }
        const distance = Math.sqrt(distanceSq);
        const force = (repulsion * weights[i] * weights[j]) / distanceSq;
        const fx = (dx / distance) * force;
        const fy = (dy / distance) * force;
        dispX[i] += fx;
        dispY[i] += fy;
        dispX[j] -= fx;
        dispY[j] -= fy;
      }
    }

    for (const [from, to] of pairs) {
      const dx = xs[to] - xs[from];
      const dy = ys[to] - ys[from];
      const distance = Math.max(1, Math.hypot(dx, dy));
      const force = (distance - springLength) * springStrength;
      const fx = (dx / distance) * force;
      const fy = (dy / distance) * force;
      dispX[from] += fx;
      dispY[from] += fy;
      dispX[to] -= fx;
      dispY[to] -= fy;
    }

    for (let i = 0; i < count; i += 1) {
      dispX[i] += (cx - xs[i]) * centrePull;
      dispY[i] += (cy - ys[i]) * centrePull;
      xs[i] = Math.max(26, Math.min(width - 26, xs[i] + dispX[i] * damping));
      ys[i] = Math.max(26, Math.min(height - 26, ys[i] + dispY[i] * damping));
    }
  }

  ordered.forEach((node, index) => {
    positions.set(node.id, { x: xs[index], y: ys[index] });
  });
  return positions;
}

/** The stable global key the graph uses for a node (``node_type:node_key``). */
export function globalKey(nodeType: string, nodeKey: string): string {
  return `${nodeType}:${nodeKey}`;
}
