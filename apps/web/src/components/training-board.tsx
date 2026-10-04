"use client";

// The interactive training board.
//
// Unlike the analysis board, this one must *accept* moves — the whole point of a
// puzzle is to play the answer. Legality is enforced client-side with chess.js so
// an illegal drag never leaves the board; the server still validates every
// submission, because the client is a convenience, not the authority.
//
// The board never knows the solution: it renders whatever FEN it is handed and
// reports the move the user made. Revealing the answer is the parent's job.

import { Chess } from "chess.js";
import { useEffect, useMemo, useRef, useState } from "react";
import { Chessboard } from "react-chessboard";

import { BoardKeyboard, useVisualBoardA11y } from "./board-keyboard";

const lightSquareStyle = { background: "var(--color-board-light)" };
const darkSquareStyle = { background: "var(--color-board-dark)" };

export function TrainingBoard({
  fen,
  orientation = "white",
  disabled = false,
  onMove,
  highlighted = [],
}: {
  fen: string;
  orientation?: "white" | "black";
  disabled?: boolean;
  /** Called with the UCI of a legal move the user played. */
  onMove: (uci: string, san: string) => void;
  /** Extra square highlights, e.g. the revealed solution or a wrong attempt. */
  highlighted?: { square: string; color: string }[];
}) {
  const [position, setPosition] = useState(fen);
  const [selected, setSelected] = useState<string | null>(null);
  const [lastMove, setLastMove] = useState<{ from: string; to: string } | null>(null);
  const positionRef = useRef(position);
  useEffect(() => {
    positionRef.current = position;
  }, [position]);

  // The visual board is pointer-only; the keyboard layer beside it is the
  // accessible control. This takes the library's nameless pieces out of the
  // tab order without disabling dragging.
  const boardRef = useRef<HTMLDivElement>(null);
  useVisualBoardA11y(boardRef);

  // A new exercise (or a reset) replaces the position outright. The update is
  // deferred to a microtask so it lands after render rather than during the
  // effect body, which keeps it out of the cascading-render path the lint rule
  // (correctly) warns about.
  useEffect(() => {
    void Promise.resolve().then(() => {
      setPosition(fen);
      setSelected(null);
      setLastMove(null);
    });
  }, [fen]);

  const squareStyles = useMemo(() => {
    const styles: Record<string, React.CSSProperties> = {};
    if (selected) styles[selected] = { boxShadow: "inset 0 0 0 3px var(--color-emerald-primary, #34d399)" };
    if (lastMove) {
      for (const square of [lastMove.from, lastMove.to]) {
        styles[square] = { background: "rgba(52, 211, 153, 0.28)" };
      }
    }
    for (const entry of highlighted) {
      styles[entry.square] = { background: entry.color };
    }
    return styles;
  }, [selected, lastMove, highlighted]);

  function play(from: string, to: string): boolean {
    if (disabled) return false;
    const chess = new Chess(positionRef.current);
    let move;
    try {
      // Promotion defaults to a queen; a promotion puzzle is rare, and the
      // server grades the real move regardless.
      move = chess.move({ from, to, promotion: "q" });
    } catch {
      return false;
    }
    if (!move) return false;
    const uci = `${move.from}${move.to}${move.promotion ?? ""}`;
    setPosition(chess.fen());
    setSelected(null);
    setLastMove({ from: move.from, to: move.to });
    onMove(uci, move.san);
    return true;
  }

  // The keyboard layer reports a UCI already; route it through the same `play`
  // path as dragging so the board's own position updates identically either way.
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
            allowDragging: !disabled,
            allowDrawingArrows: true,
            animationDurationInMs: 200,
            darkSquareStyle,
            lightSquareStyle,
            squareStyles,
            onPieceDrop: ({ sourceSquare, targetSquare }) =>
              targetSquare ? play(sourceSquare, targetSquare) : false,
            onSquareClick: ({ square }) => {
              if (disabled) return;
              const chess = new Chess(positionRef.current);
              const piece = chess.get(square as never);
              if (selected && selected !== square) {
                // Second click: try to complete the move; otherwise reselect.
                const moved = play(selected, square);
                if (!moved) setSelected(piece ? square : null);
                return;
              }
              if (piece && piece.color === chess.turn()) setSelected(square);
              else setSelected(null);
            },
          }}
        />
        <BoardKeyboard fen={position} orientation={orientation} disabled={disabled} onMove={playUci} />
      </div>
      <p className="mt-2 text-meta text-mist-500 opacity-0 transition-opacity duration-200 group-focus-within:opacity-100">
        Keyboard: arrow keys move the cursor, Enter picks up and drops.
      </p>
    </div>
  );
}
