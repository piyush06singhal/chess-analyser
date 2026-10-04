"use client";

import Link from "next/link";
import type { GameListItem } from "@/lib/api";
import { AnalysisStatusBadge } from "@/components/analysis-status-badge";

// One game table for the whole app. Compact by default (dashboard) and full in
// the library, so the two can never drift apart. Every value shown comes from
// the stored game — no derived or placeholder statistics.

/** How an imported game's origin is named, per source. */
const SOURCE_LABELS: Record<string, string> = {
  chess_com: "Chess.com",
  lichess: "Lichess",
};
export function GameTable({
  games,
  compact = false,
  actions,
}: {
  games: GameListItem[];
  compact?: boolean;
  actions?: (game: GameListItem) => React.ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="data-table min-w-[640px]">
        <thead>
          <tr>
            <th>Game</th>
            <th className="w-20">Result</th>
            {compact ? null : <th className="w-28">Source</th>}
            <th className="w-28">Opening</th>
            <th className="w-24">Moves</th>
            <th className="w-40">Analysis</th>
            {actions ? <th className="w-40 text-right">Actions</th> : null}
          </tr>
        </thead>
        <tbody>
          {games.map((game) => (
            <tr key={game.id} className="group">
              <td>
                <Link
                  href={`/game/${game.id}`}
                  className="block min-w-0 transition-colors hover:text-emerald-300"
                >
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span className="font-medium text-mist-50 group-hover:text-emerald-200">
                      {game.white_player}
                    </span>
                    <span className="mono text-small text-mist-500">
                      {game.white_rating ?? "—"}
                    </span>
                    <span className="text-small text-mist-600">vs</span>
                    <span className="mono text-small text-mist-500">
                      {game.black_rating ?? "—"}
                    </span>
                    <span className="font-medium text-mist-50 group-hover:text-emerald-200">
                      {game.black_player}
                    </span>
                  </span>
                  <span className="mono mt-0.5 block text-small text-mist-600">
                    {[game.date, game.event].filter(Boolean).join(" · ") || "no date recorded"}
                  </span>
                </Link>
              </td>
              <td>
                <span className="badge mono border-ink-600 bg-ink-800 text-mist-200">
                  {game.result}
                </span>
              </td>
              {compact ? null : (
                <td>
                  {game.source_game_id?.startsWith("http") ? (
                    <a
                      href={game.source_game_id}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-small text-sky-300 underline decoration-sky-400/30 underline-offset-2 hover:decoration-sky-300"
                    >
                      {SOURCE_LABELS[game.source] ?? "source"} ↗
                    </a>
                  ) : (
                    <span className="text-small text-mist-500">
                      {game.source === "pgn_file"
                        ? "PGN file"
                        : game.source === "pgn_text"
                          ? "Pasted PGN"
                          : (SOURCE_LABELS[game.source] ?? "source")}
                    </span>
                  )}
                </td>
              )}
              <td>
                <span className="text-small text-mist-300">
                  {game.opening_name ?? game.eco_code ?? "—"}
                </span>
                {game.opening_name && game.eco_code ? (
                  <span className="mono ml-1 text-small text-mist-600">{game.eco_code}</span>
                ) : null}
              </td>
              <td className="mono text-small text-mist-400">{game.move_count}</td>
              <td>
                <AnalysisStatusBadge status={game.analysis_status} pulse />
              </td>
              {actions ? <td className="text-right">{actions(game)}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
