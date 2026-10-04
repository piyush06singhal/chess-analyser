"use client";

// The report's opening move: a verdict-first cinematic stage.
//
// Everything rendered here is read from the measured report payload — result,
// per-side accuracy, move-quality classifications from engine CPL, the largest
// measured swing, the final advantage state and the engine-derived forecast.
// The component computes *comparisons* of measured numbers (gaps, extremes)
// but never invents a quantity, and when a measurement is missing it says so
// instead of papering over it.

import { useMemo } from "react";
import type { GameReportPayload } from "@/lib/api";
import { formatEvaluation } from "@/lib/eval";
import { plural } from "@/lib/text";

type QualityKey = "brilliant" | "best" | "excellent" | "good" | "inaccurate" | "mistake" | "blunder";

const QUALITY_ORDER: QualityKey[] = [
  "brilliant",
  "best",
  "excellent",
  "good",
  "inaccurate",
  "mistake",
  "blunder",
];

const QUALITY_SEGMENT: Record<QualityKey, string> = {
  brilliant: "quality-best",
  best: "quality-best",
  excellent: "quality-best",
  good: "quality-good",
  inaccurate: "quality-inaccuracy",
  mistake: "quality-mistake",
  blunder: "quality-blunder",
};

const QUALITY_LABEL: Record<QualityKey, string> = {
  brilliant: "Brilliant",
  best: "Best",
  excellent: "Excellent",
  good: "Good",
  inaccurate: "Inaccurate",
  mistake: "Mistake",
  blunder: "Blunder",
};

interface SideQuality {
  counts: Record<QualityKey, number>;
  classified: number;
  scored: number;
}

function emptyQuality(): SideQuality {
  return {
    counts: { brilliant: 0, best: 0, excellent: 0, good: 0, inaccurate: 0, mistake: 0, blunder: 0 },
    classified: 0,
    scored: 0,
  };
}

/** Measured verdict derived from the report payload (never fabricated). */
function useVerdict(report: GameReportPayload) {
  return useMemo(() => {
    // --- move quality per side: scored accuracy moves joined with the
    // trajectory's engine classifications, by ply.
    const classificationByPly = new Map<number, string>();
    report.trajectory.trajectory.points.forEach((point) => {
      if (point.classification) classificationByPly.set(point.ply, point.classification);
    });

    const quality: Record<"white" | "black", SideQuality> = {
      white: emptyQuality(),
      black: emptyQuality(),
    };
    for (const move of report.accuracy.analysis.moves) {
      if (!move.scored) continue;
      const bucket = quality[move.side];
      bucket.scored += 1;
      const classification = classificationByPly.get(move.ply);
      if (classification && (QUALITY_ORDER as string[]).includes(classification)) {
        bucket.counts[classification as QualityKey] += 1;
        bucket.classified += 1;
      }
    }

    // --- final advantage state (last evaluated ply, White's POV value).
    const points = report.trajectory.trajectory.points;
    let finalPoint = null as (typeof points)[number] | null;
    for (let index = points.length - 1; index >= 0; index -= 1) {
      if (points[index].available) {
        finalPoint = points[index];
        break;
      }
    }

    // --- the largest measured swing of the game.
    const swings = report.turning_points.turning_points
      .filter((point) => point.swing_cp !== null)
      .sort((a, b) => Math.abs(b.swing_cp ?? 0) - Math.abs(a.swing_cp ?? 0));
    const biggestSwing = swings[0] ?? null;

    // --- accuracy gap (a comparison of two measured numbers).
    const whiteAccuracy = report.accuracy.analysis.white.accuracy;
    const blackAccuracy = report.accuracy.analysis.black.accuracy;
    const accuracyGap =
      whiteAccuracy !== null && blackAccuracy !== null ? whiteAccuracy - blackAccuracy : null;

    return {
      quality,
      finalPoint,
      biggestSwing,
      whiteAccuracy,
      blackAccuracy,
      accuracyGap,
      worstPhaseWhite: report.summary.phase_with_largest_drop.white ?? null,
      worstPhaseBlack: report.summary.phase_with_largest_drop.black ?? null,
    };
  }, [report]);
}

