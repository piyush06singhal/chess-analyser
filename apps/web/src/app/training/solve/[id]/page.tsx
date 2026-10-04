"use client";

// The solver: one training exercise, played out.
//
// The answer is never in the page until the player has attempted the move — the
// backend does not send the solution to this view at all. Hints are progressive
// and come from stored evidence; "reveal" is an explicit action. After a move,
// the feedback is short by design: the evaluation difference and the reason, not
// a wall of engine variation.

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";

import { TrainingBoard } from "@/components/training-board";
import { EmptyState, ErrorState, LoadingState } from "@/components/empty-state";
import { Disclosure, Panel, Stat } from "@/components/ui";
import {
  api,
  ApiError,
  type TrainingAttemptFeedback,
  type TrainingContinueResult,
  type TrainingExplanation,
  type TrainingHintResponse,
  type TrainingPositionSummary,
  type TrainingSessionSummary,
} from "@/lib/api";

function outcomeTone(outcome: string): { label: string; className: string } {
  if (outcome === "correct") return { label: "Correct", className: "text-emerald-300" };
  if (outcome === "near_best") return { label: "Good move — also playable", className: "text-amber-300" };
  return { label: "Not the best choice", className: "text-rose-300" };
}

export default function TrainingSolvePage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const positionId = Number.parseInt(params?.id ?? "", 10);
  const playerId = searchParams.get("player") ?? "";
  const sessionParam = searchParams.get("session");

  const [position, setPosition] = useState<TrainingPositionSummary | null>(null);
  const [revealed, setRevealed] = useState<TrainingPositionSummary | null>(null);
  const [hints, setHints] = useState<TrainingHintResponse | null>(null);
  const [feedback, setFeedback] = useState<TrainingAttemptFeedback | null>(null);
  const [session, setSession] = useState<TrainingSessionSummary | null>(null);
  const [explanation, setExplanation] = useState<TrainingExplanation | null>(null);
  const [continuation, setContinuation] = useState<TrainingContinueResult | null>(null);
  const [continueInput, setContinueInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [startedAt] = useState(() => Date.now());

  const load = useCallback(async () => {
    if (!Number.isFinite(positionId)) {
      setError("Invalid exercise id");
      return;
    }
    try {
      const [detail, hintData] = await Promise.all([
        api.getTrainingPosition(positionId, playerId || undefined),
        api.getTrainingHints(positionId),
      ]);
      setPosition(detail);
      setHints(hintData);
      setFeedback(null);
      setRevealed(null);
      setExplanation(null);
      setContinuation(null);
      setContinueInput("");
      if (sessionParam) {
        api
          .getTrainingSession(Number(sessionParam), playerId)
          .then(setSession)
          .catch(() => setSession(null));
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load this exercise");
    }
  }, [positionId, playerId, sessionParam]);

  useEffect(() => {
    // Deferred: the loader sets state after its awaits, avoiding a synchronous
    // cascading render in the effect body.
    void Promise.resolve().then(() => load());
  }, [load]);

  const revealHint = useCallback(async () => {
    if (!hints) return;
    const nextIndex = Math.min(hints.hint_index + 1, hints.hints.length);
    try {
      const updated = await api.getTrainingHints(positionId, nextIndex);
      setHints(updated);
    } catch {
      /* a hint failing must not break the exercise */
    }
  }, [hints, positionId]);

  const submit = useCallback(
    async (uci: string) => {
      if (!playerId) {
        setNotice("Open training from the Training page so Caissa knows which player this is.");
        return;
      }
      setSubmitting(true);
      setNotice(null);
      try {
        const result = await api.submitTrainingAttempt(positionId, {
          player_id: playerId,
          submitted_uci: uci,
          session_id: sessionParam ? Number(sessionParam) : undefined,
          hints_used: hints?.hint_index ?? 0,
          response_time_ms: Date.now() - startedAt,
        });
        setFeedback(result);
        if (sessionParam) {
          api.getTrainingSession(Number(sessionParam), playerId).then(setSession).catch(() => undefined);
        }
      } catch (err) {
        setNotice(err instanceof ApiError ? err.message : "Could not submit the move");
      } finally {
        setSubmitting(false);
      }
    },
    [playerId, positionId, sessionParam, hints, startedAt]
  );

  const explain = useCallback(async () => {
    try {
      setExplanation(await api.explainTrainingPosition(positionId, playerId || undefined));
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Could not load the explanation");
    }
  }, [positionId, playerId]);

  const submitContinuation = useCallback(
    async (moves: string[]) => {
      if (!playerId || moves.length === 0) return;
      try {
        setContinuation(
          await api.continueTrainingLine(positionId, { player_id: playerId, moves })
        );
      } catch (err) {
        setNotice(err instanceof ApiError ? err.message : "Could not grade the continuation");
      }
    },
    [playerId, positionId]
  );

  const revealSolution = useCallback(async () => {
    try {
      const full = await api.revealTrainingSolution(positionId);
      setRevealed(full);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Could not reveal the solution");
    }
  }, [positionId]);

  const orientation = position?.side_to_move === "black" ? "black" : "white";

  const squareHighlights = useMemo(() => {
    const highlights: { square: string; color: string }[] = [];
    const full = revealed;
    if (full?.solution && full.fen) {
      // Highlight the from/to of the solution once it is revealed.
      const from = full.solution.uci.slice(0, 2);
      const to = full.solution.uci.slice(2, 4);
      highlights.push({ square: from, color: "rgba(52, 211, 153, 0.35)" });
      highlights.push({ square: to, color: "rgba(52, 211, 153, 0.45)" });
    }
    return highlights;
  }, [revealed]);

  if (error) {
    return <ErrorState title="Training exercise" message={error} />;
  }
  if (!position) {
    return <LoadingState label="Loading exercise…" />;
  }

  const origin = position.origin;
  const tone = feedback ? outcomeTone(feedback.outcome) : null;
  const showSolution = revealed?.solution ?? feedback?.solution ?? null;
  const pv = revealed?.principal_variation ?? feedback?.principal_variation ?? [];

  return (
    <div className="space-y-5">
      <section className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Coach</p>
          <h1 className="title mt-1">
            {position.side_to_move === "white" ? "White" : "Black"} to move
          </h1>
          <p className="subtitle mt-1">{position.source_reason || "Find the best move."}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`badge ${position.is_personalized ? "border-violet-primary/40 text-violet-300" : "border-sky-primary/40 text-sky-300"}`}>
            {position.is_personalized ? "Personalized" : "General"}
          </span>
          <span className="badge border-ink-600 text-mist-300">{position.category_label}</span>
          <span className="badge border-ink-600 text-mist-400">{position.difficulty}</span>
        </div>
      </section>

      {session ? (
        <div className="rounded-xl border border-ink-700 bg-ink-800/50 p-3 text-small text-mist-300">
          Session <span className="capitalize text-mist-100">{session.kind.replace("_", " ")}</span> ·{" "}
          {session.completed}/{session.planned} solved
          {session.remaining_position_ids.length > 1 ? (
            <span className="ml-2">
              · next exercise available
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,420px)]">
        <div className="space-y-3">
          <TrainingBoard
            fen={position.fen}
            orientation={orientation}
            disabled={submitting}
            onMove={(uci) => void submit(uci)}
            highlighted={squareHighlights}
          />
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn btn-ghost" onClick={() => void load()}>
              Reset
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => void revealHint()}
              disabled={!hints || hints.hint_index >= hints.hints.length}
            >
              Hint ({hints ? hints.hints.length - hints.hint_index : 0} left)
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => void explain()}>
              Why is this right?
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => void revealSolution()}>
              Reveal solution
            </button>
            {origin.game_id ? (
              <Link href={`/game/${origin.game_id}?ply=${origin.ply ?? 0}`} className="btn btn-ghost">
                View original game
              </Link>
            ) : null}
          </div>
        </div>

        <div className="space-y-3">
          {notice ? (
            <div className="rounded-xl border border-amber-primary/40 bg-amber-primary/10 p-3 text-small text-amber-200">
              {notice}
            </div>
          ) : null}

          {feedback ? (
            <Panel title="Feedback">
              <p className={`text-lg font-semibold ${tone?.className}`}>{tone?.label}</p>
              <p className="mt-1 text-small text-mist-300">{feedback.reason}</p>
              <dl className="mt-3 space-y-1 text-small text-mist-400">
                <div className="flex justify-between">
                  <dt>Your move</dt>
                  <dd className="text-mist-100">{feedback.submitted_san ?? feedback.submitted_uci}</dd>
                </div>
                {feedback.evaluation_delta_cp !== null ? (
                  <div className="flex justify-between">
                    <dt>Evaluation difference</dt>
                    <dd className="text-mist-100">{feedback.evaluation_delta_cp} cp</dd>
                  </div>
                ) : null}
                <div className="flex justify-between">
                  <dt>Review scheduled</dt>
                  <dd className="text-mist-100">
                    {feedback.next_review_at ? new Date(feedback.next_review_at).toLocaleDateString() : "—"}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt>State</dt>
                  <dd className="text-mist-100">{feedback.state}</dd>
                </div>
              </dl>
              {pv.length > 0 ? (
                <Disclosure summary="Engine line">
                  <p className="mono text-small text-mist-300">{pv.join(" ")}</p>
                </Disclosure>
              ) : null}
              <button type="button" className="btn btn-primary mt-3" onClick={() => void load()}>
                Try again
              </button>
            </Panel>
          ) : (
            <Panel title="Your move" subtitle="Play the best move on the board. The answer is not shown until you attempt it.">
              <p className="text-small text-mist-400">
                Drag a piece, or tap the piece and then the destination square.
              </p>
            </Panel>
          )}

          {hints && hints.revealed.length > 0 ? (
            <Panel title="Hints">
              <ul className="list-disc space-y-1 pl-4 text-small text-mist-300">
                {hints.revealed.map((hint) => (
                  <li key={hint}>{hint}</li>
                ))}
              </ul>
            </Panel>
          ) : null}

          {explanation ? (
            <Panel
              title="Why this is right"
              subtitle="Assembled from the stored analysis and board facts — no engine run, no invention."
            >
              <p className="text-small text-mist-200">{explanation.reason}</p>
              {explanation.board_facts.length > 0 ? (
                <p className="mt-2 text-small text-mist-400">
                  The move {explanation.board_facts.join(", and ")}.
                </p>
              ) : null}
              {explanation.recurring_pattern ? (
                <p className="mt-2 text-small text-violet-300">
                  {explanation.recurring_pattern.statement}
                </p>
              ) : null}
              {explanation.principal_variation.length > 0 ? (
                <Disclosure summary="Engine line">
                  <p className="mono text-small text-mist-300">
                    {explanation.principal_variation.join(" ")}
                  </p>
                </Disclosure>
              ) : null}
              <p className="mt-2 text-small text-mist-500">{explanation.note}</p>
            </Panel>
          ) : null}

          {revealed || showSolution ? (
            <Panel title="Solution">
              <div className="flex flex-wrap items-center gap-2">
                <span className="badge border-emerald-primary/40 text-emerald-300">
                  {showSolution?.san} ({showSolution?.uci})
                </span>
                {showSolution?.eval_mate ? (
                  <span className="badge border-ink-600 text-mist-300">mate in {Math.abs(showSolution.eval_mate)}</span>
                ) : showSolution?.eval_cp !== null && showSolution?.eval_cp !== undefined ? (
                  <span className="badge border-ink-600 text-mist-300">{(showSolution.eval_cp / 100).toFixed(2)}</span>
                ) : null}
              </div>
              {revealed?.played_move?.san ? (
                <p className="mt-2 text-small text-mist-400">
                  In the original game you played{" "}
                  <span className="text-mist-200">{revealed.played_move.san}</span>
                  {revealed.played_move.loss_cp !== null ? ` (${revealed.played_move.loss_cp} cp lost)` : ""}.
                </p>
              ) : null}
            </Panel>
          ) : null}

          {position.position_type === "continue_line" && revealed?.continuation_line?.length ? (
            <Panel
              title="Continue the line"
              subtitle="The opponent's replies come from the engine's stored line; play the engine's continuation moves yourself. Graded move by move — nothing is invented."
            >
              <p className="mono text-small text-mist-400">
                line: {revealed.continuation_line.join(" ")}
              </p>
              <p className="mt-2 text-small text-mist-400">
                Enter your moves in UCI (space-separated), e.g.{" "}
                <span className="mono text-mist-200">
                  {revealed.continuation_line.filter((_, index) => index % 2 === 1).join(" ") || "—"}
                </span>
                .
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <input
                  className="input flex-1"
                  value={continueInput}
                  onChange={(event) => setContinueInput(event.target.value)}
                  aria-label="Continuation moves (UCI)"
                  placeholder="g1f3 …"
                />
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() =>
                    void submitContinuation(
                      continueInput.trim().split(/\s+/).filter(Boolean)
                    )
                  }
                >
                  Submit continuation
                </button>
              </div>
              {continuation ? (
                <div className="mt-3">
                  <p
                    className={`text-small font-semibold ${continuation.outcome === "correct" ? "text-emerald-300" : "text-rose-300"}`}
                  >
                    {continuation.correct_moves}/{continuation.expected_moves} continuation moves matched
                  </p>
                  <ul className="mt-2 space-y-1 text-small">
                    {continuation.moves.map((move) => (
                      <li key={move.ply} className="flex items-center justify-between gap-3">
                        <span className="text-mist-400">move {move.ply}</span>
                        <span className={move.correct ? "text-emerald-300" : "text-rose-300"}>
                          {move.submitted_san ?? move.submitted_uci ?? "—"}
                          {move.correct ? " ✓" : ` (engine: ${move.expected_san ?? move.expected_uci})`}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-small text-mist-500">{continuation.note}</p>
                </div>
              ) : null}
            </Panel>
          ) : null}

          <Panel title="Position facts">
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Attempts" value={position.attempts} hint={`${position.correct_attempts} correct`} />
              <Stat label="Streak" value={position.streak} hint={position.state} />
            </div>
            <Disclosure summary="Provenance">
              <p className="text-small text-mist-400">
                Engine {position.engine}
                {position.engine_version ? ` ${position.engine_version}` : ""} · depth {position.depth} · analysis{" "}
                {position.analysis_version} · methodology {position.methodology_version}.
              </p>
            </Disclosure>
          </Panel>
        </div>
      </div>

      {!playerId ? (
        <EmptyState
          title="No player selected"
          message="Open this exercise from the Training page so Caissa can attribute the attempt."
          action={<Link href="/training" className="btn btn-primary">Go to Training</Link>}
        />
      ) : null}
    </div>
  );
}
