"use client";

// The interactive board for a live game (§16, §39).
//
// It accepts moves, but it is *not* the authority. Legality is checked locally
// with chess.js only so an illegal drag never leaves the board; every move is
// still submitted to the server, which validates it against its own state. When
// the server answers — accepted or refused — the board re-syncs to the server's
// FEN, so a refused move rolls back rather than leaving a phantom position.
//
// The board is deliberately honest while it waits: it shows a pending marker, and
// an unreachable server leaves the last *confirmed* position on screen.

import { Chess } from "chess.js";
import { useEffect, useMemo, useRef, useState } from "react";
import { Chessboard } from "react-chessboard";

import { BoardKeyboard, useVisualBoardA11y } from "./board-keyboard";

const lightSquareStyle = { background: "var(--color-board-light)" };
const darkSquareStyle = { background: "var(--color-board-dark)" };

export function LiveBoard({
  fen,
  version,
  orientation = "white",
  disabled = false,
  pending = false,
  onMove,
  lastMove = null,
  checkSquare = null,
}: {
  fen: string;
  /** The server version. A change re-syncs the board to the server's FEN. */
  version: number;
  orientation?: "white" | "black";
  disabled?: boolean;
  pending?: boolean;
  onMove: (uci: string, san: string) => void;
  lastMove?: { from: string; to: string } | null;
  checkSquare?: string | null;
}) {
  const [position, setPosition] = useState(fen);
  const [selected, setSelected] = useState<string | null>(null);
  const positionRef = useRef(position);
  useEffect(() => {
    positionRef.current = position;
  }, [position]);

  // The visual board is pointer-only; the keyboard layer beside it is the
  // accessible control, so the library's nameless pieces leave the tab order.
  const boardRef = useRef<HTMLDivElement>(null);
  useVisualBoardA11y(boardRef);

  // Any server response (accepted or refused) carries a new version, which is
  // the trigger to snap back to the authoritative position.
  useEffect(() => {
    void Promise.resolve().then(() => {
      setPosition(fen);
      setSelected(null);
    });
  }, [fen, version]);

  const squareStyles = useMemo(() => {
    const styles: Record<string, React.CSSProperties> = {};
    if (selected) {
      styles[selected] = { boxShadow: "inset 0 0 0 3px var(--color-emerald-primary, #34d399)" };
    }
    if (lastMove) {
      for (const square of [lastMove.from, lastMove.to]) {
        styles[square] = { background: "rgba(96, 165, 250, 0.30)" };
      }
    }
    if (checkSquare) {
      styles[checkSquare] = {
        background: "radial-gradient(circle, rgba(248,113,113,0.75) 0%, rgba(248,113,113,0.15) 70%)",
      };
    }
    return styles;
  }, [selected, lastMove, checkSquare]);

  function play(from: string, to: string): boolean {
    if (disabled || pending) return false;
    const chess = new Chess(positionRef.current);
    let move;
    try {
      // Promotion defaults to a queen; the server grades the real move regardless.
      move = chess.move({ from, to, promotion: "q" });
    } catch {
      return false;
    }
    if (!move) return false;
    const uci = `${move.from}${move.to}${move.promotion ?? ""}`;
    const next = chess.fen();
    setPosition(next);
    positionRef.current = next;
    setSelected(null);
    onMove(uci, move.san);
    return true;
  }

  // The keyboard layer reports a UCI already; route it through the same `play`
  // path as dragging so the optimistic position matches either way.
  function playUci(uci: string): void {
    play(uci.slice(0, 2), uci.slice(2, 4));
  }

  return (
    <div className="group w-full max-w-[520px]">
      <div
        ref={boardRef}
        className="relative w-full overflow-hidden rounded-2xl border border-ink-600 shadow-[0_24px_60px_-30px_rgba(8,12,22,0.55)]"
      >
        <Chessboard
          options={{
            position,
            boardOrientation: orientation,
            allowDragging: !disabled && !pending,
            allowDrawingArrows: true,
            animationDurationInMs: 180,
            darkSquareStyle,
            lightSquareStyle,
            squareStyles,
            onPieceDrop: ({ sourceSquare, targetSquare }) =>
              targetSquare ? play(sourceSquare, targetSquare) : false,
            onSquareClick: ({ square }) => {
              if (disabled || pending) return;
              const chess = new Chess(positionRef.current);
              const piece = chess.get(square as never);
              if (selected && selected !== square) {
                const moved = play(selected, square);
                if (!moved) setSelected(piece ? square : null);
                return;
              }
              if (piece && piece.color === chess.turn()) setSelected(square);
              else setSelected(null);
            },
          }}
        />
        <BoardKeyboard
          fen={position}
          orientation={orientation}
          disabled={disabled || pending}
          onMove={playUci}
        />
        {pending ? (
          <div className="pointer-events-none absolute right-2 top-2 rounded-full bg-ink-900/80 px-2 py-1 text-meta text-mist-300">
            confirming…
          </div>
        ) : null}
      </div>
      <p className="mt-2 text-meta text-mist-500 opacity-0 transition-opacity duration-200 group-focus-within:opacity-100">
        Keyboard: arrow keys move the cursor, Enter picks up and drops.
      </p>
    </div>
  );
}
