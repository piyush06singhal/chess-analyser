"use client";

// The Caissa Coach: the Phase 7 agent's front door.
//
// This is not a chatbot. It is a tool-using chess intelligence system with a
// text box: every answer arrives with the evidence behind it, the claims that
// were machine-checked, the capabilities that were missing, and buttons that
// perform real navigation. The page makes those visible by default rather than
// hiding them behind a "trust me" paragraph.
//
// Three design choices worth stating:
//
// * **Board awareness is a first-class control.** Which game, which ply and
//   which player are explicit inputs. When they are set, "why is this bad?"
//   resolves without the user pasting a FEN (spec §7) — and when they are not,
//   the agent says what it cannot do instead of guessing.
// * **It works with no LLM configured.** The agent answers stored-fact
//   questions from tools and says plainly that it cannot write an explanation.
//   So the input is never disabled; the panel explains the state instead.
// * **The tool catalogue is shown.** Available and declared-but-unavailable
//   capabilities are both listed, with reasons, straight from the backend.

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { AgentAnswerCard } from "@/components/agent-answer";
import { ErrorState, LoadingState, StatusDot } from "@/components/empty-state";
import { Disclosure, Panel } from "@/components/ui";
import {
  api,
  ApiError,
  type AgentToolCatalog,
  type AgentTurnResponse,
  type GameListItem,
  type PlayerListItem,
} from "@/lib/api";

const MODES = ["coach", "beginner", "analyst", "advanced", "game_review", "player_coach"] as const;

const MODE_LABELS: Record<string, string> = {
  coach: "Coach",
  beginner: "Beginner",
  analyst: "Analyst",
  advanced: "Advanced",
  game_review: "Game review",
  player_coach: "Player coach",
};

/** Questions the planner and tools can genuinely answer — not a generic starter list. */
const EXAMPLES = [
  "Why was this move bad?",
  "What should I have played?",
  "What is the opening here?",
  "Where did the game turn?",
  "What is my biggest weakness?",
  "Can you predict my win probability?",
];

type Turn = {
  question: string;
  answer: AgentTurnResponse | null;
  error: string | null;
};

