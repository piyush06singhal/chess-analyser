"use client";

import { evalShare, formatEvaluation } from "@/lib/eval";

// Evaluation bar. The fill is visually bounded; the true numeric value (and
// mate distance) is always shown unclipped beside it.
export function EvalBar({
  cp,
  mate,
  orientation = "white",
  label = true,
}: {
  cp: number | null;
  mate: number | null;
  orientation?: "white" | "black";
  label?: boolean;
}) {
  const share = evalShare(cp, mate);
  const whiteShare = orientation === "white" ? share : 1 - share;
  const text = formatEvaluation(cp, mate);

  return (
    <div className="flex w-full items-center gap-3">
      <div
        className="relative h-3 flex-1 overflow-hidden rounded-full bg-ink-900 ring-1 ring-ink-600"
        role="img"
        aria-label={`Evaluation ${text} (positive is better for White)`}
      >
        <div
          className="absolute inset-y-0 left-0 bg-mist-200 transition-[width] duration-300 ease-out"
          style={{ width: `${whiteShare * 100}%` }}
        />
        <div className="absolute inset-y-0 left-1/2 w-px bg-ink-500/70" />
      </div>
      {label ? (
        <span
          className={`w-16 text-right mono text-sm font-semibold tabular-nums ${
            mate !== null ? "text-rose-300" : "text-mist-100"
          }`}
        >
          {text}
        </span>
      ) : null}
    </div>
  );
}
