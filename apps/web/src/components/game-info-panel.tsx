import type { GameDetail } from "@/lib/api";
import { AnalysisStatusBadge } from "@/components/analysis-status-badge";

function formatRatings(white: number | null, black: number | null) {
  if (white === null && black === null) return null;
  return `${white ?? "?"} — ${black ?? "?"}`;
}

function ResultBadge({ result }: { result: string }) {
  const style =
    result === "1-0"
      ? "bg-mist-50 text-ink-900"
      : result === "0-1"
        ? "bg-ink-700 text-mist-200 ring-1 ring-ink-500"
        : "bg-ink-700 text-mist-400 ring-1 ring-ink-600";
  return (
    <span className={`badge mono ${style}`}>
      {result === "*" ? "ongoing" : result}
    </span>
  );
}

// Game metadata panel. Renders only what the source PGN provided; missing
// fields are omitted rather than invented.
export function GameInfoPanel({ game }: { game: GameDetail }) {
  const ratings = formatRatings(game.white_rating, game.black_rating);
  const opening = [game.eco_code, game.opening_name].filter(Boolean).join(" · ");

  return (
    <div className="card card-hover p-4">
      <div className="flex items-center justify-between">
        <h3 className="label">Game Information</h3>
        <ResultBadge result={game.result} />
      </div>
      <div className="mt-3.5 space-y-2.5 text-sm">
        <div className="flex items-baseline justify-between gap-3">
          <span className="font-medium text-mist-50">{game.white_player}</span>
          <span className="text-xs text-mist-600">vs</span>
          <span className="font-medium text-mist-50">{game.black_player}</span>
        </div>
        {ratings ? (
          <div className="flex justify-between text-mist-500">
            <span className="text-xs">Ratings</span>
            <span className="mono text-mist-200">{ratings}</span>
          </div>
        ) : null}
        {opening ? (
          <div className="flex justify-between gap-4 text-mist-500">
            <span className="text-xs">Opening</span>
            <span className="text-right text-mist-200">{opening}</span>
          </div>
        ) : null}
        {game.date ? (
          <div className="flex justify-between text-mist-500">
            <span className="text-xs">Date</span>
            <span className="mono text-mist-200">{game.date}</span>
          </div>
        ) : null}
        {game.event ? (
          <div className="flex justify-between gap-4 text-mist-500">
            <span className="text-xs">Event</span>
            <span className="truncate text-right text-mist-200">{game.event}</span>
          </div>
        ) : null}
        {game.time_control ? (
          <div className="flex justify-between text-mist-500">
            <span className="text-xs">Time control</span>
            <span className="mono text-mist-200">{game.time_control}</span>
          </div>
        ) : null}
        <div className="flex justify-between text-mist-500">
          <span className="text-xs">Moves</span>
          <span className="mono text-mist-200">{game.moves.length}</span>
        </div>
        <div className="flex items-center justify-between text-mist-500">
          <span className="text-xs">Analysis</span>
          <AnalysisStatusBadge status={game.analysis_status} pulse />
        </div>
      </div>
    </div>
  );
}