export function CoachConversation() {
  // Deep-link support: /coach?game_id=…&ply=… is how the game page hands over the
  // board the user is looking at, so the question is asked in place. The params are
  // read through `useSearchParams` and used as *initial* state, which is why this
  // component sits under a Suspense boundary: on the server it renders the
  // fallback, and on the client it renders once with the real params — no effect
  // that copies the URL into state, and no hydration mismatch.
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<{ configured: boolean; provider: string | null; model: string | null } | null>(
    null
  );
  const [catalog, setCatalog] = useState<AgentToolCatalog | null>(null);
  const [games, setGames] = useState<GameListItem[]>([]);
  const [players, setPlayers] = useState<PlayerListItem[]>([]);

  const [gameId, setGameId] = useState(() => searchParams.get("game_id") ?? "");
  const [ply, setPly] = useState(() => searchParams.get("ply") ?? "");
  const [playerId, setPlayerId] = useState(() => searchParams.get("player_id") ?? "");
  const [mode, setMode] = useState<string>(() => {
    const requested = searchParams.get("mode") ?? "";
    return (MODES as readonly string[]).includes(requested) ? requested : "coach";
  });

  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.coachStatus().then(setStatus).catch(() => setStatus(null));
    api.agentTools().then(setCatalog).catch(() => setCatalog(null));
    api
      .listGames()
      .then((payload) => setGames(payload.games))
      .catch(() => setGames([]));
    api
      .listPlayers()
      .then((payload) => setPlayers(payload.players))
      .catch(() => setPlayers([]));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, sending]);

  const ask = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || sending) return;
      setInput("");
      const history = turns.flatMap((turn) =>
        turn.answer
          ? [
              { role: "user", content: turn.question },
              { role: "assistant", content: turn.answer.message },
            ]
          : []
      );
      const placeholder: Turn = { question: text, answer: null, error: null };
      setTurns((prev) => [...prev, placeholder]);
      setSending(true);
      try {
        const answer = await api.agentAsk({
          question: text,
          game_id: gameId || null,
          ply: ply ? Number(ply) : null,
          player_id: playerId || null,
          mode,
          history,
          include_evidence: true,
        });
        setTurns((prev) =>
          prev.map((turn) => (turn === placeholder ? { ...turn, answer } : turn))
        );
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.message
            : "The agent request failed — is the backend running?";
        setTurns((prev) => prev.map((turn) => (turn === placeholder ? { ...turn, error: message } : turn)));
      } finally {
        setSending(false);
      }
    },
    [gameId, mode, playerId, ply, sending, turns]
  );

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    void ask(input);
  }

  const contextSummary = useMemo(() => {
    const parts: string[] = [];
    const game = games.find((entry) => entry.id === gameId);
    if (game) {
      parts.push(`${game.white_player} vs ${game.black_player}`);
      if (ply) parts.push(`move ${Math.ceil(Number(ply) / 2)}`);
    } else if (!gameId) {
      parts.push("no game selected");
    } else {
      parts.push(`game ${gameId.slice(0, 8)}…`);
    }
    if (ply) parts.push(`ply ${ply}`);
    const player = players.find((entry) => entry.id === playerId);
    if (player) parts.push(`player ${player.name}`);
    return parts.join(" · ");
  }, [gameId, games, playerId, players, ply]);

  // Three *different* gaps, told apart by the backend's own reason rather than by a
  // label invented here. Collapsing them would show "not yet" next to get_move_analysis
  // simply because no game was selected — which reads as "this does not work" when the
  // truth is "choose a game".
  const gaps = useMemo(() => {
    const tools = catalog ? catalog.tools.filter((tool) => !tool.available) : [];
    const needsGame: string[] = [];
    const needsPlayer: string[] = [];
    const notDeployed: { name: string; reason: string }[] = [];
    for (const tool of tools) {
      const reason = tool.reason ?? "Declared but unavailable in this deployment.";
      if (reason.includes("no active game")) needsGame.push(tool.name);
      else if (reason.includes("no active player")) needsPlayer.push(tool.name);
      else notDeployed.push({ name: tool.name, reason });
    }
    return { needsGame, needsPlayer, notDeployed };
  }, [catalog]);

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        {/* Conversation column */}
        <div className="flex min-h-[62vh] flex-col">
          {/* Context controls: the board the agent should reason about. */}
          <div className="card animate-fade-up mb-4 space-y-3 p-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="label">Context the agent will use</p>
              <span className="text-small text-mist-500">{contextSummary}</span>
            </div>
            <div className="grid gap-2.5 sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="text-small text-mist-400">Game</span>
                <select className="input" value={gameId} onChange={(e) => setGameId(e.target.value)}>
                  <option value="">No game (general questions)</option>
                  {games.map((game) => (
                    <option key={game.id} value={game.id}>
                      {game.white_player} vs {game.black_player} · {game.result} · {game.date ?? "—"} ·{" "}
                      {game.move_count} moves · {game.analysis_status}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-small text-mist-400">Selected ply</span>
                <input
                  className="input"
                  type="number"
                  min={1}
                  placeholder="e.g. 17"
                  value={ply}
                  onChange={(e) => setPly(e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-small text-mist-400">Player</span>
                <select className="input" value={playerId} onChange={(e) => setPlayerId(e.target.value)}>
                  <option value="">No player (no historical claims)</option>
                  {players.map((player) => (
                    <option key={player.id} value={player.id}>
                      {player.name} · {player.analyzed_games} analysed
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-small text-mist-400">Explanation style</span>
                <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
                  {MODES.map((entry) => (
                    <option key={entry} value={entry}>
                      {MODE_LABELS[entry] ?? entry}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          <div className="card animate-fade-up flex-1 space-y-4 overflow-y-auto p-4">
            {turns.length === 0 ? (
              <div className="flex h-full min-h-56 flex-col justify-center">
                <div className="flex flex-col items-center text-center">
                  <span
                    aria-hidden
                    className="flex h-12 w-12 items-center justify-center rounded-2xl border border-emerald-primary/40 bg-gradient-to-br from-emerald-primary/25 via-emerald-primary/10 to-violet-primary/20 text-2xl text-emerald-primary"
                  >
                    ♞
                  </span>
                  <h2 className="mt-3 text-lg font-semibold tracking-tight text-mist-50">
                    Ask about a game, a move or your own play
                  </h2>
                  <p className="mt-1.5 max-w-md text-body leading-relaxed text-mist-400">
                    Pick a game above (or open the coach from a game page) and the agent already knows which
                    board you mean. Its answers cite the tool that produced each fact.
                  </p>
                </div>
                <div className="mt-6 flex flex-wrap justify-center gap-2">
                  {EXAMPLES.map((example) => (
                    <button
                      key={example}
                      type="button"
                      className="chip"
                      onClick={() => void ask(example)}
                      disabled={sending}
                    >
                      {example}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              turns.map((turn, index) => (
                <div key={index} className="animate-fade-up space-y-3">
                  <div className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-sm border border-emerald-primary/40 bg-emerald-primary/15 px-4 py-2.5 text-body leading-relaxed text-mist-50">
                      {turn.question}
                    </div>
                  </div>
                  <div className="flex justify-start">
                    <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-ink-700 bg-ink-800 px-4 py-3">
                      {turn.error ? (
                        <ErrorState title="The agent could not answer" message={turn.error} />
                      ) : turn.answer ? (
                        <AgentAnswerCard answer={turn.answer} onFollowUp={(prompt) => void ask(prompt)} />
                      ) : (
                        <LoadingState label="Selecting tools and reading evidence…" />
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
            {sending && turns[turns.length - 1]?.answer ? (
              <div className="flex justify-start">
                <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-ink-700 bg-ink-800 px-4 py-3">
                  <LoadingState label="Selecting tools and reading evidence…" />
                </div>
              </div>
            ) : null}
            <div ref={bottomRef} />
          </div>

          <form onSubmit={onSubmit} className="mt-4 flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about a move, a game or your own play…"
              disabled={sending}
              className="input flex-1"
            />
            <button type="submit" className="btn btn-primary" disabled={sending || !input.trim()}>
              Ask
            </button>
          </form>
        </div>

        {/* Status rail */}
        <aside className="animate-fade-up space-y-4 self-start" style={{ animationDelay: "80ms" }}>
          <Panel title="Agent">
            <ul className="space-y-2.5">
              <li className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-2 text-small text-mist-400">
                  <StatusDot ok={status?.configured === true} pulse={status?.configured === true} />
                  Explanation model
                </span>
                <span className="mono min-w-0 truncate text-small text-mist-200">
                  {status?.configured ? status.model ?? status.provider ?? "configured" : "not configured"}
                </span>
              </li>
              <li className="flex items-center justify-between">
                <span className="text-small text-mist-400">Tools available</span>
                <span className="mono text-small text-mist-200">
                  {catalog ? `${catalog.available.length}/${catalog.count}` : "—"}
                </span>
              </li>
            </ul>
            {status && !status.configured ? (
              <p className="mt-3 border-t border-ink-700 pt-3 text-small leading-relaxed text-mist-400">
                No explanation model is configured, so the agent answers from tools and says so. Set{" "}
                <code className="mono rounded bg-ink-800 px-1">ARGUS_LLM_PROVIDER</code> to enable prose.
              </p>
            ) : null}
          </Panel>

          {catalog ? (
            <Disclosure
              summary={`Capabilities · ${catalog.available.length} available, ${catalog.count - catalog.available.length} not`}
            >
              <div className="space-y-2.5">
                {gaps.needsGame.length > 0 ? (
                  <div>
                    <p className="label">Available once a game is selected</p>
                    <ul className="mt-1.5 flex flex-wrap gap-1.5">
                      {gaps.needsGame.map((name) => (
                        <li key={name} className="badge mono border-ink-600 bg-ink-800 text-mist-400">
                          {name}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {gaps.needsPlayer.length > 0 ? (
                  <div>
                    <p className="label">Available once a player is selected</p>
                    <ul className="mt-1.5 flex flex-wrap gap-1.5">
                      {gaps.needsPlayer.map((name) => (
                        <li key={name} className="badge mono border-ink-600 bg-ink-800 text-mist-400">
                          {name}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {gaps.notDeployed.length > 0 ? (
                  <div>
                    <p className="label">Not in this deployment</p>
                    <ul className="mt-1.5 space-y-1">
                      {gaps.notDeployed.map((tool) => (
                        <li key={tool.name} className="text-small leading-snug text-mist-500">
                          <span className="mono text-mist-300">{tool.name}</span> — {tool.reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <div>
                  <p className="label">Available now</p>
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {catalog.available.map((name) => (
                      <li key={name} className="badge mono border-emerald-primary/30 bg-emerald-primary/10 text-emerald-300">
                        {name}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </Disclosure>
          ) : (
            <div className="space-y-2">
              <div className="skeleton h-4 w-2/3" />
              <div className="skeleton h-4 w-1/2" />
            </div>
          )}

          {games.length === 0 ? (
            <Panel title="No games yet">
              <p className="text-small leading-relaxed text-mist-400">
                The agent cites only games Caissa has stored and analysed.
              </p>
              <Link href="/import" className="btn btn-ghost mt-2.5 w-full">
                Import a game →
              </Link>
            </Panel>
          ) : null}

        </aside>
      </div>
    </div>
  );
}

// `useSearchParams` suspends during static prerender, so the conversation is
// wrapped in a boundary here. The fallback is a real skeleton, not a blank frame.
export default function CoachConversationBoundary() {
  return (
    <Suspense
      fallback={
        <div className="card p-4">
          <LoadingState label="Loading the coach…" />
        </div>
      }
    >
      <CoachConversation />
    </Suspense>
  );
}
