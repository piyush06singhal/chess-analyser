"use client";

// What-If Lab (Phase 10) — decision intelligence in the interface.
//
// Three questions, all answered from real engine output:
//   - which of these candidate moves is better, and by how much?
//   - what if I had played this instead of what I played?
//   - where did this game turn, and what were the alternatives?
//
// Two things are deliberately visible in the UI rather than buried:
//   * a *refusal* is rendered as an answer ("that move is not legal in this
//     position"), never as an empty panel or a spinner that never resolves;
//   * a score that came from a separate search of a resulting position is
//     labelled as such, because it is not comparable to a same-search score.

import { useCallback, useEffect, useState } from "react";

import { AnalysisChessboard } from "@/components/chessboard";
import { EmptyState, ErrorState, LoadingState } from "@/components/empty-state";
import { Note, Panel, Stat } from "@/components/ui";
import {
  api,
  ApiError,
  type PlayerListItem,
  type ScenarioBranch,
  type ScenarioCandidateComparison,
  type ScenarioExplorer,
  type ScenarioOutcome,
  type ScenarioWhyNot,
} from "@/lib/api";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

const QUALITY_TONE: Record<string, string> = {
  best: "border-emerald-primary/40 text-emerald-300",
  near_best: "border-emerald-primary/30 text-emerald-200",
  playable: "border-sky-primary/40 text-sky-300",
  inaccurate: "border-amber-primary/40 text-amber-300",
  mistake: "border-orange-500/40 text-orange-300",
  blunder: "border-rose-500/40 text-rose-300",
  illegal: "border-ink-600 text-mist-400",
  unknown: "border-ink-600 text-mist-400",
};

function score(cp?: number | null, mate?: number | null): string {
  if (mate !== null && mate !== undefined) return `#${mate > 0 ? "+" : "-"}${Math.abs(mate)}`;
  if (cp === null || cp === undefined) return "—";
  return `${cp > 0 ? "+" : ""}${(cp / 100).toFixed(2)}`;
}

function pawns(cp?: number | null): string {
  if (cp === null || cp === undefined) return "—";
  return `${cp >= 0 ? "+" : ""}${(cp / 100).toFixed(2)}`;
}

/** A refusal the API returned as a status rather than as an error. */
function RefusalNotice({ status, message }: { status: string; message?: string | null }) {
  if (status === "ok" || !status) return null;
  const tone =
    status === "illegal_move"
      ? "border-amber-primary/40 bg-amber-primary/5"
      : "border-ink-600 bg-ink-800/40";
  return (
    <div className={`rounded-xl border p-3 text-sm ${tone}`}>
      <span className="font-semibold text-mist-100">{status.replace(/_/g, " ")}</span>
      {message ? <p className="mt-1 text-mist-300">{message}</p> : null}
      <p className="mt-1 text-xs text-mist-400">
        This is a result, not a failure: Caissa does not score a move it cannot play.
      </p>
    </div>
  );
}

