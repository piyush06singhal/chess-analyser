import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { GameDetail, GamePosition } from "@/lib/api";
import { GameAnalysisView } from "./analysis-view";

export const metadata = { title: "Game Analysis" };

export default async function GamePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ ply?: string }>;
}) {
  const { id } = await params;
  const { ply } = await searchParams;

  let game: GameDetail;
  try {
    game = await api.getGame(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  // The canonical position sequence drives the board — never a client-side
  // chess state that could drift from the backend representation.
  let positions: GamePosition[] = [];
  try {
    positions = (await api.getGamePositions(id)).positions;
  } catch {
    positions = [];
  }

  // Evidence links from a player profile and the report deep-link to the exact
  // position a measurement came from (?ply=N); the board opens there instead of
  // at move zero.
  const initialPly = Number.parseInt(ply ?? "0", 10);

  return (
    <GameAnalysisView
      game={game}
      positions={positions}
      initialPly={Number.isFinite(initialPly) && initialPly > 0 ? initialPly : 0}
    />
  );
}
