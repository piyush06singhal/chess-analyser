"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

/**
 * A live strip of real numbers for the landing page.
 *
 * It shows only what the backend actually reports. With an empty library or an
 * unreachable API the strip renders nothing at all rather than a zero or a
 * placeholder — a marketing number that is not measured is exactly the thing
 * this product refuses to fabricate.
 */
interface Stats {
  games: number;
  analyzed: number;
  plies: number;
  engine: string | null;
}

export function LandingStats() {
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .listGames()
      .then((res) => {
        const games = res.games ?? [];
        if (!alive) return;
        const analyzed = games.filter((game) => game.analysis_status === "analyzed");
        setStats({
          games: games.length,
          analyzed: analyzed.length,
          plies: analyzed.reduce((total, game) => total + game.move_count, 0),
          engine: null,
        });
      })
      .catch(() => {
        /* No API numbers to show — render nothing. */
      });
    api
      .health()
      .then((health) => {
        if (!alive) return;
        setStats((current) =>
          current ? { ...current, engine: health.engine?.version ?? null } : current
        );
      })
      .catch(() => {
        /* Engine version is optional. */
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!stats || stats.games === 0) return null;

  const items: Array<{ value: string; label: string }> = [
    { value: stats.games.toLocaleString(), label: "games stored" },
    { value: stats.analyzed.toLocaleString(), label: "analysed end to end" },
    { value: stats.plies.toLocaleString(), label: "positions evaluated" },
  ];
  if (stats.engine) items.push({ value: stats.engine, label: "engine running" });

  return (
    <section
      aria-label="This instance, measured"
      className={`animate-fade-up grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-ink-700 bg-ink-700 ${
        items.length >= 4 ? "sm:grid-cols-4" : "sm:grid-cols-3"
      }`}
    >
      {items.map((item) => (
        <div key={item.label} className="bg-ink-850 px-5 py-4">
          <p className="mono text-2xl font-semibold tracking-tight text-mist-50">{item.value}</p>
          <p className="mt-0.5 text-meta text-mist-400">{item.label}</p>
        </div>
      ))}
    </section>
  );
}