function Continuation({
  title,
  plies,
  move,
}: {
  title: string;
  plies: ScenarioBranch["alternative_continuation"];
  move?: string | null;
}) {
  return (
    <div className="rounded-xl border border-ink-700 bg-ink-800/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="label">{title}</span>
        {move ? <span className="mono text-sm text-mist-100">{move}</span> : null}
      </div>
      {plies.length === 0 ? (
        <p className="mt-2 text-sm text-mist-400">No line was produced for this move.</p>
      ) : (
        <ol className="mt-2 space-y-1">
          {plies.slice(0, 8).map((ply) => (
            <li key={`${ply.ply}-${ply.uci}`} className="flex items-center justify-between text-sm">
              <span className="mono text-mist-200">
                {ply.move_number}
                {ply.color === "black" ? "…" : "."} {ply.san ?? ply.uci}
              </span>
              <span className="tabular-nums text-mist-400">{score(ply.cp, ply.mate)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export default function ScenariosPage() {
  const [fen, setFen] = useState(START_FEN);
  const [movesText, setMovesText] = useState("e4, d4, Nf3, c4");
  const [comparison, setComparison] = useState<ScenarioCandidateComparison | null>(null);
  const [outcome, setOutcome] = useState<ScenarioOutcome | null>(null);
  const [whyNot, setWhyNot] = useState<ScenarioWhyNot | null>(null);
  const [explorer, setExplorer] = useState<ScenarioExplorer | null>(null);
  const [gameId, setGameId] = useState("");
  const [ply, setPly] = useState<number | null>(null);
  const [players, setPlayers] = useState<PlayerListItem[]>([]);
  const [playerIndex, setPlayerIndex] = useState(0);
  const [practiseNote, setPractiseNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [engineAvailable, setEngineAvailable] = useState<boolean | null>(null);

  useEffect(() => {
    void api
      .getScenarioMeta()
      .then((meta) => {
        const engine = meta["engine"] as { available?: boolean } | undefined;
        setEngineAvailable(Boolean(engine?.available));
      })
      .catch(() => setEngineAvailable(null));
    void api
      .listPlayers()
      .then((payload) => setPlayers(payload.players))
      .catch(() => setPlayers([]));
  }, []);

  const player = players[playerIndex];

  const run = useCallback(async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "The request failed.");
    } finally {
      setBusy(null);
    }
  }, []);

  // A position override lets a handler branch from a ply it just selected
  // without waiting a render for that selection to reach component state —
  // otherwise the counterfactual would be computed against the previous
  // position (or the FEN box) rather than the turn the user clicked.
  const positionBody = (at?: { game_id?: string; ply?: number }) => {
    const id = at?.game_id ?? gameId;
    const move = at?.ply ?? ply;
    return id && move ? { game_id: id, ply: move } : { fen };
  };

  const compare = () =>
    run("compare", async () => {
      setOutcome(null);
      setWhyNot(null);
      const moves = movesText
        .split(/[,\s]+/)
        .map((entry) => entry.trim())
        .filter(Boolean);
      const result = await api.compareCandidateMoves({
        ...positionBody(),
        moves,
        include_top: 3,
      });
      setComparison(result);
      setFen(result.fen);
    });

  const explore = () =>
    run("explore", async () => {
      if (!gameId) {
        setError("Enter a game id to explore its turning points.");
        return;
      }
      setExplorer(await api.getTurningPoints(gameId, 12));
    });

  const whatIf = (move: string, at?: { game_id?: string; ply?: number }) =>
    run("what-if", async () => {
      const result = await api.counterfactual({
        ...positionBody(at),
        alternative_move: move,
        plies_ahead: 6,
      });
      setOutcome(result);
      if (result.branch?.source_fen) setFen(result.branch.source_fen);
    });

  const explainWhyNot = (move: string) =>
    run("why-not", async () => {
      setWhyNot(await api.whyNotThisMove({ ...positionBody(), move }));
    });

  const practise = (move: string) =>
    run("practise", async () => {
      if (!player) {
        setError("Import a game first so a player exists to own the exercise.");
        return;
      }
      const result = await api.createTrainingFromScenario({
        ...positionBody(),
        alternative_move: move,
        player_id: Number(player.id),
      });
      setPractiseNote(
        result.status === "ok"
          ? `Stored exercise #${result.position_id} · ${result.position?.category} · ${result.position?.difficulty}. Its solution is the engine's own move here.`
          : `${result.status.replace(/_/g, " ")}: ${result.message ?? ""}`
      );
    });

  const boardFen = comparison?.fen ?? outcome?.branch?.source_fen ?? fen;

  return (
    <div className="space-y-5">
      <header className="space-y-2">
        <p className="eyebrow">Coach</p>
        <h1 className="title mt-1">What-If Lab</h1>
        <p className="subtitle mt-1 max-w-3xl">
          Compare candidate moves and branch a game into alternative futures — every evaluation
          comes from Stockfish.
        </p>
        {engineAvailable === false ? (
          <Note>
            No engine is available in this deployment, so counterfactual analysis is switched off.
            Caissa will not estimate a line.
          </Note>
        ) : null}
      </header>

      {error ? <ErrorState title="That did not work" message={error} /> : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <Panel title="Position" subtitle="A FEN, or a ply of a stored game">
          <div className="space-y-4">
            <div>
              <label className="label" htmlFor="fen">
                FEN
              </label>
              <textarea
                id="fen"
                className="input mt-1 mono text-xs"
                rows={2}
                value={fen}
                onChange={(event) => setFen(event.target.value)}
                spellCheck={false}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="game-id">
                  Game id (optional)
                </label>
                <input
                  id="game-id"
                  className="input mt-1 mono text-xs"
                  value={gameId}
                  placeholder="paste a game id"
                  onChange={(event) => setGameId(event.target.value.trim())}
                />
              </div>
              <div>
                <label className="label" htmlFor="ply">
                  Ply (optional)
                </label>
                <input
                  id="ply"
                  className="input mt-1"
                  type="number"
                  min={1}
                  value={ply ?? ""}
                  placeholder="e.g. 18"
                  onChange={(event) => setPly(event.target.value ? Number(event.target.value) : null)}
                />
              </div>
            </div>
            <div>
              <label className="label" htmlFor="moves">
                Candidate moves
              </label>
              <input
                id="moves"
                className="input mt-1 mono text-sm"
                value={movesText}
                onChange={(event) => setMovesText(event.target.value)}
              />
              <p className="mt-1 text-xs text-mist-500">SAN or UCI, separated by commas.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary" onClick={compare} disabled={busy !== null}>
                {busy === "compare" ? "Analysing…" : "Compare moves"}
              </button>
              <button className="btn" onClick={explore} disabled={busy !== null || !gameId}>
                {busy === "explore" ? "Reading…" : "Explore turning points"}
              </button>
            </div>
            {players.length > 0 ? (
              <div>
                <label className="label" htmlFor="player">
                  Practise as
                </label>
                <select
                  id="player"
                  className="input mt-1"
                  value={playerIndex}
                  onChange={(event) => setPlayerIndex(Number(event.target.value))}
                >
                  {players.map((entry, index) => (
                    <option key={entry.id} value={index}>
                      {entry.name}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
            {comparison?.truncated ? (
              <Note>The comparison was truncated to respect the MultiPV ceiling.</Note>
            ) : null}
            {comparison?.notes.map((note) => <Note key={note}>{note}</Note>)}
          </div>
        </Panel>

        <Panel
          title="Board"
          subtitle={boardFen.split(" ")[1] === "b" ? "Black to move" : "White to move"}
        >
          <AnalysisChessboard fen={boardFen} />
        </Panel>
      </div>

      {comparison ? (
        <Panel
          title="Candidate moves"
          subtitle={`depth ${comparison.engine_config.depth ?? "—"} · MultiPV ${comparison.engine_config.multipv} · one search`}
        >
          <div className="mb-3 flex flex-wrap gap-4">
            <Stat label="Phase" value={comparison.phase} />
            <Stat label="Engine best" value={comparison.best_move_uci ?? "—"} />
            <Stat label="Compared" value={String(comparison.candidates.length)} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left">
              <thead>
                <tr className="text-xs uppercase tracking-wide text-mist-400">
                  <th className="px-3 py-2">Move</th>
                  <th className="px-3 py-2">Eval</th>
                  <th className="px-3 py-2">CPL</th>
                  <th className="px-3 py-2">Quality</th>
                  <th className="px-3 py-2">Eval source</th>
                  <th className="px-3 py-2">Resulting position</th>
                </tr>
              </thead>
              <tbody>
                {comparison.candidates.map((candidate) => (
                  <tr
                    key={candidate.uci}
                    className="cursor-pointer border-t border-ink-700 hover:bg-ink-800/50"
                    onClick={() => {
                      if (!candidate.legal) {
                        setOutcome({
                          status: "illegal_move",
                          message:
                            candidate.legality_note ?? "That move is not legal in this position.",
                        });
                        return;
                      }
                      void whatIf(candidate.uci);
                    }}
                  >
                    <td className="px-3 py-2 mono text-sm text-mist-50">
                      {candidate.san ?? candidate.uci}
                      {candidate.is_played_move ? (
                        <span className="ml-2 text-[11px] text-mist-400">played</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 text-sm tabular-nums text-mist-100">
                      {score(candidate.cp, candidate.mate)}
                    </td>
                    <td className="px-3 py-2 text-sm tabular-nums text-mist-300">
                      {candidate.centipawn_loss ?? "—"}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`badge ${QUALITY_TONE[candidate.quality] ?? "border-ink-600 text-mist-400"}`}
                      >
                        {candidate.quality.replace(/_/g, " ")}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-mist-400">
                      {candidate.eval_source.replace(/_/g, " ")}
                    </td>
                    <td className="px-3 py-2 text-xs text-mist-300">
                      {candidate.position_type ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-mist-400">
            Click a move to build the counterfactual. A row marked{" "}
            <span className="mono">resulting position</span> was scored by a separate search,
            so a small gap against a same-search score is not meaningful.
          </p>
        </Panel>
      ) : null}

      {outcome ? (
        <Panel
          title="Counterfactual"
          subtitle={
            outcome.source?.game_id
              ? `game ${outcome.source.game_id} · ply ${outcome.source.ply}`
              : "the position you entered"
          }
        >
          <RefusalNotice status={outcome.status} message={outcome.message} />
          {outcome.branch ? (
            <div className="space-y-4">
              <div className="flex flex-wrap gap-4">
                <Stat
                  label="Eval change"
                  value={pawns(outcome.branch.evaluation_change_cp)}
                  hint="alternative minus played, mover's perspective"
                />
                <Stat label="Engine depth" value={String(outcome.branch.engine_config.depth ?? "—")} />
                <Stat label="Plies" value={String(outcome.branch.alternative_continuation.length)} />
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <Continuation
                  title="What was played"
                  plies={outcome.branch.actual_continuation}
                  move={outcome.branch.actual_move_san}
                />
                <Continuation
                  title="The alternative"
                  plies={outcome.branch.alternative_continuation}
                  move={outcome.branch.alternative_move_san}
                />
              </div>
              {outcome.branch.notes.map((note) => (
                <Note key={note}>{note}</Note>
              ))}
              {outcome.explanation && outcome.explanation.facts.length > 0 ? (
                <div>
                  <h3 className="label">What the engine measured</h3>
                  <ul className="mt-2 space-y-1 text-sm text-mist-300">
                    {outcome.explanation.facts.slice(0, 10).map((fact) => (
                      <li key={fact} className="flex gap-2">
                        <span className="text-emerald-primary">·</span>
                        <span>{fact}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {outcome.branch.comparison?.structural_differences?.length ? (
                <div>
                  <h3 className="label">Board differences</h3>
                  <ul className="mt-2 space-y-1 text-sm text-mist-300">
                    {outcome.branch.comparison.structural_differences
                      .slice(0, 6)
                      .map((difference) => (
                        <li key={`${difference.domain}-${difference.feature}`}>
                          <span className="mono text-xs text-mist-400">{difference.domain}</span>{" "}
                          {difference.feature}: {String(difference.value_a)} →{" "}
                          {String(difference.value_b)}
                        </li>
                      ))}
                  </ul>
                  {outcome.branch.comparison.notes.map((note) => (
                    <Note key={note}>{note}</Note>
                  ))}
                </div>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <button
                  className="btn btn-primary"
                  disabled={busy !== null}
                  onClick={() => practise(outcome.branch!.alternative_move_uci)}
                >
                  {busy === "practise" ? "Storing…" : "Practise this move"}
                </button>
                <button
                  className="btn"
                  disabled={busy !== null || !outcome.branch.actual_move_uci}
                  onClick={() => explainWhyNot(outcome.branch!.actual_move_uci!)}
                >
                  Why was the played move worse?
                </button>
              </div>
              {practiseNote ? <Note>{practiseNote}</Note> : null}
            </div>
          ) : null}
        </Panel>
      ) : null}

      {whyNot ? (
        <Panel
          title="Why not this move?"
          subtitle={whyNot.move ? `${whyNot.move.san ?? whyNot.move.uci} · ${whyNot.quality}` : ""}
        >
          <RefusalNotice status={whyNot.status} message={whyNot.message} />
          <div className="mt-3 grid gap-4 sm:grid-cols-3">
            <Stat label="Move eval" value={score(whyNot.move?.cp, whyNot.move?.mate)} />
            <Stat label="Centipawn loss" value={String(whyNot.move?.centipawn_loss ?? "—")} />
            <Stat label="Strongest reply" value={whyNot.best_response ?? "—"} />
          </div>
          {whyNot.critical_issue ? (
            <p className="mt-3 text-sm text-mist-200">
              <span className="font-semibold">Main issue: </span>
              {whyNot.critical_issue}
            </p>
          ) : null}
          {whyNot.better_alternatives.length > 0 ? (
            <div className="mt-3">
              <h3 className="label">Better alternatives the engine found</h3>
              <ul className="mt-1 space-y-1 text-sm text-mist-300">
                {whyNot.better_alternatives.map((alternative) => (
                  <li key={alternative.uci} className="flex items-center justify-between">
                    <span className="mono">{alternative.san ?? alternative.uci}</span>
                    <span className="tabular-nums">{score(alternative.cp, alternative.mate)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {whyNot.explanation?.facts.length > 0 ? (
            <ul className="mt-3 space-y-1 text-sm text-mist-300">
              {whyNot.explanation.facts.slice(0, 8).map((fact) => (
                <li key={fact} className="flex gap-2">
                  <span className="text-emerald-primary">·</span>
                  <span>{fact}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      ) : null}

      {explorer ? (
        <Panel
          title="Turning points"
          subtitle={`${explorer.turning_points.length} moments · ${explorer.plies_analyzed} plies analysed · read from stored analysis, no new search`}
        >
          {explorer.turning_points.length === 0 ? (
            <EmptyState
              title="Nothing to explore yet"
              message="This game has no stored analysis, so there are no moments to branch from."
            />
          ) : (
            <ul className="space-y-3">
              {explorer.turning_points.map((moment) => (
                <li key={moment.ply} className="rounded-xl border border-ink-700 bg-ink-800/40 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="mono text-sm text-mist-100">
                      {moment.move_number}
                      {moment.color === "black" ? "…" : "."} {moment.san}
                    </span>
                    <span className="badge border-ink-600 text-mist-300">
                      {moment.classification ?? "unclassified"}
                    </span>
                    {moment.is_critical ? (
                      <span className="badge border-amber-primary/40 text-amber-300">
                        {moment.severity ?? "critical"}
                      </span>
                    ) : null}
                    <span className="text-xs text-mist-400">swing {moment.swing_cp ?? "—"} cp</span>
                  </div>
                  {moment.critical_reason ? (
                    <p className="mt-1 text-sm text-mist-300">{moment.critical_reason}</p>
                  ) : null}
                  {moment.alternatives.length > 0 ? (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {moment.alternatives
                        .filter((alternative) => !alternative.is_played_move)
                        .map((alternative) => (
                          <button
                            key={alternative.uci}
                            className="btn btn-ghost text-xs"
                            disabled={busy !== null}
                            onClick={() => {
                              setGameId(explorer.game_id);
                              setPly(moment.ply);
                              void whatIf(alternative.uci, {
                                game_id: explorer.game_id,
                                ply: moment.ply,
                              });
                            }}
                          >
                            what if {alternative.san ?? alternative.uci}?{" "}
                            <span className="tabular-nums text-mist-400">
                              {score(alternative.cp, alternative.mate)}
                            </span>
                          </button>
                        ))}
                    </div>
                  ) : (
                    <p className="mt-2 text-xs text-mist-400">
                      No stored alternatives for this ply, so no branch is offered.
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
          {explorer.notes.map((note) => (
            <Note key={note}>{note}</Note>
          ))}
        </Panel>
      ) : null}

      {busy ? <LoadingState label={`Working: ${busy}…`} /> : null}
    </div>
  );
}
