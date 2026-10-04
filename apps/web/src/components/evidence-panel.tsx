"use client";

// "Show me why" and the EvidencePanel.
//
// One reusable way to answer "where did that come from?", used everywhere a
// claim appears. The panel fetches the evidence packet the backend assembles and
// renders each reference by *what kind of knowledge it is* (engine fact, Caissa
// feature, interpretation, prediction, refusal), plus every gap — a reference
// Caissa cannot follow is shown as unavailable with its reason, never dropped.
//
// The component invents nothing: with no data it says so, and it never renders a
// link the backend did not mark as followable.

import { useCallback, useState } from "react";

import { api, type EvidenceItem, type EvidencePacketResponse } from "@/lib/api";

const KIND_LABELS: Record<EvidenceItem["kind"], string> = {
  engine_fact: "Engine fact",
  argus_feature: "Caissa feature",
  interpretation: "Interpretation",
  prediction: "Prediction",
  refusal: "Cannot present",
};

const KIND_TONE: Record<EvidenceItem["kind"], string> = {
  engine_fact: "border-sky-primary/40 text-sky-300",
  argus_feature: "border-ink-600 text-mist-400",
  interpretation: "border-violet-primary/40 text-violet-300",
  prediction: "border-emerald-primary/40 text-emerald-300",
  refusal: "border-amber-primary/40 text-amber-300",
};

export function ShowMeWhy({
  claim,
  gameId,
  ply,
  playerId,
  insightKey,
  label = "Show me why",
}: {
  claim: string;
  gameId?: string;
  ply?: number;
  playerId?: number;
  insightKey?: string;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" className="btn btn-ghost" onClick={() => setOpen((value) => !value)}>
        {open ? "Hide evidence" : label}
      </button>
      {open ? (
        <EvidencePanel
          claim={claim}
          gameId={gameId}
          ply={ply}
          playerId={playerId}
          insightKey={insightKey}
        />
      ) : null}
    </div>
  );
}

export function EvidencePanel({
  claim,
  gameId,
  ply,
  playerId,
  insightKey,
}: {
  claim: string;
  gameId?: string;
  ply?: number;
  playerId?: number;
  insightKey?: string;
}) {
  const [packet, setPacket] = useState<EvidencePacketResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.showMeWhy({
        claim,
        game_id: gameId,
        ply,
        player_id: playerId,
        insight_key: insightKey,
      });
      setPacket(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Evidence could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [claim, gameId, ply, playerId, insightKey]);

  if (!packet && !loading && !error) {
    return (
      <div className="mt-2 rounded-xl border border-ink-700 bg-ink-800/60 p-3">
        <p className="text-small text-mist-400">
          Caissa will show the stored evidence behind this claim — and say so when it has none.
        </p>
        <button type="button" className="btn btn-secondary mt-2" onClick={load}>
          Load evidence
        </button>
      </div>
    );
  }

  if (loading) {
    return <p className="mt-2 text-small text-mist-400">Reading stored evidence…</p>;
  }

  if (error) {
    return (
      <p className="mt-2 text-small text-rose-300" role="alert">
        {error}
      </p>
    );
  }

  if (!packet) return null;

  return (
    <div className="mt-2 space-y-2 rounded-xl border border-ink-700 bg-ink-800/60 p-3">
      <p className="text-small text-mist-300">
        <span className="font-medium text-mist-100">Claim:</span> {packet.claim}
        {packet.claim_level ? (
          <span className="ml-2 badge border-ink-600 text-mist-400">{packet.claim_level}</span>
        ) : null}
      </p>

      {packet.items.length === 0 ? (
        <p className="text-small text-amber-300">
          No stored evidence backs this claim, so Caissa does not present it as a fact.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {packet.items.map((item, index) => (
            <li key={`${item.kind}-${index}`} className="flex items-start gap-2">
              <span className={`badge shrink-0 ${KIND_TONE[item.kind]}`}>
                {KIND_LABELS[item.kind]}
              </span>
              <span className="min-w-0 flex-1 text-small text-mist-300">
                {item.followable && item.href ? (
                  <a className="text-emerald-300 hover:underline" href={item.href}>
                    {item.statement || item.label}
                  </a>
                ) : (
                  item.statement || item.label
                )}
                {item.value !== null && item.unit ? (
                  <span className="ml-1 mono text-mist-500">
                    ({item.value} {item.unit})
                  </span>
                ) : null}
                {!item.followable && item.unavailable_reason ? (
                  <span className="ml-1 text-mist-500">— {item.unavailable_reason}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      )}

      {packet.gaps.length > 0 ? (
        <div className="border-t border-ink-700 pt-2">
          <p className="label">Gaps</p>
          <ul className="mt-1 space-y-1">
            {packet.gaps.map((gap, index) => (
              <li key={index} className="text-small text-mist-500">
                {gap}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
