"use client";

import { EmptyState } from "@/components/empty-state";
import { classificationBadgeClass } from "@/components/analysis-panel";
import type { GameReport } from "@/lib/api";

function MomentList({
  moments,
  onSelectPly,
}: {
  moments: GameReport["critical_moments"];
  onSelectPly?: (ply: number) => void;
}) {
  return (
    <ul className="space-y-2">
      {moments.map((moment) => (
        <li key={moment.ply}>
          <button
            type="button"
            onClick={() => onSelectPly?.(moment.ply)}
            className="w-full rounded-lg border border-neutral-200 bg-white px-3 py-2 text-left transition-colors hover:border-neutral-400"
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-neutral-500">
                  {moment.move_number}
                  {moment.color === "white" ? "." : "…"}
                </span>
                <span className="font-mono text-sm font-medium text-neutral-900">
                  {moment.san}
                </span>
              </div>
              <span
                className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${classificationBadgeClass(
                  moment.classification
                )}`}
              >
                {moment.classification}
              </span>
            </div>
            <p className="mt-1 text-xs text-neutral-600">{moment.description}</p>
          </button>
        </li>
      ))}
    </ul>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  );
}

// Report panel: renders the deterministic report produced by the backend.
// Sections not implemented yet are listed honestly in "Planned sections".
export function ReportPanel({
  report,
  onSelectPly,
}: {
  report: GameReport | null;
  onSelectPly?: (ply: number) => void;
}) {
  if (!report) {
    return (
      <EmptyState
        title="No report yet"
        message="Run the engine analysis to generate an explainable game report."
      />
    );
  }

  const { summary } = report;
  return (
    <div className="space-y-4">
      <Section title="Summary">
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-neutral-500">White avg CPL</dt>
            <dd className="font-mono font-medium text-neutral-900">
              {summary.white.average_centipawn_loss ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-neutral-500">Black avg CPL</dt>
            <dd className="font-mono font-medium text-neutral-900">
              {summary.black.average_centipawn_loss ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-neutral-500">Sacrifices</dt>
            <dd className="font-mono font-medium text-neutral-900">{summary.sacrifices}</dd>
          </div>
          <div>
            <dt className="text-neutral-500">Classified</dt>
            <dd className="font-mono font-medium text-neutral-900">
              {summary.classified_moves}/{summary.total_moves}
            </dd>
          </div>
        </dl>
      </Section>

      {report.turning_point ? (
        <Section title="Turning Point">
          <MomentList moments={[report.turning_point]} onSelectPly={onSelectPly} />
        </Section>
      ) : null}

      {report.critical_moments.length > 0 ? (
        <Section title="Critical Moments">
          <MomentList moments={report.critical_moments} onSelectPly={onSelectPly} />
        </Section>
      ) : null}

      {report.best_moves.length > 0 ? (
        <Section title="Best Moves">
          <MomentList moments={report.best_moves} onSelectPly={onSelectPly} />
        </Section>
      ) : null}

      {report.pending_sections.length > 0 ? (
        <Section title="Planned Sections (not implemented yet)">
          <div className="flex flex-wrap gap-1.5">
            {report.pending_sections.map((section) => (
              <span
                key={section}
                className="rounded bg-neutral-100 px-2 py-0.5 text-[11px] text-neutral-500"
              >
                {section}
              </span>
            ))}
          </div>
        </Section>
      ) : null}
    </div>
  );
}
