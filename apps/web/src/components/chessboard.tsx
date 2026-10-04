"use client";

import { Chessboard } from "react-chessboard";

// Square colours come from the theme tokens, so the board belongs to the same
// palette as everything else in both themes.
const lightSquareStyle = { background: "var(--color-board-light)" };
const darkSquareStyle = { background: "var(--color-board-dark)" };

// Analysis board: positions come from the backend's analysis data.
// Dragging stays disabled for the read-only views; arrow drawing is enabled for
// exploring lines.
export function AnalysisChessboard({
  fen,
  orientation = "white",
  arrows = [],
  squareStyles,
  animationDurationInMs = 220,
}: {
  fen: string;
  orientation?: "white" | "black";
  arrows?: { startSquare: string; endSquare: string; color: string }[];
  squareStyles?: Record<string, React.CSSProperties>;
  animationDurationInMs?: number;
}) {
  // Exposed as a single image with a text alternative, not as a field of
  // nameless buttons. This board is read-only (dragging is off), so the library's
  // `role="button"` piece nodes announce nothing a screen-reader user can use and
  // are pure noise; the FEN is the honest alternative. The library markup is
  // hidden from assistive tech so those nameless buttons are not announced.
  return (
    <div
      className="w-full max-w-[520px] overflow-hidden rounded-2xl border border-ink-600 shadow-[0_24px_60px_-30px_rgba(8,12,22,0.55)]"
      role="img"
      aria-label={`Chess position (${orientation} to move): ${fen}`}
    >
      {/* `inert` (not aria-hidden) removes this subtree from the tab order and the
          accessibility tree in one move, so the library's focusable, nameless
          piece buttons disappear cleanly instead of triggering aria-hidden-focus. */}
      <div inert>
        <Chessboard
          options={{
            position: fen,
            boardOrientation: orientation,
            allowDragging: false,
            allowDrawingArrows: true,
            animationDurationInMs,
            showAnimations: true,
            darkSquareStyle,
            lightSquareStyle,
            arrows,
            squareStyles,
          }}
        />
      </div>
    </div>
  );
}
