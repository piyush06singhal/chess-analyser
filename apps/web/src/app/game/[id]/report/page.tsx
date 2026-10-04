import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { GameDetail, GamePosition } from "@/lib/api";
import { GameReportView } from "./report-view";

export const metadata = { title: "Game report" };

export default async function GameReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let game: GameDetail;
  try {
    game = await api.getGame(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  let positions: GamePosition[] = [];
  try {
    positions = (await api.getGamePositions(id)).positions;
  } catch {
    positions = [];
  }

  return <GameReportView game={game} positions={positions} />;
}
