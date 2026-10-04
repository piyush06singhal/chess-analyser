"use client";

// The keyboard layer for the interactive boards.
//
// The visual board is a pointer widget: `react-chessboard` renders every piece
// as a nameless `role="button"`, which is a serious accessibility defect once the
// board is interactive (drag-to-move). This component fixes it in two moves:
//
//   1. `useVisualBoardA11y` takes the library's pieces out of the tab order and
//      the accessibility tree — they are decoration now, not controls — without
//      `inert`, which would also kill pointer dragging.
//   2. This component lays a transparent, `pointer-events-none` grid over the
//      board. Each square is a real, named, focusable button. Arrow keys move a
//      single roving tab stop; Enter/Space picks a piece up and sets it down. A
//      screen reader hears "e2, white pawn"; a sighted keyboard user sees the
//      focus ring land on the square. Because the layer never receives pointer
//      events, dragging with a mouse still works exactly as before.
//
// It knows no rules of its own: every move is checked with chess.js and then
// handed to the caller, which remains the authority.

import { Chess } from "chess.js";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from "react";

const FILES = ["a", "b", "c", "d", "e", "f", "g", "h"] as const;
const RANKS = [1, 2, 3, 4, 5, 6, 7, 8] as const;

const PIECE_NAMES: Record<string, string> = {
  p: "pawn",
  n: "knight",
  b: "bishop",
  r: "rook",
  q: "queen",
  k: "king",
};

function pieceName(piece: { type: string; color: string } | null): string {
  if (!piece) return "empty";
  const colour = piece.color === "w" ? "white" : "black";
  return `${colour} ${PIECE_NAMES[piece.type] ?? piece.type}`;
}

/**
 * Take the visual board's pieces out of the tab order and the accessibility tree.
 *
 * The pieces are re-created whenever the position changes, so a `MutationObserver`
 * re-applies the patch. Only `childList` is observed: patching an attribute must
 * not re-trigger the observer, or it would loop.
 */
export function useVisualBoardA11y(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const patch = () => {
      root.querySelectorAll<HTMLElement>('[role="button"]').forEach((node) => {
        node.setAttribute("tabindex", "-1");
        node.setAttribute("aria-hidden", "true");
        node.removeAttribute("aria-roledescription");
        node.removeAttribute("aria-pressed");
      });
    };
    patch();
    const observer = new MutationObserver(patch);
    observer.observe(root, { childList: true, subtree: true });

    // The library's pieces are pointer widgets, but clicking one still focuses
    // it. Focus inside an `aria-hidden` subtree is blocked by the browser (and
    // logs a warning), so drop the focus the moment it lands there. The overlay
    // beside the board is the real control and keeps its own focus untouched.
    const onFocusIn = (event: FocusEvent) => {
      const target = event.target;
      if (target instanceof HTMLElement && target.closest('[role="button"][aria-hidden="true"]')) {
        target.blur();
      }
    };
    root.addEventListener("focusin", onFocusIn, true);

    return () => {
      observer.disconnect();
      root.removeEventListener("focusin", onFocusIn, true);
    };
  }, [ref]);
}

export function BoardKeyboard({
  fen,
  orientation = "white",
  disabled = false,
  onMove,
}: {
  fen: string;
  orientation?: "white" | "black";
  disabled?: boolean;
  /** Called with the UCI of a legal move the user composed from the keyboard. */
  onMove: (uci: string, san: string) => void;
}) {
  // DOM order matches what is on screen: white sees rank 8 first, black is
  // flipped so the near rank is on the bottom here too.
  const squares = useMemo(() => {
    const files = orientation === "white" ? [...FILES] : [...FILES].reverse();
    const ranks = orientation === "white" ? [...RANKS].reverse() : [...RANKS];
    return ranks.flatMap((rank) => files.map((file) => `${file}${rank}`));
  }, [orientation]);

  const pieces = useMemo(() => {
    const game = new Chess(fen);
    const map: Record<string, { type: string; color: string } | null> = {};
    for (const square of squares) {
      map[square] = (game.get(square as never) as { type: string; color: string } | undefined) ?? null;
    }
    return map;
  }, [fen, squares]);

  const [cursor, setCursor] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  // A new position (a move landed, or a new exercise) clears the selection.
  useEffect(() => {
    void Promise.resolve().then(() => setSelected(null));
  }, [fen]);

  function focusSquare(index: number) {
    setCursor(index);
    refs.current[index]?.focus();
  }

  function activate(square: string) {
    if (disabled) return;
    const game = new Chess(fen);
    const piece = pieces[square];
    if (selected === square) {
      setSelected(null);
      setAnnouncement(`${square} deselected`);
      return;
    }
    if (selected) {
      let san: string | null = null;
      let uci: string | null = null;
      try {
        const move = game.move({ from: selected, to: square, promotion: "q" });
        san = move.san;
        uci = `${move.from}${move.to}${move.promotion ?? ""}`;
      } catch {
        san = null;
      }
      if (uci && san) {
        setSelected(null);
        setAnnouncement(`Played ${san}`);
        onMove(uci, san);
        return;
      }
      // Not a legal move: keep a piece of ours selected if one is here, else clear.
      if (piece && piece.color === game.turn()) {
        setSelected(square);
        setAnnouncement(`${square} selected, ${pieceName(piece)}`);
      } else {
        setSelected(null);
        setAnnouncement(`${square} is empty — nothing to move there`);
      }
      return;
    }
    if (piece && piece.color === game.turn()) {
      setSelected(square);
      setAnnouncement(`${square} selected, ${pieceName(piece)}`);
    } else if (piece) {
      setAnnouncement(`${square}, ${pieceName(piece)} — not your piece to move`);
    } else {
      setAnnouncement(`${square}, empty`);
    }
  }

  function handleKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const row = Math.floor(index / 8);
    const col = index % 8;
    let target = -1;
    switch (event.key) {
      case "ArrowLeft":
        if (col > 0) target = index - 1;
        break;
      case "ArrowRight":
        if (col < 7) target = index + 1;
        break;
      case "ArrowUp":
        if (row > 0) target = index - 8;
        break;
      case "ArrowDown":
        if (row < 7) target = index + 8;
        break;
      case "Home":
        target = row * 8;
        break;
      case "End":
        target = row * 8 + 7;
        break;
      case "Enter":
      case " ":
      case "Spacebar":
        event.preventDefault();
        activate(squares[index]);
        return;
      default:
        return;
    }
    event.preventDefault();
    if (target >= 0) focusSquare(target);
  }

  return (
    <div
      role="group"
      aria-label="Chessboard keyboard control — arrow keys move the cursor, Enter picks up and drops"
      className="pointer-events-none absolute inset-0 grid grid-cols-8 grid-rows-8"
    >
      {squares.map((square, index) => {
        const isSelected = selected === square;
        return (
          <button
            key={square}
            ref={(node) => {
              refs.current[index] = node;
            }}
            type="button"
            tabIndex={index === cursor ? 0 : -1}
            aria-label={`${square}, ${pieceName(pieces[square])}${isSelected ? ", selected" : ""}`}
            aria-pressed={isSelected}
            onKeyDown={(event) => handleKey(event, index)}
            onFocus={() => setCursor(index)}
            className={`m-0 border-0 bg-transparent p-0 outline-offset-[-3px] ${
              isSelected ? "ring-2 ring-inset ring-emerald-400/80" : ""
            }`}
          />
        );
      })}
      <span aria-live="polite" className="sr-only">
        {announcement}
      </span>
    </div>
  );
}
