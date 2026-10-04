"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type {
  PlatformImportResponse,
  PlatformMonthResponse,
  PlatformPlayerResponse,
  PlatformSourceId,
  PgnValidationResponse,
  ValidationIssue,
} from "@/lib/api";
import { ErrorState, LoadingState } from "@/components/empty-state";
import { AnalysisStatusBadge } from "@/components/analysis-status-badge";
import { Chip, Panel, Tabs } from "@/components/ui";
import { plural } from "@/lib/text";

type Mode = "paste" | "upload" | "platform";
type Busy = "idle" | "validating" | "importing";

/** What each platform source needs the UI to know. Nothing here is decorative: */
/** the time-control list, the wording and the month semantics all differ.      */
const PLATFORMS: Record<
  PlatformSourceId,
  {
    label: string;
    placeholder: string;
    timeClasses: string[];
    /** True when the platform publishes an index of months that contain games. */
    publishesArchives: boolean;
    monthsNote: string;
  }
> = {
  chess_com: {
    label: "Chess.com",
    placeholder: "Chess.com username, e.g. hikaru",
    timeClasses: ["all", "bullet", "blitz", "rapid", "daily"],
    publishesArchives: true,
    monthsNote: "Chess.com publishes which months have games.",
  },
  lichess: {
    label: "Lichess",
    placeholder: "Lichess username, e.g. thibault",
    timeClasses: ["all", "bullet", "blitz", "rapid", "classical", "correspondence"],
    publishesArchives: false,
    monthsNote: "Lichess publishes every finished game; pick the month you want.",
  },
};

