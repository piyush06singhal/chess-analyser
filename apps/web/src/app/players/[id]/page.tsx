import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { PlayerProfileView } from "./profile-view";

export const metadata = { title: "Player profile" };

export default async function PlayerProfilePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  // The profile is served from the backend's stored snapshot; the page never
  // derives statistics of its own.
  let profile;
  try {
    profile = await api.getPlayerProfile(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  return <PlayerProfileView profile={profile} />;
}
