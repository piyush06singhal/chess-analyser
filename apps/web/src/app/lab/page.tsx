"use client";

import { useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { PositionAnalysisResponse } from "@/lib/api";
import { AnalysisChessboard } from "@/components/chessboard";
import { ErrorState, LoadingState } from "@/components/empty-state";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
// A real middlegame position with the queen committed to g4: enough material and
// tension that MultiPV returns meaningfully different lines rather than a forced
// sequence. The FEN is only a starting point for the engine — Caissa claims
// nothing about it beyond what Stockfish reports.
const SAMPLE_FEN = "rnb1kbnr/ppp2ppp/3p4/4p3/2B1P1q1/5N2/PPPP1PPP/RNBQK2R w KQkq - 2 5";

function formatScore(line: PositionAnalysisResponse["lines"][number]): string {
  if (line.mate !== null && line.mate !== undefined) return `#${line.mate}`;
  if (line.cp === null || line.cp === undefined) return "—";
  const pawns = line.cp / 100;
  return `${pawns > 0 ? "+" : ""}${pawns.toFixed(2)}`;
}

export default function PositionLabPage() {
  const [fen, setFen] = useState(START_FEN);
  const [depth, setDepth] = useState(14);
  const [multipv, setMultipv] = useState(3);
  const [result, setResult] = useState<PositionAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const whiteAdvantage = useMemo(() => {
    if (!result?.lines[0]) return 0.5;
    const line = result.lines[0];
    if (line.mate !== null && line.mate !== undefined) return line.mate > 0 === (fenSplit(fen) === "w") ? 1 : 0;
    const cp = Math.max(-800, Math.min(800, line.cp ?? 0));
    return 0.5 + cp / 1600;
  }, [result, fen]);

  const bestArrow = useMemo(() => {
    const uci = result?.best_move_uci;
    if (!uci || uci.length < 4) return [];
    return [{ startSquare: uci.slice(0, 2), endSquare: uci.slice(2, 4), color: "rgba(52, 211, 153, 0.85)" }];
  }, [result]);

  async function analyze(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    if (!fen.trim()) return;
    setLoading(true);
    try {
      const analysis = await api.analyzePosition({ fen: fen.trim(), depth, multipv });
      setResult(analysis);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Analysis failed — is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-5">
      <section className="animate-fade-up">
        <p className="eyebrow">Play</p>
        <h1 className="title mt-1">Position Lab</h1>
        <p className="subtitle mt-1 max-w-2xl">
          Send any position straight to Stockfish. MultiPV lines, best moves, and
          mate distances come straight from the engine.
        </p>
      </section>

      <form onSubmit={analyze} className="card animate-fade-up space-y-4 p-5" style={{ animationDelay: "60ms" }}>
        <div>
          <label htmlFor="lab-fen" className="label">FEN</label>
          <input
            id="lab-fen"
            value={fen}
            onChange={(e) => setFen(e.target.value)}
            spellCheck={false}
            className="input mt-1.5 mono text-xs"
            placeholder="Paste a FEN position"
          />
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <label className="flex items-center gap-2 text-sm text-mist-300">
            Depth
            <input
              type="number"
              min={1}
              max={30}
              value={depth}
              onChange={(e) => setDepth(Number(e.target.value))}
              className="input w-20 px-2 py-1 text-center mono text-xs"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-mist-300">
            MultiPV
            <input
              type="number"
              min={1}
              max={5}
              value={multipv}
              onChange={(e) => setMultipv(Number(e.target.value))}
              className="input w-20 px-2 py-1 text-center mono text-xs"
            />
          </label>
          <button
            type="button"
            className="btn btn-ghost text-xs"
            onClick={() => setFen(SAMPLE_FEN)}
          >
            Load sample position
          </button>
          <button type="submit" className="btn btn-primary ml-auto" disabled={loading || !fen.trim()}>
            {loading ? "Analyzing…" : "Analyze"}
          </button>
        </div>
      </form>

      {error ? <ErrorState title="Analysis failed" message={error} /> : null}
      {loading ? <LoadingState label="Stockfish is thinking…" /> : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_420px]">
        <div className="card animate-fade-up flex flex-col items-center gap-4 p-4 sm:p-6" style={{ animationDelay: "120ms" }}>
          <AnalysisChessboard fen={fen} arrows={bestArrow} />
          {result ? (
            <p className="mono text-small text-mist-500">
              depth {result.depth} · {result.multipv} lines
              {result.is_terminal ? ` · ${result.terminal_reason}` : ""}
            </p>
          ) : null}
        </div>

        <div className="animate-fade-up space-y-4" style={{ animationDelay: "160ms" }}>
          {result ? (
            <>
              {/* Eval bar */}
              <div className="card p-4">
                <h3 className="label mb-3">Evaluation</h3>
                <div className="flex items-center gap-3">
                  <span className="mono text-small text-mist-600">BLACK</span>
                  <div className="eval-gradient relative h-3 flex-1 overflow-hidden rounded-full ring-1 ring-ink-600">
                    <div
                      className="absolute inset-y-0 left-0 bg-emerald-400/90 transition-all duration-500"
                      style={{ width: `${Math.round(whiteAdvantage * 100)}%` }}
                    />
                  </div>
                  <span className="mono text-small text-mist-600">WHITE</span>
                  <span className="w-14 text-right mono text-sm font-semibold text-mist-50">
                    {formatScore(result.lines[0] ?? { cp: null, mate: null } as PositionAnalysisResponse["lines"][number])}
                  </span>
                </div>
              </div>

              {/* Engine lines */}
              <div className="card p-4">
                <h3 className="label mb-3">Engine Lines</h3>
                <ul className="space-y-2">
                  {result.lines.map((line, index) => (
                    <li
                      key={line.index}
                      className="animate-fade-up rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2"
                      style={{ animationDelay: `${index * 60}ms` }}
                    >
                      <div className="flex items-center justify-between">
                        <span className="mono text-sm font-semibold text-mist-50">
                          {line.move_san ?? line.move_uci}
                        </span>
                        <span
                          className={`mono text-sm font-semibold ${
                            (line.mate !== null && line.mate !== undefined) || (line.cp ?? 0) >= 0
                              ? "text-mist-50"
                              : "text-mist-400"
                          }`}
                        >
                          {formatScore(line)}
                        </span>
                      </div>
                      {line.pv.length > 1 ? (
                        <p className="mt-1 truncate mono text-small text-mist-600">
                          {line.pv.slice(1, 7).join(" ")}
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            </>
          ) : (
            <div className="card p-4">
              <p className="text-xs leading-relaxed text-mist-500">
                Enter a FEN and press <span className="text-mist-300">Analyze</span>. Results appear
                here: MultiPV lines with centipawn / mate scores from Stockfish.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function fenSplit(fen: string): "w" | "b" {
  return (fen.split(" ")[1] as "w" | "b") ?? "w";
}