function resultParts(result: string): { won: "white" | "black" | "draw" | null; label: string } {
  if (result === "1-0") return { won: "white", label: "White won" };
  if (result === "0-1") return { won: "black", label: "Black won" };
  if (result === "1/2-1/2") return { won: "draw", label: "Draw" };
  return { won: null, label: "Result unknown" };
}

function QualityBar({ quality }: { quality: SideQuality }) {
  const total =
    quality.counts.brilliant +
    quality.counts.best +
    quality.counts.excellent +
    quality.counts.good +
    quality.counts.inaccurate +
    quality.counts.mistake +
    quality.counts.blunder;
  if (total === 0) {
    return <p className="text-small text-mist-500">No classified scored moves for this side.</p>;
  }
  const legend = QUALITY_ORDER.filter((key) => quality.counts[key] > 0);
  return (
    <div>
      <div
        className="quality-bar"
        role="img"
        aria-label={legend.map((key) => `${QUALITY_LABEL[key]} ${quality.counts[key]}`).join(", ")}
      >
        {legend.map((key) => (
          <span
            key={key}
            className={QUALITY_SEGMENT[key]}
            style={{ width: `${(quality.counts[key] / total) * 100}%` }}
            title={`${QUALITY_LABEL[key]}: ${quality.counts[key]}`}
          />
        ))}
      </div>
      <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-meta text-mist-400">
        {legend.map((key) => (
          <span key={key} className="inline-flex items-center gap-1.5">
            <span aria-hidden className={`inline-block h-2 w-2 rounded-full ${QUALITY_SEGMENT[key]}`} />
            {QUALITY_LABEL[key]} {quality.counts[key]}
          </span>
        ))}
      </p>
    </div>
  );
}

