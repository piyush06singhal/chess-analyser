"use client";

// Live chess lobby (§5, §6, §37).
//
// Pick who you are, open a game, and list the ones already open. Nothing here
// decides anything about chess: the mode, the colour and the time control are
// sent to the server, and the *server* fixes the fair-play permission at
// creation — a competitive game comes back clamped to no analysis whatever the
// form requested. The form says so rather than pretending otherwise.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/empty-state";
import { Panel, Stat } from "@/components/ui";
import {
  api,
  ApiError,
  createLiveGame,
  listLiveGames,
  type CreateLiveGameInput,
  type LiveGameListEntry,
  type LiveMode,
  type LiveVisibility,
  type PlayerListItem,
} from "@/lib/api";

const TIME_CONTROLS = ["1+0", "3+2", "5+0", "10+5", "15+10", "30+0"] as const;

const MODES: { id: LiveMode; label: string; hint: string }[] = [
  { id: "private_match", label: "Private match", hint: "Competitive: no engine during play" },
  { id: "training", label: "Training", hint: "Coach may help; engine allowed" },
  { id: "sandbox", label: "Sandbox", hint: "Full analysis against the engine" },
  { id: "local", label: "Local", hint: "Two people, one board" },
];

const TRAINING_MODES = [
  "coach_game",
  "practice_game",
  "opening_practice",
  "endgame_practice",
  "free_analysis",
] as const;

function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function statusTone(status: string): string {
  if (status === "active") return "border-emerald-primary/40 text-emerald-300";
  if (status === "waiting" || status === "ready") return "border-amber-primary/40 text-amber-300";
  if (status === "finished") return "border-ink-600 text-mist-400";
  return "border-ink-600 text-mist-500";
}

