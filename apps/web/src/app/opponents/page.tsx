"use client";

// Opponent Intelligence (Phase 9) — the preparation workbench.
//
// Pick an opponent, and the page answers one question from stored games only:
// what does the data say about their repertoire, recurring positions,
// tendencies and preparation opportunities? Every statement carries its sample
// size and claim level; nothing here profiles psychology or predicts a result,
// and a weak sample is shown as weak rather than smoothed into a finding.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { EmptyState, ErrorState, LoadingState } from "@/components/empty-state";
import { Disclosure, Field, Note, Panel, ProgressBar, Stat } from "@/components/ui";
import {
  api,
  ApiError,
  type OpponentPositionResponse,
  type OpponentPreparationReport,
  type OpponentProfile,
  type OpponentTendency,
  type PlayerListItem,
} from "@/lib/api";
import { plural } from "@/lib/text";

const CLAIM_TONE: Record<string, string> = {
  tendency: "border-emerald-primary/40 text-emerald-300",
  pattern: "border-sky-primary/40 text-sky-300",
  observation: "border-ink-600 text-mist-300",
  insufficient: "border-amber-primary/40 text-amber-300",
};

function ClaimBadge({ level, sample }: { level: string; sample?: number }) {
  return (
    <span
      className={`badge ${CLAIM_TONE[level] ?? "border-ink-600 text-mist-400"}`}
      title={`Evidence level: ${level}${sample !== undefined ? ` · ${sample} observations` : ""}`}
    >
      {level}
      {sample !== undefined ? <span className="ml-1">n={sample}</span> : null}
    </span>
  );
}

function CoverageBadge({ coverage }: { coverage: string }) {
  const tone =
    coverage === "robust"
      ? "border-emerald-primary/40 text-emerald-300"
      : coverage === "moderate"
        ? "border-sky-primary/40 text-sky-300"
        : coverage === "limited"
          ? "border-amber-primary/40 text-amber-300"
          : "border-ink-600 text-mist-400";
  return <span className={`badge ${tone}`}>coverage: {coverage}</span>;
}

function TendencyRow({ tendency }: { tendency: OpponentTendency }) {
  return (
    <li className="rounded-xl border border-ink-700 bg-ink-800/50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-small font-semibold text-mist-100">{tendency.label}</span>
        <ClaimBadge level={tendency.claim_level} sample={tendency.sample_size} />
        {tendency.share !== null ? (
          <span className="badge border-ink-600 text-mist-400">
            {Math.round(tendency.share * 100)}%
          </span>
        ) : null}
      </div>
      <p className="mt-1.5 text-small text-mist-300">
        {tendency.measurement}: <span className="text-mist-100">{String(tendency.value)}</span>
      </p>
      {tendency.note ? <p className="mt-1 text-small text-mist-500">{tendency.note}</p> : null}
    </li>
  );
}

