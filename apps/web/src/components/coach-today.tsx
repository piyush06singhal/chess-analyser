"use client";

// Today's Caissa (Phase 11 §3, §6, §7, §9, §10): where the user *is*, what Caissa
// noticed, what to work on, and the automatic debrief of the game they just
// reviewed.
//
// Three things this component deliberately does not do:
//
// * It never fills an empty section with advice. When a section has nothing, the
//   card shows the reason the backend gave ("no player profile is computed yet")
//   rather than a motivational sentence — which is the point of shipping this
//   data shape at all.
// * It never presents a prediction. Prediction availability is displayed as the
//   registry reports it; with no production model it says so.
// * It never guesses the situation. The context panel shows what Caissa resolved
//   and which facts it had, so a wrong guess is visibly wrong rather than subtly
//   wrong.

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { EmptyState, ErrorState, LoadingState } from "@/components/empty-state";
import { ShowMeWhy } from "@/components/evidence-panel";
import { Panel, Stat } from "@/components/ui";
import {
  api,
  ApiError,
  type CoachContextResponse,
  type CoachFeedResponse,
  type CoachFocusResponse,
  type FeedCard,
  type GameDebriefResponse,
} from "@/lib/api";

const PRIORITY_TONE: Record<string, string> = {
  critical: "text-rose-300",
  high: "text-amber-300",
  normal: "text-mist-200",
  low: "text-mist-400",
};

const SITUATION_LABEL: Record<string, string> = {
  live_game: "live game",
  game_review: "game review",
  training: "training",
  opponent_preparation: "opponent preparation",
  position_analysis: "position analysis",
  opening_study: "opening study",
  endgame_study: "endgame study",
  general_coaching: "general coaching",
};

function FeedCardView({ card }: { card: FeedCard }) {
  return (
    <article className="rounded-xl border border-ink-700 bg-ink-850 p-3">
      <header className="flex items-start justify-between gap-3">
        <h4 className="text-small font-semibold text-mist-100">{card.title}</h4>
        <span className={`mono text-meta ${PRIORITY_TONE[card.priority] ?? "text-mist-400"}`}>
          {card.priority}
          {card.score !== null ? ` · ${card.score}` : ""}
        </span>
      </header>
      <p className="mt-1 text-small leading-relaxed text-mist-300">{card.statement}</p>
      <p className="mono mt-1.5 text-meta text-mist-500">
        {card.sample_size !== null ? `n=${card.sample_size}` : "sample size not applicable"}
        {card.capped_by ? ` · capped by ${card.capped_by}` : ""}
        {card.evidence.length ? ` · ${card.evidence.length} evidence ref(s)` : ""}
      </p>
      {card.actions.length ? (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {card.actions.map((action) => (
            <Link key={`${card.key}-${action.label}`} href={action.href} className="chip">
              {action.label}
            </Link>
          ))}
        </div>
      ) : null}
    </article>
  );
}

