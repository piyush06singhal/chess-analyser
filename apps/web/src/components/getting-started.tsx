"use client";

import Link from "next/link";
import { useState, useSyncExternalStore } from "react";

import { ProgressBar } from "@/components/ui";

const STORAGE_KEY = "caissa-getting-started-dismissed";

// Reading a browser store is exactly what `useSyncExternalStore` is for: it
// renders the server-safe default (`false`) during hydration and swaps to the
// stored value afterwards, without a setState-in-effect and without a mismatch.
function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

function readDismissed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * A guided first run.
 *
 * The least obvious state in the product is a library that already holds games
 * but no analysis: the empty state is gone, so the page shows counts and a table
 * and gives a new user no obvious next move. This card closes that gap by
 * deriving the single path forward from the real library rather than a fixed
 * script — with nothing imported it points at the importer, with games but no
 * analysis it points at the analysis, and once a game is analysed it removes
 * itself because the dashboard's own surfaces are the right guide from there.
 *
 * A step is never checkable by hand: it is done only when the stored data says
 * so, which is the same rule the rest of the product follows. Nothing here is
 * fabricated, and dismissing it is remembered locally so it does not nag.
 */
export function GettingStarted({ imported, analyzed }: { imported: number; analyzed: number }) {
  // The stored preference, plus a session flag so the card hides immediately
  // when dismissed in this tab (a `storage` event only fires across tabs).
  const storedDismissed = useSyncExternalStore(subscribe, readDismissed, () => false);
  const [dismissedNow, setDismissedNow] = useState(false);

  const done = analyzed > 0;
  if (storedDismissed || dismissedNow || done) return null;

  const steps = [
    {
      label: "Add a game",
      detail: "Import a PGN, or read your public Chess.com or Lichess games.",
      done: imported > 0,
      href: "/import",
      cta: imported > 0 ? "Import another" : "Open the importer",
    },
    {
      label: "Measure it",
      detail: "Stockfish evaluates every position — usually under a minute a game.",
      done: false,
      href: "/games",
      cta: imported > 0 ? "Open the Library" : "Import first",
    },
    {
      label: "Read the report",
      detail: "Accuracy, turning points and lessons, each traceable to the engine line.",
      done: false,
      href: "/games",
      cta: "Browse games",
    },
  ];
  const completed = steps.filter((step) => step.done).length;

  function dismiss() {
    setDismissedNow(true);
    try {
      window.localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      // Storage unavailable; the card stays hidden for this session only.
    }
  }

  return (
    <section
      className="card animate-fade-up overflow-hidden p-5 sm:p-6"
      aria-labelledby="getting-started-heading"
    >
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="eyebrow">Getting started</p>
          <h2
            id="getting-started-heading"
            className="mt-1.5 text-xl font-bold tracking-tight text-mist-50"
          >
            {imported > 0 ? "Your games are in — now measure them." : "Three steps to your first report."}
          </h2>
        </div>
        <button type="button" onClick={dismiss} className="btn btn-ghost text-xs">
          Dismiss
        </button>
      </div>

      <div className="mt-4">
        <ProgressBar value={completed} max={steps.length} />
      </div>

      <ol className="mt-4 space-y-2.5">
        {steps.map((step, index) => {
          // Only the first not-yet-done step is "active"; later ones are pending.
          const active = !step.done && steps.slice(0, index).every((prior) => prior.done);
          return (
            <li
              key={step.label}
              className={`onboard-step ${
                step.done ? "onboard-done" : active ? "onboard-active" : ""
              }`}
            >
              <span className="onboard-index" aria-hidden>
                {step.done ? "✓" : index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-small font-semibold text-mist-100">{step.label}</p>
                <p className="text-small leading-relaxed text-mist-400">{step.detail}</p>
              </div>
              <Link
                href={step.href}
                className={`btn ${active ? "btn-primary" : "btn-ghost"} shrink-0 text-xs`}
              >
                {step.cta}
              </Link>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
