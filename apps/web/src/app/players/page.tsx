"use client";

// Players — the entry point to Player Intelligence.
//
// The library answers "what games do I have"; this page answers "who have I
// played, and whose history can Caissa actually say something about". Analysis
// status is shown per player, because a player profile is built only from
// *analyzed* games — an imported-but-unanalyzed game contributes nothing, and
// saying so here is cheaper than saying it in the profile.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { PlayerListItem } from "@/lib/api";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/empty-state";
import { Panel, Stat } from "@/components/ui";

function CoverageCell({ player }: { player: PlayerListItem }) {
  const analyzed = player.analyzed_games;
  const { label, className } = (() => {
    if (analyzed >= 20)
      return { label: "Robust history", className: "border-emerald-primary/40 text-emerald-300" };
    if (analyzed >= 5)
      return { label: "Usable history", className: "border-sky-primary/40 text-sky-300" };
    if (analyzed >= 2)
      return { label: "Limited history", className: "border-amber-primary/40 text-amber-300" };
    return { label: "Insufficient", className: "border-ink-600 text-mist-500" };
  })();
  return (
    <span className={`badge ${className}`} title={`${analyzed} analyzed game(s)`}>
      {label}
    </span>
  );
}

export default function PlayersPage() {
  const [players, setPlayers] = useState<PlayerListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    api
      .listPlayers()
      .then((res) => {
        if (!cancelled) setPlayers(res.players);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 503
            ? "Game storage is not configured on the backend (set ARGUS_DATABASE_URL)."
            : err instanceof Error
              ? err.message
              : "Failed to load players"
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const all = useMemo(() => players ?? [], [players]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return all;
    return all.filter((player) => player.name.toLowerCase().includes(needle));
  }, [all, query]);

  const analyzed = all.reduce((total, player) => total + player.analyzed_games, 0);
  const profiled = all.filter((player) => player.analyzed_games >= 2).length;

  return (
    <div className="space-y-5">
      <section className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Players</p>
          <h1 className="title mt-1">Players</h1>
          <p className="subtitle mt-1">
            A profile describes a player across games, built only from that player&apos;s
            analysed games.
          </p>
        </div>
        {all.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            <Link href="/games" className="btn btn-ghost">
              Open the library
            </Link>
            <Link href="/import" className="btn btn-primary">
              + Import a game
            </Link>
          </div>
        ) : null}
      </section>

      {all.length > 0 ? (
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Players" value={all.length} icon="♟" />
          <Stat label="With a profile" value={profiled} tone="accent" icon="✓" hint="2+ analyzed games" />
          <Stat label="Analyzed games" value={analyzed} icon="⇄" />
          <Stat
            label="Showing"
            value={filtered.length}
            icon="⌕"
            hint={query.trim() ? "matching the search" : "all players"}
          />
        </section>
      ) : null}

      {error ? <ErrorState title="Could not load players" message={error} /> : null}
      {players === null && !error ? <SkeletonRows rows={4} /> : null}

      {players !== null && all.length === 0 ? (
        <EmptyState
          title="No players yet"
          message="Players appear here as you import games: each side of a game becomes a player row. Analyze those games and a profile can be built from them."
          icon="♟"
          action={
            <Link href="/import" className="btn btn-primary">
              Import a game
            </Link>
          }
        />
      ) : null}

      {all.length > 0 ? (
        <Panel
          title="Profiles"
          subtitle="Coverage describes the sample, not the player: a profile needs 2 analyzed games, repeated patterns need 20."
          bodyClassName="panel-body"
        >
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <input
              className="input max-w-xs"
              placeholder="Search players…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              spellCheck={false}
              aria-label="Search players"
            />
            {query.trim() ? (
              <button type="button" className="btn btn-ghost" onClick={() => setQuery("")}>
                Clear
              </button>
            ) : null}
          </div>

          <div className="overflow-x-auto">
            <table className="data-table min-w-[720px]">
              <thead>
                <tr>
                  <th scope="col">Player</th>
                  <th scope="col">Games</th>
                  <th scope="col">Analyzed</th>
                  <th scope="col">Record</th>
                  <th scope="col">Coverage</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {filtered.map((player) => (
                  <tr key={player.id}>
                    <td>
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-mist-100">{player.name}</span>
                        {player.title ? (
                          <span className="badge border-violet-primary/40 text-violet-300">
                            {player.title}
                          </span>
                        ) : null}
                      </div>
                      {player.platform ? (
                        <p className="text-meta text-mist-500">
                          {player.platform}
                          {player.platform_username ? ` · ${player.platform_username}` : ""}
                        </p>
                      ) : null}
                    </td>
                    <td className="mono">{player.games}</td>
                    <td className="mono">{player.analyzed_games}</td>
                    <td className="mono text-mist-300">
                      {player.wins}W · {player.draws}D · {player.losses}L
                    </td>
                    <td>
                      <CoverageCell player={player} />
                    </td>
                    <td className="text-right">
                      <Link href={`/players/${player.id}`} className="btn btn-ghost">
                        Profile
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>


          {filtered.length === 0 ? (
            <p className="mt-3 text-small text-mist-500">
              No player matches “{query.trim()}”.
            </p>
          ) : null}
        </Panel>
      ) : null}
    </div>
  );
}