function OpponentWorkspace() {
  const [players, setPlayers] = useState<PlayerListItem[]>([]);
  const [playerId, setPlayerId] = useState("");
  const [color, setColor] = useState<"white" | "black">("white");
  const [profile, setProfile] = useState<OpponentProfile | null>(null);
  const [report, setReport] = useState<OpponentPreparationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [fen, setFen] = useState("");
  const [response, setResponse] = useState<OpponentPositionResponse | null>(null);
  const [preparingId, setPreparingId] = useState("");
  const [prepResult, setPrepResult] = useState<{
    accepted: number;
    rejected: number;
    seen: number;
    note: string;
  } | null>(null);
  const [preparing, setPreparing] = useState(false);

  useEffect(() => {
    api
      .listPlayers()
      .then((payload) => {
        setPlayers(payload.players);
        const first = payload.players[0]?.id ?? "";
        setPlayerId((current) => current || first);
        // "Prepare for" defaults to a different player than the opponent.
        setPreparingId(
          (current) => current || payload.players.find((p) => p.id !== first)?.id || first
        );
      })
      .catch(() => setPlayers([]));
  }, []);

  const refresh = useCallback(async (pid: string, col: "white" | "black") => {
    if (!pid) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [profilePayload, reportPayload] = await Promise.all([
        api.getOpponentProfile(pid),
        api.getOpponentPreparationReport(pid, col),
      ]);
      setProfile(profilePayload);
      setReport(reportPayload);
      setNotice(
        profilePayload.rebuilt
          ? "Report rebuilt from the stored games on this request."
          : "Served from the stored snapshot (inputs unchanged)."
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the opponent report.");
      setProfile(null);
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Deferred: refresh sets loading state after its awaits, avoiding a
    // synchronous cascading render in the effect body.
    void Promise.resolve().then(() => refresh(playerId, color));
  }, [playerId, color, refresh]);

  async function prepareForOpponent() {
    if (!playerId || !preparingId || preparingId === playerId) return;
    setPreparing(true);
    setPrepResult(null);
    try {
      const result = await api.prepareForOpponent(playerId, { player_id: preparingId });
      setPrepResult({
        accepted: result.accepted,
        rejected: result.rejected,
        seen: result.seen,
        note: result.note,
      });
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Could not generate preparation exercises.");
    } finally {
      setPreparing(false);
    }
  }

  async function lookupPosition() {
    if (!playerId || !fen.trim()) return;
    try {
      const payload = await api.getOpponentPositionResponse(playerId, fen.trim());
      setResponse(payload);
    } catch (err) {
      setResponse(null);
      setError(err instanceof ApiError ? err.message : "Could not look up that position.");
    }
  }

  const statistics = report?.statistics ?? profile?.statistics;
  const repertoire = report?.repertoire ?? null;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="eyebrow">Players</p>
          <h1 className="title mt-1">Opponent intelligence</h1>
          <p className="subtitle mt-1 max-w-2xl">
            What the stored games say about an opponent&apos;s repertoire, recurring positions and
            tendencies — each with its sample size.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-small text-mist-400" htmlFor="opponent">
            Opponent
          </label>
          <select
            id="opponent"
            className="input"
            value={playerId}
            onChange={(event) => setPlayerId(event.target.value)}
          >
            {players.map((player) => (
              <option key={player.id} value={player.id}>
                {player.name}
              </option>
            ))}
          </select>
          <div className="flex overflow-hidden rounded-xl border border-ink-700">
            {(["white", "black"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setColor(option)}
                className={`px-3 py-2 text-small ${color === option ? "bg-ink-700 text-mist-50" : "text-mist-400 hover:text-mist-100"}`}
                title={`Prepare against the opponent as ${option === "white" ? "Black" : "White"}`}
              >
                as {option}
              </button>
            ))}
          </div>
        </div>
      </header>

      {error ? <ErrorState title="Opponent intelligence unavailable" message={error} /> : null}
      {notice ? <p className="text-small text-mist-500">{notice}</p> : null}

      {players.length === 0 ? (
        <EmptyState
          title="No players yet"
          message="Import a game first; opponent intelligence is derived from your stored games."
        />
      ) : loading ? (
        <LoadingState label="Aggregating stored games…" />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Analysed games"
              value={statistics?.analyzed_games ?? 0}
              hint={`of ${statistics?.total_games ?? 0} imported`}
              tone="accent"
            />
            <Stat label="As White" value={statistics?.as_white ?? 0} />
            <Stat label="As Black" value={statistics?.as_black ?? 0} />
            <Stat
              label="Record"
              value={`${statistics?.wins ?? 0}/${statistics?.draws ?? 0}/${statistics?.losses ?? 0}`}
              hint="win / draw / loss"
            />
          </div>

          {report ? (
            <Panel
              title="Preparation report"
              subtitle={`Methodology ${report.methodology_version} · ${report.insights.length} evidence-gated findings · ${report.evidence_count} evidence references`}
              actions={<CoverageBadge coverage={report.coverage} />}
            >
              {report.insights.length === 0 ? (
                <EmptyState
                  title="Not enough data for findings"
                  message="With too few analysed games, Caissa prints counts but does not call them patterns."
                />
              ) : (
                <ul className="space-y-3">
                  {report.insights.map((insight) => (
                    <li key={insight.key} className="rounded-xl border border-ink-700 bg-ink-800/50 p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-mist-100">{insight.title}</span>
                        <ClaimBadge level={insight.claim_level} sample={insight.sample_size} />
                        <span className="badge border-ink-600 text-mist-500">{insight.category}</span>
                      </div>
                      <p className="mt-1.5 text-small text-mist-300">{insight.statement}</p>
                      {insight.preparation_hint ? (
                        <p className="mt-1.5 text-small text-emerald-300">{insight.preparation_hint}</p>
                      ) : null}
                      {insight.evidence.length > 0 ? (
                        <Disclosure summary={`Evidence (${insight.evidence.length})`}>
                          {insight.evidence.map((ref, index) => (
                            <Note key={`${ref.game_id}-${ref.ply}-${index}`}>
                              <Link
                                href={`/game/${ref.game_id}?ply=${ref.ply ?? 0}`}
                                className="link"
                                title="Open the position behind this claim"
                              >
                                {ref.label ?? "position"} · ply {ref.ply ?? "?"}
                              </Link>
                            </Note>
                          ))}
                        </Disclosure>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              {report.limitations.length > 0 ? (
                <Disclosure summary="Limitations">
                  {report.limitations.map((limitation) => (
                    <Note key={limitation}>{limitation}</Note>
                  ))}
                </Disclosure>
              ) : null}
            </Panel>
          ) : null}

          {repertoire ? (
            <Panel
              title={`Repertoire as ${repertoire.color}`}
              subtitle={repertoire.note}
              actions={<CoverageBadge coverage={repertoire.coverage} />}
            >
              {repertoire.top_lines.length > 0 ? (
                <div className="mb-4">
                  <p className="label">Most-played lines</p>
                  <ul className="mt-1 space-y-1">
                    {repertoire.top_lines.map((line) => (
                      <li key={line} className="mono text-small text-mist-200">
                        {line}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {repertoire.nodes.length === 0 ? (
                <EmptyState
                  title="No repertoire moves"
                  message="No analysed games as this colour, so there is nothing to report yet."
                />
              ) : (
                <table className="w-full text-small">
                  <thead>
                    <tr className="text-left text-mist-500">
                      <th className="py-1 font-normal">Move</th>
                      <th className="py-1 font-normal">Move no.</th>
                      <th className="py-1 font-normal">Played</th>
                      <th className="py-1 font-normal">Share</th>
                      <th className="py-1 font-normal">W/D/L</th>
                      <th className="py-1 font-normal">Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {repertoire.nodes.slice(0, 25).map((node) => (
                      <tr key={`${node.fen}-${node.uci}`} className="border-t border-ink-800">
                        <td className="py-1.5 mono text-mist-100">{node.san}</td>
                        <td className="py-1.5 text-mist-400">{node.move_number}</td>
                        <td className="py-1.5 text-mist-200">{node.occurrences}</td>
                        <td className="py-1.5 text-mist-300">{Math.round(node.share * 100)}%</td>
                        <td className="py-1.5 text-mist-400">
                          {node.wins}/{node.draws}/{node.losses}
                        </td>
                        <td className="py-1.5">
                          <ClaimBadge level={node.claim_level} sample={node.occurrences} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Panel>
          ) : null}

          {profile ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <Panel title="Tendencies" subtitle="Measured behaviours, each with its observations count.">
                {profile.tendencies.length === 0 ? (
                  <EmptyState title="No tendencies yet" message="Not enough analysed moves to measure a behaviour." />
                ) : (
                  <ul className="space-y-2">
                    {profile.tendencies.map((tendency) => (
                      <TendencyRow key={tendency.key} tendency={tendency} />
                    ))}
                  </ul>
                )}
              </Panel>

              <Panel
                title="Performance by phase"
                subtitle={profile.phase_statistics?.sample_note ?? "No phase data."}
              >
                {profile.phase_statistics && profile.phase_statistics.phases.length > 0 ? (
                  <div className="space-y-3">
                    {profile.phase_statistics.phases.map((phase) => (
                      <div key={phase.phase}>
                        <div className="flex items-center justify-between text-small">
                          <span className="text-mist-200">{phase.phase}</span>
                          <span className="text-mist-400">
                            {plural(phase.moves, "move")} · {plural(phase.games, "game")} · {plural(phase.significant_errors, "error")}
                          </span>
                        </div>
                        <div className="mt-1">
                          <ProgressBar
                            value={Math.round((phase.error_rate ?? 0) * 100)}
                            max={100}
                          />
                        </div>
                        <div className="mt-1 flex items-center justify-between text-small text-mist-500">
                          <span>error rate {(100 * (phase.error_rate ?? 0)).toFixed(1)}%</span>
                          <span>avg {(phase.avg_centipawn_loss ?? 0).toFixed(0)}cp lost</span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState title="No phase data" message="Analyse this opponent's games to measure phases." />
                )}
              </Panel>
            </div>
          ) : null}

          <Panel
            title="Preparation training"
            subtitle="Generate exercises from this opponent's own games: the positions after the moves they demonstrably play, with the engine's stored best reply as the solution. Nothing is predicted; a move seen too few times yields no exercise."
          >
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-small text-mist-400" htmlFor="prepare-for">
                Prepare for
              </label>
              <select
                id="prepare-for"
                className="input"
                value={preparingId}
                onChange={(event) => setPreparingId(event.target.value)}
              >
                {players
                  .filter((player) => player.id !== playerId)
                  .map((player) => (
                    <option key={player.id} value={player.id}>
                      {player.name}
                    </option>
                  ))}
              </select>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void prepareForOpponent()}
                disabled={preparing || !preparingId || preparingId === playerId}
              >
                {preparing ? "Generating…" : "Generate preparation exercises"}
              </button>
            </div>
            {prepResult ? (
              <div className="mt-3 text-small">
                <p className="text-mist-200">
                  {prepResult.accepted} exercise(s) generated from {prepResult.seen} candidate(s)
                  {prepResult.rejected > 0 ? `, ${prepResult.rejected} refused` : ""}.
                </p>
                <p className="mt-1 text-mist-500">{prepResult.note}</p>
                {prepResult.accepted > 0 ? (
                  <Link
                    href={`/training?player=${encodeURIComponent(preparingId)}`}
                    className="btn mt-2"
                  >
                    Open these exercises
                  </Link>
                ) : null}
              </div>
            ) : null}
          </Panel>

          <Panel
            title="Position lookup"
            subtitle="Paste a position (FEN) and Caissa shows how this opponent has answered it — or says they never have."
          >
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="input flex-1"
                placeholder="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
                value={fen}
                onChange={(event) => setFen(event.target.value)}
                aria-label="Position FEN"
              />
              <button type="button" className="btn btn-primary" onClick={lookupPosition}>
                Look up
              </button>
            </div>
            {response ? (
              <div className="mt-4">
                <div className="flex flex-wrap items-center gap-2">
                  <ClaimBadge level={response.claim_level} sample={response.occurrences} />
                  <span className="badge border-ink-600 text-mist-400">match: {response.match}</span>
                </div>
                <p className="mt-1.5 text-small text-mist-400">{response.sample_note}</p>
                {response.responses.length > 0 ? (
                  <dl className="mt-2">
                    {response.responses.map((option) => (
                      <Field key={option.uci} term={option.san}>
                        {option.occurrences}× ({Math.round(option.share * 100)}%) ·{" "}
                        {option.wins}/{option.draws}/{option.losses}
                      </Field>
                    ))}
                  </dl>
                ) : null}
              </div>
            ) : null}
          </Panel>
        </>
      )}
    </div>
  );
}

export default function OpponentsPage() {
  return <OpponentWorkspace />;
}
