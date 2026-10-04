"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Board controls.
 *
 * Everything here acts on data the page already has: flipping only changes how
 * the same position is drawn, sharing copies the URL that encodes the position
 * currently on screen, and copying the FEN copies the exact stored FEN. None of
 * these recompute or reinterpret the analysis.
 */
export function BoardToolbar({
  fen,
  orientation,
  onFlip,
  label,
  className = "",
}: {
  fen: string;
  orientation: "white" | "black";
  onFlip: () => void;
  /** Small readout shown before the buttons (e.g. the ply being viewed). */
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  const flash = useCallback((what: string) => {
    setCopied(what);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(null), 1800);
  }, []);

  const copy = useCallback(
    async (text: string, what: string) => {
      try {
        await navigator.clipboard.writeText(text);
        flash(what);
      } catch {
        // Clipboard access can be denied; the FEN stays selectable in the panel
        // rather than the button pretending it worked.
        flash("copy blocked by the browser");
      }
    },
    [flash]
  );

  return (
    <div className={`flex items-center justify-between gap-3 ${className}`}>
      <span className="truncate text-small text-mist-400">
        {copied ? <span className="text-emerald-primary">Copied {copied}</span> : label}
      </span>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          className="btn-icon"
          onClick={onFlip}
          title={`Flip board (currently ${orientation} at the bottom)`}
          aria-label="Flip board"
        >
          <FlipIcon />
        </button>
        <button
          type="button"
          className="btn-icon"
          onClick={() => copy(window.location.href, "position link")}
          title="Copy a link to this exact position"
          aria-label="Copy position link"
        >
          <ShareIcon />
        </button>
        <button
          type="button"
          className="btn-icon"
          onClick={() => copy(fen, "FEN")}
          title="Copy the FEN of this position"
          aria-label="Copy FEN"
        >
          <FenIcon />
        </button>
      </div>
    </div>
  );
}

function FlipIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M7 21V5m0 0L3.6 8.4M7 5l3.4 3.4" />
      <path d="M17 3v16m0 0 3.4-3.4M17 19l-3.4-3.4" />
    </svg>
  );
}

function ShareIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="18" cy="5.5" r="2.6" />
      <circle cx="6" cy="12" r="2.6" />
      <circle cx="18" cy="18.5" r="2.6" />
      <path d="M8.4 10.8 15.6 7M8.4 13.2 15.6 17" />
    </svg>
  );
}

function FenIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3.5" y="3.5" width="17" height="17" rx="3" />
      <path d="M8 12h8M12 8v8" />
    </svg>
  );
}
