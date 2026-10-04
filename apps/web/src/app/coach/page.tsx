"use client";

// The Coach section: two surfaces that share one context.
//
// * **AI Coach** — the tool-using conversation (Phase 7 + 11).
// * **Today** — the workspace: resolved context, prioritised focus, the
//   personalised feed, and the automatic debrief of the game being reviewed.
//
// They are tabs rather than separate pages because they answer the same
// question from two directions ("what should I do about this position?" vs
// "what does Caissa already know?"), and both read the same query string. A
// `?tab=` parameter keeps the selection linkable: the game page can hand over
// `?game_id=…&ply=…` and it lands on the right surface.

import { Suspense, useCallback, useEffect, useRef } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import CoachConversation from "@/components/coach-conversation";
import CoachToday from "@/components/coach-today";
import { LoadingState } from "@/components/empty-state";
import { Tabs } from "@/components/ui";

const TABS = [
  { id: "coach", label: "AI Coach" },
  { id: "today", label: "Today" },
] as const;

function CoachTabs() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const requested = searchParams.get("tab") ?? "";
  const active = (TABS as readonly { id: string }[]).some((tab) => tab.id === requested)
    ? requested
    : "coach";

  // The live query string, so a write issued from an older render cannot rebuild
  // the URL from stale state and revert a selection made since (the same hazard
  // the intelligence explorer documents).
  const queryRef = useRef(searchParams.toString());
  useEffect(() => {
    queryRef.current = searchParams.toString();
  }, [searchParams]);

  // The URL is the state. Writing to it re-renders the server component tree, so
  // the two surfaces are genuinely unmounted/mounted rather than both kept alive
  // (which would mean two copies of the same context request in flight).
  const select = useCallback(
    (id: string) => {
      const params = new URLSearchParams(queryRef.current);
      if (id === "coach") params.delete("tab");
      else params.set("tab", id);
      const query = params.toString();
      if (query === queryRef.current) return;
      queryRef.current = query;
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router]
  );

  return (
    <div className="space-y-5">
      <Tabs items={TABS.map((tab) => ({ id: tab.id, label: tab.label }))} active={active} onChange={select} />

      <div>
        <p className="eyebrow">Coach</p>
        <h1 className="title mt-1">Caissa Coach</h1>
        <p className="subtitle mt-1 max-w-2xl">
          {active === "today"
            ? "Where you stand, what to work on, what Caissa noticed, and the review of your latest game."
            : "Ask a question; the agent answers from your stored games and engine analysis, showing the evidence it used."}
        </p>
      </div>

      {active === "today" ? <CoachToday /> : <CoachConversation />}
    </div>
  );
}

// `useSearchParams` suspends during static prerender, so the surface selector and
// everything below it sit under one boundary. The fallback keeps the heading so
// the page does not flash as empty.
export default function CoachPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-5">
          <p className="eyebrow">Coach</p>
          <h1 className="title mt-1">Caissa Coach</h1>
          <div className="card p-4">
            <LoadingState label="Loading the coach…" />
          </div>
        </div>
      }
    >
      <CoachTabs />
    </Suspense>
  );
}
