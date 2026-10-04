"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { GameListItem } from "@/lib/api";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/empty-state";
import { GameTable } from "@/components/game-table";
import { Chip, Panel, Stat } from "@/components/ui";

type StatusFilter = "all" | "analyzed" | "pending";
type SourceFilter = "all" | "chess_com" | "lichess" | "pgn";

export default function MyGamesPage() {
  const [games, setGames] = useState<GameListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [source, setSource] = useState<SourceFilter>("all");
  const [query, setQuery] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listGames()
      .then((res) => {
        if (!cancelled) setGames(res.games);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 503) {
          setError("Game storage is not configured on the backend (set ARGUS_DATABASE_URL).");
        } else {
          setError(err instanceof Error ? err.message : "Failed to load games");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function remove(id: string) {
    setDeleting(id);
    try {
      await api.deleteGame(id);
      setGames((current) => (current ? current.filter((game) => game.id !== id) : current));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete game");
    } finally {
      setDeleting(null);
      setConfirming(null);
    }
  }

  const all = useMemo(() => games ?? [], [games]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return all.filter((game) => {
      if (status === "analyzed" && game.analysis_status !== "analyzed") return false;
      if (status === "pending" && game.analysis_status === "analyzed") return false;
      if (source === "chess_com" && game.source !== "chess_com") return false;
      if (source === "lichess" && game.source !== "lichess") return false;
      if (source === "pgn" && game.source !== "pgn_text" && game.source !== "pgn_file") return false;
      if (!needle) return true;
      return (
        game.white_player.toLowerCase().includes(needle) ||
        game.black_player.toLowerCase().includes(needle) ||
        (game.opening_name ?? "").toLowerCase().includes(needle) ||
        (game.eco_code ?? "").toLowerCase().includes(needle)
      );
    });
  }, [all, status, source, query]);

  return (
    <div className="space-y-5">
      <section className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Play</p>
          <h1 className="title mt-1">Library</h1>
          <p className="subtitle mt-1">
            Every imported game and its analysis status, exactly as stored.
          </p>
        </div>
        {all.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            <Link href="/import?source=platform" className="btn btn-ghost">
              Connect an account
            </Link>
            <Link href="/import" className="btn btn-primary">
              + Import a game
            </Link>
          </div>
        ) : null}
      </section>

      {all.length > 0 ? (
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Games" value={all.length} icon="♟" />
          <Stat
            label="Analyzed"
            value={all.filter((g) => g.analysis_status === "analyzed").length}
            tone="accent"
            icon="✓"
          />
          <Stat
            label="From an account"
            value={all.filter((g) => g.source === "chess_com" || g.source === "lichess").length}
            icon="⇄"
          />
          <Stat label="Showing" value={filtered.length} hint="matches the filters" icon="⌕" />
        </section>
      ) : null}

      {all.length > 0 ? (
        <Panel
          title="Filters"
          bodyClassName="panel-body flex flex-wrap items-center gap-3"
        >
          <input
            className="input max-w-xs"
            placeholder="Search players, opening or ECO…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            spellCheck={false}
          />
          <div className="flex flex-wrap items-center gap-1.5">
            <Chip active={status === "all"} onClick={() => setStatus("all")}>
              All statuses
            </Chip>
            <Chip active={status === "analyzed"} onClick={() => setStatus("analyzed")}>
              Analyzed
            </Chip>
            <Chip active={status === "pending"} onClick={() => setStatus("pending")}>
              Not analyzed
            </Chip>
          </div>
          <span className="hidden h-5 w-px bg-ink-700 sm:block" />
          <div className="flex flex-wrap items-center gap-1.5">
            <Chip active={source === "all"} onClick={() => setSource("all")}>
              All sources
            </Chip>
            <Chip active={source === "chess_com"} onClick={() => setSource("chess_com")}>
              Chess.com
            </Chip>
            <Chip active={source === "lichess"} onClick={() => setSource("lichess")}>
              Lichess
            </Chip>
            <Chip active={source === "pgn"} onClick={() => setSource("pgn")}>
              PGN
            </Chip>
          </div>
        </Panel>
      ) : null}

      {error ? <ErrorState title="Could not load games" message={error} /> : null}
      {games === null && !error ? <SkeletonRows rows={5} /> : null}

      {games !== null && all.length === 0 ? (
        <EmptyState
          title="Your library is empty"
          message="Nothing is preloaded — this library only ever contains games you import yourself. Add one from a PGN file, or pull a month of games from a Chess.com or Lichess account."
          icon="♟"
          action={
            <div className="flex flex-wrap items-center justify-center gap-2">
              <Link href="/import?source=platform" className="btn btn-ghost">
                Connect an account
              </Link>
              <Link href="/import" className="btn btn-primary">
                Import a game
              </Link>
            </div>
          }
        />
      ) : null}

      {all.length > 0 && filtered.length === 0 ? (
        <EmptyState
          title="No games match"
          message="Nothing in the library matches the current filters. Clear them to see everything."
          icon="⌕"
          action={
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setQuery("");
                setStatus("all");
                setSource("all");
              }}
            >
              Clear filters
            </button>
          }
        />
      ) : null}

      {filtered.length > 0 ? (
        <Panel bodyClassName="pt-1 pb-2">
          <GameTable
            games={filtered}
            actions={(game) => (
              <div className="flex items-center justify-end gap-2">
                <Link href={`/game/${game.id}`} className="btn btn-ghost">
                  Open
                </Link>
                {confirming === game.id ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-danger"
                      onClick={() => remove(game.id)}
                      disabled={deleting === game.id}
                    >
                      {deleting === game.id ? "Deleting…" : "Confirm"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => setConfirming(null)}
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => setConfirming(game.id)}
                  >
                    Delete
                  </button>
                )}
              </div>
            )}
          />
        </Panel>
      ) : null}
    </div>
  );
}
