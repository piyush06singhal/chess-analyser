"use client";

// Progress (§26–§28): the measured difference between earlier and recent games.
//
// The page exists to make the causality warning impossible to miss: it renders the
// backend's own `causality_note` prominently, shows each measure's sample size,
// and marks a below-threshold measure as "insufficient" rather than as a number.
// Nothing here computes a statistic — it renders what the comparison returned.

import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/empty-state";
import { Panel, Stat } from "@/components/ui";
import { api, type PlayerListItem, type ProgressComparisonResponse } from "@/lib/api";

function formatValue(value: number | null, unit: string): string {
  if (value === null) return "—";
  if (unit === "ratio") return `${(value * 100).toFixed(1)}%`;
  if (unit === "cp") return `${value.toFixed(1)} cp`;
  return `${value.toFixed(2)} / game`;
}

export default function ProgressPage() {
  const [players, setPlayers] = useState<PlayerListItem[]>([]);
  const [playerId, setPlayerId] = useState<number | null>(null);
  const [result, setResult] = useState<ProgressComparisonResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (id: number) => {
    setLoading(true);
    setError(null);
    try {
      const body = await api.compareProgress(id);
      setResult(body);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Progress could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    api
      .listPlayers()
      .then((body) => {
        setPlayers(body.players);
        if (body.players.length) {
          const id = Number(body.players[0].id);
          setPlayerId(id);
          void load(id);
        }
      })
      .catch(() => undefined);
  }, [load]);

  return (
    <div className="space-y-5">
      <div>
        <p className="eyebrow">Analytics</p>
        <h1 className="title mt-1">Progress</h1>
        <p className="subtitle mt-1 max-w-2xl">
          How your measurable numbers compare between your earlier and more recent analysed games.
        </p>
      </div>

      {players.length ? (
        <Panel title="Player">
          <select
            className="input"
            aria-label="Player"
            value={playerId ?? ""}
            onChange={(event) => {
              const id = Number(event.target.value);
              setPlayerId(id);
              void load(id);
            }}
          >
            {players.map((player) => (
              <option key={player.id} value={player.id}>
                {player.name}
              </option>
            ))}
          </select>
        </Panel>
      ) : null}

      {loading ? <LoadingState label="Comparing stored games…" /> : null}
      {error ? <ErrorState title="Progress" message={error} /> : null}

      {result && !loading ? (
        <div className="space-y-4">
          <div className="rounded-xl border border-amber-primary/40 bg-amber-primary/10 p-3">
            <p className="label text-amber-300">Causality</p>
            <p className="mt-1 text-small leading-relaxed text-mist-200">{result.causality_note}</p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Stat
              label={result.before.label}
              value={`${result.before.analysed_games} analysed`}
              hint={`${result.before.games} game(s) in the period`}
            />
            <Stat
              label={result.after.label}
              value={`${result.after.analysed_games} analysed`}
              hint={`${result.after.games} game(s) in the period`}
            />
          </div>

          {result.status === "insufficient_data" ? (
            <EmptyState title="Not enough analysed games" message={result.reason ?? ""} />
          ) : (
            <Panel title="Measures">
              <p className="text-small text-mist-300">{result.summary}</p>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-small">
                  <thead>
                    <tr className="text-left text-mist-500">
                      <th className="py-1 pr-3">Measure</th>
                      <th className="py-1 pr-3">Earlier</th>
                      <th className="py-1 pr-3">Recent</th>
                      <th className="py-1 pr-3">Change</th>
                      <th className="py-1">Verdict</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.measures.map((measure) => (
                      <tr key={measure.key} className="border-t border-ink-800/70">
                        <td className="py-1.5 pr-3 text-mist-200">{measure.label}</td>
                        <td className="py-1.5 pr-3 mono text-mist-300">
                          {formatValue(measure.before, measure.unit)}
                        </td>
                        <td className="py-1.5 pr-3 mono text-mist-300">
                          {formatValue(measure.after, measure.unit)}
                        </td>
                        <td className="py-1.5 pr-3 mono text-mist-400">
                          {measure.delta === null ? "—" : measure.delta}
                        </td>
                        <td className="py-1.5">
                          <span className="text-mist-300">{measure.verdict}</span>
                          <span className="mono ml-2 text-meta text-mist-500">
                            n={measure.sample_before}→{measure.sample_after}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {result.limitations.length ? (
            <Panel title="What this does not account for">
              <ul className="space-y-1">
                {result.limitations.map((limitation, index) => (
                  <li key={index} className="text-small text-mist-400">
                    {limitation}
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
