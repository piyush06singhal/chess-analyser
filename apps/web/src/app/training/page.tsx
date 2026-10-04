"use client";

// Training — the personalized chess training engine's front door.
//
// The page is deliberately a *workbench*, not a marketing screen. It shows, for
// the selected player: what Caissa recommends and why (with the evidence and the
// sample sizes), what is due for review, the exercise library with its traceable
// origin, and the sessions that have been run. Every figure comes from the
// backend's measured data; nothing here is estimated in the browser.
//
// Deep links:
//   /training?player=<id>            open a player's training
//   /training?game=<gameId>          generate from that game and show its exercises

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { EmptyState, ErrorState, LoadingState } from "@/components/empty-state";
import { Disclosure, Panel, ProgressBar, Stat } from "@/components/ui";
import {
  api,
  ApiError,
  type PlayerListItem,
  type TrainingMeta,
  type TrainingPositionSummary,
  type TrainingProgress,
  type TrainingRecommendations,
  type TrainingReviewQueue,
  type TrainingSessionSummary,
} from "@/lib/api";

const SESSION_KINDS = ["quick", "daily", "weakness", "game_review", "endgame", "tactical"] as const;

function accuracy(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function SourceBadge({ position }: { position: TrainingPositionSummary }) {
  if (position.is_opponent_preparation) {
    return (
      <span
        className="badge border-amber-primary/40 text-amber-300"
        title="Opponent preparation: a reply to a move an opponent demonstrably plays, built from that opponent's stored games. Private to you."
      >
        Opponent prep
      </span>
    );
  }
  const personalized = position.is_personalized;
  return (
    <span
      className={`badge ${personalized ? "border-violet-primary/40 text-violet-300" : "border-sky-primary/40 text-sky-300"}`}
      title={
        personalized
          ? "Personalized: derived from your own analysed games, private to you."
          : "General exercise: not derived from a specific player's game."
      }
    >
      {personalized ? "Personalized" : "General"}
    </span>
  );
}

function PositionCard({ position, playerId }: { position: TrainingPositionSummary; playerId: string }) {
  const origin = position.origin;
  return (
    <div className="rounded-xl border border-ink-700 bg-ink-800/60 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <SourceBadge position={position} />
        <span className="badge border-ink-600 text-mist-300">{position.category_label}</span>
        <span className="badge border-ink-600 text-mist-400">{position.difficulty}</span>
        <span className="badge border-ink-600 text-mist-500">{position.side_to_move} to move</span>
      </div>
      <p className="mt-2 text-small text-mist-300">{position.source_reason || "Engine-verified better move"}</p>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-small text-mist-500">
        <span>
          state <span className="text-mist-200">{position.state}</span>
        </span>
        <span>
          attempts <span className="text-mist-200">{position.attempts}</span>
        </span>
        {origin.game_id ? (
          <Link
            href={`/game/${origin.game_id}?ply=${origin.ply ?? 0}`}
            className="text-emerald-300 hover:underline"
            title="Open the original position this exercise came from"
          >
            Original game · move {origin.ply ?? "?"}
          </Link>
        ) : (
          <span title="The source game was deleted; the exercise and its history survive.">
            source unavailable
          </span>
        )}
      </div>
      <Link
        href={`/training/solve/${position.id}?player=${encodeURIComponent(playerId)}`}
        className="btn btn-primary mt-3"
      >
        Solve
      </Link>
    </div>
  );
}

function TrainingWorkspace() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [players, setPlayers] = useState<PlayerListItem[]>([]);
  const [playerId, setPlayerId] = useState(() => searchParams.get("player") ?? "");
  const [gameId] = useState(() => searchParams.get("game") ?? "");

  const [meta, setMeta] = useState<TrainingMeta | null>(null);
  const [progress, setProgress] = useState<TrainingProgress | null>(null);
  const [recommendations, setRecommendations] = useState<TrainingRecommendations | null>(null);
  const [queue, setQueue] = useState<TrainingReviewQueue | null>(null);
  const [positions, setPositions] = useState<TrainingPositionSummary[]>([]);
  const [sessions, setSessions] = useState<TrainingSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    api
      .listPlayers()
      .then((payload) => {
        setPlayers(payload.players);
        setPlayerId((current) => current || payload.players[0]?.id || "");
      })
      .catch(() => setPlayers([]));
    api.trainingMeta().then(setMeta).catch(() => setMeta(null));
  }, []);

  const refresh = useCallback(async (pid: string) => {
    if (!pid) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [progressData, recs, review, library, sessionList] = await Promise.all([
        api.trainingProgress(pid),
        api.trainingRecommendations(pid).catch(() => null),
        api.trainingReviewQueue(pid),
        api.listTrainingPositions({ player_id: pid, limit: 60 }),
        api.listTrainingSessions(pid),
      ]);
      setProgress(progressData);
      setRecommendations(recs);
      setQueue(review);
      setPositions(library.positions);
      setSessions(sessionList.sessions);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load training data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Deferred to a microtask: the loader sets state after its awaits, so this
    // avoids a synchronous cascading render on mount and on player change.
    void Promise.resolve().then(() => refresh(playerId));
  }, [playerId, refresh]);

  const generateFromGame = useCallback(async () => {
    if (!gameId) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.generateTraining(gameId, { player_id: playerId || null });
      setNotice(
        result.accepted > 0
          ? `Generated ${result.accepted} exercise(s) from this game.`
          : `No exercise qualified from this game (${result.seen} moves examined).`
      );
      await refresh(playerId);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Generation failed");
    } finally {
      setBusy(false);
    }
  }, [gameId, playerId, refresh]);

  const startSession = useCallback(
    async (kind: string) => {
      if (!playerId) return;
      setBusy(true);
      setNotice(null);
      try {
        const session = await api.startTrainingSession({ player_id: playerId, kind });
        if (session.remaining_position_ids.length === 0) {
          setNotice(session.notes?.[0] ?? "This session has no exercises yet.");
          await refresh(playerId);
          return;
        }
        router.push(
          `/training/solve/${session.remaining_position_ids[0]}?player=${encodeURIComponent(playerId)}&session=${session.id}`
        );
      } catch (err) {
        setNotice(err instanceof ApiError ? err.message : "Could not start the session");
      } finally {
        setBusy(false);
      }
    },
    [playerId, refresh, router]
  );

  const selectedPlayer = useMemo(
    () => players.find((player) => player.id === playerId) ?? null,
    [players, playerId]
  );

  const categoryRows = useMemo(() => {
    if (!progress) return [] as { key: string; value: TrainingProgress["by_category"][string] }[];
    return Object.entries(progress.by_category)
      .map(([key, value]) => ({ key, value }))
      .sort((a, b) => b.value.decided - a.value.decided);
  }, [progress]);

  return (
    <div className="space-y-5">
      <section className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Coach</p>
          <h1 className="title mt-1">Training</h1>
          <p className="subtitle mt-1">
            Exercises generated from your own analysed mistakes — each traced to a game and a ply,
            with an engine-verified solution.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-small text-mist-400" htmlFor="training-player">
            Player
          </label>
          <select
            id="training-player"
            className="input"
            value={playerId}
            onChange={(event) => setPlayerId(event.target.value)}
          >
            {players.length === 0 ? <option value="">No players yet</option> : null}
            {players.map((player) => (
              <option key={player.id} value={player.id}>
                {player.name} ({player.analyzed_games} analyzed)
              </option>
            ))}
          </select>
        </div>
      </section>

      {gameId ? (
        <Panel
          title="Generate from game"
          subtitle="Turn this game's stored analysis into training exercises. Nothing runs the engine here — the solutions were verified when the game was analysed."
          actions={
            <button type="button" className="btn btn-primary" onClick={generateFromGame} disabled={busy}>
              {busy ? "Generating…" : "Generate exercises"}
            </button>
          }
        >
          <p className="text-small text-mist-400">
            Game <span className="mono text-mist-200">{gameId}</span>
          </p>
          {notice ? <p className="mt-2 text-small text-mist-200">{notice}</p> : null}
        </Panel>
      ) : null}

      {!playerId ? (
        <EmptyState
          title="No players yet"
          message="Import and analyze a game first; training exercises come from analysed mistakes."
          action={<Link href="/import" className="btn btn-primary">Import a game</Link>}
        />
      ) : error ? (
        <ErrorState title="Could not load training" message={error} />
      ) : loading ? (
        <LoadingState label="Loading training data…" />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Exercises in library"
              value={progress?.library_size ?? 0}
              hint={`${progress?.mastered_count ?? 0} mastered`}
              icon="♟"
            />
            <Stat
              label="Attempts recorded"
              value={progress?.attempts_total ?? 0}
              hint={`${progress?.correct_total ?? 0} correct · ${progress?.near_best_total ?? 0} near-best · ${progress?.incorrect_total ?? 0} incorrect`}
              icon="✓"
            />
            <Stat
              label="Overall accuracy"
              value={accuracy(progress?.accuracy_overall ?? null)}
              hint={
                progress && progress.attempts_total > 0
                  ? `${progress.attempts_total} decided attempt(s)`
                  : "no attempts yet"
              }
              tone="accent"
              icon="%"
            />
            <Stat
              label="Due for review"
              value={queue?.count ?? 0}
              hint={queue ? `${queue.total_queued} scheduled in total` : undefined}
              tone={(queue?.count ?? 0) > 0 ? "warn" : "default"}
              icon="⟳"
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel
              title="What to practise"
              subtitle={recommendations?.evidence_policy}
            >
              {recommendations && recommendations.opportunities.length > 0 ? (
                <ul className="space-y-3">
                  {recommendations.opportunities.map((opportunity) => (
                    <li key={opportunity.category}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-mist-100">{opportunity.label}</span>
                        <span className="badge border-ink-600 text-mist-400">
                          priority {opportunity.priority.toFixed(3)}
                        </span>
                      </div>
                      <div className="mt-1">
                        <ProgressBar value={opportunity.priority} max={1} />
                      </div>
                      <p className="mt-1 text-small text-mist-300">{opportunity.reason}</p>
                      <Disclosure summary="Evidence">
                        <ul className="list-disc space-y-1 pl-4">
                          {opportunity.evidence.map((line) => (
                            <li key={line} className="text-small text-mist-400">
                              {line}
                            </li>
                          ))}
                        </ul>
                      </Disclosure>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  title="Not enough evidence yet"
                  message={
                    recommendations?.categories_without_evidence.length
                      ? `These categories have too few attempts to recommend: ${recommendations.categories_without_evidence.join(", ")}.`
                      : "Attempt some exercises and Caissa will recommend from measured performance."
                  }
                />
              )}
            </Panel>

            <Panel title="Progress by category" subtitle="Accuracy over decided attempts, with the sample size attached.">
              {categoryRows.length === 0 ? (
                <EmptyState title="No attempts yet" message="Solve an exercise to start measuring." />
              ) : (
                <div className="space-y-2">
                  {categoryRows.map(({ key, value }) => (
                    <div key={key} className="space-y-1">
                      <div className="flex items-center justify-between text-small">
                        <span className="text-mist-200">{value.label}</span>
                        <span className="text-mist-400">
                          {accuracy(value.accuracy)} · {value.decided} decided
                        </span>
                      </div>
                      <ProgressBar value={(value.accuracy ?? 0) * 100} />
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>

          <Panel
            title="Start a session"
            subtitle="A session is a resumable plan over your library. It is filled from what you need, never padded with substitutes."
            actions={
              <span className="text-small text-mist-500">
                {selectedPlayer ? `${selectedPlayer.name} · ${selectedPlayer.analyzed_games} analyzed game(s)` : ""}
              </span>
            }
          >
            <div className="flex flex-wrap gap-2">
              {SESSION_KINDS.map((kind) => (
                <button
                  key={kind}
                  type="button"
                  className="btn btn-ghost capitalize"
                  disabled={busy}
                  onClick={() => startSession(kind)}
                >
                  {kind.replace("_", " ")}
                </button>
              ))}
            </div>
            {notice && !gameId ? <p className="mt-3 text-small text-mist-200">{notice}</p> : null}
          </Panel>

          <Panel
            title={`Exercise library (${positions.length})`}
            subtitle="Every exercise traces to a game and a ply. Personalized exercises are private to their player; general exercises are the only shareable ones."
          >
            {positions.length === 0 ? (
              <EmptyState
                title="No exercises yet"
                message={
                  gameId
                    ? "Generate from the game above."
                    : "Open an analysed game and use 'Practice this game', or generate from a game page."
                }
                action={<Link href="/games" className="btn btn-ghost">Open the library</Link>}
              />
            ) : (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {positions.map((position) => (
                  <PositionCard key={position.id} position={position} playerId={playerId} />
                ))}
              </div>
            )}
          </Panel>

          <Panel title={`Sessions (${sessions.length})`} subtitle="Attempts live inside sessions; cancelling a session deletes nothing.">
            {sessions.length === 0 ? (
              <EmptyState title="No sessions yet" message="Start one above." />
            ) : (
              <ul className="space-y-2">
                {sessions.map((session) => (
                  <li key={session.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-ink-700 bg-ink-800/50 p-3">
                    <div>
                      <span className="text-mist-100 capitalize">{session.kind.replace("_", " ")}</span>
                      <span className="ml-2 badge border-ink-600 text-mist-400">{session.status}</span>
                    </div>
                    <div className="text-small text-mist-400">
                      {session.completed}/{session.planned} solved · score{" "}
                      {session.score === null ? "—" : `${Math.round(session.score * 100)}%`}
                    </div>
                    {session.status === "active" && session.remaining_position_ids.length > 0 ? (
                      <Link
                        className="btn btn-ghost"
                        href={`/training/solve/${session.remaining_position_ids[0]}?player=${encodeURIComponent(playerId)}&session=${session.id}`}
                      >
                        Resume
                      </Link>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          {meta ? (
            <Disclosure summary="Training methodology">
              <p className="text-small text-mist-400">
                Methodology {meta.methodology_version} · hints {meta.hint_policy_version}. Categories:{" "}
                {meta.categories.map((category) => meta.category_labels[category] ?? category).join(", ")}.
              </p>
              <p className="text-small text-mist-400">{meta.note}</p>
            </Disclosure>
          ) : null}
        </>
      )}
    </div>
  );
}

// The fallback carries the page header so the prerendered shell shows the title
// rather than a bare spinner (useSearchParams suspends during prerender).
export default function TrainingPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-5">
          <section>
            <p className="eyebrow">Coach</p>
            <h1 className="title mt-1">Training</h1>
            <p className="subtitle mt-1">
              Exercises generated from your own analysed mistakes — each traced to a game and a ply,
              with an engine-verified solution.
            </p>
          </section>
          <LoadingState label="Loading training…" />
        </div>
      }
    >
      <TrainingWorkspace />
    </Suspense>
  );
}
