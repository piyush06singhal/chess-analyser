import type { Metadata } from "next";
import Link from "next/link";

import { LandingStats } from "@/components/landing-stats";

export const metadata: Metadata = {
  title: "Caissa — Chess intelligence you can verify",
};

// The public landing page. It is the only indexable surface (see robots.ts); the
// workspace behind it is private. Marketing copy here is short on purpose — the
// product's claim is that it does not overstate, and the page should not either.

const FEATURES: Array<{ icon: string; title: string; text: string; accent: string }> = [
  {
    icon: "♜",
    title: "Explainable reports",
    text: "Accuracy, turning points and lessons — each one traceable to the engine line behind it.",
    accent: "text-emerald-primary",
  },
  {
    icon: "♞",
    title: "Player intelligence",
    text: "A versioned profile and Chess DNA. Every claim carries the sample it came from.",
    accent: "text-violet-primary",
  },
  {
    icon: "♟",
    title: "Training from your mistakes",
    text: "Exercises generated only from your own analysed blunders, with spaced review.",
    accent: "text-emerald-primary",
  },
  {
    icon: "✦",
    title: "An honest AI coach",
    text: "Grounded in evidence. It refuses to state a number it cannot prove.",
    accent: "text-violet-primary",
  },
  {
    icon: "⇄",
    title: "What-If & Live play",
    text: "Compare alternatives, or play a server-authoritative game with fair play enforced.",
    accent: "text-sky-primary",
  },
  {
    icon: "◈",
    title: "An evidence graph",
    text: "Follow any insight down to the positions and games it was derived from.",
    accent: "text-sky-primary",
  },
];

const STEPS: Array<{ step: string; title: string; text: string }> = [
  {
    step: "01",
    title: "Bring in a game",
    text: "Import a PGN, or read your public Chess.com or Lichess games by username.",
  },
  {
    step: "02",
    title: "Let Stockfish measure it",
    text: "Every position is evaluated; nothing is guessed and no number is invented.",
  },
  {
    step: "03",
    title: "Read it, then train it",
    text: "Open the report, ask the coach, and turn the weakness into an exercise.",
  },
];

export default function LandingPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-20 py-6 sm:space-y-24 sm:py-12">
      {/* Hero */}
      <section className="text-center">
        <p className="eyebrow animate-fade-in">Caissa</p>
        <h1 className="display-hero display-hero-xl animate-fade-up mt-3">
          Chess intelligence you can <span className="text-gradient">verify</span>.
        </h1>
        <p className="subtitle animate-fade-up mx-auto mt-5 max-w-2xl">
          Turn your games into explainable reports, a versioned player profile and training
          built from your own mistakes. Stockfish measures every position; the coach only says
          what the evidence supports.
        </p>
        <div className="animate-fade-up mt-8 flex flex-wrap items-center justify-center gap-2.5">
          <Link href="/dashboard" className="btn btn-primary btn-pill-lg">
            Enter the workspace
          </Link>
          <Link href="/import" className="btn btn-ghost btn-pill-lg">
            Import a game
          </Link>
        </div>
        <p className="mt-4 text-meta text-mist-500">Self-hosted · no accounts · no telemetry</p>
      </section>

      <LandingStats />

      {/* Capabilities */}
      <section aria-labelledby="capabilities-heading">
        <div className="mb-6 flex items-end justify-between gap-4">
          <div>
            <p className="eyebrow">Capabilities</p>
            <h2 id="capabilities-heading" className="title mt-1.5">
              Everything measured, nothing fabricated
            </h2>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((feature, index) => (
            <article
              key={feature.title}
              className="card card-hover animate-fade-up p-5"
              style={{ animationDelay: `${index * 60}ms` }}
            >
              <span
                className={`flex h-10 w-10 items-center justify-center rounded-xl border border-ink-600 bg-ink-800 text-xl ${feature.accent}`}
                aria-hidden
              >
                {feature.icon}
              </span>
              <h3 className="mt-3.5 text-base font-semibold tracking-tight text-mist-50">
                {feature.title}
              </h3>
              <p className="mt-1.5 text-small leading-relaxed text-mist-400">{feature.text}</p>
            </article>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section aria-labelledby="how-heading">
        <div className="mb-6">
          <p className="eyebrow">How it works</p>
          <h2 id="how-heading" className="title mt-1.5">
            Three steps to your first report
          </h2>
        </div>
        <ol className="grid gap-4 sm:grid-cols-3">
          {STEPS.map((step, index) => (
            <li
              key={step.step}
              className="panel animate-fade-up p-5"
              style={{ animationDelay: `${index * 80}ms` }}
            >
              <span className="mono text-sm font-semibold text-emerald-primary">{step.step}</span>
              <h3 className="mt-2 text-base font-semibold tracking-tight text-mist-50">
                {step.title}
              </h3>
              <p className="mt-1.5 text-small leading-relaxed text-mist-400">{step.text}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* Closing CTA */}
      <section className="verdict-stage px-6 py-10 text-center sm:px-12 sm:py-14">
        <p className="verdict-eyebrow">The rule, in code</p>
        <p className="verdict-headline mx-auto mt-3 max-w-3xl">
          A capability that is not built is reported as not built.
        </p>
        <p className="verdict-sub mx-auto mt-4">
          A number that was not measured is not shown, and a claim without evidence is refused.
          Open the workspace and it holds the same way behind every panel.
        </p>
        <div className="mt-7 flex flex-wrap items-center justify-center gap-2.5">
          <Link href="/dashboard" className="btn btn-primary btn-pill-lg">
            Enter the workspace
          </Link>
          <Link href="/system" className="btn btn-ghost btn-pill-lg">
            See system health
          </Link>
        </div>
      </section>
    </div>
  );
}
