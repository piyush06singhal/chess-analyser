"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type {
  GameDetail,
  GamePosition,
  GameReportPayload,
  ReportFinding,
  ReportRecommendation,
  ResultForecast,
  StoredReportResponse,
} from "@/lib/api";
import { AnalysisChessboard } from "@/components/chessboard";
import { BoardToolbar } from "@/components/board-toolbar";
import { EvalGraph } from "@/components/eval-graph";
import { CriticalTimeline } from "@/components/critical-timeline";
import { VerdictHero } from "@/components/verdict-hero";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/empty-state";
import {
  Disclosure,
  ForecastBar,
  Note,
  Panel,
  SourceBadge,
  TabPanel,
  Tabs,
} from "@/components/ui";
import { formatEvaluation } from "@/lib/eval";
import { plural } from "@/lib/text";

const SEVERITY_BADGE: Record<string, string> = {
  high: "bg-rose-400/15 text-rose-300 ring-1 ring-rose-400/30",
  medium: "bg-amber-400/15 text-amber-300 ring-1 ring-amber-400/30",
  low: "bg-ink-700 text-mist-300 ring-1 ring-ink-600",
};

// Compact source labels: the evidence model is kept, but it no longer repeats a
// full sentence under every finding. The full name lives in the tooltip.
const SOURCE_BADGE: Record<string, string> = {
  engine_fact: "engine",
  argus_derived_feature: "board",
  argus_interpretation: "Caissa",
};

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; report: GameReportPayload; source: StoredReportResponse["source"]; generatedAt: string | null }
  | { kind: "needs-analysis"; message: string }
  | { kind: "error"; message: string };

type TabId =
  | "overview"
  | "moments"
  | "play"
  | "phases"
  | "accuracy"
  | "lessons";

/** The six chapters, in reading order; numbers surface in the tab strip. */
const CHAPTERS: { id: TabId; num: string; label: string }[] = [
  { id: "overview", num: "1", label: "What happened" },
  { id: "moments", num: "2", label: "Key moments" },
  { id: "play", num: "3", label: "How they played" },
  { id: "phases", num: "4", label: "By phase" },
  { id: "accuracy", num: "5", label: "The numbers" },
  { id: "lessons", num: "6", label: "Lessons" },
];