function IssueList({ validation }: { validation: PgnValidationResponse }) {
  if (validation.is_valid) {
    return (
      <div className="animate-fade-in rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-3.5 py-2.5 text-sm text-emerald-200">
        Valid PGN · {validation.game_count} game{validation.game_count === 1 ? "" : "s"} ·{" "}
        {validation.ply_count} plies
      </div>
    );
  }
  return (
    <div className="animate-fade-in rounded-lg border border-rose-500/25 bg-rose-500/10 px-3.5 py-2.5">
      <p className="text-sm font-medium text-rose-300">Could not import this PGN.</p>
      <ul className="mt-2 space-y-1">
        {validation.issues.map((issue: ValidationIssue, i) => (
          <li key={i} className="flex gap-2 text-xs text-rose-200">
            <span className="mono text-rose-300">{issue.type}</span>
            <span>
              {issue.message}
              {issue.move_number ? ` (move ${issue.move_number})` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- platform connect (Chess.com, Lichess) ---------------------------------------

function PlatformPanel({
  source,
  initialUsername = "",
}: {
  source: PlatformSourceId;
  initialUsername?: string;
}) {
  const platform = PLATFORMS[source];
  const router = useRouter();
  const [username, setUsername] = useState(initialUsername);
  const [player, setPlayer] = useState<PlatformPlayerResponse | null>(null);
  const [monthIndex, setMonthIndex] = useState(0);
  const [month, setMonth] = useState<PlatformMonthResponse | null>(null);
  const [timeClass, setTimeClass] = useState<string>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [analyze, setAnalyze] = useState(true);
  const [depth, setDepth] = useState(12);
  const [connecting, setConnecting] = useState(false);
  const [loadingMonth, setLoadingMonth] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PlatformImportResponse | null>(null);
  const deepLinked = useRef(false);

  const loadMonth = useCallback(
    async (response: PlatformPlayerResponse, index: number, filter: string) => {
      const archive = response.months[index];
      if (!archive) return;
      setLoadingMonth(true);
      setError(null);
      setSelected(new Set());
      try {
        setMonth(
          await api.getSourceGames(
            source,
            response.profile.username,
            archive.year,
            archive.month,
            filter === "all" ? undefined : filter
          )
        );
      } catch (err) {
        setMonth(null);
        setError(err instanceof ApiError ? err.message : "Could not load that month.");
      } finally {
        setLoadingMonth(false);
      }
    },
    [source]
  );

  const connect = useCallback(
    async (handle: string, filter: string = "all") => {
      const clean = handle.trim().replace(/^@/, "");
      if (!clean) return;
      setConnecting(true);
      setError(null);
      setResult(null);
      setPlayer(null);
      setMonth(null);
      try {
        const response = await api.getSourcePlayer(source, clean, 3);
        setPlayer(response);
        setMonthIndex(0);
        await loadMonth(response, 0, filter);
      } catch (err) {
        setError(
          err instanceof ApiError
            ? err.message
            : `Could not reach ${platform.label}. Check your connection and try again.`
        );
      } finally {
        setConnecting(false);
      }
    },
    [loadMonth, platform.label, source]
  );

  // Deep link from the dashboard: /import?chesscom=<username> looks the player up
  // straight away instead of making the user press Connect twice. `initialUsername`
  // already seeds the field, so the effect only has to start the request — and it
  // does so in a microtask, keeping the effect body free of synchronous state
  // updates (React's guidance) while still fetching on deep link.
  useEffect(() => {
    if (deepLinked.current || !initialUsername) return;
    deepLinked.current = true;
    Promise.resolve().then(() => void connect(initialUsername));
  }, [initialUsername, connect]);

  function toggle(url: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  }

  const selectable = (month?.games ?? []).filter((game) => !game.already_imported);

  function toggleAll() {
    setSelected((current) =>
      current.size === selectable.length
        ? new Set()
        : new Set(selectable.map((game) => game.url))
    );
  }

  async function runImport() {
    if (!player || !month || selected.size === 0) return;
    setImporting(true);
    setError(null);
    setResult(null);
    try {
      const response = await api.importSourceGames(source, {
        username: player.profile.username,
        year: month.year,
        month: month.month,
        game_urls: [...selected],
        analyze,
        depth,
      });
      setResult(response);
      await loadMonth(player, monthIndex, timeClass);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Import failed.");
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="space-y-4">      <Panel
        title={`Connect a ${platform.label} account`}
        subtitle="Public games only — Caissa reads, never writes, and needs no password."
      >
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void connect(username);
          }}
        >
          <input
            className="input flex-1 basis-64"
            placeholder={platform.placeholder}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            spellCheck={false}
            autoComplete="off"
            aria-label={`${platform.label} username`}
          />
          <button type="submit" className="btn btn-primary" disabled={connecting || !username.trim()}>
            {connecting ? "Connecting…" : "Connect"}
          </button>
        </form>

        {error ? (
          <div className="mt-3">
            <ErrorState title={`${platform.label} lookup failed`} message={error} />
          </div>
        ) : null}
        {connecting ? <LoadingState label={`Reading public games from ${platform.label}…`} /> : null}

        {player ? (
          <div className="mt-4 flex flex-wrap items-center gap-4 rounded-xl border border-ink-700 bg-ink-800/50 p-3.5">
            {player.profile.avatar ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={player.profile.avatar}
                alt=""
                className="h-11 w-11 rounded-lg border border-ink-600 object-cover"
              />
            ) : (
              <div className="flex h-11 w-11 items-center justify-center rounded-lg border border-ink-600 bg-ink-750 text-lg text-mist-500">
                ♟
              </div>
            )}
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-mist-50">
                {player.profile.title ? (
                  <span className="badge bg-amber-400/15 text-amber-300">{player.profile.title}</span>
                ) : null}
                {player.profile.username}
                {player.is_closed ? (
                  <span className="badge bg-rose-400/15 text-rose-300">account closed</span>
                ) : null}
              </p>
              <p className="mono mt-0.5 text-small text-mist-500">
                {typeof player.profile.followers === "number"
                  ? `${player.profile.followers.toLocaleString()} followers · `
                  : ""}
                {typeof player.profile.total_games === "number"
                  ? `${player.profile.total_games.toLocaleString()} games · `
                  : ""}
                {player.profile.ratings && Object.keys(player.profile.ratings).length > 0
                  ? `${Object.entries(player.profile.ratings)
                      .slice(0, 3)
                      .map(([speed, rating]) => `${speed} ${rating}`)
                      .join(" · ")} · `
                  : ""}
                {player.profile.joined
                  ? `joined ${player.profile.joined.slice(0, 10)}`
                  : player.profile.created_at
                    ? `joined ${player.profile.created_at.slice(0, 10)}`
                    : "join date not published"}
              </p>
            </div>
            {player.profile.url ? (
              <a
                href={player.profile.url}
                target="_blank"
                rel="noreferrer noopener"
                className="btn btn-ghost"
              >
                Profile ↗
              </a>
            ) : null}
          </div>
        ) : null}
      </Panel>

      {player && player.months.length > 0 ? (
        <Panel
          title="Select games"
          subtitle={`Only standard chess is listed — variants are excluded and counted. ${platform.monthsNote}`}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <select
                className="input w-auto py-1 text-xs"
                aria-label="Month"
                value={monthIndex}
                onChange={(event) => {
                  const index = Number(event.target.value);
                  setMonthIndex(index);
                  void loadMonth(player, index, timeClass);
                }}
              >
                {player.months.map((archive, index) => (
                  <option key={`${archive.year}-${archive.month}`} value={index}>
                    {archive.label}
                  </option>
                ))}
              </select>
              <select
                className="input w-auto py-1 text-xs"
                aria-label="Time control"
                value={timeClass}
                onChange={(event) => {
                  setTimeClass(event.target.value);
                  void loadMonth(player, monthIndex, event.target.value);
                }}
              >
                {platform.timeClasses.map((value) => (
                  <option key={value} value={value}>
                    {value === "all" ? "All time controls" : value}
                  </option>
                ))}
              </select>
            </div>
          }
          bodyClassName="p-0"
        >
          {loadingMonth ? (
            <LoadingState label="Loading that month…" />
          ) : month && month.games.length > 0 ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 px-4 py-2.5">
                <button type="button" className="btn btn-ghost" onClick={toggleAll}>
                  {selected.size === selectable.length && selectable.length > 0
                    ? "Clear selection"
                    : `Select all (${selectable.length})`}
                </button>
                <span className="text-small text-mist-500">
                  {month.count} game{month.count === 1 ? "" : "s"}
                  {month.imported_count > 0 ? ` · ${month.imported_count} already imported` : ""}
                  {month.truncated ? " · showing the newest 50" : ""}
                </span>
              </div>

              <ul className="max-h-[26rem] divide-y divide-ink-800 overflow-y-auto">
                {month.games.map((game) => {
                  const checked = selected.has(game.url);
                  return (
                    <li
                      key={game.url}
                      className={`flex items-center gap-3 px-4 py-2.5 transition-colors ${
                        game.already_imported ? "opacity-60" : "hover:bg-ink-800/40"
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="h-4 w-4 shrink-0 rounded accent-emerald-500"
                        checked={checked}
                        disabled={game.already_imported}
                        onChange={() => toggle(game.url)}
                        aria-label={`Select ${game.white_username} vs ${game.black_username}`}
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[13px] text-mist-100">
                          {game.white_username ?? "?"}{" "}
                          <span className="mono text-small text-mist-500">
                            {game.white_rating ?? "—"}
                          </span>
                          <span className="mx-1.5 text-mist-600">vs</span>
                          {game.black_username ?? "?"}{" "}
                          <span className="mono text-small text-mist-500">
                            {game.black_rating ?? "—"}
                          </span>
                        </p>
                        <p className="mono mt-0.5 text-small text-mist-600">
                          {[
                            game.time_class,
                            game.rated === false ? "unrated" : null,
                            game.eco_code,
                            game.opening_name,
                            (game.end_time ?? game.played_at)?.slice(0, 10) ?? null,
                            game.ply_count ? `${game.ply_count} plies` : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                      </div>
                      <span className="badge mono shrink-0 border-ink-600 bg-ink-800 text-mist-200">
                        {game.result ?? "*"}
                      </span>
                      {game.already_imported ? (
                        <span className="badge shrink-0 bg-emerald-400/15 text-emerald-300">
                          in library
                        </span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-700 bg-ink-900/40 px-4 py-3">
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-small text-mist-300">
                  <label className="flex cursor-pointer items-center gap-2">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded accent-emerald-500"
                      checked={analyze}
                      onChange={(event) => setAnalyze(event.target.checked)}
                    />
                    Analyze with Stockfish
                  </label>
                  <label className={`flex items-center gap-2 ${analyze ? "" : "text-mist-600"}`}>
                    Depth
                    <input
                      type="number"
                      min={6}
                      max={22}
                      value={depth}
                      disabled={!analyze}
                      onChange={(event) => setDepth(Number(event.target.value))}
                      className="input w-16 px-2 py-1 text-center text-xs disabled:opacity-50"
                    />
                  </label>
                  <span className="text-small text-mist-500">
                    {analyze
                      ? "queued per game after import"
                      : "you can analyze any game later from its page"}
                  </span>
                </div>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={selected.size === 0 || importing}
                  onClick={runImport}
                >
                  {importing
                    ? "Importing…"
                    : `Import ${selected.size || ""} game${selected.size === 1 ? "" : "s"}`}
                </button>
              </div>
              <p className="px-4 py-2 text-small text-mist-600">{month.note}</p>
            </>
          ) : (
            <div className="px-4 py-8 text-center text-sm text-mist-500">
              No standard games published in this month.
            </div>
          )}
        </Panel>
      ) : null}

      {result ? (
        <Panel
          title="Import result"
          subtitle={`${result.counts.imported} imported · ${result.counts.skipped} already in library · ${result.counts.failed} failed`}
        >
          {result.imported.length > 0 ? (
            <div className="space-y-2">
              {result.imported.map((entry) => (
                <div
                  key={entry.game_id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2"
                >
                  <span className="text-small text-mist-200">
                    {plural(entry.moves, "move")} stored
                    {entry.queued_for_analysis ? " · analysis queued" : ""}
                  </span>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => router.push(`/game/${entry.game_id}`)}
                  >
                    Open game →
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          {result.skipped.length > 0 ? (
            <ul className="mt-3 space-y-1">
              {result.skipped.map((entry) => (
                <li key={entry.url} className="flex items-center justify-between gap-3 text-small">
                  <span className="text-mist-500">Already in your library — skipped.</span>
                  <button
                    type="button"
                    className="text-emerald-300 underline decoration-emerald-400/30 underline-offset-2"
                    onClick={() => router.push(`/game/${entry.game_id}`)}
                  >
                    open
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {result.failed.length > 0 ? (
            <ul className="mt-3 space-y-1">
              {result.failed.map((entry) => (
                <li key={entry.url} className="text-small text-rose-300">
                  {entry.error} <span className="mono text-rose-300">({entry.code})</span>
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      ) : null}
    </div>
  );
}

// --- page -------------------------------------------------------------------------

function ImportPageInner() {
  const router = useRouter();
  const params = useSearchParams();
  // The source can be chosen by a link: ?chesscom=<handle> / ?lichess=<handle> (from
  // the dashboard) or ?source=chesscom|lichess (from the nav). Until the user picks a
  // tab explicitly, the link decides — no effect, no flash of the wrong tab.
  const requested = params.get("source");
  const linkedSource: PlatformSourceId = requested === "lichess" ? "lichess" : "chess_com";
  const deepLink =
    params.get("username") ?? params.get("chesscom") ?? params.get("lichess") ?? "";
  const wantsPlatform =
    Boolean(deepLink) || requested === "chesscom" || requested === "lichess" || requested === "platform";
  const [chosenMode, setChosenMode] = useState<Mode | null>(null);
  const mode: Mode = chosenMode ?? (wantsPlatform ? "platform" : "paste");
  const [platform, setPlatform] = useState<PlatformSourceId>(linkedSource);
  const [pgn, setPgn] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const [runAnalysis, setRunAnalysis] = useState(false);
  const [depth, setDepth] = useState(12);
  const [busy, setBusy] = useState<Busy>("idle");
  const [validation, setValidation] = useState<PgnValidationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imported, setImported] = useState<{
    game_id: string;
    moves: number;
    analyzed: boolean;
    analysis_status: import("@/lib/api").AnalysisStatus;
  } | null>(null);

  const hasPayload = mode === "paste" ? pgn.trim().length > 0 : file !== null;

  const validate = useCallback(async () => {
    if (mode !== "paste" || !pgn.trim()) return;
    setBusy("validating");
    setError(null);
    try {
      setValidation(await api.validatePgn({ pgn_text: pgn }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validation failed");
    } finally {
      setBusy("idle");
    }
  }, [mode, pgn]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setImported(null);
    setValidation(null);
    if (!hasPayload) {
      setError("Provide a PGN to import.");
      return;
    }

    setBusy("importing");
    try {
      const result =
        mode === "paste"
          ? await api.importGame({ pgn_text: pgn, run_analysis: runAnalysis, depth })
          : await api.importGameFile(file as File, { run_analysis: runAnalysis, depth });
      setImported(result);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        if (mode === "paste") {
          try {
            setValidation(await api.validatePgn({ pgn_text: pgn }));
          } catch {
            /* keep the plain message below */
          }
        }
        setError(err.message);
      } else {
        setError(
          err instanceof ApiError
            ? `${err.message}${err.code !== "http_error" ? ` (${err.code})` : ""}`
            : "Import failed — is the backend running?"
        );
      }
    } finally {
      setBusy("idle");
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) setFile(dropped);
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <section>
        <p className="eyebrow">Play</p>
        <h1 className="title mt-1">Import games</h1>
        <p className="subtitle mt-1">
          Bring in a single game from PGN, or a whole month from a Chess.com or Lichess account.
        </p>
      </section>

      <Tabs
        items={[
          { id: "paste", label: "Paste PGN" },
          { id: "upload", label: "Upload file" },
          { id: "platform", label: "Connect an account" },
        ]}
        active={mode}
        onChange={(id) => {
          setChosenMode(id as Mode);
          setValidation(null);
          setError(null);
        }}
      />

      {mode === "platform" ? (
        <div className="space-y-4">
          {/* Which account Caissa should read. Both sources are real, key-less public APIs. */}
          <div className="flex flex-wrap items-center gap-1.5">
            {(Object.keys(PLATFORMS) as PlatformSourceId[]).map((id) => (
              <Chip
                key={id}
                active={platform === id}
                onClick={() => setPlatform(id)}
                title={PLATFORMS[id].monthsNote}
              >
                {PLATFORMS[id].label}
              </Chip>
            ))}
          </div>
          <PlatformPanel key={platform} source={platform} initialUsername={deepLink} />
        </div>
      ) : null}

      {mode !== "platform" ? (
        <>
          <form onSubmit={submit}>
            <Panel bodyClassName="panel-body space-y-4">
              {mode === "paste" ? (
                <div>
                  <label htmlFor="pgn" className="label">
                    PGN text
                  </label>
                  <textarea
                    id="pgn"
                    value={pgn}
                    onChange={(e) => {
                      setPgn(e.target.value);
                      setValidation(null);
                    }}
                    onBlur={validate}
                    rows={7}
                    spellCheck={false}
                    placeholder={'[Event "..."]\n[White "..."]\n[Black "..."]\n\n1. e4 e5 2. Nf3 ...'}
                    className="input mono mt-1.5 resize-y text-small leading-relaxed"
                  />
                  <div className="mt-2 flex items-center gap-2 text-small text-mist-400">
                    <span
                      className={`inline-block h-1.5 w-1.5 rounded-full ${
                        pgn.trim() ? "bg-emerald-400" : "bg-ink-500"
                      }`}
                    />
                    {pgn.trim() ? `${pgn.trim().split(/\n+/).length} lines ready` : "waiting for PGN"}
                  </div>
                </div>
              ) : (
                <div
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragging(true);
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={onDrop}
                  className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors duration-150 ${
                    dragging ? "border-emerald-400/60 bg-emerald-500/5" : "border-ink-600 bg-ink-850/40"
                  }`}
                >
                  <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-ink-600 bg-ink-800 text-lg text-mist-500">
                    ♟
                  </div>
                  {file ? (
                    <>
                      <p className="text-sm font-medium text-mist-100">{file.name}</p>
                      <p className="mono mt-0.5 text-small text-mist-600">
                        {Math.max(1, Math.round(file.size / 1024))} KB
                      </p>
                      <button
                        type="button"
                        onClick={() => setFile(null)}
                        className="btn btn-ghost mt-3"
                      >
                        Remove
                      </button>
                    </>
                  ) : (
                    <>
                      <p className="text-sm text-mist-300">Drag &amp; drop a .pgn file here</p>
                      <p className="mt-1 text-small text-mist-600">or</p>
                      <button
                        type="button"
                        onClick={() => inputRef.current?.click()}
                        className="btn btn-ghost mt-3"
                      >
                        Choose file
                      </button>
                    </>
                  )}
                  <input
                    ref={inputRef}
                    type="file"
                    accept=".pgn,.txt,application/x-chess-pgn,text/plain"
                    className="hidden"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  />
                </div>
              )}

              <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-ink-700 pt-4">
                <label className="flex cursor-pointer items-center gap-2 text-[13px] text-mist-300">
                  <input
                    type="checkbox"
                    checked={runAnalysis}
                    onChange={(e) => setRunAnalysis(e.target.checked)}
                    className="h-4 w-4 rounded accent-emerald-500"
                  />
                  Run Stockfish analysis on import
                </label>
                <label
                  className={`flex items-center gap-2 text-[13px] ${runAnalysis ? "text-mist-300" : "text-mist-600"}`}
                >
                  Depth
                  <input
                    type="number"
                    min={6}
                    max={22}
                    value={depth}
                    disabled={!runAnalysis}
                    onChange={(e) => setDepth(Number(e.target.value))}
                    className="input w-20 py-1 text-center text-xs disabled:opacity-50"
                  />
                </label>
                <span className="text-small text-mist-600">
                  analysis is optional — you can run it later from the game page
                </span>
              </div>

              {validation ? <IssueList validation={validation} /> : null}
              {error ? <ErrorState title="Import failed" message={error} /> : null}

              <div className="flex items-center justify-end gap-3">
                {mode === "paste" ? (
                  <button
                    type="button"
                    onClick={validate}
                    disabled={!pgn.trim() || busy !== "idle"}
                    className="btn btn-ghost"
                  >
                    {busy === "validating" ? "Validating…" : "Validate"}
                  </button>
                ) : null}
                <button type="submit" className="btn btn-primary" disabled={!hasPayload || busy === "importing"}>
                  {busy === "importing" ? "Importing…" : "Import game"}
                </button>
              </div>
            </Panel>
          </form>

          {imported ? (
            <Panel bodyClassName="panel-body border-t-0">
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-sm font-semibold text-emerald-300">Game imported.</h2>
                <AnalysisStatusBadge status={imported.analysis_status} pulse />
              </div>
              <p className="mt-1.5 text-xs text-mist-500">
                {plural(imported.moves, "move")} stored ·{" "}
                <span className="mono">{imported.game_id}</span>
              </p>
              <div className="mt-4 flex gap-3">
                <button
                  type="button"
                  onClick={() => router.push(`/game/${imported.game_id}`)}
                  className="btn btn-primary"
                >
                  Open game →
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setPgn("");
                    setFile(null);
                    setValidation(null);
                    setImported(null);
                  }}
                  className="btn btn-ghost"
                >
                  Import another
                </button>
              </div>
            </Panel>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

// useSearchParams needs a Suspense boundary during prerender. The fallback
// carries the page header, so the static shell shows the title immediately
// instead of a bare spinner and a title that pops in after hydration.
export default function ImportPage() {
  return (
    <Suspense
      fallback={
        <div className="mx-auto max-w-4xl space-y-6">
          <section>
            <p className="eyebrow">Play</p>
            <h1 className="title mt-1">Import games</h1>
            <p className="subtitle mt-1">
              Bring in a single game from PGN, or a whole month from a Chess.com or Lichess account.
            </p>
          </section>
          <LoadingState label="Loading import options…" />
        </div>
      }
    >
      <ImportPageInner />
    </Suspense>
  );
}
