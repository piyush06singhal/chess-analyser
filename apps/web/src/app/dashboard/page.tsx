"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { GameListItem, PlatformSourceId } from "@/lib/api";
import { ErrorState, SkeletonRows } from "@/components/empty-state";
import { GameTable } from "@/components/game-table";
import { GettingStarted } from "@/components/getting-started";
import { Chip, Panel, Stat } from "@/components/ui";
import { plural } from "@/lib/text";

// The dashboard shows exactly two things: what is in the library, and how to
// add something new. Everything rendered here is read from the API — there are
// no placeholder statistics, and no section is drawn when it has no data.

const PLATFORMS: { id: PlatformSourceId; label: string; placeholder: string }[] = [
  { id: "chess_com", label: "Chess.com", placeholder: "e.g. hikaru" },
  { id: "lichess", label: "Lichess", placeholder: "e.g. thibault" },
];

/** Look up a player on either supported platform, then hand off to the importer. */
function ConnectAccount({ platforms }: { platforms: typeof PLATFORMS }) {
  const router = useRouter();
  const [platform, setPlatform] = useState<PlatformSourceId>(platforms[0].id);
  const [username, setUsername] = useState("");
  const active = platforms.find((entry) => entry.id === platform) ?? platforms[0];

  return (
    <form
      className="space-y-2.5"
      onSubmit={(event) => {
        event.preventDefault();
        const handle = username.trim().replace(/^@/, "");
        router.push(
          handle
            ? `/import?source=${platform}&username=${encodeURIComponent(handle)}`
            : `/import?source=${platform}`
        );
      }}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        {platforms.map((entry) => (
          <Chip
            key={entry.id}
            active={platform === entry.id}
            onClick={() => setPlatform(entry.id)}
          >
            {entry.label}
          </Chip>
        ))}
      </div>
      <label htmlFor="platform-username" className="sr-only">
        {active.label} username
      </label>
      <input
        id="platform-username"
        className="input"
        placeholder={active.placeholder}
        value={username}
        onChange={(event) => setUsername(event.target.value)}
        spellCheck={false}
        autoComplete="off"
      />
      <button type="submit" className="btn btn-primary w-full">
        Look up games
      </button>
    </form>
  );
}