function Lobby() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryPlayer = Number(searchParams.get("player_id")) || undefined;

  const [players, setPlayers] = useState<PlayerListItem[] | null>(null);
  const [playerId, setPlayerId] = useState<number | undefined>(queryPlayer);
  const [games, setGames] = useState<LiveGameListEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [mode, setMode] = useState<LiveMode>("private_match");
  const [colour, setColour] = useState<"white" | "black">("white");
  const [timeControl, setTimeControl] = useState<string>("10+5");
  const [visibility, setVisibility] = useState<LiveVisibility>("private");
  const [opponent, setOpponent] = useState<"human" | "engine">("human");
  const [opponentPlayerId, setOpponentPlayerId] = useState<number | "">("");
  const [trainingMode, setTrainingMode] = useState<string>("coach_game");

  useEffect(() => {
    let cancelled = false;
    api
      .listPlayers()
      .then((payload) => {
        if (cancelled) return;
        setPlayers(payload.players);
        setPlayerId((current) => current ?? (payload.players[0] ? Number(payload.players[0].id) : undefined));
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, []);

  // The fetch happens after an await inside the effect, so state is only set
  // once the promise resolves — setting it synchronously in the effect body is
  // the cascading render React (and the lint rule) warns about.
  useEffect(() => {
    if (playerId === undefined) return;
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const payload = await listLiveGames(playerId);
        if (!cancelled) setGames(payload.games);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load your live games.");
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [playerId]);

  const selectedPlayer = useMemo(
    () => players?.find((p) => Number(p.id) === playerId) ?? null,
    [players, playerId],
  );

  const engineAvailable = mode === "training" || mode === "sandbox";

  async function onCreate() {
    if (!selectedPlayer) return;
    setCreating(true);
    setError(null);
    const input: CreateLiveGameInput = {
      player_id: Number(selectedPlayer.id),
      mode,
      colour,
      time_control: timeControl,
      visibility,
      opponents: engineAvailable ? opponent : "human",
    };
    if (opponent === "human" && opponentPlayerId) input.opponent_player_id = Number(opponentPlayerId);
    if (mode === "training") input.training_mode = trainingMode;
    try {
      const result = await createLiveGame(input);
      router.push(`/live/${result.state.game_id}?player_id=${selectedPlayer.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Could not create the game.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <p className="eyebrow">Play</p>
        <h1 className="title mt-1">Live chess</h1>
        <p className="subtitle mt-1 max-w-2xl">
          Play a real game with a server-authoritative clock. Competitive games get no engine help
          during play.
        </p>
      </div>

      {error ? <ErrorState title="Live chess" message={error} /> : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <Panel
          className="min-w-0"
          title="Create a game"
          subtitle="The mode fixes the fair-play permission. It cannot be changed mid-game."
        >
          {!players ? (
            <LoadingState label="Loading players…" />
          ) : players.length === 0 ? (
            <EmptyState
              title="No players yet"
              message="Import a game so Caissa knows who you are, then come back."
              action={<Link className="link" href="/import">Import a game</Link>}
            />
          ) : (
            <div className="space-y-3">
              <label className="field">
                <span className="label">You</span>
                <select
                  className="select"
                  value={playerId ?? ""}
                  onChange={(e) => setPlayerId(Number(e.target.value))}
                >
                  {players.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span className="label">Mode</span>
                <select className="select" value={mode} onChange={(e) => setMode(e.target.value as LiveMode)}>
                  {MODES.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label} — {m.hint}
                    </option>
                  ))}
                </select>
              </label>

              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                <label className="field">
                  <span className="label">Colour</span>
                  <select className="select" value={colour} onChange={(e) => setColour(e.target.value as "white" | "black")}>
                    <option value="white">White</option>
                    <option value="black">Black</option>
                  </select>
                </label>
                <label className="field">
                  <span className="label">Time</span>
                  <select className="select" value={timeControl} onChange={(e) => setTimeControl(e.target.value)}>
                    {TIME_CONTROLS.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span className="label">Visibility</span>
                  <select
                    className="select"
                    value={visibility}
                    onChange={(e) => setVisibility(e.target.value as LiveVisibility)}
                  >
                    <option value="private">Private</option>
                    <option value="unlisted">Unlisted</option>
                    <option value="public">Public</option>
                  </select>
                </label>
              </div>

              {engineAvailable ? (
                <label className="field">
                  <span className="label">Opponent</span>
                  <select
                    className="select"
                    value={opponent}
                    onChange={(e) => setOpponent(e.target.value as "human" | "engine")}
                  >
                    <option value="engine">Caissa engine</option>
                    <option value="human">A person</option>
                  </select>
                </label>
              ) : null}

              {opponent === "human" && !engineAvailable ? (
                <label className="field">
                  <span className="label">Invite a specific player (optional)</span>
                  <select
                    className="select"
                    value={opponentPlayerId}
                    onChange={(e) => setOpponentPlayerId(e.target.value ? Number(e.target.value) : "")}
                  >
                    <option value="">Anyone with the invite link</option>
                    {players
                      .filter((p) => Number(p.id) !== playerId)
                      .map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                  </select>
                </label>
              ) : null}

              {mode === "training" ? (
                <label className="field">
                  <span className="label">Training mode</span>
                  <select
                    className="select"
                    value={trainingMode}
                    onChange={(e) => setTrainingMode(e.target.value)}
                  >
                    {TRAINING_MODES.map((t) => (
                      <option key={t} value={t}>
                        {t.replace(/_/g, " ")}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              <button className="btn-primary w-full" onClick={onCreate} disabled={creating || !selectedPlayer}>
                {creating ? "Creating…" : "Create game"}
              </button>
              <p className="text-meta text-mist-500">
                Competitive games are clamped to <span className="mono">no_analysis</span> by the server,
                whatever is chosen above.
              </p>
            </div>
          )}
        </Panel>

        <Panel
          className="min-w-0"
          title="Your live games"
          subtitle="Open games, in progress, and finished ones waiting for review."
          actions={selectedPlayer ? <Stat label="Games" value={games?.length ?? "—"} /> : undefined}
        >
          {!selectedPlayer ? (
            <EmptyState title="Pick a player" message="Choose who you are to see your games." />
          ) : !games ? (
            <LoadingState label="Loading your games…" />
          ) : games.length === 0 ? (
            <EmptyState
              title="No live games yet"
              message="Create one on the left, and it will appear here."
              icon="♜"
            />
          ) : (
            <ul className="divide-y divide-ink-700">
              {games.map((game) => (
                <li key={game.game_id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`badge ${statusTone(game.status)}`}>{game.status}</span>
                      <span className="text-small text-mist-400">{game.mode.replace(/_/g, " ")}</span>
                      {game.training_mode ? (
                        <span className="text-meta text-mist-500">{game.training_mode.replace(/_/g, " ")}</span>
                      ) : null}
                    </div>
                    <p className="mt-1 truncate text-small text-mist-200">
                      {game.white ?? "Open seat"} <span className="text-mist-500">vs</span>{" "}
                      {game.black ?? "Open seat"}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="mono text-meta text-mist-400">
                      {formatClock(game.clock.white_ms)} · {formatClock(game.clock.black_ms)}
                    </span>
                    <Link
                      className="link"
                      href={`/live/${game.game_id}?player_id=${selectedPlayer.id}`}
                    >
                      open
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

// The fallback carries the page header so the prerendered shell shows the title
// rather than a bare spinner (useSearchParams suspends during prerender).
export default function LivePage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-5">
          <div>
            <p className="eyebrow">Play</p>
            <h1 className="title mt-1">Live chess</h1>
            <p className="subtitle mt-1 max-w-2xl">
              Play a real game with a server-authoritative clock. Competitive games get no engine
              help during play.
            </p>
          </div>
          <LoadingState label="Loading live chess…" />
        </div>
      }
    >
      <Lobby />
    </Suspense>
  );
}