export function GameReportView({
  game,
  positions,
}: {
  game: GameDetail;
  positions: GamePosition[];
}) {
  // A shared link carries the position, so it is read once as the initial state
  // (never synced in an effect, which would fight the user's navigation).
  const linkedPly = Number(useSearchParams().get("ply") ?? "");
  const [ply, setPlyState] = useState(Number.isFinite(linkedPly) && linkedPly > 0 ? linkedPly : 0);
  const [orientation, setOrientation] = useState<"white" | "black">("white");
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [request, setRequest] = useState({ nonce: 0, refresh: false });
  const [tab, setTab] = useState<TabId>("overview");

  const lastIndex = Math.max(positions.length - 1, 0);
  const clampedPly = Math.min(ply, lastIndex);
  const current = positions[clampedPly];

  /** Select a ply and keep the URL in step, so the address bar is shareable. */
  const setPly = useCallback((next: number | ((value: number) => number)) => {
    setPlyState((value) => {
      const resolved = typeof next === "function" ? next(value) : next;
      const url = new URL(window.location.href);
      if (resolved > 0) url.searchParams.set("ply", String(resolved));
      else url.searchParams.delete("ply");
      window.history.replaceState(null, "", url.toString());
      return resolved;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchReportState(game.id, request.refresh)
      .then((next) => {
        if (!cancelled) setState(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to build the report",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [game.id, request]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        setPly((value) => Math.min(value + 1, lastIndex));
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        setPly((value) => Math.max(value - 1, 0));
      } else if (event.key === "Home") {
        event.preventDefault();
        setPly(0);
      } else if (event.key === "End") {
        event.preventDefault();
        setPly(lastIndex);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lastIndex, setPly]);

  const report = state.kind === "ready" ? state.report : null;

  // The single biggest measured swing — the anchor the guide and the Overview
  // chapter both point at.
  const biggestSwing = useMemo(() => {
    if (!report) return null;
    const swings = report.turning_points.turning_points
      .filter((point) => point.swing_cp !== null)
      .sort((a, b) => Math.abs(b.swing_cp ?? 0) - Math.abs(a.swing_cp ?? 0));
    return swings[0] ?? null;
  }, [report]);

  // The guided path: up to three steps, chosen from what this game actually
  // contains. A new user follows it top to bottom instead of facing seven
  // undifferentiated tabs.
  const guide = useMemo(() => {
    if (!report) return [];
    const steps: { chapter: TabId; headline: string; detail: string }[] = [
      {
        chapter: "overview",
        headline: "Start with the story",
        detail: "The opening, the turning point and the phase that decided the game — in plain language.",
      },
    ];
    if (report.critical_moments.timeline.length > 0) {
      steps.push({
        chapter: "moments",
        headline: `Walk the ${report.critical_moments.timeline.length} key moments`,
        detail: "Every swing the engine measured, on the board, in order.",
      });
    }
    if (report.accuracy.analysis.white.accuracy !== null && report.accuracy.analysis.black.accuracy !== null) {
      const gap = Math.abs(report.accuracy.analysis.white.accuracy - report.accuracy.analysis.black.accuracy);
      steps.push({
        chapter: "accuracy",
        headline:
          gap >= 5
            ? `The numbers: a ${gap.toFixed(0)}-point accuracy gap`
            : "The numbers: too close to call on accuracy",
        detail: "Where each side's accuracy came from — and where it was lost.",
      });
    }
    return steps.slice(0, 3);
  }, [report]);

  const pointByPly = useMemo(() => {
    const map = new Map<number, GameReportPayload["trajectory"]["trajectory"]["points"][number]>();
    report?.trajectory.trajectory.points.forEach((point) => map.set(point.ply, point));
    return map;
  }, [report]);

  const accuracyByPly = useMemo(() => {
    const map = new Map<number, GameReportPayload["accuracy"]["analysis"]["moves"][number]>();
    report?.accuracy.analysis.moves.forEach((row) => map.set(row.ply, row));
    return map;
  }, [report]);

  const selectedPoint = pointByPly.get(clampedPly) ?? null;
  const selectedAccuracy = accuracyByPly.get(clampedPly) ?? null;

  const phaseByPly = useMemo(() => report?.phases.phase_by_ply ?? {}, [report]);

  const eventsByPhase = useMemo(() => {
    type Bucket = Record<
      string,
      { ply: number; label: string; kind: string; statement: string; severity: string; side: string }[]
    >;
    const empty: Bucket = { opening: [], middlegame: [], endgame: [] };
    if (!report) return empty;
    const buckets: Bucket = { opening: [], middlegame: [], endgame: [] };
    const push = (
      plyValue: number,
      entry: { label: string; kind: string; statement: string; severity: string; side: string }
    ) => {
      const phase = phaseByPly[String(plyValue)] ?? "middlegame";
      (buckets[phase] ?? buckets.middlegame).push({ ply: plyValue, ...entry });
    };
    report.tactical.analysis.events.forEach((event) =>
      push(event.ply, {
        label: `Tactical · ${event.type.replace(/_/g, " ")}${event.certainty === "candidate" ? " (candidate)" : ""}`,
        kind: "tactical",
        statement: event.statement,
        severity: event.severity,
        side: event.side,
      })
    );
    report.positional.analysis.events.forEach((event) =>
      push(event.ply, {
        label: `Positional · ${event.type.replace(/_/g, " ")}${event.classification === "error_candidate" ? " (error candidate)" : ""}`,
        kind: "positional",
        statement: event.statement,
        severity: event.severity,
        side: event.side,
      })
    );
    report.king_safety.analysis.events.forEach((event) =>
      push(event.ply, {
        label: `King safety · ${event.type.replace(/_/g, " ")}`,
        kind: "king_safety",
        statement: event.statement,
        severity: event.severity,
        side: event.side,
      })
    );
    for (const key of Object.keys(buckets)) buckets[key].sort((a, b) => a.ply - b.ply);
    return buckets;
  }, [report, phaseByPly]);

  const highlight = useMemo(() => {
    if (!current?.uci) return {} as Record<string, React.CSSProperties>;
    const from = current.uci.slice(0, 2);
    const to = current.uci.slice(2, 4);
    return {
      [from]: { background: "rgba(251, 191, 36, 0.32)" },
      [to]: { background: "rgba(251, 191, 36, 0.45)" },
    } as Record<string, React.CSSProperties>;
  }, [current]);

  if (positions.length === 0) {
    return <EmptyState title="No positions stored" message="This game has no generated positions to display." />;
  }

  const forecast = report?.forecast?.forecast ?? null;

  // Overview anchors, all measured.
  const worstPhaseWhite = report?.summary.phase_with_largest_drop.white ?? null;
  const worstPhaseBlack = report?.summary.phase_with_largest_drop.black ?? null;
  const flagTotal = report
    ? Object.values(report.error_categories.analysis.by_side_category.white ?? {}).reduce((a, b) => a + b, 0) +
      Object.values(report.error_categories.analysis.by_side_category.black ?? {}).reduce((a, b) => a + b, 0)
    : 0;
  const flagTopCategory = (() => {
    if (!report) return "";
    const merged: Record<string, number> = {};
    for (const bucket of [report.error_categories.analysis.by_side_category.white ?? {}, report.error_categories.analysis.by_side_category.black ?? {}]) {
      for (const [category, count] of Object.entries(bucket)) merged[category] = (merged[category] ?? 0) + count;
    }
    return Object.entries(merged).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
  })();

  // When the report is ready, the verdict stage IS the header — a second
  // player/result header above it would only repeat what the stage already
  // says. Non-ready states keep the identity header so the page is never
  // nameless.
  const actions = (
    <div className="flex flex-wrap items-center gap-2">
      {state.kind === "ready" && state.source === "generated" && state.generatedAt ? (
        <span className="mono text-small text-mist-600">
          generated {new Date(state.generatedAt).toLocaleString(undefined, {
            dateStyle: "medium",
            timeStyle: "short",
          })}
        </span>
      ) : null}
      <Link href={`/game/${game.id}`} className="btn btn-ghost btn-pill">
        Analysis board
      </Link>
      <button
        type="button"
        className="btn btn-primary btn-pill"
        onClick={() => setRequest((current) => ({ nonce: current.nonce + 1, refresh: true }))}
      >
        Rebuild report
      </button>
    </div>
  );

  return (
    <div className="space-y-5">
      {state.kind !== "ready" ? (
        <header className="animate-fade-up flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <h1 className="flex flex-wrap items-baseline gap-x-2 text-xl font-semibold tracking-tight text-mist-50">
              <span className="truncate">{game.white_player}</span>
              <span className="mono text-sm text-mist-500">{game.result}</span>
              <span className="truncate">{game.black_player}</span>
            </h1>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <span className="badge bg-emerald-500/15 text-emerald-200 ring-1 ring-emerald-400/30">
                Game report
              </span>
            </div>
          </div>
          {actions}
        </header>
      ) : (
        <div className="animate-fade-in flex justify-end">{actions}</div>
      )}

      {state.kind === "loading" ? <SkeletonRows rows={4} /> : null}

      {state.kind === "needs-analysis" ? (
        <Panel title="Engine analysis required">
          <p className="text-xs leading-relaxed text-mist-400">{state.message}</p>
          <Link href={`/game/${game.id}`} className="btn btn-primary mt-3 w-fit">
            Open the analysis board
          </Link>
        </Panel>
      ) : null}

            {state.kind === "error" ? <ErrorState title="Report unavailable" message={state.message} /> : null}

      {report ? (
        <div className="space-y-5">
          {/* The verdict comes first: outcome, headline numbers, move quality —
              all measured, before any tab. */}
          <VerdictHero report={report} onSelectPly={setPly} />

          {/* The guided path: one recommended chapter at a time, computed from
              the measured content of this game. A new user follows it top to
              bottom; nothing else on the page demands attention first. */}
          <Panel
            title="How to read this report"
            subtitle="The recommended path through this game, from what happened to why."
            className="animate-fade-in"
          >
            <ol className="grid gap-2.5 lg:grid-cols-3">
              {guide.map((step, index) => {
                const chapter = CHAPTERS.find((entry) => entry.id === step.chapter);
                if (!chapter) return null;
                return (
                  <li key={step.chapter} className="rounded-xl border border-ink-700 bg-ink-900/50 p-3.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="chapter-num" aria-hidden>
                        {index + 1}
                      </span>
                      <span className="mono text-meta text-mist-500">chapter {chapter.num}</span>
                    </div>
                    <h3 className="mt-2 text-small font-semibold text-mist-50">{step.headline}</h3>
                    <p className="mt-1 text-small leading-relaxed text-mist-400">{step.detail}</p>
                    <button
                      type="button"
                      className="btn btn-ghost mt-3 w-full"
                      onClick={() => setTab(step.chapter)}
                    >
                      {chapter.label}
                    </button>
                </li>
                );
              })}
            </ol>
          </Panel>

          <div className="grid gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
            {/* --- sticky board column: always-on context for every section --- */}
            <aside className="self-start space-y-4 lg:sticky lg:top-20">
            <div className="panel p-4">
              <AnalysisChessboard
                fen={current?.fen ?? game.initial_position}
                orientation={orientation}
                arrows={[]}
                squareStyles={highlight}
              />
              <BoardToolbar
                className="mt-3"
                fen={current?.fen ?? game.initial_position}
                orientation={orientation}
                onFlip={() =>
                  setOrientation((value) => (value === "white" ? "black" : "white"))
                }
                label={
                  clampedPly === 0
                    ? "Starting position"
                    : `Move ${current?.side_to_move === "white" ? (current?.move_number ?? 1) - 1 : current?.move_number} of ${Math.ceil(lastIndex / 2)} · ply ${clampedPly}`
                }
              />
              <div className="mt-3 flex items-center justify-center gap-1.5 border-t border-ink-700 pt-3">
                <button type="button" className="btn btn-ghost px-3" onClick={() => setPly(0)} title="First (Home)">
                  ⏮
                </button>
                <button
                  type="button"
                  className="btn btn-ghost px-3"
                  onClick={() => setPly((value) => Math.max(0, value - 1))}
                  title="Previous (←)"
                >
                  ◀
                </button>
                <button
                  type="button"
                  className="btn btn-ghost px-3"
                  onClick={() => setPly((value) => Math.min(lastIndex, value + 1))}
                  title="Next (→)"
                >
                  ▶
                </button>
                <button type="button" className="btn btn-ghost px-3" onClick={() => setPly(lastIndex)} title="Last (End)">
                  ⏭
                </button>
              </div>

              <div className="mt-3 flex items-center justify-between border-t border-ink-700 pt-3 text-xs">
                <span className="text-mist-500">Evaluation</span>
                <span className="mono text-base font-semibold text-mist-50">
                  {selectedPoint ? selectedPoint.evaluation_display : "—"}
                </span>
              </div>
              <p className="mt-0.5 text-right text-small text-mist-600">White perspective · #n = mate</p>

              {selectedAccuracy ? (
                <div className="mt-2 flex items-center justify-between gap-2 border-t border-ink-700 pt-2 text-small text-mist-500">
                  <span className="truncate">
                    {selectedAccuracy.san ?? "—"} ·{" "}
                    {selectedAccuracy.scored
                      ? `accuracy ${selectedAccuracy.accuracy?.toFixed(1)}`
                      : "no engine evaluation"}
                    {selectedAccuracy.excluded ? " (decided)" : ""}
                  </span>
                  <span className="mono shrink-0">
                    {selectedAccuracy.centipawn_loss !== null
                      ? `CPL ${selectedAccuracy.centipawn_loss}`
                      : "CPL —"}
                  </span>
                </div>
              ) : null}
            </div>
          </aside>

          {/* --- content column: graph up top, everything else in tabs --- */}
          <div className="min-w-0 space-y-4">
            <Panel
              title="Game trajectory"
              subtitle={`${report.trajectory.trajectory.evaluated_plies} plies evaluated · click the graph to jump to a position`}
              bodyClassName="px-3 pb-3 pt-1"
            >
              <EvalGraph
                points={report.trajectory.trajectory.points}
                timeline={report.critical_moments.timeline}
                selectedPly={clampedPly}
                onSelectPly={setPly}
              />
            </Panel>

            <div className="chapter-strip -mx-2 px-2 py-2">
              <Tabs
                active={tab}
                onChange={(id) => setTab(id as TabId)}
                items={[
                  { id: "overview", label: "What happened", badge: "1" },
                  { id: "moments", label: "Key moments", count: report.critical_moments.timeline.length, badge: "2" },
                  { id: "play", label: "How they played", badge: "3" },
                  { id: "phases", label: "By phase", badge: "4" },
                  { id: "accuracy", label: "The numbers", badge: "5" },
                  { id: "lessons", label: "Lessons", count: report.key_lessons.length, badge: "6" },
                ]}
              />
            </div>

            <TabPanel>
              {tab === "overview" ? (
                <div className="space-y-4">
                  {/* Overview is a story, not a data dump: three plain-language
                      statements pointing at the measured moments, then the
                      machine's own fact list for anyone who wants more. */}
                  <Panel title="The three things that decided this game">
                    {biggestSwing ? (
                      <button
                        type="button"
                        onClick={() => setPly(biggestSwing.ply)}
                        className="w-full rounded-xl border border-ink-700 bg-ink-900/50 p-4 text-left transition-colors hover:border-ink-500"
                      >
                        <p className="text-body leading-relaxed text-mist-100">
                          <span className="font-semibold">
                            {biggestSwing.move_number}
                            {biggestSwing.side === "white" ? "." : "…"} {biggestSwing.san}
                          </span>{" "}
                          was the moment the game turned: the engine measured a swing of{" "}
                          {biggestSwing.swing_cp} centipawns.
                        </p>
                        {biggestSwing.statement ? (
                          <p className="mt-1.5 text-small leading-relaxed text-mist-400">{biggestSwing.statement}</p>
                        ) : null}
                        <span className="mt-2 inline-block text-small font-semibold text-emerald-300">
                          See it on the board →
                        </span>
                      </button>
                    ) : (
                      <p className="text-body leading-relaxed text-mist-400">
                        The engine found no single decisive swing — the result came from many small
                        edges rather than one blunder.
                      </p>
                    )}
                    {(worstPhaseWhite || worstPhaseBlack) ? (
                      <div className="mt-3 grid gap-3 sm:grid-cols-2">
                        {worstPhaseWhite ? (
                          <div className="rounded-xl border border-ink-700 bg-ink-900/50 p-3.5">
                            <p className="label">White&apos;s hardest phase</p>
                            <p className="mt-1.5 text-body capitalize text-mist-100">{worstPhaseWhite}</p>
                          </div>
                        ) : null}
                        {worstPhaseBlack ? (
                          <div className="rounded-xl border border-ink-700 bg-ink-900/50 p-3.5">
                            <p className="label">Black&apos;s hardest phase</p>
                            <p className="mt-1.5 text-body capitalize text-mist-100">{worstPhaseBlack}</p>
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                    {flagTotal > 0 ? (
                      <p className="mt-3 text-small leading-relaxed text-mist-400">
                        In total the engine flagged {plural(flagTotal, "move")} for a second look
                        {flagTopCategory
                          ? `, most often for ${flagTopCategory.replace(/_/g, " ")}`
                          : ""}
                        . They are listed chapter by chapter in How they played.
                      </p>
                    ) : null}
                  </Panel>

                  <div className="grid gap-4 lg:grid-cols-2">
                    <Panel title="Identified opening">
                      <dl className="space-y-1 text-xs">
                        <Row label="Name" value={report.opening.identification.name ?? "Unknown / Unclassified"} />
                        <Row label="ECO" value={report.opening.identification.eco ?? "—"} />
                        <Row label="Source" value={report.opening.identification.source} />
                        <Row label="Book plies matched" value={String(report.opening.identification.matched_plies)} />
                        {report.opening.identification.header_eco ? (
                          <Row
                            label="PGN header ECO"
                            value={`${report.opening.identification.header_eco}${
                              report.opening.identification.header_agreement === false ? " (differs)" : ""
                            }`}
                          />
                        ) : null}
                      </dl>
                      {report.opening.identification.note ? (
                        <Disclosure summary="Method">
                          <Note>{report.opening.identification.note}</Note>
                        </Disclosure>
                      ) : null}
                    </Panel>

                    <Panel title="Deviation">
                      {report.opening.deviation.deviated ? (
                        <>
                          <dl className="space-y-1 text-xs">
                            <Row
                              label="Left the line on"
                              value={`move ${report.opening.deviation.move_number} (${report.opening.deviation.played_san})`}
                            />
                            <Row
                              label="Table expected"
                              value={report.opening.deviation.expected_continuation_san.join(" ") || "—"}
                            />
                          </dl>
                          <button
                            type="button"
                            className="btn btn-ghost mt-3"
                            onClick={() => setPly(report.opening.deviation.ply ?? 0)}
                          >
                            Go to the deviation
                          </button>
                        </>
                      ) : (
                        <p className="text-xs text-mist-400">
                          The game stayed inside the Caissa opening table; no deviation was observed.
                        </p>
                      )}
                      <Disclosure summary="Method">
                        <Note>{report.opening.deviation.note}</Note>
                      </Disclosure>
                    </Panel>
                  </div>

                  <Panel
                    title="Flagged moves by category"
                    subtitle="Where the engine-flagged moves were: tactical, positional, material, king safety or conversion."
                  >
                    <div className="grid gap-3 sm:grid-cols-2">
                      {(["white", "black"] as const).map((side) => {
                        const bucket = report.error_categories.analysis.by_side_category[side] ?? {};
                        return (
                          <div key={side} className="rounded-lg border border-ink-700 bg-ink-900/50 p-3">
                            <h4 className="label mb-2 capitalize">{side}</h4>
                            {Object.keys(bucket).length === 0 ? (
                              <p className="text-xs text-mist-500">No flagged moves.</p>
                            ) : (
                              <ul className="space-y-1 text-xs">
                                {Object.entries(bucket).map(([category, count]) => (
                                  <li key={category} className="flex items-center justify-between">
                                    <span className="capitalize text-mist-300">
                                      {category.replace(/_/g, " ")}
                                    </span>
                                    <span className="mono text-mist-200">{count}</span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        );
                      })}
                    </div>
                    <Disclosure summary="Method">
                      <Note>{report.error_categories.analysis.note}</Note>
                    </Disclosure>
                  </Panel>

                  <Panel
                    title="Material timeline"
                    subtitle={report.material.timeline.note}
                    actions={
                      <>
                        <span className="badge border-ink-600 bg-ink-800 text-mist-300">
                          final balance {report.material.timeline.final_balance > 0 ? "+" : ""}
                          {report.material.timeline.final_balance}
                        </span>
                        <span className="badge border-ink-600 bg-ink-800 text-mist-400">
                          {report.material.timeline.total_captures} captures · {report.material.timeline.exchanges} exchanges ·{" "}
                          {report.material.timeline.promotions.length} promotions
                        </span>
                      </>
                    }
                  >
                    {report.material.timeline.transitions.length === 0 ? (
                      <p className="text-small text-mist-500">No material transitions were measured.</p>
                    ) : (
                      <div className="space-y-1.5">
                        {report.material.timeline.transitions.map((event) => (
                          <button
                            key={`${event.ply}-${event.san}`}
                            type="button"
                            onClick={() => setPly(event.ply)}
                            className={`w-full rounded-lg border p-2.5 text-left text-small transition-colors ${
                              event.ply === clampedPly
                                ? "border-emerald-400/50 bg-emerald-500/10"
                                : "border-ink-700 bg-ink-900/50 hover:border-ink-500"
                            } text-mist-300`}
                          >
                            <span className="mono text-mist-500">
                              {event.move_number}
                              {event.side === "white" ? "." : "…"} {event.san}
                            </span>{" "}
                            · balance {event.balance_before} → {event.balance_after} pawns
                          </button>
                        ))}
                      </div>
                    )}
                  </Panel>

                  {/* The engine's own summary statements, unfiltered — the raw
                      layer under the story above. */}
                  <Panel title="All measured findings">
                    <FindingList findings={report.summary.facts} onSelectPly={setPly} selectedPly={clampedPly} />
                  </Panel>
                </div>
              ) : null}

              {tab === "moments" ? (
                <div className="space-y-4">
                  <CriticalTimeline
                    entries={report.critical_moments.timeline}
                    selectedPly={clampedPly}
                    onSelectPly={setPly}
                  />
                  <Panel title="Turning points" subtitle={report.turning_points.note}>
                    {report.turning_points.turning_points.length === 0 ? (
                      <p className="text-xs text-mist-500">
                        No turning points were identified from the engine measurements.
                      </p>
                    ) : (
                      <div className="space-y-2">
                        {report.turning_points.turning_points.map((point) => (
                          <button
                            key={`${point.ply}-${point.type}`}
                            type="button"
                            onClick={() => setPly(point.ply)}
                            className={`w-full rounded-lg border p-3 text-left transition-colors ${
                              point.ply === clampedPly
                                ? "border-emerald-400/50 bg-emerald-500/10"
                                : "border-ink-700 bg-ink-900/50 hover:border-ink-500"
                            }`}
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="mono text-small text-mist-400">
                                {point.move_number}
                                {point.side === "white" ? "." : "…"} {point.san}
                              </span>
                              <span className="badge border-ink-600 bg-ink-800 text-small text-mist-300">
                                {point.type.replace(/_/g, " ")}
                              </span>
                              <span className={`badge text-small ${SEVERITY_BADGE[point.severity] ?? SEVERITY_BADGE.low}`}>
                                {point.severity}
                              </span>
                              {point.certainty === "candidate" ? (
                                <span className="badge bg-amber-400/10 text-small text-amber-300">candidate</span>
                              ) : null}
                              {point.persistent === false ? (
                                <span className="badge border-ink-600 bg-ink-800 text-small text-mist-400">
                                  not persistent
                                </span>
                              ) : null}
                            </div>
                            <p className="mt-1 text-small leading-relaxed text-mist-300">{point.statement}</p>
                            <p className="mono mt-1 text-small text-mist-600">
                              {formatEvaluation(point.evaluation_before_cp, null)} →{" "}
                              {formatEvaluation(point.evaluation_after_cp, null)}
                              {point.swing_cp !== null ? ` · swing ${point.swing_cp}cp` : ""}
                            </p>
                          </button>
                        ))}
                      </div>
                    )}
                  </Panel>

                  {report.conversion.analysis.events.length > 0 ? (
                    <Panel title="Conversion" subtitle={report.conversion.analysis.note}>
                      <div className="space-y-1.5">
                        {report.conversion.analysis.events.map((event) => (
                          <button
                            key={`${event.ply}-${event.type}`}
                            type="button"
                            onClick={() => setPly(event.ply)}
                            className="w-full rounded-lg border border-ink-700 bg-ink-900/50 p-2.5 text-left text-small text-mist-300 transition-colors hover:border-ink-500"
                          >
                            <span className="badge border-ink-600 bg-ink-800 text-small text-mist-300">
                              {event.type.replace(/_/g, " ")}
                            </span>
                            <p className="mt-1 leading-relaxed">{event.statement}</p>
                          </button>
                        ))}
                      </div>
                    </Panel>
                  ) : null}
                </div>
              ) : null}

              {tab === "play" ? (
                <div className="space-y-4">
                  <Panel
                    title="Tactical analysis"
                    subtitle={report.tactical.analysis.note}
                    actions={
                      <>
                        <span className="badge bg-emerald-500/10 text-emerald-200 ring-1 ring-emerald-400/20">
                          {report.tactical.analysis.confirmed_count} confirmed
                        </span>
                        <span className="badge bg-amber-400/10 text-amber-200 ring-1 ring-amber-400/20">
                          {report.tactical.analysis.candidate_count} candidates
                        </span>
                      </>
                    }
                  >
                    <EventList
                      events={report.tactical.analysis.events.map((event) => ({
                        ply: event.ply,
                        label: `${event.type.replace(/_/g, " ")}${event.certainty === "candidate" ? " (candidate)" : ""}`,
                        kind: "tactical",
                        statement: event.statement,
                        severity: event.severity,
                        side: event.side,
                      }))}
                      onSelectPly={setPly}
                      selectedPly={clampedPly}
                    />
                  </Panel>

                  <Panel
                    title="Positional analysis"
                    subtitle={report.positional.analysis.note}
                    actions={
                      <>
                        <span className="badge border-ink-600 bg-ink-800 text-mist-300">
                          {report.positional.analysis.feature_count} features
                        </span>
                        <span className="badge bg-amber-400/10 text-amber-200 ring-1 ring-amber-400/20">
                          {report.positional.analysis.error_candidate_count} error candidates
                        </span>
                      </>
                    }
                  >
                    <EventList
                      events={report.positional.analysis.events.map((event) => ({
                        ply: event.ply,
                        label: `${event.type.replace(/_/g, " ")}${event.classification === "error_candidate" ? " (error candidate)" : ""}`,
                        kind: "positional",
                        statement: event.statement,
                        severity: event.severity,
                        side: event.side,
                      }))}
                      onSelectPly={setPly}
                      selectedPly={clampedPly}
                    />
                  </Panel>

                  <Panel title="King safety">
                    {report.king_safety.analysis.events.length === 0 ? (
                      <p className="text-xs text-mist-500">No king-safety events were measured.</p>
                    ) : (
                      <EventList
                        events={report.king_safety.analysis.events.map((event) => ({
                          ply: event.ply,
                          label: event.type.replace(/_/g, " "),
                          kind: "king_safety",
                          statement: event.statement,
                          severity: event.severity,
                          side: event.side,
                        }))}
                        onSelectPly={setPly}
                        selectedPly={clampedPly}
                      />
                    )}
                  </Panel>
                </div>
              ) : null}

              {tab === "phases" ? (
                <div className="space-y-4">
                  <Panel
                    title="Detected phases"
                    subtitle="From board-state characteristics — never from move numbers."
                    actions={
                      <span className="badge border-ink-600 bg-ink-800 text-mist-300">
                        final phase: {report.phases.final_phase}
                      </span>
                    }
                  >
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(report.phases.performance.phase_move_counts).map(([phase, count]) => (
                        <span key={phase} className="badge border-ink-600 bg-ink-800 text-mist-400">
                          {phase}: {count} plies
                        </span>
                      ))}
                    </div>
                    <div className="mt-3 grid gap-3 lg:grid-cols-2">
                      {(["white", "black"] as const).map((side) => (
                        <div key={side} className="rounded-lg border border-ink-700 bg-ink-900/40 p-3.5">
                          <h4 className="label mb-2 capitalize">{side} per phase</h4>
                          <div className="space-y-2">
                            {Object.entries(report.phases.performance[side]).map(([phase, stats]) => (
                              <div key={phase} className="rounded-lg border border-ink-700 bg-ink-900/50 p-2.5">
                                <div className="flex items-center justify-between text-xs">
                                  <span className="capitalize text-mist-200">{phase}</span>
                                  <span className="mono text-mist-400">
                                    {plural(stats.moves, "move")} · CPL{" "}
                                    {stats.average_centipawn_loss !== null
                                      ? stats.average_centipawn_loss.toFixed(1)
                                      : "—"}
                                  </span>
                                </div>
                                <div className="mono mt-1 flex flex-wrap gap-2 text-small text-mist-600">
                                  <span>{stats.problem_moves} flagged</span>
                                  <span>{stats.tactical_events} tactical</span>
                                  <span>{stats.evaluated_moves} evaluated</span>
                                </div>
                                {stats.small_sample ? (
                                  <p className="mt-1 text-small text-amber-300">{stats.note}</p>
                                ) : null}
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                    {report.phases.transitions.length > 0 ? (
                      <div className="mt-3 space-y-1.5">
                        {report.phases.transitions.map((transition) => (
                          <button
                            key={transition.ply}
                            type="button"
                            onClick={() => setPly(transition.ply)}
                            className="w-full rounded-lg border border-ink-700 bg-ink-900/50 p-2.5 text-left text-small text-mist-300 transition-colors hover:border-ink-500"
                          >
                            <span className="mono text-mist-500">ply {transition.ply}</span>{" "}
                            {transition.from_phase} → {transition.to_phase}
                            {transition.reasons.length > 0 ? (
                              <span className="mt-0.5 block text-mist-500">{transition.reasons[0]}</span>
                            ) : null}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </Panel>

                  {(["opening", "middlegame", "endgame"] as const).map((phase) => {
                    const events = eventsByPhase[phase] ?? [];
                    if (phase === "opening" || events.length > 0) {
                      return (
                        <Panel
                          key={phase}
                          title={`${phase === "opening" ? "Opening" : phase} events`}
                          subtitle={`${events.length} detected`}
                        >
                          {events.length === 0 ? (
                            <p className="text-xs text-mist-500">
                              No tactical, positional or king-safety events were detected in the {phase}.
                            </p>
                          ) : (
                            <EventList events={events} onSelectPly={setPly} selectedPly={clampedPly} />
                          )}
                        </Panel>
                      );
                    }
                    return null;
                  })}
                </div>
              ) : null}

              {tab === "accuracy" ? (
                <div className="space-y-4">
                  <Panel title="Caissa accuracy" subtitle={report.accuracy.analysis.methodology}>
                    <div className="grid gap-3 sm:grid-cols-2">
                      {[report.accuracy.analysis.white, report.accuracy.analysis.black].map((side) => (
                        <div key={side.side} className="rounded-lg border border-ink-700 bg-ink-900/40 p-4">
                          <div className="flex items-center justify-between">
                            <span className="text-sm font-medium capitalize text-mist-100">{side.side}</span>
                            <span className="mono text-lg font-semibold text-mist-50">
                              {side.accuracy !== null ? side.accuracy.toFixed(2) : "—"}
                            </span>
                          </div>
                          <dl className="mt-2 space-y-1 text-small text-mist-400">
                            <Row label="Scored moves" value={String(side.scored_moves)} />
                            <Row label="Excluded (decided)" value={String(side.excluded_decided_moves)} />
                            <Row label="Unscored moves" value={String(side.unscored_moves)} />
                            <Row
                              label="Scored in one search (exact)"
                              value={String(side.exact_scores)}
                            />
                            <Row
                              label="Needed a second search (approximate)"
                              value={String(side.approximate_scores)}
                            />
                            <Row
                              label="Average centipawn loss (raw)"
                              value={side.average_centipawn_loss !== null ? String(side.average_centipawn_loss) : "—"}
                            />
                          </dl>
                          {side.small_sample ? (
                            <p className="mt-2 text-small text-amber-300">
                              Small sample — treat this number as indicative.
                            </p>
                          ) : null}
                          {side.approximate_scores > 0 ? (
                            <p className="mt-2 text-small leading-relaxed text-mist-500">
                              {side.approximate_scores} of {side.scored_moves} scored moves fell outside
                              the multi-move window, so their comparison mixes two engine searches.
                              Those moves are counted rather than hidden.
                            </p>
                          ) : null}
                        </div>
                      ))}
                    </div>
                    <Disclosure summary="Method and disclaimer">
                      <Note>{report.accuracy.analysis.disclaimer}</Note>
                    </Disclosure>
                    <div className="mt-4">
                      <FindingList findings={report.accuracy.facts} onSelectPly={setPly} selectedPly={clampedPly} />
                    </div>
                  </Panel>

                  {report.accuracy.analysis.breakdown ? (
                    <Panel
                      title="Where accuracy was lost"
                      subtitle={report.accuracy.analysis.breakdown.note}
                    >
                      <div className="grid gap-4 lg:grid-cols-2">
                        {[
                          report.accuracy.analysis.breakdown.white,
                          report.accuracy.analysis.breakdown.black,
                        ].map((side) => (
                          <div key={side.side} className="space-y-3">
                            <p className="text-xs font-medium capitalize text-mist-100">{side.side}</p>
                            {[
                              { label: "By phase", groups: side.by_phase },
                              { label: "By error type", groups: side.by_classification },
                              { label: "By material state", groups: side.by_material_state },
                            ].map((dimension) => (
                              <div key={dimension.label} className="overflow-hidden rounded-lg border border-ink-700">
                                <p className="border-b border-ink-800 bg-ink-900/60 px-3 py-1.5 text-small uppercase tracking-wide text-mist-500">
                                  {dimension.label}
                                </p>
                                {dimension.groups.length === 0 ? (
                                  <p className="px-3 py-2 text-small text-mist-600">
                                    Nothing measured here in this game.
                                  </p>
                                ) : (
                                  <table className="w-full text-small">
                                    <tbody>
                                      {dimension.groups.map((group) => (
                                        <tr key={group.key} className="border-t border-ink-800/60 first:border-t-0">
                                          <td className="px-3 py-1.5 text-mist-300">
                                            {group.label}
                                            {group.small_sample ? (
                                              <span className="ml-1 text-amber-300" title="Fewer moves than the minimum sample size">
                                                small sample
                                              </span>
                                            ) : null}
                                          </td>
                                          <td className="mono px-2 py-1.5 text-right text-mist-200">
                                            {group.scored_moves} mv
                                          </td>
                                          <td className="mono px-2 py-1.5 text-right text-mist-200">
                                            {group.accuracy !== null ? group.accuracy.toFixed(1) : "—"}
                                          </td>
                                          <td className="mono px-3 py-1.5 text-right text-mist-400">
                                            {group.share_of_loss !== null
                                              ? `${Math.round(group.share_of_loss * 100)}% of loss`
                                              : "—"}
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                )}
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    </Panel>
                  ) : null}

                  {forecast ? <ForecastPanel forecast={forecast} onSelectPly={setPly} selectedPly={clampedPly} /> : null}
                </div>
              ) : null}

              {tab === "lessons" ? (
                <div className="space-y-4">
                  <Panel title="Key data-driven lessons">
                    <FindingList findings={report.key_lessons} onSelectPly={setPly} selectedPly={clampedPly} />
                  </Panel>

                  <Panel title="Training recommendations">
                    {report.training_recommendations.length === 0 ? (
                      <p className="text-xs leading-relaxed text-mist-500">
                        No repeated pattern was detected in this single game, so Caissa makes no
                        recommendation. A player profile requires multiple games (Phase 5).
                      </p>
                    ) : (
                      <ul className="space-y-2">
                        {report.training_recommendations.map((recommendation) => (
                          <RecommendationCard
                            key={recommendation.key}
                            recommendation={recommendation}
                            onSelectPly={setPly}
                          />
                        ))}
                      </ul>
                    )}
                  </Panel>

                  {report.unavailable.length > 0 ? (
                    <Panel title="Not available in this phase">
                      <ul className="space-y-2">
                        {report.unavailable.map((item) => (
                          <li
                            key={item.section}
                            className="rounded-lg border border-ink-700 bg-ink-900/50 p-2.5 text-small"
                          >
                            <span className="mono text-mist-400">{item.section}</span>
                            <p className="mt-0.5 leading-relaxed text-mist-500">{item.reason}</p>
                            {item.required ? (
                              <p className="mt-0.5 text-mist-600">Requires: {item.required}</p>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </Panel>
                  ) : null}

                  <Disclosure summary="Evidence policy">
                    <Note>{report.evidence_policy}</Note>
                  </Disclosure>
                </div>
              ) : null}
            </TabPanel>
          </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ForecastPanel({
  forecast,
  onSelectPly,
  selectedPly,
}: {
  forecast: ResultForecast;
  onSelectPly: (ply: number) => void;
  selectedPly: number;
}) {
  return (
    <Panel
      title="Outcome forecast"
      subtitle="A documented reading of the engine's evaluation — not a trained model."
      actions={
        <span className="badge border-ink-600 bg-ink-800 text-small text-mist-400">
          {forecast.evaluated_plies} evaluated moves
        </span>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {forecast.at_start ? (
          <div className="rounded-lg border border-ink-700 bg-ink-900/40 p-3.5">
            <p className="label">First evaluated move</p>
            <div className="mt-2">
              <ForecastBar
                white={forecast.at_start.white}
                draw={forecast.at_start.draw}
                black={forecast.at_start.black}
              />
            </div>
          </div>
        ) : null}
        {forecast.final ? (
          <div className="rounded-lg border border-ink-700 bg-ink-900/40 p-3.5">
            <p className="label">Final evaluated move</p>
            <div className="mt-2">
              <ForecastBar
                white={forecast.final.white}
                draw={forecast.final.draw}
                black={forecast.final.black}
              />
            </div>
          </div>
        ) : null}
      </div>

      <dl className="mt-3 space-y-1 text-xs">
        {forecast.peak_white ? (
          <Row
            label="White's best chance"
            value={`${(forecast.peak_white.probability * 100).toFixed(1)}% after move ${forecast.peak_white.move_number}`}
          />
        ) : null}
        {forecast.peak_black ? (
          <Row
            label="Black's best chance"
            value={`${(forecast.peak_black.probability * 100).toFixed(1)}% after move ${forecast.peak_black.move_number}`}
          />
        ) : null}
        {forecast.unforecastable_plies > 0 ? (
          <Row label="Moves with no evaluation" value={String(forecast.unforecastable_plies)} />
        ) : null}
      </dl>

      <div className="mt-4">
        <FindingList findings={forecast.facts} onSelectPly={onSelectPly} selectedPly={selectedPly} />
      </div>

      <details className="mt-3 rounded-lg border border-ink-700 bg-ink-900/40 p-3">
        <summary className="cursor-pointer text-small font-medium text-mist-300">
          How this is calculated
        </summary>
        <p className="mt-2 text-small leading-relaxed text-mist-500">{forecast.methodology}</p>
        <p className="mt-2 text-small leading-relaxed text-mist-600">{forecast.disclaimer}</p>
      </details>
    </Panel>
  );
}

// Fetches the report state without touching React state, so the effect only
// ever sets state asynchronously (no cascading renders).
async function fetchReportState(gameId: string, refresh: boolean): Promise<LoadState> {
  if (!refresh) {
    try {
      const stored = await api.getStoredReport(gameId);
      return {
        kind: "ready",
        report: stored.report,
        source: stored.source,
        generatedAt: stored.generated_at,
      };
    } catch (err) {
      // 404 (no stored report) and 409 (no analysis) both fall through to the
      // status check below, which explains exactly which one applies.
      if (!(err instanceof ApiError)) throw err;
    }
  }
  try {
    const status = await api.getReportStatus(gameId);
    if (!status.has_analysis) {
      return {
        kind: "needs-analysis",
        message:
          "This game has no stored engine analysis yet. The intelligence layer reads the engine's structured output and never runs its own search, so a report cannot be produced until the analysis runs.",
      };
    }
    const report = await api.generateReport(gameId);
    return { kind: "ready", report, source: "generated", generatedAt: report.generated_at };
  } catch (err) {
    if (err instanceof ApiError && err.code === "analysis_required") {
      return { kind: "needs-analysis", message: err.message };
    }
    throw err;
  }
}

// --- small presentational helpers ---------------------------------------------

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-mist-500">{label}</dt>
      <dd className="mono truncate text-mist-200" title={value}>
        {value}
      </dd>
    </div>
  );
}

function FindingList({
  findings,
  onSelectPly,
  selectedPly,
}: {
  // The minimum every finding-like payload provides (report findings and
  // forecast facts have slightly different extra fields).
  findings: Pick<ReportFinding, "key" | "statement" | "source" | "ply">[];
  onSelectPly: (ply: number) => void;
  selectedPly: number;
}) {
  if (findings.length === 0) {
    return <p className="text-xs text-mist-500">No findings recorded.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {findings.map((finding) => {
        const clickable = finding.ply !== null;
        const active = clickable && finding.ply === selectedPly;
        return (
          <li key={finding.key}>
            <button
              type="button"
              disabled={!clickable}
              onClick={() => (clickable ? onSelectPly(finding.ply as number) : undefined)}
              className={`flex w-full items-start gap-2.5 rounded-xl border px-3 py-2.5 text-left text-small leading-relaxed transition-colors ${
                active
                  ? "border-emerald-primary/50 bg-emerald-primary/10 text-mist-100"
                  : "border-ink-700 bg-ink-800/60 text-mist-200"
              } ${clickable ? "hover:border-ink-500" : "cursor-default"}`}
            >
              {clickable ? (
                <span className="mono mt-0.5 shrink-0 rounded-md bg-ink-750 px-1.5 py-0.5 text-micro text-mist-300">
                  ply {finding.ply}
                </span>
              ) : null}
              <span className="min-w-0 flex-1">{finding.statement}</span>
              <SourceBadge source={finding.source} label={SOURCE_BADGE[finding.source] ?? finding.source} />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function EventList({
  events,
  onSelectPly,
  selectedPly,
}: {
  events: { ply: number; label: string; kind: string; statement: string; severity: string; side: string }[];
  onSelectPly: (ply: number) => void;
  selectedPly: number;
}) {
  if (events.length === 0) {
    return <p className="text-xs text-mist-500">No events recorded for this section.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {events.map((event, index) => (
        <li key={`${event.ply}-${event.kind}-${index}`}>
          <button
            type="button"
            onClick={() => onSelectPly(event.ply)}
            className={`w-full rounded-lg border p-2.5 text-left text-small transition-colors ${
              event.ply === selectedPly
                ? "border-emerald-400/50 bg-emerald-500/10"
                : "border-ink-700 bg-ink-900/50 hover:border-ink-500"
            }`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="mono text-small text-mist-500">ply {event.ply}</span>
              <span className="capitalize text-mist-300">{event.label}</span>
              <span className={`badge text-small ${SEVERITY_BADGE[event.severity] ?? SEVERITY_BADGE.low}`}>
                {event.severity}
              </span>
              <span className="mono text-small text-mist-600">{event.side}</span>
            </div>
            <p className="mt-1 leading-relaxed text-mist-400">{event.statement}</p>
          </button>
        </li>
      ))}
    </ul>
  );
}

function RecommendationCard({
  recommendation,
  onSelectPly,
}: {
  recommendation: ReportRecommendation;
  onSelectPly: (ply: number) => void;
}) {
  return (
    <li className="rounded-lg border border-ink-700 bg-ink-900/50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium text-mist-100">{recommendation.focus}</span>
        <span className="badge border-ink-600 bg-ink-800 text-small text-mist-400">
          {recommendation.observed_count} observation(s)
        </span>
      </div>
      <p className="mt-1 text-small leading-relaxed text-mist-400">{recommendation.rationale}</p>
      {recommendation.evidence_refs.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {recommendation.evidence_refs.map((ply) => (
            <button
              key={ply}
              type="button"
              onClick={() => onSelectPly(ply)}
              className="badge border-ink-600 bg-ink-800 mono text-small text-mist-300 hover:text-mist-100"
            >
              ply {ply}
            </button>
          ))}
        </div>
      ) : null}
    </li>
  );
}
