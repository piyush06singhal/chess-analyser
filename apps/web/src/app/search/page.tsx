"use client";

// Unified search (§30): one box across games, players, training, scenarios,
// insights, openings and collections.
//
// The page renders the backend's ranking verbatim — every hit carries the terms
// that matched and a sentence explaining its rank. Crucially, it distinguishes
// "you have nothing stored in these categories" from "nothing here matches your
// query": those are different products and the page never collapses them into a
// bare "no results".

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { EmptyState, ErrorState, LoadingState } from "@/components/empty-state";
import { Panel } from "@/components/ui";
import { api, type PlayerListItem, type UnifiedSearchResponse } from "@/lib/api";

const KIND_LABEL: Record<string, string> = {
  game: "Game",
  player: "Player",
  opponent: "Opponent",
  opening: "Opening",
  training: "Exercise",
  scenario: "Scenario",
  insight: "Insight",
  collection: "Collection",
};

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<UnifiedSearchResponse | null>(null);
  const [players, setPlayers] = useState<PlayerListItem[]>([]);
  const [playerId, setPlayerId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listPlayers()
      .then((body) => {
        setPlayers(body.players);
        if (body.players.length) setPlayerId(Number(body.players[0].id));
      })
      .catch(() => undefined);
  }, []);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await api.unifiedSearch({ q: query, player_id: playerId ?? undefined });
      setResult(body);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed.");
    } finally {
      setLoading(false);
    }
  }, [query, playerId]);

  return (
    <div className="space-y-5">
      <div>
        <p className="eyebrow">Analytics</p>
        <h1 className="title mt-1">Search</h1>
        <p className="subtitle mt-1 max-w-2xl">
          One query across everything Caissa stores about your chess. Results are ranked and each
          one names the terms that matched.
        </p>
      </div>

      <Panel title="Query">
        <div className="flex flex-wrap items-end gap-2">
          <label className="min-w-[12rem] flex-1">
            <span className="label">Search</span>
            <input
              className="input mt-1 w-full"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void run();
              }}
              placeholder="opening, player, move, exercise…"
            />
          </label>
          {players.length ? (
            <label>
              <span className="label">Player scope</span>
              <select
                className="input mt-1"
                value={playerId ?? ""}
                onChange={(event) => setPlayerId(Number(event.target.value))}
              >
                {players.map((player) => (
                  <option key={player.id} value={player.id}>
                    {player.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <button type="button" className="btn btn-primary" onClick={() => void run()}>
            Search
          </button>
        </div>
      </Panel>

      {loading ? <LoadingState label="Searching stored Caissa data…" /> : null}
      {error ? <ErrorState title="Search failed" message={error} /> : null}

      {result && !loading ? (
        result.status === "ok" ? (
          <div className="space-y-2">
            <p className="mono text-meta text-mist-500">
              {result.hits.length} hit(s) from {result.total_candidates} stored item(s)
            </p>
            <ul className="space-y-2">
              {result.hits.map((hit) => (
                <li key={`${hit.kind}-${hit.id}`} className="card p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <span className="badge border-ink-600 text-mist-400">
                        {KIND_LABEL[hit.kind] ?? hit.kind}
                      </span>
                      <h3 className="mt-1 text-small font-semibold text-mist-100">
                        {hit.href ? (
                          <Link className="hover:underline" href={hit.href}>
                            {hit.title}
                          </Link>
                        ) : (
                          hit.title
                        )}
                      </h3>
                      {hit.subtitle ? (
                        <p className="text-small text-mist-400">{hit.subtitle}</p>
                      ) : null}
                      <p className="mono mt-1 text-meta text-mist-500">{hit.explanation}</p>
                    </div>
                    <span className="mono shrink-0 text-meta text-mist-500">{hit.score}</span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <EmptyState
            title={
              result.status === "empty_query"
                ? "Enter a search term"
                : result.status === "no_candidates"
                  ? "Nothing stored yet"
                  : "No matches"
            }
            message={result.reason ?? ""}
          />
        )
      ) : null}
    </div>
  );
}
