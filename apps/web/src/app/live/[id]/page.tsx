"use client";

// The live game room (§37): board, clocks, move list, status and controls.
//
// The board is a view of the server's state and nothing more. Every action —
// move, resign, draw, start — is a REST call with an ordinary status, and the
// page re-renders from whatever the server returns. A WebSocket carries the
// events so an opponent's move appears without polling; if the socket drops, the
// page says so and re-syncs from the server rather than pretending to be live.

import { Chess } from "chess.js";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState, ErrorState, LoadingState, StatusDot } from "@/components/empty-state";
import { LiveBoard } from "@/components/live-board";
import { Panel } from "@/components/ui";
import {
  ApiError,
  getLiveGame,
  liveAbort,
  liveAcceptDraw,
  liveCoach,
  liveDeclineDraw,
  liveInvite,
  liveMove,
  liveOfferDraw,
  livePgn,
  liveResign,
  liveStart,
  liveSocketUrl,
  liveSync,
  type LiveCoachAnswer,
  type LiveGamePayload,
} from "@/lib/api";

const TERMINAL = new Set(["finished", "resigned", "timeout", "draw_agreed", "aborted"]);

function formatClock(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function uciToSquares(uci: string): { from: string; to: string } | null {
  if (uci.length < 4) return null;
  return { from: uci.slice(0, 2), to: uci.slice(2, 4) };
}

function checkSquareFrom(fen: string): string | null {
  try {
    const chess = new Chess(fen);
    if (!chess.isCheck()) return null;
    const turn = chess.turn();
    for (const row of chess.board()) {
      for (const square of row) {
        if (square && square.type === "k" && square.color === turn) return square.square;
      }
    }
  } catch {
    return null;
  }
  return null;
}

type ConnState = "connecting" | "connected" | "reconnecting" | "closed";

function Room() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const gameId = params.id;
  const playerId = Number(searchParams.get("player_id")) || undefined;

  const [state, setState] = useState<LiveGamePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [coach, setCoach] = useState<LiveCoachAnswer | null>(null);
  const [coachBusy, setCoachBusy] = useState(false);
  const [invite, setInvite] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const lastSequence = useRef(0);
  const socketRef = useRef<WebSocket | null>(null);

  const applyState = useCallback((next: LiveGamePayload) => {
    setState(next);
    lastSequence.current = Math.max(lastSequence.current, next.sequence);
  }, []);

  const reload = useCallback(async () => {
    try {
      const payload = await getLiveGame(gameId, playerId);
      applyState(payload);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the game.");
    }
  }, [gameId, playerId, applyState]);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const payload = await getLiveGame(gameId, playerId);
        if (!cancelled) applyState(payload);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load the game.");
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [gameId, playerId, applyState]);

  // The event stream. On connect the server sends the difference the client
  // missed first; a gap it cannot fill arrives as `resync`, and the page falls
  // back to a full state fetch rather than skipping events.
  useEffect(() => {
    if (!gameId) return;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      const socket = new WebSocket(liveSocketUrl(gameId, playerId ?? null, lastSequence.current));
      socketRef.current = socket;
      setConn((current) => (current === "closed" ? "reconnecting" : "connecting"));

      socket.onopen = () => setConn("connected");
      socket.onmessage = (event) => {
        let message: { type?: string; [key: string]: unknown };
        try {
          message = JSON.parse(event.data as string);
        } catch {
          return;
        }
        if (message.type === "sync") {
          if (message.resync) void reload();
          const events = (message.events as { sequence_number: number }[] | undefined) ?? [];
          for (const item of events) lastSequence.current = Math.max(lastSequence.current, item.sequence_number);
          void reload();
        } else if (message.type === "event") {
          const envelope = message.event as { sequence_number: number };
          if (envelope.sequence_number > lastSequence.current + 1) {
            // A gap: ask the server to resynchronize.
            void liveSync(gameId, lastSequence.current, playerId).then((sync) => {
              if (sync.resync) void reload();
              lastSequence.current = Math.max(lastSequence.current, sync.server_sequence);
              void reload();
            });
          } else {
            lastSequence.current = envelope.sequence_number;
            void reload();
          }
        }
      };
      socket.onclose = () => {
        if (closed) return;
        setConn("reconnecting");
        retry = setTimeout(connect, 1500);
      };
      socket.onerror = () => socket.close();
    };

    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      socketRef.current?.close();
    };
  }, [gameId, playerId, reload]);

  const orientation = useMemo<"white" | "black">(() => {
    if (!state) return "white";
    if (playerId && state.players.white.player_id === playerId) return "white";
    if (playerId && state.players.black.player_id === playerId) return "black";
    return "white";
  }, [state, playerId]);

  const mySide = useMemo<"white" | "black" | null>(() => {
    if (!state || !playerId) return null;
    if (state.players.white.player_id === playerId) return "white";
    if (state.players.black.player_id === playerId) return "black";
    return null;
  }, [state, playerId]);

  const canAct = Boolean(state && mySide && !TERMINAL.has(state.status) && state.viewer !== "spectator");
  const myTurn =
    Boolean(state && mySide && state.side_to_move === mySide && state.status === "active") &&
    state?.turn_owner === "player";

  async function act(action: () => Promise<{ state: LiveGamePayload }>) {
    if (!state) return;
    setPending(true);
    setNotice(null);
    try {
      const result = await action();
      applyState(result.state);
    } catch (err) {
      if (err instanceof ApiError) {
        setNotice(`${err.code}: ${err.message}`);
        // A refused move means the board is stale; take the server's word for it.
        void reload();
      } else {
        setNotice("That action could not be completed.");
      }
    } finally {
      setPending(false);
    }
  }

  function onMove(uci: string) {
    if (!state || !playerId) return;
    void act(() => liveMove(state.game_id, playerId, uci, state.version));
  }

  async function askCoach() {
    if (!state || !playerId) return;
    setCoachBusy(true);
    try {
      setCoach(await liveCoach(state.game_id, playerId));
    } catch (err) {
      setCoach(null);
      setNotice(err instanceof ApiError ? err.message : "The coach could not answer.");
    } finally {
      setCoachBusy(false);
    }
  }

  async function showInvite() {
    if (!state || !playerId) return;
    try {
      const payload = await liveInvite(state.game_id, playerId);
      setInvite(`${typeof window !== "undefined" ? window.location.origin : ""}/live/${state.game_id}?invite=${payload.invite_token}`);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "No invitation available.");
    }
  }

  async function copyPgn() {
    if (!state) return;
    try {
      const payload = await livePgn(state.game_id, playerId);
      await navigator.clipboard?.writeText(payload.pgn);
      setNotice("PGN copied to the clipboard.");
    } catch {
      setNotice("Could not export the PGN.");
    }
  }

  if (error) {
    return <ErrorState title="Live game" message={error} />;
  }
  if (!state) {
    return <LoadingState label="Loading the game…" />;
  }

  const lastMove = state.moves.length ? uciToSquares(state.moves[state.moves.length - 1].uci) : null;
  const checkSquare = checkSquareFrom(state.current_fen);
  const connLabel =
    conn === "connected" ? "Connected" : conn === "connecting" ? "Connecting" : conn === "reconnecting" ? "Reconnecting" : "Disconnected";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow">Live game · {state.mode.replace(/_/g, " ")}</p>
          <h1 className="title mt-1">
            {state.players.white.name ?? "Open seat"} <span className="text-mist-500">vs</span>{" "}
            {state.players.black.name ?? "Open seat"}
          </h1>
          <p className="subtitle mt-1">
            {state.status} · {state.clock_config.base_ms / 60000}+
            {state.clock_config.increment_ms / 1000} · {state.visibility}
            {state.rated ? " · rated" : ""}
          </p>
        </div>
        <div className="flex items-center gap-2 text-small text-mist-400">
          <StatusDot ok={conn === "connected"} pulse={conn === "connected"} />
          {connLabel}
          <span className="mono text-meta text-mist-500">v{state.version}</span>
        </div>
      </div>

      {notice ? <ErrorState title="Notice" message={notice} /> : null}

      {/* §18: more than one open tab for this side is reported, not hidden. The
          game stays authoritative; the warning just explains why two tabs can
          fight over the clock. */}
      {mySide && state.session_counts[mySide] > 1 ? (
        <p className="rounded-xl border border-amber-primary/40 bg-amber-primary/10 px-3 py-2 text-small text-amber-200">
          This game is open in {state.session_counts[mySide]} tabs. Moves from any of them are
          validated the same way, but only one tab should be used to play.
        </p>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,320px)]">
        <div className="space-y-3">
          <div className="flex items-center justify-between rounded-xl border border-ink-700 bg-ink-850 px-3 py-2">
            <span className="text-small text-mist-300">
              {state.players[orientation === "white" ? "black" : "white"].name ?? "Open seat"}
            </span>
            <span className={`mono text-base ${state.clock.turn_side === (orientation === "white" ? "black" : "white") ? "text-emerald-300" : "text-mist-200"}`}>
              {formatClock(orientation === "white" ? state.clock.black_ms : state.clock.white_ms)}
            </span>
          </div>

          <LiveBoard
            fen={state.current_fen}
            version={state.version}
            orientation={orientation}
            disabled={!canAct || !myTurn || pending}
            pending={pending}
            onMove={onMove}
            lastMove={lastMove}
            checkSquare={checkSquare}
          />

          <div className="flex items-center justify-between rounded-xl border border-ink-700 bg-ink-850 px-3 py-2">
            <span className="text-small text-mist-300">
              {state.players[orientation].name ?? "Open seat"}
              {mySide ? <span className="ml-2 text-meta text-mist-500">(you)</span> : null}
            </span>
            <span className={`mono text-base ${state.clock.turn_side === orientation ? "text-emerald-300" : "text-mist-200"}`}>
              {formatClock(orientation === "white" ? state.clock.white_ms : state.clock.black_ms)}
            </span>
          </div>

          <div className="flex flex-wrap gap-2">
            {state.status === "ready" && canAct ? (
              <button className="btn-primary" disabled={pending} onClick={() => void act(() => liveStart(state.game_id, playerId!))}>
                Start clock
              </button>
            ) : null}
            {state.status === "waiting" ? (
              <button className="btn" onClick={() => void showInvite()}>
                Show invite link
              </button>
            ) : null}
            {state.status === "active" && canAct ? (
              <>
                <button className="btn" disabled={pending} onClick={() => void act(() => liveOfferDraw(state.game_id, playerId!))}>
                  Offer draw
                </button>
                <button className="btn" disabled={pending} onClick={() => void act(() => liveResign(state.game_id, playerId!))}>
                  Resign
                </button>
              </>
            ) : null}
            {state.draw_offer && state.draw_offer !== mySide && canAct ? (
              <>
                <button className="btn-primary" disabled={pending} onClick={() => void act(() => liveAcceptDraw(state.game_id, playerId!))}>
                  Accept draw
                </button>
                <button className="btn" disabled={pending} onClick={() => void act(() => liveDeclineDraw(state.game_id, playerId!))}>
                  Decline
                </button>
              </>
            ) : null}
            {!TERMINAL.has(state.status) && canAct && state.status !== "active" && state.status !== "ready" ? (
              <button className="btn" disabled={pending} onClick={() => void act(() => liveAbort(state.game_id, playerId!))}>
                Abort
              </button>
            ) : null}
            <button className="btn" onClick={() => void copyPgn()}>
              Copy PGN
            </button>
          </div>

          {invite ? (
            <div className="rounded-xl border border-ink-700 bg-ink-850 p-3">
              <p className="label">Invite link (single use, expires)</p>
              <p className="mono mt-1 break-all text-meta text-mist-300">{invite}</p>
            </div>
          ) : null}
        </div>

        <div className="space-y-5">
          <Panel title="Coach" subtitle={state.permissions.competitive ? state.permissions.refusal ?? undefined : `Analysis mode: ${state.analysis_mode}`}>
            {state.permissions.may_give_engine_moves ? (
              <div className="space-y-3">
                <button className="btn-primary w-full" disabled={coachBusy} onClick={() => void askCoach()}>
                  {coachBusy ? "Analysing…" : "Analyse this position"}
                </button>
                {coach?.analysis?.available ? (
                  <div className="space-y-2">
                    <p className="text-small text-mist-200">
                      Best move: <span className="mono">{coach.analysis.best_move_san ?? "—"}</span>
                    </p>
                    <ul className="space-y-1">
                      {coach.analysis.lines?.map((line) => (
                        <li key={line.rank} className="mono text-meta text-mist-400">
                          {line.move_san} {line.mate != null ? `#${line.mate}` : `${(line.cp ?? 0) / 100 >= 0 ? "+" : ""}${((line.cp ?? 0) / 100).toFixed(2)}`}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : coach?.analysis && !coach.analysis.available ? (
                  <p className="text-small text-mist-400">{coach.analysis.detail ?? coach.analysis.reason}</p>
                ) : null}
              </div>
            ) : (
              <div className="space-y-2">
                {coach?.hint ? <p className="text-small text-mist-200">{coach.hint}</p> : null}
                <p className="text-small text-mist-400">
                  {coach?.message ?? state.permissions.refusal ?? "The coach offers general guidance, not a move, in this game."}
                </p>
                <button className="btn w-full" disabled={coachBusy} onClick={() => void askCoach()}>
                  Ask for a hint
                </button>
              </div>
            )}
          </Panel>

          <Panel title="Moves" subtitle={`${state.moves.length} ply${state.moves.length === 1 ? "" : "s"}`}>
            {state.moves.length === 0 ? (
              <p className="text-small text-mist-500">No moves yet.</p>
            ) : (
              <ol className="max-h-[360px] space-y-0.5 overflow-auto">
                {state.moves.map((move) => (
                  <li key={move.ply} className="flex items-baseline justify-between text-small text-mist-300">
                    <span className="mono">
                      {move.move_number}
                      {move.side === "white" ? "." : "…"} {move.san}
                    </span>
                    <span className="mono text-meta text-mist-500">
                      {formatClock(move.clock_after?.white_ms ?? state.clock_config.base_ms)} ·{" "}
                      {formatClock(move.clock_after?.black_ms ?? state.clock_config.base_ms)}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </Panel>

          {state.library_game_id ? (
            <Panel title="Post-game">
              <p className="text-small text-mist-300">
                This game is in your library. The full analysis, debrief and training extraction are
                there.
              </p>
              <div className="mt-2 flex gap-2">
                <Link className="link" href={`/game/${state.library_game_id}`}>
                  open analysis
                </Link>
                <Link className="link" href={`/coach?tab=today&game_id=${state.library_game_id}`}>
                  open debrief
                </Link>
              </div>
            </Panel>
          ) : null}

          <Panel title="This game">
            <dl className="space-y-1.5">
              <div className="flex justify-between">
                <dt className="text-meta text-mist-500">Result</dt>
                <dd className="mono text-small text-mist-200">
                  {state.result} {state.result_reason ? `(${state.result_reason})` : ""}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-meta text-mist-500">Training mode</dt>
                <dd className="text-small text-mist-300">{state.training_mode || "—"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-meta text-mist-500">You</dt>
                <dd className="text-small text-mist-300">{mySide ?? state.viewer}</dd>
              </div>
            </dl>
          </Panel>
        </div>
      </div>

      {TERMINAL.has(state.status) && !state.library_game_id ? (
        <EmptyState
          title="Game over"
          message="The post-game pipeline runs once the game is saved to your library; refresh in a moment."
          icon="⚑"
        />
      ) : null}
    </div>
  );
}

export default function LiveGamePage() {
  return (
    <Suspense fallback={<LoadingState label="Loading the game…" />}>
      <Room />
    </Suspense>
  );
}
