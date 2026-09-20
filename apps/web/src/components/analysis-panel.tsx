"use client";

import { EmptyState } from "@/components/empty-state";
import type { StoredAnalysisRow } from "@/lib/api";

const CLASSIFICATION_STYLES: Record<string, string> = {
  brilliant: "bg-cyan-100 text-cyan-800",
  best: "bg-emerald-100 text-emerald-800",
  good: "bg-green-100 text-green-800",
  inaccurate: "bg-yellow-100 text-yellow-800",
  mistake: "bg-orange-100 text-orange-800",
  blunder: "bg-red-100 text-red-800",
};

export function classificationBadgeClass(classification: string | null): string {
  if (!classification) return "bg-neutral-100 text-neutral-500";
  return CLASSIFICATION_STYLES[classification] ?? "bg-neutral-100 text-neutral-500";
}

// Analysis panel: renders stored engine analyses per move. Clicking a row
// jumps the board to that position (parent-supplied callback).
export function AnalysisPanel({
  analyses,
  selectedPly,
  onSelectPly,
}: {
  analyses: StoredAnalysisRow[] | null;
  selectedPly: number | null;
  onSelectPly: (ply: number) => void;
}) {
  if (!analyses || analyses.length === 0) {
    return (
      <EmptyState
        title="Not analyzed yet"
        message="This game has no stored engine analysis. Run the analysis from the button above — Stockfish evaluations will appear here per move."
      />
    );
  }

  return (
    <div className="space-y-2">
      {analyses.map((row) => {
        const selected = selectedPly === row.ply;
        return (
          <button
            key={row.ply}
            type="button"
            onClick={() => onSelectPly(row.ply)}
            className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
              selected
                ? "border-neutral-900 bg-neutral-50"
                : "border-neutral-200 bg-white hover:border-neutral-400"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-neutral-500">
                  {row.move_number}
                  {row.color === "white" ? "." : "…"}
                </span>
                <span className="font-mono text-sm font-medium text-neutral-900">
                  {row.played_move}
                </span>
                {row.best_move && row.best_move !== row.played_move ? (
                  <span className="font-mono text-xs text-neutral-500">
                    best: {row.best_move}
                  </span>
                ) : null}
              </div>
              <span
                className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${classificationBadgeClass(
                  row.classification
                )}`}
              >
                {row.classification ?? "unclassified"}
              </span>
            </div>
            <div className="mt-1 flex gap-4 text-[11px] text-neutral-500">
              {row.centipawn_loss !== null ? <span>CPL {row.centipawn_loss}</span> : null}
              {row.evaluation_change !== null ? (
                <span>Δ eval {row.evaluation_change > 0 ? "+" : ""}
                  {row.evaluation_change}
                </span>
              ) : null}
              <span>depth {row.depth}</span>
              {row.is_sacrifice ? <span>sacrifice</span> : null}
            </div>
          </button>
        );
      })}
    </div>
  );
}
