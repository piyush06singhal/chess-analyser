import type { GameDetail } from "@/lib/api";

function formatRatings(white: number | null, black: number | null) {
  if (white === null && black === null) return null;
  return `${white ?? "?"} — ${black ?? "?"}`;
}

// Game metadata panel. Renders only what the source PGN provided; missing
// fields are omitted rather than invented.
export function GameInfoPanel({ game }: { game: GameDetail }) {
  const ratings = formatRatings(game.white_rating, game.black_rating);
  const opening = [game.eco_code, game.opening_name].filter(Boolean).join(" · ");

  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
        Game Information
      </h3>
      <div className="mt-3 space-y-2 text-sm">
        <div className="flex items-baseline justify-between gap-4">
          <span className="font-medium text-neutral-900">{game.white_player}</span>
          <span className="text-neutral-500">vs</span>
          <span className="font-medium text-neutral-900">{game.black_player}</span>
        </div>
        <div className="flex justify-between text-neutral-600">
          <span>Result</span>
          <span className="font-mono text-neutral-900">{game.result}</span>
        </div>
        {ratings ? (
          <div className="flex justify-between text-neutral-600">
            <span>Ratings</span>
            <span className="font-mono">{ratings}</span>
          </div>
        ) : null}
        {opening ? (
          <div className="flex justify-between text-neutral-600">
            <span>Opening</span>
            <span className="text-right text-neutral-900">{opening}</span>
          </div>
        ) : null}
        {game.date ? (
          <div className="flex justify-between text-neutral-600">
            <span>Date</span>
            <span className="font-mono">{game.date}</span>
          </div>
        ) : null}
        {game.event ? (
          <div className="flex justify-between text-neutral-600">
            <span>Event</span>
            <span className="text-right text-neutral-900">{game.event}</span>
          </div>
        ) : null}
        {game.time_control ? (
          <div className="flex justify-between text-neutral-600">
            <span>Time control</span>
            <span className="font-mono">{game.time_control}</span>
          </div>
        ) : null}
        <div className="flex justify-between text-neutral-600">
          <span>Moves</span>
          <span className="font-mono">{game.moves.length}</span>
        </div>
      </div>
    </div>
  );
}