export function VerdictHero({
  report,
  onSelectPly,
}: {
  report: GameReportPayload;
  onSelectPly: (ply: number) => void;
}) {
  const verdict = useVerdict(report);
  const result = resultParts(report.summary.result);
  const forecast = report.forecast?.forecast ?? null;

  const winnerName =
    result.won === "white"
      ? report.summary.white_player
      : result.won === "black"
        ? report.summary.black_player
        : null;

  // Keep the hero lean: the why-line and hardest phases live in the Overview
  // chapter, so the stage reads in one glance.
  const finalStateLabel = verdict.finalPoint?.white_state
    ? verdict.finalPoint.white_state.replace(/_/g, " ")
    : null;

  return (
    <section className="verdict-stage animate-fade-up" aria-labelledby="verdict-headline">
      <div className="relative p-5 sm:p-7 lg:p-8">
        {/* eyebrow + game metadata */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="verdict-eyebrow">Game verdict</span>
          <span className="badge border-ink-600 bg-ink-850/70 text-mist-300">
            {report.summary.eco_code ? `${report.summary.eco_code} · ` : ""}
            {report.summary.opening_name ?? "Unknown opening"}
          </span>
          {report.summary.time_control ? (
            <span className="badge border-ink-600 bg-ink-850/70 text-mist-400">{report.summary.time_control}</span>
          ) : null}
          {report.summary.date ? (
            <span className="badge border-ink-600 bg-ink-850/70 text-mist-400">{report.summary.date}</span>
          ) : null}
        </div>

        {/* headline + headline accuracy */}
        <div className="mt-4 flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
          <div className="min-w-0 max-w-3xl">
            {/* The verdict headline is the page's primary heading. It is an <h1>
                because when the report is ready there is no other top-level
                heading on the page — a document with no h1 is a real
                accessibility defect, not a style choice. */}
            <h1 id="verdict-headline" className="verdict-headline">
              {result.won === "draw" ? (
                <>
                  A measured draw<span className="text-emerald-300">.</span>
                </>
              ) : winnerName ? (
                <>
                  {winnerName} wins<span className="text-emerald-300">.</span>
                </>
              ) : (
                <>{result.label}</>
              )}
            </h1>
            <p className="verdict-sub mt-2.5">
              {report.summary.white_player} vs {report.summary.black_player} · {result.label.toLowerCase()}
              {report.summary.moves ? ` · ${plural(report.summary.moves, "move")}` : ""}
            </p>
          </div>

          <dl className="flex items-end gap-7">
            {(
              [
                { label: "White accuracy", value: verdict.whiteAccuracy, side: "white" as const },
                { label: "Black accuracy", value: verdict.blackAccuracy, side: "black" as const },
              ] as const
            ).map((entry) => (
              <div key={entry.side} className="text-right">
                <dt className="label">{entry.label}</dt>
                <dd className="verdict-number mt-1.5">
                  {entry.value !== null ? entry.value.toFixed(1) : "—"}
                  <span className="text-xl text-mist-400">%</span>
                </dd>
                <dd className="mono mt-1 text-small text-mist-500">
                  CPL {report.accuracy.analysis[entry.side].average_centipawn_loss ?? "—"}
                </dd>
              </div>
            ))}
          </dl>
        </div>

        {/* direction cards: the story of the game in three measured reads */}
        <div className="mt-6 grid gap-3 lg:grid-cols-3">
          <div className="rounded-xl border border-ink-700/70 bg-ink-900/55 p-4">
            <div className="flex items-center justify-between gap-2">
              <h3 className="label">The decisive moment</h3>
              {verdict.biggestSwing ? (
                <button type="button" className="btn btn-ghost text-meta" onClick={() => onSelectPly(verdict.biggestSwing!.ply)}>
                  Jump to move {verdict.biggestSwing.move_number}
                </button>
              ) : null}
            </div>
            {verdict.biggestSwing ? (
              <>
                <p className="mono mt-2 text-small text-mist-300">
                  {verdict.biggestSwing.move_number}
                  {verdict.biggestSwing.side === "white" ? "." : "…"} {verdict.biggestSwing.san} ·{" "}
                  {formatEvaluation(verdict.biggestSwing.evaluation_before_cp, null)} →{" "}
                  {formatEvaluation(verdict.biggestSwing.evaluation_after_cp, null)}
                </p>
                <p className="mt-1.5 text-small leading-relaxed text-mist-300">
                  {verdict.biggestSwing.statement}
                </p>
              </>
            ) : (
              <p className="mt-2 text-small leading-relaxed text-mist-500">
                No single swing crossed the turning-point thresholds — the engine measured no
                decisive collapse in this game.
              </p>
            )}
          </div>

          <div className="rounded-xl border border-ink-700/70 bg-ink-900/55 p-4">
            <h3 className="label">Move quality, side by side</h3>
            <div className="mt-3 space-y-3">
              <div>
                <p className="mb-1.5 text-small font-semibold text-mist-200">White</p>
                <QualityBar quality={verdict.quality.white} />
              </div>
              <div>
                <p className="mb-1.5 text-small font-semibold text-mist-200">Black</p>
                <QualityBar quality={verdict.quality.black} />
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-ink-700/70 bg-ink-900/55 p-4">
            <h3 className="label">Where it ended</h3>
            <p className="mt-2 text-small leading-relaxed text-mist-300">
              {finalStateLabel ? (
                <>
                  Final engine read: <span className="font-semibold text-mist-100">{finalStateLabel} for White</span>
                  {verdict.finalPoint?.evaluation_display ? (
                    <span className="mono text-mist-400"> ({verdict.finalPoint.evaluation_display})</span>
                  ) : null}
                  .
                </>
              ) : (
                "No stored evaluation for the final position."
              )}
            </p>
            {forecast?.final ? (
              <div className="mt-3">
                <div className="forecast-bar" role="img" aria-label={`White ${(forecast.final.white * 100).toFixed(0)}%, draw ${(forecast.final.draw * 100).toFixed(0)}%, Black ${(forecast.final.black * 100).toFixed(0)}%`}>
                  <span className="forecast-white" style={{ width: `${forecast.final.white * 100}%` }} />
                  <span className="forecast-draw" style={{ width: `${forecast.final.draw * 100}%` }} />
                  <span className="forecast-black" style={{ width: `${forecast.final.black * 100}%` }} />
                </div>
                <p className="mt-1.5 flex justify-between mono text-meta text-mist-400">
                  <span>White {(forecast.final.white * 100).toFixed(0)}%</span>
                  <span>Draw {(forecast.final.draw * 100).toFixed(0)}%</span>
                  <span>Black {(forecast.final.black * 100).toFixed(0)}%</span>
                </p>
              </div>
            ) : null}
            <p className="mt-3 border-t border-ink-700/70 pt-3 text-meta text-mist-400">
              {report.material.timeline.total_captures} captures · {report.material.timeline.exchanges} exchanges
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