export function CoachToday() {
  // Deep-link: /coach?tab=today&game_id=…&fen=…&opponent_id=… lets the board and
  // the coach agree on which position is being discussed, without the user
  // describing it twice.
  const searchParams = useSearchParams();
  const gameId = searchParams.get("game_id") ?? "";
  const fen = searchParams.get("fen") ?? undefined;
  const opponentId = searchParams.get("opponent_id")
    ? Number(searchParams.get("opponent_id"))
    : undefined;
  const userIdParam = searchParams.get("user_id")
    ? Number(searchParams.get("user_id"))
    : undefined;

  // A focus needs a player, and no link carries one. Rather than leave the
  // "What should I work on?" panel permanently unreachable, fall back to the
  // first stored player — the same default the Progress, Collections and
  // Opponents pages use. An explicit `?user_id=` still wins.
  const [defaultUserId, setDefaultUserId] = useState<number | undefined>(undefined);
  useEffect(() => {
    if (userIdParam !== undefined) return;
    api
      .listPlayers()
      .then((payload) => {
        if (payload.players[0]) setDefaultUserId(Number(payload.players[0].id));
      })
      .catch(() => undefined);
  }, [userIdParam]);
  const userId = userIdParam ?? defaultUserId;

  const [context, setContext] = useState<CoachContextResponse | null>(null);
  const [feed, setFeed] = useState<CoachFeedResponse | null>(null);
  const [focus, setFocus] = useState<CoachFocusResponse | null>(null);
  const [debrief, setDebrief] = useState<GameDebriefResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [reloadToken, setReloadToken] = useState(0);

  // The fetch steps are async helpers called from inside the effect's promise
  // chain rather than a `useCallback`, because every state update has to happen
  // after an await: setting state synchronously inside an effect body causes the
  // cascading render React warns about (and the lint rule enforces).
  useEffect(() => {
    let cancelled = false;
    const still = () => !cancelled;

    const stepContext = async (): Promise<void> => {
      const payload = await api.coachContext({
        user_id: userId,
        game_id: gameId || undefined,
        fen,
        opponent_id: opponentId,
      });
      if (still()) setContext(payload);
    };

    const stepFeed = async (): Promise<void> => {
      const payload = await api.coachFeed({
        user_id: userId,
        game_id: gameId || undefined,
        opponent_id: opponentId,
        dismissed,
      });
      if (!still()) return;
      setFeed(payload);

      // "What should I work on?" needs a player who has a profile at all. Without
      // one the endpoint answers with a status and a reason, which is displayed
      // as-is rather than as a failure.
      if (!userId) {
        setFocus(null);
        return;
      }
      const focusPayload = await api.coachFocus(userId, dismissed);
      if (still()) setFocus(focusPayload);
    };

    const stepDebrief = async (): Promise<void> => {
      if (!gameId) {
        if (still()) setDebrief(null);
        return;
      }
      const payload = await api.gameDebrief(gameId, userId);
      if (still()) setDebrief(payload);
    };

    void stepContext()
      .then(stepFeed)
      .then(stepDebrief)
      .then(() => {
        if (!still()) return;
        setError(null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (!still()) return;
        setError(
          err instanceof ApiError
            ? err.message
            : "The coaching workspace could not be loaded — is the backend running?"
        );
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [userId, gameId, fen, opponentId, dismissed, reloadToken]);

  if (loading) return <LoadingState label="Resolving your coaching context…" />;
  if (error) return <ErrorState title="The coaching workspace could not load" message={error} />;
  if (!context) {
    return (
      <EmptyState
        title="No coaching context is available"
        message="Caissa could not resolve a situation from the current selection."
      />
    );
  }

  const brief = context.brief;
  const exposure = [
    brief.exposure.evaluation ? "evaluation" : null,
    brief.exposure.candidate_moves ? "candidates" : null,
    brief.exposure.principal_variation ? "principal variation" : null,
    brief.exposure.statistics ? "statistics" : null,
  ].filter((value): value is string => Boolean(value));

  return (
    <div className="space-y-5">
      {brief.gaps.length ? (
        <div className="rounded-xl border border-amber-primary/30 bg-amber-primary/5 px-3.5 py-2.5">
          <p className="text-small font-medium text-amber-300">What Caissa does not have here</p>
          <ul className="mt-1 space-y-0.5">
            {brief.gaps.map((gap) => (
              <li key={gap} className="text-small text-mist-300">
                {gap}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <Panel
        title="Where you are"
        subtitle={
          "The situation and coaching mode are resolved from what is open — you should not have to " +
          "announce the context. The mode decides how much engine detail is exposed and which tool " +
          "families the coach may use."
        }
        actions={
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => setReloadToken((token) => token + 1)}
          >
            Refresh
          </button>
        }
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat
            label="Situation"
            value={SITUATION_LABEL[brief.situation] ?? brief.situation}
            icon="⌖"
          />
          <Stat
            label="Mode"
            value={brief.mode_label}
            hint={`${brief.explanation_depth} explanation depth`}
            icon="♞"
          />
          <Stat
            label="Exposing"
            value={exposure.length ? exposure.join(", ") : "no engine detail"}
            icon="◎"
          />
        </div>
        <dl className="mt-3 space-y-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <dt className="text-small text-mist-500">Evidence available</dt>
            <dd className="text-small text-mist-300">
              {brief.evidence_available.length ? brief.evidence_available.join(", ") : "none yet"}
            </dd>
          </div>
          {brief.notes.length ? (
            <div className="flex flex-wrap items-baseline gap-2">
              <dt className="text-small text-mist-500">Notes</dt>
              <dd className="text-small text-mist-300">{brief.notes.join(" · ")}</dd>
            </div>
          ) : null}
        </dl>
      </Panel>

      {focus ? (
        <Panel
          title="What should I work on?"
          subtitle={
            focus.status === "ok"
              ? "Derived from your stored games and training attempts. If there is not enough evidence " +
                "to name a focus, Caissa says so rather than suggesting something generic."
              : undefined
          }
        >
          {focus.status === "ok" && focus.primary_focus ? (
            <div className="space-y-3">
              <div className="rounded-xl border border-ink-700 bg-ink-850 p-3">
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-small font-semibold text-mist-100">
                    {focus.primary_focus.title}
                  </h3>
                  <span
                    className={`mono text-meta ${
                      PRIORITY_TONE[focus.primary_focus.priority] ?? "text-mist-400"
                    }`}
                  >
                    {focus.primary_focus.priority} · n={focus.primary_focus.sample_size}
                  </span>
                </div>
                <p className="mt-1 text-small leading-relaxed text-mist-300">
                  {focus.primary_focus.statement}
                </p>
                {focus.primary_focus.claim_level ? (
                  <p className="mono mt-1.5 text-meta text-mist-500">
                    claim level: {focus.primary_focus.claim_level}
                  </p>
                ) : null}
                <div className="mt-2">
                  {/* §24/§25: any claim can be opened to the evidence behind it. */}
                  <ShowMeWhy
                    claim={focus.primary_focus.statement}
                    playerId={userId ?? undefined}
                    insightKey={focus.primary_focus.key.replace(/^profile:/, "")}
                  />
                </div>
              </div>

              {focus.evidence.length ? (
                <div>
                  <h4 className="label">Evidence</h4>
                  <ul className="mt-1 space-y-1">
                    {focus.evidence.slice(0, 6).map((item, index) => (
                      <li key={index} className="text-small text-mist-300">
                        {String(item.statement ?? item.metric ?? JSON.stringify(item))}
                        {item.game_id ? (
                          <Link href={`/game/${String(item.game_id)}`} className="link ml-2">
                            open game
                          </Link>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {focus.affected_games.length ? (
                <div>
                  <h4 className="label">Affected games ({focus.affected_games.length})</h4>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {focus.affected_games.slice(0, 12).map((entry, index) => (
                      <Link
                        key={`${entry.game_id}-${index}`}
                        href={`/game/${entry.game_id ?? ""}${entry.ply ? `?ply=${entry.ply}` : ""}`}
                        className="chip mono"
                      >
                        {String(entry.game_id ?? "").slice(0, 8)}
                        {entry.ply ? ` · ply ${entry.ply}` : ""}
                      </Link>
                    ))}
                  </div>
                </div>
              ) : null}

              {focus.recommended_exercises.length ? (
                <div>
                  <h4 className="label">Recommended exercises</h4>
                  <ul className="mt-1 space-y-1">
                    {focus.recommended_exercises.map((exercise, index) => (
                      <li key={index} className="text-small text-mist-300">
                        {String(exercise.title ?? exercise.pattern ?? JSON.stringify(exercise))}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {focus.how_progress_will_be_measured.length ? (
                <div>
                  <h4 className="label">How progress will be measured</h4>
                  <ul className="mt-1 space-y-0.5">
                    {focus.how_progress_will_be_measured.map((line) => (
                      <li key={line} className="text-small text-mist-400">
                        {line}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : (
            <EmptyState
              icon="◔"
              title="Not enough evidence to name a focus"
              message={focus.reason ?? "Caissa has no stored analysis it can build a focus from yet."}
            />
          )}
        </Panel>
      ) : null}

      {feed ? (
        <Panel
          title="Today's Caissa"
          subtitle="Each card links to the analysis behind it, and every card can be dismissed so the feed does not repeat itself."
        >
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {(Object.entries(feed.counts) as [string, number][])
              .filter(([, count]) => count > 0)
              .map(([priority, count]) => (
                <span
                  key={priority}
                  className={`badge mono border-ink-600 bg-ink-800 ${
                    PRIORITY_TONE[priority] ?? "text-mist-400"
                  }`}
                >
                  {priority} {count}
                </span>
              ))}
            {feed.generated_at ? (
              <span className="mono text-meta text-mist-500">
                generated {new Date(feed.generated_at).toLocaleString()}
              </span>
            ) : null}
            {dismissed.length ? (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setDismissed([])}
              >
                Restore {dismissed.length} dismissed
              </button>
            ) : null}
          </div>
          <div className="space-y-4">
            {feed.sections.map((section) => (
              <section key={section.key}>
                <h3 className="label">{section.title}</h3>
                {section.cards.length ? (
                  <div className="mt-2 grid gap-2 lg:grid-cols-2">
                    {section.cards.map((card) => (
                      <div key={card.key} className="relative">
                        <FeedCardView card={card} />
                        {card.dismissible ? (
                          <button
                            type="button"
                            className="absolute right-2 top-2 text-meta text-mist-500 hover:text-mist-200"
                            title="Dismiss this card"
                            onClick={() =>
                              setDismissed((prev) =>
                                prev.includes(card.key) ? prev : [...prev, card.key]
                              )
                            }
                          >
                            ✕
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="mt-1 text-small text-mist-500">
                    {section.reason ?? "Nothing to show."}
                  </p>
                )}
              </section>
            ))}
          </div>
          {feed.gaps.length ? (
            <ul className="mt-4 space-y-0.5 border-t border-ink-700 pt-3">
              {feed.gaps.map((gap) => (
                <li key={gap} className="text-small text-mist-500">
                  {gap}
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      ) : null}

      {debrief ? (
        <Panel
          title="Game debrief"
          subtitle={
            "The guided review, read from this game's stored intelligence report. A section with no " +
            "stored evidence says so instead of being padded."
          }
          actions={
            <Link href={`/game/${debrief.game_id}`} className="btn btn-ghost">
              Open the board
            </Link>
          }
        >
          <div className="space-y-4">
            {debrief.sections.map((section) => (
              <section key={section.key}>
                <h3 className="label flex flex-wrap items-center gap-2">
                  {section.title}
                  {section.sample_size !== null ? (
                    <span className="mono text-meta text-mist-500">n={section.sample_size}</span>
                  ) : null}
                </h3>
                {section.available && section.observations.length ? (
                  <ul className="mt-1 space-y-1">
                    {section.observations.slice(0, 6).map((observation, index) => (
                      <li key={`${section.key}-${index}`} className="text-small text-mist-300">
                        {String(
                          observation.statement ??
                            observation.metric ??
                            observation.san ??
                            JSON.stringify(observation)
                        )}
                        {observation.ply !== undefined && observation.ply !== null ? (
                          <span className="mono ml-2 text-meta text-mist-500">
                            ply {String(observation.ply)}
                          </span>
                        ) : null}
                        {observation.classification ? (
                          <span className="mono ml-2 text-meta text-amber-300">
                            {String(observation.classification)}
                          </span>
                        ) : null}
                        {index < 3 && observation.ply !== undefined && observation.ply !== null ? (
                          <div className="mt-1">
                            <ShowMeWhy
                              claim={String(
                                observation.statement ?? observation.san ?? "this moment"
                              )}
                              gameId={debrief.game_id}
                              ply={Number(observation.ply)}
                            />
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-small text-mist-500">
                    {section.reason ?? "Nothing stored for this section."}
                  </p>
                )}
              </section>
            ))}
          </div>
          {debrief.focus.length ? (
            <div className="mt-4 border-t border-ink-700 pt-3">
              <h3 className="label">Prioritised focus</h3>
              <ul className="mt-1.5 space-y-1.5">
                {debrief.focus.map((item) => (
                  <li key={item.key} className="text-small text-mist-300">
                    <span className={PRIORITY_TONE[item.priority] ?? "text-mist-200"}>
                      [{item.priority}]
                    </span>{" "}
                    {item.title} — {item.statement}
                    <span className="mono ml-2 text-meta text-mist-500">
                      score {item.score} · n={item.sample_size}
                      {item.capped_by ? ` · capped by ${item.capped_by}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {debrief.gaps.length ? (
            <ul className="mt-3 space-y-0.5 border-t border-ink-700 pt-3">
              {debrief.gaps.map((gap) => (
                <li key={gap} className="text-small text-mist-500">
                  {gap}
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      ) : null}
    </div>
  );
}

// `useSearchParams` suspends during static prerender, so the workspace sits in a
// boundary. The fallback is a real loading state, not a blank frame.
export default function CoachTodayBoundary() {
  return (
    <Suspense fallback={<LoadingState label="Resolving your coaching context…" />}>
      <CoachToday />
    </Suspense>
  );
}
