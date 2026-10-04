import type { AnalysisStatus } from "@/lib/api";

const STYLES: Record<AnalysisStatus, string> = {
  imported: "bg-ink-700 text-mist-400 ring-1 ring-ink-600",
  validating: "bg-sky-400/15 text-sky-300 ring-1 ring-sky-400/30",
  ready: "bg-sky-400/15 text-sky-300 ring-1 ring-sky-400/30",
  analyzing: "bg-amber-400/15 text-amber-300 ring-1 ring-amber-400/30",
  // `emerald-600` maps to the dim shade: the bright emerald on its own tint
  // only reaches ~4.3:1. The other signals already clear AA on their tints.
  analyzed: "bg-emerald-400/15 text-emerald-600 ring-1 ring-emerald-400/30",
  failed: "bg-rose-400/15 text-rose-300 ring-1 ring-rose-400/30",
};

const LABELS: Record<AnalysisStatus, string> = {
  imported: "Imported",
  validating: "Validating",
  ready: "Ready to analyze",
  analyzing: "Analyzing…",
  analyzed: "Analyzed",
  failed: "Analysis failed",
};

// The backend owns the status value; this component only renders it. It never
// infers whether analysis exists.
export function AnalysisStatusBadge({
  status,
  pulse = false,
}: {
  status: AnalysisStatus;
  pulse?: boolean;
}) {
  const style = STYLES[status] ?? STYLES.imported;
  return (
    <span className={`badge inline-flex items-center gap-1.5 ${style}`}>
      {pulse && status === "analyzing" ? (
        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-300" />
      ) : null}
      {LABELS[status] ?? status}
    </span>
  );
}