export default function DashboardPage() {
  const [games, setGames] = useState<GameListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listGames()
      .then((res) => setGames(res.games))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 503) {
          setError("Game storage is not configured on the backend (set ARGUS_DATABASE_URL).");
        } else {
          setError(err instanceof Error ? err.message : "Failed to load games");
        }
      });
  }, []);

  const stats = useMemo(() => {
    const list = games ?? [];
    const analyzed = list.filter((game) => game.analysis_status === "analyzed").length;
    const inProgress = list.filter((game) =>
      ["analyzing", "validating"].includes(game.analysis_status)
    ).length;
    const fromPlatform = list.filter((game) => game.source !== "pgn_text" && game.source !== "pgn_file").length;
    const plies = list
      .filter((game) => game.analysis_status === "analyzed")
      .reduce((total, game) => total + game.move_count, 0);
    return { total: list.length, analyzed, inProgress, fromPlatform, plies };
  }, [games]);

  const loaded = games !== null;
  const isEmpty = loaded && games.length === 0;
  const recent = (games ?? []).slice(0, 5);
  const latest = recent[0] ?? null;

  return (
    <div className="space-y-5">
      {/* Header: a quiet page title over the backdrop. The marketing story lives
          on the landing page; this page is the workbench. */}
      <section className="animate-fade-up flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
        <div className="min-w-0 max-w-2xl">
          <p className="eyebrow">Workspace</p>
          <h1 className="display-hero display-hero-md mt-2">Your games, measured.</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/import" className="btn btn-primary btn-pill">
            Import a game
          </Link>
        </div>
      </section>

      {error ? <ErrorState title="Could not load games" message={error} /> : null}
      {!loaded && !error ? <SkeletonRows rows={3} /> : null}

      {/* Empty library: a deliberate first-run path, then the two real ways to add a game. */}
      {isEmpty ? (
        <>
          <section className="panel animate-fade-up overflow-hidden px-6 py-8 sm:px-10">
            <div className="max-w-2xl">
              <h2 className="text-2xl font-bold tracking-tight text-mist-50">
                Your library is empty — add a game to get started.
              </h2>
            </div>
            <ol className="mt-6 grid gap-3 sm:grid-cols-3">
              {[
                {
                  step: "1",
                  title: "Connect or paste",
                  text: "Your Chess.com or Lichess username, or a PGN you already have.",
                },
                {
                  step: "2",
                  title: "Analyze",
                  text: "Stockfish evaluates every position — typically under a minute for a game.",
                },
                {
                  step: "3",
                  title: "Read the report",
                  text: "Accuracy, turning points and lessons, each backed by the engine's output.",
                },
              ].map((item) => (
                <li key={item.step} className="rounded-xl border border-ink-700 bg-ink-900/50 p-4">
                  <span className="mono flex h-7 w-7 items-center justify-center rounded-lg border border-emerald-primary/40 bg-emerald-primary/10 text-small font-semibold text-emerald-300">
                    {item.step}
                  </span>
                  <h3 className="mt-2.5 text-base font-semibold text-mist-50">{item.title}</h3>
                  <p className="mt-1 text-small leading-relaxed text-mist-400">{item.text}</p>
                </li>
              ))}
            </ol>
          </section>

          <section className="grid gap-4 lg:grid-cols-2">
            <Panel
              title="Import from an account"
              subtitle="Chess.com and Lichess, read publicly."
            >
              <ConnectAccount platforms={PLATFORMS} />
            </Panel>

            <Panel title="Import a PGN" subtitle="Paste a game or upload a .pgn file.">
              <Link href="/import" className="btn btn-primary">
                Open the importer
              </Link>
            </Panel>
          </section>
        </>
      ) : null}

      {/* Populated library: real counts, then the newest games. */}
      {loaded && games.length > 0 ? (
        <>
          {/* Guided first run: shown only until something is analysed, so the
              "imported but not yet measured" state is never a dead end. */}
          <GettingStarted imported={stats.total} analyzed={stats.analyzed} />

          <section className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat
              label="Games in library"
              value={stats.total}
              hint={`${stats.fromPlatform} from an account`}
              icon="♟"
            />
            <Stat
              label="Analyzed"
              value={stats.analyzed}
              tone={stats.analyzed > 0 ? "accent" : "default"}
              hint={`${stats.plies} moves evaluated`}
              icon="✓"
            />
            {/* The hint must agree with the number above it: "queue is clear"
                next to a non-zero count reads as a contradiction. */}
            <Stat
              label="Awaiting analysis"
              value={stats.total - stats.analyzed - stats.inProgress}
              tone={stats.total - stats.analyzed - stats.inProgress > 0 ? "warn" : "default"}
              hint={
                stats.inProgress > 0
                  ? `${stats.inProgress} running now`
                  : stats.total - stats.analyzed - stats.inProgress > 0
                    ? "ready to analyse"
                    : "all analysed"
              }
              icon="◷"
            />
          </section>

          {/* The newest game with its report one click away, then the rest.
              The Library page owns "everything". */}
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
            <Panel
              className="min-w-0"
              title="Pick up where you left off"
              subtitle="Your latest imports."
              actions={
                <Link href="/games" className="btn btn-ghost">
                  Browse the Library
                </Link>
              }
              bodyClassName="pt-1 pb-2"
            >
              {latest ? (
                <div className="px-2 pb-1">
                  <Link
                    href={`/game/${latest.id}/report`}
                    className="card card-hover block p-4"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-base font-semibold tracking-tight text-mist-50">
                        {latest.white_player} vs {latest.black_player}
                      </p>
                      <span className="mono text-small text-mist-500">{latest.result}</span>
                    </div>
                    <p className="mt-1 text-small text-mist-400">
                      {[latest.opening_name ?? latest.eco_code, plural(latest.move_count, "move")]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                    <span className="mt-2.5 inline-block text-small font-semibold text-emerald-300">
                      Open the game report →
                    </span>
                  </Link>
                </div>
              ) : null}
              <GameTable games={recent} compact />
            </Panel>

            <Panel title="Import from an account" subtitle="Read a player's public games by username.">
              <ConnectAccount platforms={PLATFORMS} />
            </Panel>
          </div>
        </>
      ) : null}
    </div>
  );
}
