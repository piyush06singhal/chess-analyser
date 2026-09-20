"use client";

import { Chessboard } from "react-chessboard";

// Read-only analysis board: positions come from the backend's analysis data;
// dragging is disabled, right-click arrow drawing stays enabled for exploring.
export function AnalysisChessboard({
  fen,
  orientation = "white",
}: {
  fen: string;
  orientation?: "white" | "black";
}) {
  return (
    <div className="w-full max-w-md">
      <Chessboard
        options={{
          position: fen,
          boardOrientation: orientation,
          allowDragging: false,
          allowDrawingArrows: true,
          animationDurationInMs: 200,
        }}
      />
    </div>
  );
}
