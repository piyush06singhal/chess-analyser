"use client";

// Move-classification presentation: the badge and its style map. (The panel that
// once rendered the legacy positional-analysis rows was removed with that store.)

const CLASSIFICATION_STYLES: Record<string, string> = {
  brilliant: "bg-cyan-400/15 text-cyan-300 ring-1 ring-cyan-400/30",
  best: "bg-emerald-400/15 text-emerald-300 ring-1 ring-emerald-400/30",
  excellent: "bg-teal-400/15 text-teal-300 ring-1 ring-teal-400/30",
  good: "bg-green-400/10 text-green-300 ring-1 ring-green-400/20",
  inaccurate: "bg-amber-400/15 text-amber-300 ring-1 ring-amber-400/30",
  mistake: "bg-orange-400/15 text-orange-300 ring-1 ring-orange-400/30",
  blunder: "bg-rose-400/15 text-rose-300 ring-1 ring-rose-400/30",
};

// Single-character marks for the compact (move-list) form.
const COMPACT_MARKS: Record<string, string> = {
  brilliant: "!!",
  best: "✓",
  excellent: "✓",
  good: "·",
  inaccurate: "?!",
  mistake: "?",
  blunder: "??",
};

export function classificationBadgeClass(classification: string | null): string {
  if (!classification) return "bg-ink-700 text-mist-500 ring-1 ring-ink-600";
  return CLASSIFICATION_STYLES[classification] ?? "bg-ink-700 text-mist-400";
}

export function ClassificationBadge({
  classification,
  compact = false,
}: {
  classification: string | null;
  compact?: boolean;
}) {
  if (compact) {
    return (
      <span
        title={classification ?? "unclassified"}
        className={`inline-flex h-4 w-5 items-center justify-center rounded text-small font-bold ${
          classificationBadgeClass(classification)
        }`}
      >
        {classification ? COMPACT_MARKS[classification] ?? "?" : "·"}
      </span>
    );
  }
  return (
    <span className={`badge ${classificationBadgeClass(classification)}`}>
      {classification ?? "unclassified"}
    </span>
  );
}


