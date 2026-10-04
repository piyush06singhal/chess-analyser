"use client";

// Small, shared UI primitives. Pages compose these instead of re-inventing
// panels, tabs and stat blocks — which is what kept the old layout growing into
// one endless column.

import { useId } from "react";

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className = "",
  bodyClassName = "panel-body",
}: {
  title?: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title ? (
        <header className="panel-header">
          <div className="min-w-0">
            <h2 className="label">{title}</h2>
            {/* A short subtitle is a caption; a long one is methodology. The long
                form is folded into a disclosure so a page is not a wall of prose,
                without deleting a word of it. */}
            {subtitle ? (
              subtitle.length > 90 ? (
                <Disclosure summary="Method" bare>
                  <Note>{subtitle}</Note>
                </Disclosure>
              ) : (
                <p className="mt-1 text-small text-mist-400">{subtitle}</p>
              )
            ) : null}
          </div>
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "default",
  icon,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: "default" | "accent" | "warn" | "danger";
  icon?: string;
}) {
  const toneClass =
    tone === "accent"
      ? "text-emerald-300"
      : tone === "warn"
        ? "text-amber-300"
        : tone === "danger"
          ? "text-rose-300"
          : "text-mist-50";
  // The icon chip follows the tile's tone, so a warning tile reads as one
  // object rather than a number with a random glyph pinned to it.
  const iconToneClass =
    tone === "accent"
      ? "border-emerald-primary/40 bg-emerald-primary/10 text-emerald-300"
      : tone === "warn"
        ? "border-amber-primary/40 bg-amber-primary/10 text-amber-300"
        : tone === "danger"
          ? "border-rose-primary/40 bg-rose-primary/10 text-rose-300"
          : "border-ink-600 bg-ink-800 text-mist-400";
  return (
    <div className="stat">
      <div className="flex items-start justify-between gap-2">
        <p className="label">{label}</p>
        {icon ? (
          <span
            aria-hidden
            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border text-sm ${iconToneClass}`}
          >
            {icon}
          </span>
        ) : null}
      </div>
      <p className={`stat-value mt-1.5 ${toneClass}`}>{value}</p>
      {hint ? <p className="stat-hint mt-1">{hint}</p> : null}
    </div>
  );
}

/**
 * A collapsed "how / why" note.
 *
 * Methodology, disclaimers and long caveats stay one click away instead of
 * sitting in front of every number: the measurement is visible, the reasoning is
 * available. Nothing is removed — it is only not shouted.
 */
export function Disclosure({
  summary = "How this is computed",
  children,
  className = "",
  bare = false,
}: {
  summary?: string;
  children: React.ReactNode;
  className?: string;
  /** No top rule/space: for use directly under a panel title. */
  bare?: boolean;
}) {
  return (
    <details
      className={`disclosure ${bare ? "mt-1" : "mt-3 border-t border-ink-700 pt-3"} ${className}`}
    >
      <summary>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden>
          <path d="m9 6 6 6-6 6" />
        </svg>
        {summary}
      </summary>
      <div className="mt-2 space-y-1.5">{children}</div>
    </details>
  );
}

/** One line of prose inside a :class:`Disclosure`. */
export function Note({ children }: { children: React.ReactNode }) {
  return <p className="text-small leading-relaxed text-mist-400">{children}</p>;
}

/**
 * Short label for where a claim came from.
 *
 * The full source name stays in the tooltip, so the evidence model is intact
 * without repeating "source: Caissa-derived feature" under every finding.
 */
export function SourceBadge({ source, label }: { source: string; label: string }) {
  const tone =
    source === "engine_fact"
      ? "border-sky-primary/40 text-sky-300"
      : source === "argus_interpretation"
        ? "border-violet-primary/40 text-violet-300"
        : "border-ink-600 text-mist-400";
  return (
    <span className={`badge badge-source ${tone}`} title={`Source: ${fullSourceName(source)}`}>
      {label}
    </span>
  );
}

function fullSourceName(source: string): string {
  if (source === "engine_fact") return "engine fact — read from Stockfish output";
  if (source === "argus_interpretation") return "Caissa interpretation — a rule Caissa applies to measured facts";
  if (source === "argus_derived_feature") return "Caissa-derived feature — measured from the board";
  return source;
}

export interface TabItem {
  id: string;
  label: string;
  count?: number;
  badge?: string;
}

export function Tabs({
  items,
  active,
  onChange,
  className = "",
}: {
  items: TabItem[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={`tabs ${className}`} role="tablist" aria-label="Sections">
      {items.map((item) => {
        const selected = item.id === active;
        return (
          <button
            key={item.id}
            id={`${id}-${item.id}`}
            type="button"
            role="tab"
            aria-selected={selected}
            // No aria-controls: the panels are rendered conditionally by each
            // caller, so pointing at a panel id would reference an element that
            // is not in the document for every inactive tab — which axe flags as
            // a critical invalid ARIA value. `aria-selected` + `role="tabpanel"`
            // is a valid, honest tablist without a dangling reference.
            onClick={() => onChange(item.id)}
            className={`tab ${selected ? "tab-active" : ""} ${item.badge !== undefined ? "chapter-tab" : ""}`}
          >
            {item.label}
            {item.count !== undefined ? <span className="tab-count">{item.count}</span> : null}
            {item.badge ? (
              <span className="badge mono border-ink-600 bg-ink-800 text-mist-400">{item.badge}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  children,
  id,
}: {
  children: React.ReactNode;
  id?: string;
}) {
  return (
    <div id={id} role="tabpanel" className="animate-fade-in">
      {children}
    </div>
  );
}

export function Chip({
  children,
  active = false,
  onClick,
  title,
}: {
  children: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`chip ${active ? "chip-active" : ""}`}
    >
      {children}
    </button>
  );
}

/**
 * Three-way outcome forecast: White win / draw / Black win.
 *
 * The values come from the backend's documented logistic mapping of the engine
 * evaluation. This component only renders them — it never computes a probability
 * of its own, so the UI cannot drift from the methodology in the report.
 */
export function ForecastBar({
  white,
  draw,
  black,
  labels = true,
}: {
  white: number;
  draw: number;
  black: number;
  labels?: boolean;
}) {
  const total = white + draw + black || 1;
  const pct = (value: number) => `${((value / total) * 100).toFixed(1)}%`;
  return (
    <div>
      <div className="forecast-bar" role="img" aria-label={`White ${pct(white)}, draw ${pct(draw)}, Black ${pct(black)}`}>
        <span className="forecast-white" style={{ width: pct(white) }} />
        <span className="forecast-draw" style={{ width: pct(draw) }} />
        <span className="forecast-black" style={{ width: pct(black) }} />
      </div>
      {labels ? (
        <div className="mt-2 flex items-center justify-between mono text-small">
          <span className="text-mist-100">White {pct(white)}</span>
          <span className="text-mist-400">Draw {pct(draw)}</span>
          <span className="text-mist-400">Black {pct(black)}</span>
        </div>
      ) : null}
    </div>
  );
}

/** Compact key/value pair row used inside panels. */
export function Field({
  term,
  children,
}: {
  term: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-ink-800/70 py-1.5 last:border-0">
      <dt className="shrink-0 text-small text-mist-500">{term}</dt>
      <dd className="min-w-0 truncate text-right text-small text-mist-100">{children}</dd>
    </div>
  );
}

export function ProgressBar({ value, max = 100 }: { value: number; max?: number }) {
  const percent = Math.max(0, Math.min(100, max === 0 ? 0 : (value / max) * 100));
  return (
    <div className="progress">
      <div className="progress-bar" style={{ width: `${percent}%` }} />
    </div>
  );
}
