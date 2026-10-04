"use client";

import type { TimelineRow } from "@/lib/api";

// Critical-moment timeline. Each entry is a real navigation target: clicking it
// moves the board to that ply. Entries keep their source (engine fact / Caissa
// feature / Caissa interpretation) and whether they are confirmed or a candidate.

const SEVERITY_STYLE: Record<string, string> = {
  high: "border-rose-400/40 bg-rose-400/10 text-rose-200",
  medium: "border-amber-400/40 bg-amber-400/10 text-amber-200",
  low: "border-ink-700 bg-ink-900/60 text-mist-300",
};

const KIND_LABEL: Record<string, string> = {
  opening_deviation: "Opening",
  tactical_event: "Tactics",
  turning_point: "Turning point",
  conversion: "Conversion",
  critical_position: "Engine",
};

const SOURCE_LABEL: Record<string, string> = {
  engine_fact: "engine",
  argus_derived_feature: "board",
  argus_interpretation: "Caissa rule",
};

export function CriticalTimeline({
  entries,
  selectedPly,
  onSelectPly,
}: {
  entries: TimelineRow[];
  selectedPly: number;
  onSelectPly: (ply: number) => void;
}) {
  if (entries.length === 0) {
    return (
      <div className="card p-4">
        <h3 className="label mb-2">Critical-moment timeline</h3>
        <p className="text-xs leading-relaxed text-mist-500">
          No critical moments were detected. Large swings are candidates, not verdicts — nothing is
          invented when the game is clean.
        </p>
      </div>
    );
  }

  return (
    <div className="card p-4">
      <div className="mb-2.5 flex items-center justify-between">
        <h3 className="label">Critical-moment timeline</h3>
        <span className="mono text-small text-mist-600">{entries.length} events</span>
      </div>
      <ol className="relative max-h-[26rem] space-y-2 overflow-y-auto pr-1">
        {entries.map((entry, index) => {
          const active = entry.ply === selectedPly;
          return (
            <li key={`${entry.ply}-${entry.kind}-${index}`} className="relative pl-4">
              <span
                className={`absolute left-0 top-2 h-2 w-2 rounded-full ${
                  entry.severity === "high"
                    ? "bg-rose-400"
                    : entry.severity === "medium"
                      ? "bg-amber-400"
                      : "bg-ink-500"
                }`}
              />
              <button
                type="button"
                onClick={() => onSelectPly(entry.ply)}
                className={`w-full rounded-lg border p-2.5 text-left transition-colors ${
                  active ? "border-emerald-400/50 bg-emerald-500/10" : SEVERITY_STYLE[entry.severity] ?? SEVERITY_STYLE.low
                }`}
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="mono text-small text-mist-400">
                    {entry.move_number ?? Math.ceil(entry.ply / 2)}
                    {entry.side === "white" ? "." : entry.side === "black" ? "…" : ""}
                    {entry.san ? ` ${entry.san}` : ""}
                  </span>
                  <span className="badge bg-ink-800/80 text-small text-mist-300">
                    {KIND_LABEL[entry.kind] ?? entry.kind}
                  </span>
                  {entry.certainty === "candidate" ? (
                    <span className="badge bg-amber-400/10 text-small text-amber-300">
                      candidate
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 text-small font-medium text-mist-100">{entry.label}</p>
                <p className="mt-0.5 text-small leading-relaxed text-mist-400">{entry.statement}</p>
                <p className="mt-1 mono text-small text-mist-600">
                  source: {SOURCE_LABEL[entry.source] ?? entry.source}
                </p>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
