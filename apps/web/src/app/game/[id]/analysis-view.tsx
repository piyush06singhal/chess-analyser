"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type {
  AnalysisProgress,
  CriticalMomentRow,
  GameDetail,
  GamePosition,
  MoveAnalysisRow,
} from "@/lib/api";
import { AnalysisChessboard } from "@/components/chessboard";
import { BoardToolbar } from "@/components/board-toolbar";
import { ClassificationBadge } from "@/components/analysis-panel";
import { GameInfoPanel } from "@/components/game-info-panel";
import { AnalysisStatusBadge } from "@/components/analysis-status-badge";
import { EvalBar } from "@/components/eval-bar";
import { EmptyState, ErrorState } from "@/components/empty-state";
import { Panel, ProgressBar, Tabs } from "@/components/ui";
import { formatEvaluation, pvToSan, whitePerspective } from "@/lib/eval";

type Tab = "engine" | "critical";

const SEVERITY_STYLE: Record<string, string> = {
  high: "bg-rose-400/15 text-rose-300 ring-1 ring-rose-400/30",
  medium: "bg-amber-400/15 text-amber-300 ring-1 ring-amber-400/30",
  low: "bg-ink-700 text-mist-300 ring-1 ring-ink-600",
};

export function GameAnalysisView({
  game,
  positions,
  initialPly = 0,
}: {
  game: GameDetail;
  positions: GamePosition[];
  /** Opens the board at a specific ply (evidence deep-links use this). */
  initialPly?: number;
}) {
  const [ply, setPly] = useState(initialPly);
  const [orientation, setOrientation] = useState<"white" | "black">("white");
  const [tab, setTab] = useState<Tab>("engine");
  const [moves, setMoves] = useState<MoveAnalysisRow[] | null>(null);
  const [criticals, setCriticals] = useState<CriticalMomentRow[]>([]);
  const [progress, setProgress] = useState<AnalysisProgress | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const lastIndex = Math.max(positions.length - 1, 0);
  const clampedPly = Math.min(ply, lastIndex);
  const current = positions[clampedPly];

  const byPly = useMemo(() => {
    const map = new Map<number, MoveAnalysisRow>();
    for (const move of moves ?? []) map.set(move.ply, move);
    return map;
  }, [moves]);

  const selectedRow = clampedPly > 0 ? byPly.get(clampedPly) ?? null : null;
  const hasAnalysis = (moves?.length ?? 0) > 0 || progress?.status === "completed";

  // --- data loading ----------------------------------------------------------

  const loadAnalysis = useCallback(async () => {
    try {
      const [moveData, criticalData] = await Promise.all([
        api.getAnalysisMoves(game.id),
        api.getCriticalMoments(game.id),
      ]);
      setMoves(moveData.moves);
      setCriticals(criticalData.critical_moments);
    } catch {
      setMoves([]);
    }
  }, [game.id]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getAnalysisMoves(game.id), api.getCriticalMoments(game.id)])
      .then(([moveData, criticalData]) => {
        if (cancelled) return;
        setMoves(moveData.moves);
        setCriticals(criticalData.critical_moments);
      })
      .catch(() => {
        if (!cancelled) setMoves([]);
      });
    return () => {
      cancelled = true;
    };
  }, [game.id]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const status = await api.getAnalysisProgress(game.id);
        setProgress(status);
        // The run is live while the session says "running" or the game itself
        // is still marked "analyzing" (the session row may not exist yet).
        const running = status.status === "running" || status.analysis_status === "analyzing";
        if (!running) {
          stopPolling();
          setAnalyzing(false);
          await loadAnalysis();
        }
      } catch {
        stopPolling();
        setAnalyzing(false);
      }
    }, 1200);
  }, [game.id, loadAnalysis, stopPolling]);

  useEffect(() => stopPolling, [stopPolling]);

  async function runAnalysis() {
    setAnalyzing(true);
    setAnalysisError(null);
    try {
      await api.startAnalysis(game.id, { profile: "standard" });
      startPolling();
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : "Analysis failed to start");
      setAnalyzing(false);
    }
  }

  async function cancelAnalysis() {
    try {
      await api.cancelAnalysis(game.id);
    } finally {
      setAnalyzing(false);
      stopPolling();
    }
  }

  // --- navigation ------------------------------------------------------------

  const goTo = useCallback((next: number) => setPly(Math.max(0, Math.min(next, lastIndex))), [lastIndex]);
  const step = useCallback((delta: 1 | -1) => setPly((p) => Math.max(0, Math.min(p + delta, lastIndex))), [lastIndex]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
      else if (e.key === "Home") { e.preventDefault(); goTo(0); }
      else if (e.key === "End") { e.preventDefault(); goTo(lastIndex); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, goTo, lastIndex]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-ply="${clampedPly}"]`)?.scrollIntoView({ block: "nearest" });
  }, [clampedPly]);

  // --- evaluation / engine line ----------------------------------------------

  // White-perspective evaluation of the current position (after the selected move).
  const evaluation = useMemo(() => {
    if (!selectedRow) return { cp: null, mate: null };
    return whitePerspective(selectedRow.evaluation_after_cp, selectedRow.evaluation_after_mate, selectedRow.mover);
  }, [selectedRow]);

  const highlight = useMemo(() => {
    if (!current?.uci) return {} as Record<string, React.CSSProperties>;
    const from = current.uci.slice(0, 2);
    const to = current.uci.slice(2, 4);
    return {
      [from]: { background: "rgba(251, 191, 36, 0.32)" },
      [to]: { background: "rgba(251, 191, 36, 0.45)" },
    } as Record<string, React.CSSProperties>;
  }, [current]);

  const arrows = useMemo(() => {
    if (!selectedRow?.best_move_uci || clampedPly === 0) return [];
    const uci = selectedRow.best_move_uci;
    return [{ startSquare: uci.slice(0, 2), endSquare: uci.slice(2, 4), color: "rgba(52, 211, 153, 0.85)" }];
  }, [selectedRow, clampedPly]);

  const principalVariation = useMemo(() => {
    if (!selectedRow || clampedPly === 0) return [];
    const fenBefore = positions[clampedPly - 1]?.fen;
    if (!fenBefore) return [];
    return pvToSan(fenBefore, selectedRow.principal_variation);
  }, [selectedRow, clampedPly, positions]);

  const moveRows = useMemo(() => {
    const rows: { number: number; white?: GamePosition; black?: GamePosition }[] = [];
    for (const position of positions) {
      if (position.ply === 0 || !position.san) continue;
      if (position.side_to_move === "black") {
        rows.push({ number: position.move_number, white: position });
      } else {
        if (rows.length === 0 || rows[rows.length - 1].black) rows.push({ number: position.move_number });
        rows[rows.length - 1].black = position;
      }
    }
    return rows;
  }, [positions]);

  function moveCell(position: GamePosition | undefined) {
    if (!position || !position.san) return <span className="inline-block w-20" />;
    const active = clampedPly === position.ply;
    const row = byPly.get(position.ply);
    return (
      <button
        type="button"
        data-ply={position.ply}
        onClick={() => goTo(position.ply)}
        className={`flex w-20 items-center gap-1.5 rounded-md px-1.5 py-0.5 text-left mono text-[13px] transition-colors duration-100 ${
          active
            ? "bg-emerald-primary/20 font-semibold text-emerald-300 ring-1 ring-emerald-primary/40"
            : "text-mist-200 hover:bg-ink-750 hover:text-mist-50"
        }`}
        title={row?.classification ?? undefined}
      >
        <span className="truncate">{position.san}</span>
        {row?.classification ? (
          <span className="shrink-0">
            <ClassificationBadge classification={row.classification} compact />
          </span>
        ) : null}
      </button>
    );
  }

  if (positions.length === 0) {
    return <EmptyState title="No positions stored" message="This game has no generated positions to display." />;
  }

  const totalMoves = Math.max(1, Math.ceil(lastIndex / 2));
  const playedMoveNumber =
    clampedPly === 0 ? 0 : current?.side_to_move === "white" ? (current?.move_number ?? 1) - 1 : (current?.move_number ?? 1);
  const progressPct = progress && progress.total_positions > 0 ? Math.round((progress.current_position / progress.total_positions) * 100) : 0;

  return (
    <div className="space-y-5">
      <section className="animate-fade-up flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-baseline gap-x-2 text-xl font-semibold tracking-tight text-mist-50">
            <span className="truncate">{game.white_player}</span>
            <span className="mono text-sm text-mist-500">{game.result}</span>
            <span className="truncate">{game.black_player}</span>
          </h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <AnalysisStatusBadge status={analyzing ? "analyzing" : game.analysis_status} pulse />
            {game.opening_name ? (
              <span className="badge border-ink-600 bg-ink-800 text-mist-300">
                {game.eco_code ? `${game.eco_code} · ` : ""}
                {game.opening_name}
              </span>
            ) : null}
          </div>
        </div>
        <div className="flex gap-2">
          {analyzing ? (
            <button type="button" onClick={cancelAnalysis} className="btn btn-ghost">
              Cancel
            </button>
          ) : null}
          <Link href={`/game/${game.id}/report`} className="btn btn-ghost">
            Report
          </Link>
          {/* Hands the agent the board the user is looking at, so "why is this bad?"
              resolves without pasting a FEN (the coach reads game_id and ply). */}
          <Link href={`/coach?game_id=${game.id}&ply=${clampedPly}`} className="btn btn-ghost">
            Ask Caissa
          </Link>
          {/* Game → Training: turns this game's stored mistakes into exercises.
              The training page runs the deterministic generator and reports how
              many positions qualified (and why the rest were refused). */}
          <Link href={`/training?game=${game.id}`} className="btn btn-ghost">
            Practice this game
          </Link>
          <button type="button" onClick={runAnalysis} className="btn btn-primary" disabled={analyzing}>
            {analyzing ? "Analyzing…" : hasAnalysis ? "Re-analyze" : "Run Stockfish analysis"}
          </button>
        </div>
      </section>

      {analysisError ? <ErrorState title="Analysis failed" message={analysisError} /> : null}

      {analyzing ? (
        <Panel bodyClassName="panel-body">
          <div className="flex items-center justify-between text-small text-mist-400">
            <span>Stockfish is analyzing every position…</span>
            <span className="mono">
              {progress?.current_position ?? 0} / {progress?.total_positions ?? game.moves.length}
            </span>
          </div>
          <div className="mt-2.5">
            <ProgressBar value={progressPct} />
          </div>
        </Panel>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
        {/* Board column */}
        <div className="animate-fade-up space-y-4" style={{ animationDelay: "60ms" }}>
          <div className="panel flex flex-col items-center gap-4 p-4 sm:p-6">
            <EvalBar cp={evaluation.cp} mate={evaluation.mate} />

            <AnalysisChessboard
              fen={current?.fen ?? game.initial_position}
              orientation={orientation}
              arrows={arrows}
              squareStyles={highlight}
            />

            <BoardToolbar
              className="w-full max-w-[520px]"
              fen={current?.fen ?? game.initial_position}
              orientation={orientation}
              onFlip={() => setOrientation((value) => (value === "white" ? "black" : "white"))}
              label={
                clampedPly === 0
                  ? "Starting position"
                  : `Move ${playedMoveNumber} of ${totalMoves} · ply ${clampedPly}`
              }
            />

            <div className="flex flex-wrap items-center justify-center gap-1.5 border-t border-ink-700 pt-3">
              <button type="button" className="btn btn-ghost px-3" onClick={() => goTo(0)} title="First (Home)">⏮</button>
              <button type="button" className="btn btn-ghost px-3" onClick={() => step(-1)} title="Previous (←)">◀</button>
              <button type="button" className="btn btn-ghost px-3" onClick={() => step(1)} title="Next (→)">▶</button>
              <button type="button" className="btn btn-ghost px-3" onClick={() => goTo(lastIndex)} title="Last (End)">⏭</button>
            </div>

            <div className="flex flex-wrap items-center justify-center gap-2">
              <span className="badge bg-ink-750 text-mist-200">
                {current?.side_to_move === "white" ? "White to move" : "Black to move"}
              </span>
              {current?.is_checkmate ? (
                <span className="badge bg-rose-400/20 text-rose-200 ring-1 ring-rose-400/40">checkmate</span>
              ) : current?.is_check ? (
                <span className="badge bg-rose-400/15 text-rose-300 ring-1 ring-rose-400/30">check</span>
              ) : null}
              {current?.is_stalemate ? (
                <span className="badge bg-amber-400/15 text-amber-300 ring-1 ring-amber-400/30">stalemate</span>
              ) : null}
            </div>
          </div>

          {/* Move list */}
          <div className="panel p-4">
            <div className="mb-2.5 flex items-center justify-between">
              <h3 className="label">Moves</h3>
              <span className="text-small text-mist-500">← → to navigate</span>
            </div>
            <div ref={listRef} className="max-h-72 overflow-y-auto pr-1">
              <div className="grid gap-y-px mono text-[13px]">
                {moveRows.map((row) => (
                  <div
                    key={row.number}
                    className="grid grid-cols-[2.5rem_1fr_1fr] items-center rounded-md odd:bg-ink-800/40"
                  >
                    <span className="pl-1 text-mist-500">{row.number}.</span>
                    {moveCell(row.white)}
                    {moveCell(row.black)}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Side column */}
        <div className="animate-fade-up space-y-4 self-start lg:sticky lg:top-20" style={{ animationDelay: "120ms" }}>
          <Tabs
            active={tab}
            onChange={(id) => setTab(id as Tab)}
            items={[
              { id: "engine", label: "Engine" },
              { id: "critical", label: "Critical", count: criticals.length },
            ]}
          />

          {tab === "engine" ? (
            <div className="panel space-y-3 p-4">
              {!hasAnalysis ? (
                <>
                  <h3 className="text-sm font-semibold text-mist-50">Analysis not available yet</h3>
                  <p className="text-small leading-relaxed text-mist-400">
                    Run Stockfish analysis to get evaluations, move classifications, and engine lines.
                  </p>
                </>
              ) : clampedPly === 0 ? (
                <p className="text-small text-mist-500">Select a move to see the engine evaluation for that position.</p>
              ) : selectedRow ? (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-small text-mist-500">Evaluation (White perspective)</span>
                    <span className="mono text-lg font-semibold text-mist-50">
                      {formatEvaluation(evaluation.cp, evaluation.mate)}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="mono text-small text-mist-200">
                      {selectedRow.move_number}
                      {selectedRow.mover === "white" ? "." : "…"} {selectedRow.played_move_san}
                    </span>
                    <ClassificationBadge classification={selectedRow.classification} />
                  </div>
                  <dl className="space-y-1.5">
                    <div className="flex justify-between gap-3 border-b border-ink-800/70 pb-1.5">
                      <dt className="text-small text-mist-500">Best move</dt>
                      <dd className="mono text-small text-mist-100">{selectedRow.best_move_san ?? "—"}</dd>
                    </div>
                    <div className="flex justify-between gap-3 border-b border-ink-800/70 pb-1.5">
                      <dt className="text-small text-mist-500">Centipawn loss</dt>
                      <dd className="mono text-small text-mist-100">{selectedRow.centipawn_loss ?? "—"}</dd>
                    </div>
                    <div className="flex justify-between gap-3 border-b border-ink-800/70 pb-1.5">
                      <dt className="text-small text-mist-500">Depth</dt>
                      <dd className="mono text-small text-mist-100">{selectedRow.depth}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-small text-mist-500">Phase</dt>
                      <dd className="mono text-small text-mist-100">{selectedRow.phase ?? "—"}</dd>
                    </div>
                  </dl>
                  {principalVariation.length > 0 ? (
                    <div className="rounded-lg border border-ink-700 bg-ink-900/70 p-2.5">
                      <p className="label">Engine line</p>
                      <p className="mono mt-1 text-small leading-relaxed text-mist-200">
                        {principalVariation.join(" ")}
                      </p>
                    </div>
                  ) : null}
                </div>
              ) : (
                <p className="text-small text-mist-500">Analysis not available for this move yet.</p>
              )}
            </div>
          ) : (
            <div className="panel p-4">
              <h3 className="label mb-2">Critical moments</h3>
              {criticals.length === 0 ? (
                <p className="text-small leading-relaxed text-mist-500">
                  No critical moments detected. Large swings are candidates, not verdicts.
                </p>
              ) : (
                <ul className="max-h-96 space-y-1.5 overflow-y-auto">
                  {criticals.map((moment) => (
                    <li key={`${moment.ply}-${moment.reason}`}>
                      <button
                        type="button"
                        onClick={() => goTo(moment.ply)}
                        className="w-full rounded-lg border border-ink-700 bg-ink-900/60 p-2.5 text-left transition-colors hover:border-ink-500"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="mono text-small text-mist-100">
                            {moment.move_number}
                            {moment.color === "white" ? "." : "…"} {moment.san}
                          </span>
                          <span className={`badge text-small ${SEVERITY_STYLE[moment.severity]}`}>{moment.severity}</span>
                        </div>
                        <p className="mt-1 text-small text-mist-500">
                          <span className="mono text-mist-400">{moment.reason}</span>
                          {moment.swing_cp !== null ? ` · swing ${moment.swing_cp}` : ""}
                        </p>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {hasAnalysis ? (
            <Link href={`/game/${game.id}/report`} className="panel card-hover block p-4">
              <h3 className="label">Game intelligence report</h3>
              <p className="mt-1.5 text-small leading-relaxed text-mist-400">
                Phases, turning points, material, tactics, king safety, accuracy and the
                engine-derived outcome forecast — each insight with its evidence.
              </p>
              <span className="mt-2 inline-block text-small font-semibold text-emerald-300">
                Open the report →
              </span>
            </Link>
          ) : null}

          <GameInfoPanel game={game} />
        </div>
      </div>
    </div>
  );
}
