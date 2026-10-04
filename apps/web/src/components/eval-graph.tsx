"use client";

import { useMemo, useRef, useState } from "react";
import type { TimelineRow, TrajectoryPointRow } from "@/lib/api";

// Evaluation graph over the game.
//
// Conventions it enforces:
//  * x-axis is ply, y-axis is the engine evaluation (White perspective) —
//  * mates are drawn clamped to the edge (never as a huge centipawn number),
//  * a ply without a stored engine evaluation BREAKS the line and is marked
//    with a dashed separator. Missing data is shown as missing, never
//    interpolated and presented as an engine value,
//  * critical moments are marked; hovering and clicking select a ply so the
//    board and the rest of the report stay synchronized.

const WIDTH = 1000;
const HEIGHT = 220;
const PAD = 12;
//: Evaluations beyond this are drawn at the edge; the true value is still shown
//: in the tooltip and the panel, unclipped.
const CLAMP_CP = 800;

function toY(point: TrajectoryPointRow): number {
  if (point.mate_white !== null && point.mate_white !== undefined) {
    return point.mate_white > 0 ? PAD : HEIGHT - PAD;
  }
  const cp = point.evaluation_cp_white ?? 0;
  const clamped = Math.max(-CLAMP_CP, Math.min(CLAMP_CP, cp));
  const ratio = clamped / CLAMP_CP; // -1..1
  return HEIGHT / 2 - (ratio * (HEIGHT / 2 - PAD));
}

function toX(index: number, count: number): number {
  if (count <= 1) return PAD;
  return PAD + (index / (count - 1)) * (WIDTH - PAD * 2);
}

export function EvalGraph({
  points,
  timeline = [],
  selectedPly,
  onSelectPly,
}: {
  points: TrajectoryPointRow[];
  timeline?: TimelineRow[];
  selectedPly: number;
  onSelectPly: (ply: number) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const segments = useMemo(() => {
    const runs: { x: number; y: number; ply: number; index: number }[][] = [];
    let current: { x: number; y: number; ply: number; index: number }[] = [];
    points.forEach((point, index) => {
      if (!point.available) {
        if (current.length > 0) runs.push(current);
        current = [];
        return;
      }
      current.push({
        x: toX(index, points.length),
        y: toY(point),
        ply: point.ply,
        index,
      });
    });
    if (current.length > 0) runs.push(current);
    return runs;
  }, [points]);

  const markers = useMemo(() => {
    const indexByPly = new Map(points.map((point, index) => [point.ply, index]));
    return timeline
      .filter((entry) => entry.severity === "high" && indexByPly.has(entry.ply))
      .map((entry) => ({
        x: toX(indexByPly.get(entry.ply) ?? 0, points.length),
        label: entry.label,
        severity: entry.severity,
      }));
  }, [timeline, points]);

  const available = points.filter((point) => point.available).length;
  if (points.length === 0 || available === 0) {
    return (
      <div className="rounded-lg border border-dashed border-ink-600 bg-ink-900/40 p-4">
        <p className="text-small leading-relaxed text-mist-500">
          No stored engine evaluation for this game, so there is no trajectory to draw. Caissa
          does not interpolate or invent evaluation values.
        </p>
      </div>
    );
  }

  const selectedIndex = points.findIndex((point) => point.ply === selectedPly);
  const hoverIndex = hover ?? selectedIndex;
  const active = hoverIndex >= 0 ? points[hoverIndex] : null;

  function indexFromEvent(event: React.MouseEvent<SVGSVGElement>): number {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return -1;
    const ratio = (event.clientX - rect.left) / rect.width;
    const x = ratio * WIDTH;
    const span = WIDTH - PAD * 2;
    if (span <= 0) return 0;
    const index = Math.round(((x - PAD) / span) * (points.length - 1));
    return Math.max(0, Math.min(points.length - 1, index));
  }

  const missingPlys = points.filter((point) => !point.available);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="mono text-small text-mist-600">
          White perspective · {available}/{points.length} plies evaluated
        </span>
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="h-40 w-full cursor-crosshair rounded-lg bg-ink-900/70 ring-1 ring-ink-700"
        role="img"
        aria-label="Evaluation graph over the game, positive is better for White"
        onMouseMove={(event) => setHover(indexFromEvent(event))}
        onMouseLeave={() => setHover(null)}
        onClick={(event) => {
          const index = indexFromEvent(event);
          if (index >= 0) onSelectPly(points[index].ply);
        }}
      >
        {/* zero line and a ±200cp reference band */}
        <line x1={0} y1={HEIGHT / 2} x2={WIDTH} y2={HEIGHT / 2} stroke="rgba(148,163,184,0.35)" strokeWidth={1} />
        {[200, -200].map((cp) => {
          const y = HEIGHT / 2 - ((cp / CLAMP_CP) * (HEIGHT / 2 - PAD));
          return (
            <line
              key={cp}
              x1={0}
              y1={y}
              x2={WIDTH}
              y2={y}
              stroke="rgba(148,163,184,0.16)"
              strokeWidth={1}
              strokeDasharray="4 6"
            />
          );
        })}

        {/* gaps: plies with no stored evaluation */}
        {missingPlys.map((point) => {
          const index = points.findIndex((candidate) => candidate.ply === point.ply);
          const x = toX(index, points.length);
          return (
            <line
              key={`gap-${point.ply}`}
              x1={x}
              y1={PAD}
              x2={x}
              y2={HEIGHT - PAD}
              stroke="rgba(244,63,94,0.35)"
              strokeWidth={1.5}
              strokeDasharray="2 4"
            />
          );
        })}

        {/* high-severity critical moments */}
        {markers.map((marker, index) => (
          <line
            key={`marker-${index}`}
            x1={marker.x}
            y1={PAD}
            x2={marker.x}
            y2={HEIGHT - PAD}
            stroke="rgba(251,191,36,0.35)"
            strokeWidth={1}
          />
        ))}

        {/* the trajectory itself, split into continuous runs */}
        {segments.map((run, index) => (
          <polyline
            key={`run-${index}`}
            fill="none"
            stroke="rgb(52,211,153)"
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            points={run.map((item) => `${item.x},${item.y}`).join(" ")}
          />
        ))}

        {/* the selected ply */}
        {selectedIndex >= 0 && points[selectedIndex].available ? (
          <circle
            cx={toX(selectedIndex, points.length)}
            cy={toY(points[selectedIndex])}
            r={5}
            fill="#f9fafb"
            stroke="rgb(52,211,153)"
            strokeWidth={2}
          />
        ) : null}
      </svg>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-small">
        <span className="mono text-mist-500">
          {active
            ? `${active.san ? `after ${active.move_number}.${active.san}` : "start"} · ${active.evaluation_display}` +
              (active.white_state ? ` · ${active.white_state.replace(/_/g, " ")}` : "")
            : "Hover the graph to inspect a position"}
        </span>
        <span className="flex items-center gap-3 text-mist-600">
          <span className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 bg-emerald-400" /> evaluation
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-0.5 bg-amber-400/50" /> critical
          </span>
          {missingPlys.length > 0 ? (
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-0.5 bg-rose-400/50" /> no evaluation
              ({missingPlys.length})
            </span>
          ) : null}
        </span>
      </div>

      <p className="mt-2 text-meta">
        Positive is better for White · mate shows as {"#n"} · gaps are plies the engine did not
        evaluate.
      </p>
    </div>
  );
}
