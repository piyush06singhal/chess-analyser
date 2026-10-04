import { Chess } from "chess.js";

// Caissa convention: positive = White is better. Engine values arrive from the
// mover's perspective, so they are normalized here (mirrors the backend's
// argus.analysis.perspective module).

export function whitePerspective(
  cp: number | null,
  mate: number | null,
  mover: "white" | "black"
): { cp: number | null; mate: number | null } {
  if (mover === "white") return { cp, mate };
  return {
    cp: cp === null ? null : -cp,
    mate: mate === null ? null : -mate,
  };
}

export function formatEvaluation(cp: number | null, mate: number | null): string {
  if (mate !== null && mate !== undefined) {
    return mate > 0 ? `#${mate}` : `#-${Math.abs(mate)}`;
  }
  if (cp === null || cp === undefined) return "—";
  return `${cp >= 0 ? "+" : ""}${(cp / 100).toFixed(2)}`;
}

/**
 * Convert an engine principal variation (UCI) into SAN, starting from a FEN.
 * Returns as many moves as remain legal (a truncated PV is fine).
 */
export function pvToSan(fenBefore: string, pv: string[], maxMoves = 8): string[] {
  const board = new Chess(fenBefore);
  const moves: string[] = [];
  for (const uci of pv.slice(0, maxMoves)) {
    try {
      const move = board.move({
        from: uci.slice(0, 2),
        to: uci.slice(2, 4),
        promotion: uci.length > 4 ? uci.slice(4, 5) : undefined,
      });
      moves.push(move.san);
    } catch {
      break;
    }
  }
  return moves;
}

/**
 * Map a White-perspective evaluation to a 0..1 share of the bar for White.
 * Values are visually bounded (a +15 edge should not overflow the UI) while
 * the numeric evaluation is always displayed separately and unclipped.
 */
export function evalShare(cp: number | null, mate: number | null): number {
  if (mate !== null && mate !== undefined) return mate > 0 ? 1 : 0;
  if (cp === null || cp === undefined) return 0.5;
  const clamped = Math.max(-1000, Math.min(1000, cp));
  return 0.5 + clamped / 2000;
}
