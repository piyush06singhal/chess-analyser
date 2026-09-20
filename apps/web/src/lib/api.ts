// Typed API client for the ARGUS backend.
// The frontend contains no business logic: it only calls the backend and
// renders typed responses. All analysis authority lives behind the API.

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";

export interface ApiErrorBody {
  error: { code: string; message: string; details?: unknown };
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error?.message ?? `Request failed with status ${status}`);
    this.code = body.error?.code ?? "unknown_error";
    this.status = status;
  }
}

// --- response types (mirroring the backend schemas) ---------------------------

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
  engine: EngineInfo;
  database: { configured: boolean; connected: boolean; reason?: string; dialect?: string };
  redis: { configured: boolean; connected: boolean; reason?: string };
}

export interface EngineInfo {
  available: boolean;
  engine: string;
  path: string | null;
  version: string | null;
  running: boolean;
  reason?: string;
}

export interface GameSummary {
  total_moves: number;
  classified_moves: number;
  unclassified_moves: number;
  white: { average_centipawn_loss: number | null; counts: Record<string, number> };
  black: { average_centipawn_loss: number | null; counts: Record<string, number> };
  phase_move_counts: Record<string, number>;
  sacrifices: number;
  turning_point_ply: number | null;
}

export interface CriticalMoment {
  ply: number;
  move_number: number;
  color: "white" | "black";
  san: string;
  best_move_san: string | null;
  fen_before: string;
  evaluation_before_cp: number | null;
  evaluation_change_cp: number | null;
  centipawn_loss: number | null;
  classification: string | null;
  phase: string;
  description: string;
}

export interface GameReport {
  game_id: string | null;
  engine: string;
  engine_version: string | null;
  depth: number;
  result: string;
  white_player: string;
  black_player: string;
  summary: GameSummary;
  opening: {
    eco_code: string | null;
    name: string | null;
    variation: string | null;
    from_headers: boolean;
    note: string | null;
  };
  critical_moments: CriticalMoment[];
  best_moves: CriticalMoment[];
  turning_point: CriticalMoment | null;
  pending_sections: string[];
}

export interface GameMoveRow {
  ply: number;
  move_number: number;
  color: "white" | "black";
  san: string;
  uci: string;
  fen_before: string;
  fen_after: string;
}

export interface GameDetail {
  id: string;
  white_player: string;
  black_player: string;
  white_rating: number | null;
  black_rating: number | null;
  result: string;
  date: string | null;
  event: string | null;
  site: string | null;
  time_control: string | null;
  eco_code: string | null;
  opening_name: string | null;
  initial_position: string;
  final_position: string;
  moves: GameMoveRow[];
}

export interface StoredAnalysisRow {
  ply: number;
  move_number: number;
  color: "white" | "black";
  fen: string;
  played_move: string;
  best_move: string | null;
  evaluation_before: number | null;
  evaluation_after: number | null;
  evaluation_change: number | null;
  depth: number;
  centipawn_loss: number | null;
  classification: string | null;
  phase: string | null;
  is_sacrifice: boolean;
  principal_variation: string[];
  engine: string;
  engine_version: string | null;
}

export interface StoredAnalysis {
  game_id: string;
  positions_analyzed: number;
  note: string | null;
  analyses: StoredAnalysisRow[];
}

export interface GameImportResponse {
  game_id: string;
  moves: number;
  analyzed: boolean;
  engine: EngineInfo | null;
}

export interface GameListItem {
  id: string;
  white_player: string;
  black_player: string;
  white_rating: number | null;
  black_rating: number | null;
  result: string;
  date: string | null;
  event: string | null;
  opening_name: string | null;
  move_count: number;
}

export interface PositionAnalysisResponse {
  fen: string;
  depth: number;
  multipv: number;
  best_move_uci: string | null;
  best_move_san: string | null;
  lines: {
    index: number;
    depth: number;
    move_uci: string;
    move_san: string | null;
    cp: number | null;
    mate: number | null;
    pv: string[];
  }[];
  is_terminal: boolean;
  terminal_reason: string | null;
  engine: string;
  engine_version: string | null;
}

// --- endpoints ------------------------------------------------------------------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    let body: ApiErrorBody;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      body = { error: { code: "http_error", message: response.statusText } };
    }
    throw new ApiError(response.status, body);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  listGames: () => request<{ games: GameListItem[]; count: number }>("/api/games"),

  getGame: (gameId: string) => request<GameDetail>(`/api/games/${gameId}`),

  getStoredAnalysis: (gameId: string) => request<StoredAnalysis>(`/api/analysis/${gameId}`),

  importGame: (body: {
    pgn_text: string;
    run_analysis: boolean;
    depth?: number;
    multipv?: number;
  }) =>
    request<GameImportResponse>("/api/games/import", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  analyzeGame: (body: { game_id: string; depth?: number; multipv?: number }) =>
    request<{ game_id: string; report: GameReport }>("/api/analysis/game", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  analyzePosition: (body: { fen: string; depth?: number; multipv?: number }) =>
    request<PositionAnalysisResponse>("/api/analysis/position", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
