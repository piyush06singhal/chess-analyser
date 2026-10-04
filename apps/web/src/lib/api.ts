// Typed API client for the Caissa backend.
// The frontend contains no business logic: it only calls the backend and
// renders typed responses. All analysis authority lives behind the API.

// Browser requests use the public URL; server-side rendering (which runs inside
// the web container) uses an internal URL that can reach the API service by
// name. Without this, server components would try to reach the public host
// (e.g. localhost:8002) from inside the container and fail with ECONNREFUSED.
const PUBLIC_API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";
const INTERNAL_API_URL = process.env.API_INTERNAL_URL ?? PUBLIC_API_URL;

export const API_BASE_URL =
  typeof window === "undefined" ? INTERNAL_API_URL : PUBLIC_API_URL;

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

/** One dependency probe from ``GET /ready``. The shape differs per check. */
export interface ReadyCheck {
  ok: boolean;
  detail?: unknown;
  name?: string;
  version?: string | null;
  configured?: boolean;
  provider?: string | null;
  expected?: number;
  present?: number;
  missing?: string[];
  registered?: number;
  production_models?: string[];
}

/**
 * Readiness: whether this instance can serve, with each dependency's honest
 * status. ``status`` is ``ready`` or ``degraded``; ``blocking`` names the checks
 * that made it degraded (only the database and the engine can).
 */
export interface ReadyResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
  blocking: string[];
  checks: Record<string, ReadyCheck>;
  checked_at: string;
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
  analysis_status: AnalysisStatus;
  analysis_depth: number | null;
  analysis_error: string | null;
  source: string;
  moves: GameMoveRow[];
}

export type AnalysisStatus =
  | "imported"
  | "validating"
  | "ready"
  | "analyzing"
  | "analyzed"
  | "failed";

export interface GamePosition {
  ply: number;
  move_number: number;
  side_to_move: "white" | "black";
  fen: string;
  san: string | null;
  uci: string | null;
  previous_fen: string | null;
  resulting_fen: string;
  is_check: boolean;
  is_checkmate: boolean;
  is_stalemate: boolean;
  is_terminal: boolean;
  terminal_reason: string | null;
}

export interface GamePositionsResponse {
  game_id: string;
  count: number;
  positions: GamePosition[];
}

export interface GameStatus {
  game_id: string;
  analysis_status: AnalysisStatus;
  analysis_depth: number | null;
  positions_analyzed: number;
  analysis_error: string | null;
  updated_at: string | null;
}

export interface ValidationIssue {
  type: string;
  message: string;
  game_index: number | null;
  move_number: number | null;
  ply: number | null;
}

export interface PgnValidationResponse {
  is_valid: boolean;
  game_count: number;
  ply_count: number;
  issues: ValidationIssue[];
  errors: string[];
  available_sources: string[];
  planned_sources: string[];
}

export interface MoveAnalysisRow {
  ply: number;
  move_number: number;
  mover: "white" | "black";
  played_move_uci: string;
  played_move_san: string;
  best_move_uci: string | null;
  best_move_san: string | null;
  evaluation_before_cp: number | null;
  evaluation_before_mate: number | null;
  evaluation_after_cp: number | null;
  evaluation_after_mate: number | null;
  evaluation_change_cp: number | null;
  centipawn_loss: number | null;
  classification: string | null;
  is_best_move: boolean;
  phase: string | null;
  depth: number;
  principal_variation: string[];
}

export interface GameMoveAnalyses {
  game_id: string;
  //: The analysis generation actually read from storage — null when nothing is
  //: stored. Never the frontend's or the API's own version constant.
  analysis_version: string | null;
  //: False when the served generation does not cover every ply (an interrupted
  //: re-analysis), so a partial game is never presented as a complete one.
  analysis_complete?: boolean;
  count: number;
  moves: MoveAnalysisRow[];
}

export interface AnalysisProgress {
  game_id: string;
  analysis_status: AnalysisStatus;
  has_session: boolean;
  status: string | null;
  current_position: number;
  total_positions: number;
  positions_analyzed: number;
  profile?: string | null;
  depth?: number | null;
  multipv?: number | null;
  movetime_ms?: number | null;
  analysis_version?: string;
  engine?: string;
  engine_version?: string | null;
  duration_seconds?: number | null;
  error: string | null;
}

export interface CriticalMomentRow {
  ply: number;
  move_number: number;
  color: "white" | "black";
  san: string;
  fen_before: string;
  evaluation_before_white: number | null;
  evaluation_after_white: number | null;
  swing_cp: number | null;
  classification: string | null;
  reason: string;
  severity: "low" | "medium" | "high";
  severity_score: number;
  is_mate_related: boolean;
  detail: string | null;
}

export interface EngineMultiPvLine {
  index: number;
  depth: number;
  move_uci: string;
  move_san: string | null;
  cp: number | null;
  mate: number | null;
  pv: string[];
  nodes?: number | null;
  nps?: number | null;
}

export interface AnalyzedPositionResponse {
  fen: string;
  depth: number;
  multipv: number;
  best_move_uci: string | null;
  best_move_san: string | null;
  lines: EngineMultiPvLine[];
  nodes: number | null;
  nps: number | null;
  is_terminal: boolean;
  terminal_reason: string | null;
  engine: string;
  engine_version: string | null;
}

export interface GameImportResponse {
  game_id: string;
  moves: number;
  analyzed: boolean;
  analysis_status: AnalysisStatus;
  source: string | null;
  warnings: string[];
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
  eco_code: string | null;
  opening_name: string | null;
  move_count: number;
  analysis_status: AnalysisStatus;
  source: string;
  source_game_id: string | null;
  created_at: string | null;
}

// --- Phase 4: game intelligence report ----------------------------------------

export type Side = "white" | "black";
export type EvidenceSourceLevel =
  | "engine_fact"
  | "argus_derived_feature"
  | "argus_interpretation";
export type CertaintyLevel = "confirmed" | "candidate";

export interface ReportFinding {
  key: string;
  statement: string;
  source: EvidenceSourceLevel;
  ply: number | null;
  move_number: number | null;
  side: Side | null;
  evidence: Record<string, unknown>;
}

export interface ReportRecommendation {
  key: string;
  focus: string;
  rationale: string;
  evidence_refs: number[];
  observed_count: number;
  source: EvidenceSourceLevel;
}

export interface ReportUnavailable {
  section: string;
  reason: string;
  required: string | null;
}

export interface TrajectoryPointRow {
  ply: number;
  move_number: number;
  san: string | null;
  side_to_move: Side | null;
  evaluation_cp_white: number | null;
  mate_white: number | null;
  available: boolean;
  white_state: string | null;
  black_state: string | null;
  material_balance: number | null;
  classification: string | null;
  evaluation_display: string;
}

export interface TrajectorySegmentRow {
  state: string | null;
  start_ply: number;
  end_ply: number;
  length: number;
  stable: boolean;
}

export interface TrajectoryEventRow {
  type: string;
  side: Side;
  start_ply: number;
  end_ply: number;
  statement: string;
  source: EvidenceSourceLevel;
  certainty: CertaintyLevel;
  evidence: Record<string, unknown>;
}

export interface TimelineRow {
  ply: number;
  move_number: number | null;
  san: string | null;
  side: Side | null;
  kind: string;
  label: string;
  statement: string;
  severity: string;
  source: EvidenceSourceLevel;
  certainty: CertaintyLevel;
  evidence: Record<string, unknown>;
}

export interface PhaseTransitionRow {
  ply: number;
  move_number: number;
  from_phase: string;
  to_phase: string;
  reasons: string[];
  statement: string;
}

export interface PhaseStatsRow {
  phase: string;
  side: Side;
  moves: number;
  evaluated_moves: number;
  average_centipawn_loss: number | null;
  average_evaluation_change_cp: number | null;
  problem_moves: number;
  counts: Record<string, number>;
  tactical_events: number;
  plies: number[];
  small_sample: boolean;
  note: string | null;
}

export interface MaterialSnapshotRow {
  ply: number;
  move_number: number;
  white_points: number;
  black_points: number;
  balance: number;
  queens: number;
  rooks: number;
  minors: number;
  major_pieces: number;
}

export interface MaterialEventRow {
  type: string;
  ply: number;
  move_number: number;
  side: Side;
  san: string;
  balance_before: number;
  balance_after: number;
  delta: number;
  moved_piece: string | null;
  captured_piece: string | null;
  promoted_to: string | null;
  square: string | null;
  answered_on_same_square: boolean;
  certainty: CertaintyLevel;
  evidence: Record<string, unknown>;
}

export interface MaterialTimelinePayload {
  initial_balance: number;
  final_balance: number;
  snapshots: MaterialSnapshotRow[];
  events: MaterialEventRow[];
  transitions: MaterialEventRow[];
  captures: MaterialEventRow[];
  promotions: MaterialEventRow[];
  peak_white_balance: number;
  peak_black_balance: number;
  peak_white_ply: number | null;
  peak_black_ply: number | null;
  first_capture_ply: number | null;
  first_capture_move_number: number | null;
  total_captures: number;
  exchanges: number;
  note: string;
}

export interface TacticalEventRow {
  type: string;
  ply: number;
  move_number: number;
  side: Side;
  san: string;
  certainty: CertaintyLevel;
  severity: string;
  affected_pieces: string[];
  squares: string[];
  statement: string;
  source: EvidenceSourceLevel;
  engine_context: Record<string, unknown>;
  evidence: Record<string, unknown>;
}

export interface PositionalEventRow {
  type: string;
  ply: number;
  move_number: number;
  side: Side;
  san: string;
  classification: "feature" | "error_candidate";
  severity: string;
  certainty: CertaintyLevel;
  statement: string;
  engine_supported: boolean | null;
  evidence: Record<string, unknown>;
}

export interface KingSafetyEventRow {
  type: string;
  ply: number;
  move_number: number;
  side: Side;
  severity: string;
  statement: string;
  certainty: CertaintyLevel;
  evidence: Record<string, unknown>;
}

export interface AccuracyMoveRow {
  ply: number;
  move_number: number;
  side: Side;
  san: string;
  centipawn_loss: number | null;
  loss: number | null;
  accuracy: number | null;
  scored: boolean;
  excluded: boolean;
  exclusion_reason: string | null;
  evaluation_source: "same_search" | "resulting_position" | "unavailable";
}

export interface SideAccuracyRow {
  side: Side;
  accuracy: number | null;
  scored_moves: number;
  excluded_decided_moves: number;
  unscored_moves: number;
  average_centipawn_loss: number | null;
  best_moves: number;
  problem_moves: number;
  small_sample: boolean;
  /** Scored moves compared inside one engine search (exact, no cross-search noise). */
  exact_scores: number;
  /** Scored moves whose comparison needed a second search of the position after it. */
  approximate_scores: number;
}

/** One measured slice of a side's accuracy (a phase, an error type, a material state). */
export interface AccuracyGroupRow {
  key: string;
  label: string;
  scored_moves: number;
  accuracy: number | null;
  average_centipawn_loss: number | null;
  share_of_loss: number | null;
  small_sample: boolean;
}

export interface SideAccuracyBreakdown {
  side: Side;
  by_phase: AccuracyGroupRow[];
  by_classification: AccuracyGroupRow[];
  by_material_state: AccuracyGroupRow[];
}

export interface AccuracyBreakdownPayload {
  white: SideAccuracyBreakdown;
  black: SideAccuracyBreakdown;
  note: string;
}

export interface AccuracyPayload {
  white: SideAccuracyRow;
  black: SideAccuracyRow;
  moves: AccuracyMoveRow[];
  breakdown: AccuracyBreakdownPayload | null;
  methodology: string;
  disclaimer: string;
  scale_cp: number;
  note: string;
}

export interface TurningPointRow {
  type: string;
  ply: number;
  move_number: number;
  side: Side;
  san: string;
  evaluation_before_cp: number | null;
  evaluation_after_cp: number | null;
  swing_cp: number | null;
  classification: string | null;
  severity: string;
  severity_score: number;
  persistent: boolean | null;
  statement: string;
  certainty: CertaintyLevel;
  source: EvidenceSourceLevel;
  evidence: Record<string, unknown>;
}

export interface ConversionEventRow {
  type: string;
  side: Side;
  ply: number;
  move_number: number | null;
  peak_evaluation_white: number | null;
  peak_display: string | null;
  later_evaluation_white: number | null;
  later_display: string | null;
  statement: string;
  certainty: CertaintyLevel;
  evidence: Record<string, unknown>;
}

export interface CategorisedErrorRow {
  category: string;
  basis: string;
  ply: number;
  move_number: number;
  side: Side;
  san: string;
  classification: string | null;
  centipawn_loss: number | null;
  phase: string | null;
  evidence: Record<string, unknown>;
}

export interface ReportInsightRow {
  insight_type: string;
  category: string | null;
  source: EvidenceSourceLevel;
  certainty: CertaintyLevel;
  ply: number | null;
  move_number: number | null;
  side: Side | null;
  severity: string | null;
  statement: string | null;
  evidence: Record<string, unknown>;
}

export interface GameReportPayload {
  report_version: string;
  generated_at: string;
  game_id: string | null;
  context: {
    white_player: string;
    black_player: string;
    white_rating: number | null;
    black_rating: number | null;
    result: string;
    date: string | null;
    event: string | null;
    time_control: string | null;
    move_count: number;
  };
  provenance: {
    analysis_version: string | null;
    report_version: string;
    engine: string | null;
    engine_version: string | null;
    depth: number | null;
    multipv: number | null;
    movetime_ms: number | null;
    profile: string | null;
    positions_analyzed: number;
    moves_in_game: number;
    evaluated_moves: number;
    engine_calls_made_by_intelligence_layer: number;
  };
  /** Engine-derived outcome forecast (documented curve, never a trained model). */
  forecast?: { forecast: ResultForecast; facts: ReportFact[] } | null;
  summary: {
    white_player: string;
    black_player: string;
    white_rating: number | null;
    black_rating: number | null;
    result: string;
    result_label: string;
    date: string | null;
    event: string | null;
    time_control: string | null;
    moves: number;
    opening_name: string | null;
    eco_code: string | null;
    phase_with_largest_drop: Record<string, string | null>;
    facts: ReportFinding[];
  };
  opening: {
    identification: {
      source: string;
      eco: string | null;
      family: string | null;
      variation: string | null;
      name: string | null;
      matched_plies: number;
      header_eco: string | null;
      header_name: string | null;
      header_agreement: boolean | null;
      table_lines: number;
      note: string | null;
    };
    deviation: {
      deviated: boolean;
      ply: number | null;
      move_number: number | null;
      side: Side | null;
      played_san: string | null;
      last_book_ply: number;
      expected_continuation_san: string[];
      expected_continuation_uci: string[];
      note: string;
    };
    book_lines: number;
  };
  phases: {
    final_phase: string;
    phase_by_ply: Record<string, string>;
    transitions: PhaseTransitionRow[];
    phase_indicators_final: string[];
    detector_confidence_final: number | null;
    performance: {
      white: Record<string, PhaseStatsRow>;
      black: Record<string, PhaseStatsRow>;
      phase_move_counts: Record<string, number>;
      worst_phase_white: string | null;
      worst_phase_black: string | null;
      phase_source: string;
      note: string;
    };
    facts: ReportFinding[];
  };
  trajectory: {
    trajectory: {
      points: TrajectoryPointRow[];
      segments: TrajectorySegmentRow[];
      events: TrajectoryEventRow[];
      evaluated_plies: number;
      missing_plies: number;
      mate_plies: number;
      min_evaluation_white: number | null;
      max_evaluation_white: number | null;
      final_evaluation_white: number | null;
      final_evaluation_display: string | null;
      states_seen: string[];
      note: string;
    };
    advantage_thresholds: Record<string, unknown>;
    states_seen: string[];
    facts: ReportFinding[];
  };
  material: { timeline: MaterialTimelinePayload; facts: ReportFinding[] };
  tactical: {
    analysis: {
      events: TacticalEventRow[];
      confirmed_count: number;
      candidate_count: number;
      by_type: Record<string, number>;
      by_side: Record<string, number>;
      note: string;
    };
    facts: ReportFinding[];
  };
  positional: {
    analysis: {
      events: PositionalEventRow[];
      feature_count: number;
      error_candidate_count: number;
      by_type: Record<string, number>;
      by_side: Record<string, number>;
      weak_squares_white: string[];
      weak_squares_black: string[];
      note: string;
    };
    facts: ReportFinding[];
  };
  king_safety: {
    analysis: {
      snapshots: unknown[];
      events: KingSafetyEventRow[];
      worst_white: unknown;
      worst_black: unknown;
      note: string;
    };
    facts: ReportFinding[];
  };
  accuracy: { analysis: AccuracyPayload; facts: ReportFinding[] };
  conversion: {
    analysis: {
      events: ConversionEventRow[];
      by_side: Record<string, number>;
      note: string;
    };
    facts: ReportFinding[];
  };
  error_categories: {
    analysis: {
      errors: CategorisedErrorRow[];
      by_category: Record<string, number>;
      by_side_category: Record<string, Record<string, number>>;
      unclassified_count: number;
      note: string;
    };
    facts: ReportFinding[];
  };
  turning_points: {
    turning_points: TurningPointRow[];
    candidates_considered: number;
    dropped_by_cap: number;
    by_type: Record<string, number>;
    largest_swing_ply: number | null;
    note: string;
  };
  critical_moments: {
    engine_critical_moments: ReportInsightRow[];
    timeline: TimelineRow[];
    largest_swing_ply: number | null;
    note: string;
  };
  key_lessons: ReportFinding[];
  training_recommendations: ReportRecommendation[];
  unavailable: ReportUnavailable[];
  evidence_policy: string;
}

export interface StoredReportResponse {
  game_id: string;
  report_version: string;
  analysis_version: string | null;
  generated_at: string | null;
  source: "stored" | "generated";
  report: GameReportPayload;
}

export interface ReportStatus {
  game_id: string;
  has_report: boolean;
  report_version: string;
  analysis_version: string | null;
  generated_at: string | null;
  has_analysis: boolean;
  stored_move_analyses: number;
  analysis_status: AnalysisStatus;
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

// --- external sources (Chess.com, Lichess) ---------------------------------------

export interface SourceCatalogEntry {
  id: string;
  label: string;
  kind: string;
  available: boolean;
  description: string;
  supports_username_lookup: boolean;
}

export interface SourceCatalog {
  implemented: string[];
  planned: string[];
  sources: SourceCatalogEntry[];
}

/** Platform sources Caissa can read games from by username. */
export type PlatformSourceId = "chess_com" | "lichess";

/** Map a source id to the path segment its routes use. */
export function sourcePath(source: PlatformSourceId): string {
  return source === "chess_com" ? "chesscom" : source;
}

/**
 * A player as reported by a platform source.
 *
 * Each source fills in what it actually publishes: Chess.com reports followers
 * and a join date, Lichess reports total games and per-speed ratings. Fields a
 * source does not provide are absent, so the UI checks before rendering rather
 * than showing a placeholder that looks like data.
 */
export interface PlatformProfile {
  username: string;
  url: string | null;
  title: string | null;
  avatar?: string | null;
  followers?: number | null;
  joined?: string | null;
  status?: string | null;
  created_at?: string | null;
  seen_at?: string | null;
  total_games?: number | null;
  ratings?: Record<string, number>;
}

export interface PlatformArchive {
  year: number;
  month: number;
  label: string;
  url?: string;
}

export interface PlatformPlayerResponse {
  source: PlatformSourceId;
  profile: PlatformProfile;
  is_closed: boolean;
  months_requested: number;
  /** False when the platform publishes no archive index (Lichess). */
  months_are_published: boolean;
  months: PlatformArchive[];
  note: string;
}

export interface PlatformGameRow {
  url: string;
  pgn?: string;
  time_class: string | null;
  time_control?: string | null;
  rated: boolean | null;
  white_username: string | null;
  black_username: string | null;
  white_rating: number | null;
  black_rating: number | null;
  result: string | null;
  end_time?: string | null;
  played_at?: string | null;
  eco_code: string | null;
  opening_name?: string | null;
  ply_count: number | null;
  already_imported: boolean;
  game_id: string | null;
}

export interface PlatformMonthResponse {
  source: PlatformSourceId;
  username: string;
  year: number;
  month: number;
  time_class: string | null;
  count: number;
  variant_count: number;
  truncated: boolean;
  imported_count: number;
  games: PlatformGameRow[];
  note: string;
}

export interface PlatformImportResponse {
  source: PlatformSourceId;
  username: string;
  year: number;
  month: number;
  imported: {
    url: string;
    game_id: string;
    moves: number;
    analysis_status: string;
    queued_for_analysis: boolean;
  }[];
  skipped: { url: string; game_id: string; reason: string }[];
  failed: { url: string; code: string; error: string }[];
  counts: { requested: number; imported: number; skipped: number; failed: number };
}

// --- engine-derived outcome forecast --------------------------------------------

export interface OutcomeProbabilities {
  white: number;
  draw: number;
  black: number;
}

export interface ForecastPeak {
  side: "white" | "black";
  probability: number;
  ply: number;
  move_number: number;
}

export interface ResultForecast {
  at_start: OutcomeProbabilities | null;
  final: OutcomeProbabilities | null;
  peak_white: ForecastPeak | null;
  peak_black: ForecastPeak | null;
  decisive_ply: number | null;
  decisive_swing: number | null;
  evaluated_plies: number;
  unforecastable_plies: number;
  facts: ReportFact[];
  source: string;
  certainty: string;
  methodology: string;
  disclaimer: string;
}

export interface ReportFact {
  key: string;
  statement: string;
  source: EvidenceSourceLevel;
  ply: number | null;
  move_number: number | null;
  side?: string | null;
  evidence: Record<string, unknown>;
}

export interface CoachStatus {
  provider: string | null;
  model: string | null;
  configured: boolean;
  api_key_set: boolean;
}

export interface CoachChatResponse {
  message: string;
  tool_calls_used: number;
  tool_trace: { name: string; status: string }[];
}

// --- Phase 7: the Caissa AI chess agent ----------------------------------------

/** What kind of statement a claim is. The UI shows the distinction, not just the text. */
export type AgentClaimKind = "FACT" | "OBSERVATION" | "INTERPRETATION" | "COACHING";

/**
 * A button the agent offers, always backed by data it actually retrieved.
 *
 * `available: false` is a declared-but-unbuilt capability shown as such rather
 * than hidden — the gap stays visible. An action with an `href` navigates; one
 * without is a request the coach can run.
 */
export interface AgentAction {
  action: string;
  label: string;
  href: string | null;
  params: Record<string, unknown>;
  available: boolean;
  unavailable_reason: string | null;
}

/** One typed claim, and whether the validator could machine-check it. */
export interface AgentClaim {
  kind: AgentClaimKind | string;
  label: string;
  text: string;
  verified: boolean | null;
  note: string | null;
}

/** Where a fact came from: engine output, a board measurement, or a rule Caissa applies. */
export interface AgentEvidenceItem {
  kind: string;
  source: EvidenceSourceLevel;
  certainty: CertaintyLevel;
  tool: string;
  summary: string;
  ref: string;
  game_id: string | null;
  ply: number | null;
}

/** The evidence packet: what was retrieved, and what could not be. */
export interface AgentEvidence {
  items: AgentEvidenceItem[];
  /** Tools the agent wanted and could not use, each with the real reason. */
  missing: { tool: string; reason: string }[];
  limitations: string[];
  kinds: string[];
}

/** A record of one tool execution inside the turn. */
export interface AgentToolCall {
  tool: string;
  ok: boolean;
  duration_ms: number;
  error_code: string | null;
  error_message: string | null;
  arguments?: Record<string, unknown>;
  result_bytes?: number;
}

/** How the turn was produced: deterministic or generated, what it cost, what it checked. */
export interface AgentTrace {
  request_id?: string;
  mode?: string;
  context?: Record<string, unknown>;
  intents?: string[];
  tool_calls?: AgentToolCall[];
  tool_count?: number;
  failed_tool_count?: number;
  provider?: string | null;
  model?: string | null;
  prompt_version?: string | null;
  deterministic?: boolean;
  iterations?: number;
  validation?: { passed: boolean | null; checked: number };
  timings_ms?: Record<string, number>;
  total_ms?: number;
  status?: string;
  notes?: string[];
  evidence_items?: number;
}

export interface AgentTurnResponse {
  message: string;
  mode: string;
  deterministic: boolean;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  claims: AgentClaim[];
  actions: AgentAction[];
  validation: {
    passed: boolean;
    summary: string;
    checked: number;
    failures: { claim: string; kind: string; detail: string | null }[];
  };
  limitations: string[];
  trace: AgentTrace;
  evidence: AgentEvidence | null;
}

/** One entry in the agent's declared tool catalogue. */
export interface AgentToolEntry {
  name: string;
  description: string;
  permission: string;
  available: boolean;
  reason: string | null;
  outputs: string[];
  uses_engine: boolean;
  tags: string[];
  parameters: Record<string, unknown>;
}

export interface AgentToolCatalog {
  tools: AgentToolEntry[];
  count: number;
  available: string[];
  unavailable: Record<string, string>;
  prompt_version: string;
  note: string;
}

/** One agent turn's request: the question plus the board the user is standing in. */
export interface AgentAskRequest {
  question: string;
  game_id?: string | null;
  ply?: number | null;
  move_san?: string | null;
  fen?: string | null;
  player_id?: string | null;
  mode?: string;
  history?: { role: string; content: string }[];
  include_evidence?: boolean;
}

// --- Phase 5: player intelligence --------------------------------------------

/** How strong a statement the evidence supports. */
export type ClaimLevel = "insufficient" | "observation" | "pattern" | "tendency";
/** Data-coverage band — describes the sample, never the player. */
export type CoverageLevel = "insufficient" | "limited" | "moderate" | "robust";

export type InsightCategory =
  | "strength"
  | "weakness_candidate"
  | "recurring_pattern"
  | "improvement_trend"
  | "opening_pattern"
  | "tactical_pattern"
  | "positional_pattern"
  | "phase_pattern"
  | "conversion_pattern"
  | "recovery_pattern";

/** The sample behind any player-level number: games, events and what may be said. */
export interface SampleInfo {
  games: number;
  events: number;
  claim_level: ClaimLevel;
  coverage: CoverageLevel;
  note: string | null;
}

export interface EvidenceRef {
  game_id: string;
  ply: number;
  move_number: number | null;
  san: string | null;
  label: string | null;
}

export interface PlayerListItem {
  id: string;
  name: string;
  title: string | null;
  platform: string | null;
  platform_username: string | null;
  games: number;
  analyzed_games: number;
  wins: number;
  draws: number;
  losses: number;
}

export interface PlayerGameStatistics {
  analyzed_games: number;
  wins: number;
  draws: number;
  losses: number;
  win_rate: number | null;
  draw_rate: number | null;
  loss_rate: number | null;
  average_accuracy: number | null;
  median_accuracy: number | null;
  average_centipawn_loss: number | null;
  median_centipawn_loss: number | null;
  blunders_per_game: number | null;
  mistakes_per_game: number | null;
  inaccuracies_per_game: number | null;
  average_game_length: number | null;
  accuracy_sample: number;
  time_span: [string | null, string | null];
  sample: SampleInfo;
}

export interface PlayerColorStatistics {
  color: "white" | "black";
  games: number;
  wins: number;
  draws: number;
  losses: number;
  win_rate: number | null;
  average_accuracy: number | null;
  average_centipawn_loss: number | null;
  blunders: number;
  mistakes: number;
  inaccuracies: number;
  sample: SampleInfo;
}

export interface PlayerOpeningRow {
  key: string;
  eco_code: string | null;
  name: string | null;
  family: string | null;
  games: number;
  wins: number;
  draws: number;
  losses: number;
  win_rate: number | null;
  average_accuracy: number | null;
  average_centipawn_loss: number | null;
  deviation_games: number;
  recent_uses: number;
  sample: SampleInfo;
}

export interface PlayerOpeningStatistics {
  white_repertoire: PlayerOpeningRow[];
  black_repertoire: PlayerOpeningRow[];
  most_played: PlayerOpeningRow[];
  families: Record<string, number>;
  distinct_openings: number;
  deviation_rate: number | null;
  sample: SampleInfo;
}

export interface PlayerPhaseRow {
  phase: string;
  evaluated_moves: number;
  average_centipawn_loss: number | null;
  problem_moves: number;
  accuracy: number | null;
  share_of_loss: number | null;
  sample: SampleInfo;
}

export interface PlayerPhaseStatistics {
  phases: PlayerPhaseRow[];
  weakest_phase: string | null;
  strongest_phase: string | null;
  sample: SampleInfo;
}

export interface PlayerTacticalStatistics {
  created: Record<string, number>;
  allowed: Record<string, number>;
  created_total: number;
  allowed_total: number;
  created_per_game: number | null;
  allowed_per_game: number | null;
  missed_opportunities: number;
  sample: SampleInfo;
}

export interface PlayerPositionalStatistics {
  features: Record<string, number>;
  error_candidates: Record<string, number>;
  features_per_game: number | null;
  error_candidates_per_game: number | null;
  evidence: EvidenceRef[];
  sample: SampleInfo;
}

export interface PlayerKingSafetyStatistics {
  castled_games: number;
  uncastled_games: number;
  castling_rate: number | null;
  late_castling_games: number;
  events_created: number;
  events_allowed: number;
  top_event_types: Record<string, number>;
  by_color: Record<
    string,
    {
      games: number;
      castled_games: number;
      castling_rate: number | null;
      king_safety_events: number;
      events_per_game: number | null;
    }
  >;
  evidence: EvidenceRef[];
  sample: SampleInfo;
}

export interface PlayerMaterialStatistics {
  average_captures: number | null;
  average_exchanges: number | null;
  promotions: number;
  imbalance_games: number;
  imbalance_share: number | null;
  average_final_balance: number | null;
  sample: SampleInfo;
}

export interface PlayerConversionStatistics {
  opportunities: number;
  conversions: number;
  conversion_rate: number | null;
  advantage_lost: number;
  maintenance_rate: number | null;
  evidence: EvidenceRef[];
  sample: SampleInfo;
}

export interface PlayerRecoveryStatistics {
  situations: number;
  improvements: number;
  improvement_rate: number | null;
  saved_games: number;
  save_rate: number | null;
  evidence: EvidenceRef[];
  sample: SampleInfo;
}

export interface PlayerTimeControlRow {
  time_class: string;
  games: number;
  wins: number;
  draws: number;
  losses: number;
  win_rate: number | null;
  average_accuracy: number | null;
  average_centipawn_loss: number | null;
  blunders_per_game: number | null;
  average_game_length: number | null;
  conversion_rate: number | null;
  sample: SampleInfo;
}

export interface PlayerTimeControlStatistics {
  entries: PlayerTimeControlRow[];
  sample: SampleInfo;
}

export interface PlayerOpponentBucket {
  games: number;
  wins: number;
  draws: number;
  losses: number;
  win_rate: number | null;
  average_accuracy: number | null;
  average_centipawn_loss: number | null;
}

export interface PlayerOpponentContext {
  games_with_rating: number;
  average_player_rating: number | null;
  average_opponent_rating: number | null;
  average_rating_difference: number | null;
  by_bucket: Record<string, PlayerOpponentBucket>;
  sample: SampleInfo;
}

export interface PlayerTrendEntry {
  window: number;
  recent_games: number;
  baseline_games: number;
  recent_average_cpl: number | null;
  baseline_average_cpl: number | null;
  relative_change: number | null;
  direction: "lower_cpl" | "higher_cpl" | "steady" | "insufficient";
  supported: boolean;
  recent_wins: number;
  recent_draws: number;
  recent_losses: number;
  note: string | null;
}

export interface PlayerTrendStatistics {
  entries: PlayerTrendEntry[];
  note: string;
}

export interface ChessDnaDimension {
  key: string;
  label: string;
  value: number | null;
  unit: string;
  definition: string;
  games: number;
  events: number;
  coverage: CoverageLevel;
  claim_level: ClaimLevel;
  note: string | null;
}

export interface ChessDna {
  dimensions: ChessDnaDimension[];
  derived_from_games: number;
  notes: string[];
}

export interface PlayerInsight {
  id: string;
  category: InsightCategory;
  claim_level: ClaimLevel;
  title: string;
  statement: string;
  metric: string | null;
  value: number | null;
  unit: string | null;
  severity: string | null;
  games: number;
  occurrences: number;
  coverage: CoverageLevel;
  evidence: EvidenceRef[];
  methodology_version: string;
  generated_at: string;
}

export interface PlayerProfilePayload {
  player_id: string;
  display_name: string;
  platform: string | null;
  platform_username: string | null;
  profile_version: string;
  methodology_version: string;
  feature_version: string;
  generated_at: string;
  last_updated_at: string | null;
  imported_games: number;
  analyzed_games: number;
  excluded_games: number;
  excluded_game_ids: string[];
  coverage: CoverageLevel;
  sufficient_data: boolean;
  policy: Record<string, number | number[]>;
  games: PlayerGameStatistics;
  by_color: PlayerColorStatistics[];
  openings: PlayerOpeningStatistics;
  phases: PlayerPhaseStatistics;
  tactical: PlayerTacticalStatistics;
  positional: PlayerPositionalStatistics;
  king_safety: PlayerKingSafetyStatistics;
  material: PlayerMaterialStatistics;
  conversion: PlayerConversionStatistics;
  recovery: PlayerRecoveryStatistics;
  time_controls: PlayerTimeControlStatistics;
  opponents: PlayerOpponentContext;
  trends: PlayerTrendStatistics;
  chess_dna: ChessDna;
  insights: PlayerInsight[];
  notes: string[];
  cache?: { hit: boolean; generated_at?: string | null; updated_at?: string | null; signature?: string };
}

export interface PlayerFeature {
  name: string;
  value: number | null;
  unit: string;
  definition: string;
  sample_size: number;
  source: string;
  feature_version: string;
  /** Derived from this player's private games. */
  user_specific: boolean;
  /** Never pooled into a global training set by default. */
  training_eligible: boolean;
  note: string | null;
}

export interface PlayerFeatureSet {
  player_id: string;
  feature_version: string;
  generated_at: string;
  games_analyzed: number;
  data_range: [string | null, string | null];
  features: PlayerFeature[];
  notes: string[];
}

// --- Phase 8: personalized training engine ---------------------------------------

export interface TrainingMeta {
  methodology_version: string;
  hint_policy_version: string;
  categories: string[];
  category_labels: Record<string, string>;
  difficulties: string[];
  position_types: string[];
  session_kinds: string[];
  acceptance_policy: Record<string, number>;
  eligibility_policy: Record<string, number>;
  data_sources: string[];
  privacy: string;
  note: string;
}

export interface TrainingOrigin {
  game_id: string | null;
  ply: number | null;
  position_id: number | null;
  available: boolean;
}

export interface TrainingPositionSummary {
  id: number;
  player_id: number | null;
  data_source: "personalized" | "general" | "opponent_preparation";
  is_personalized: boolean;
  is_opponent_preparation?: boolean;
  source_reason: string;
  origin: TrainingOrigin;
  category: string;
  category_label: string;
  difficulty: string;
  difficulty_factors: Record<string, unknown>;
  position_type: string;
  side_to_move: string;
  fen: string;
  tags: string[];
  state: string;
  attempts: number;
  correct_attempts: number;
  streak: number;
  review_interval_days: number;
  next_review_at: string | null;
  last_attempted_at: string | null;
  created_at: string | null;
  methodology_version: string;
  engine: string;
  engine_version: string | null;
  depth: number;
  analysis_version: string;
  // Present only when the solution has been revealed.
  solution?: { uci: string; san: string; eval_cp: number | null; eval_mate: number | null };
  acceptable_moves?: Record<string, string>;
  principal_variation?: string[];
  // The engine's stored line after the solution (CONTINUE_LINE exercises).
  continuation_line?: string[];
  played_move?: { uci: string | null; san: string | null; eval_cp: number | null; loss_cp: number | null };
  candidate_moves?: Record<string, unknown>[];
}

export interface TrainingHintResponse {
  position_id: number;
  policy_version: string;
  hints: string[];
  revealed: string[];
  hint_index: number;
  remaining: number;
  note: string;
}

export interface TrainingExplanation {
  position_id: number;
  solution: { uci: string; san: string; eval_cp: number | null; eval_mate: number | null };
  category: string;
  category_label: string;
  position_type: string;
  difficulty: string;
  tags: string[];
  board_facts: string[];
  played_move: { san: string | null; uci: string | null; loss_cp: number | null };
  evaluation_delta_cp: number | null;
  reason: string;
  recurring_pattern: { tag_overlap: string[]; incorrect_attempts: number; statement: string } | null;
  principal_variation: string[];
  source: { game_id: string | null; ply: number | null; engine: string; depth: number; analysis_version: string };
  methodology_version: string;
  note: string;
}

export interface TrainingAttemptFeedback {
  position_id: number;
  outcome: "correct" | "near_best" | "incorrect";
  correct: boolean;
  reason: string;
  submitted_uci: string;
  submitted_san: string | null;
  submitted_eval_cp: number | null;
  evaluation_delta_cp: number | null;
  state: string;
  streak: number;
  attempts: number;
  correct_attempts: number;
  review_interval_days: number;
  next_review_at: string | null;
  attempt_id: number;
  solution: { uci: string; san: string; eval_cp: number | null; eval_mate: number | null };
  played_move: { uci: string | null; san: string | null; loss_cp: number | null };
  pv_available?: boolean;
  principal_variation?: string[];
  acceptable_moves?: Record<string, string>;
}

export interface TrainingProgress {
  attempts_total: number;
  correct_total: number;
  near_best_total: number;
  incorrect_total: number;
  accuracy_overall: number | null;
  library_size: number;
  mastered_count: number;
  due_count: number;
  by_category: Record<string, { category: string; label: string; attempts: number; correct: number; near_best: number; incorrect: number; decided: number; accuracy: number | null }>;
  by_difficulty: Record<string, { difficulty: string; attempts: number; correct: number; decided: number; accuracy: number | null }>;
  state_counts: Record<string, number>;
  retention: number | null;
  hints?: { total_hints_used: number; attempts_with_hints: number };
  response_time_ms?: { average: number | null; samples: number };
  note?: string;
}

export interface TrainingOpportunity {
  category: string;
  label: string;
  priority: number;
  factors: Record<string, number>;
  evidence: string[];
  reason: string;
}

export interface TrainingRecommendations {
  player_id: number;
  opportunities: TrainingOpportunity[];
  library_categories: string[];
  attempted_categories: string[];
  evidence_policy: string;
  categories_without_evidence: string[];
}

export interface TrainingReviewQueue {
  player_id: number | null;
  now: string;
  due: TrainingPositionSummary[];
  count: number;
  total_queued: number;
  note: string;
}

export interface TrainingSessionSummary {
  id: number;
  player_id: number;
  kind: string;
  target_category: string | null;
  status: "active" | "completed" | "cancelled";
  planned: number;
  completed: number;
  remaining: number;
  remaining_position_ids: number[];
  completed_position_ids: number[];
  planned_position_ids: number[];
  counts: { correct: number; near_best: number; incorrect: number };
  hints_used: number;
  score: number | null;
  started_at: string | null;
  completed_at: string | null;
  notes?: string[];
  describe?: Record<string, unknown>;
  current?: TrainingPositionSummary | null;
}

export interface TrainingGenerateResult {
  game_id: string;
  player_id: number | null;
  accepted: number;
  rejected: number;
  seen: number;
  reasons: Record<string, number>;
  skipped?: { ply: number | null; reason: string }[];
  generated: TrainingPositionSummary[];
  created: number[];
  note?: string;
}

// --- Phase 9: opponent intelligence ---------------------------------------------

export interface OpponentMeta {
  methodology_version: string;
  policy_defaults: Record<string, number>;
  claim_levels: string[];
  coverage_bands: string[];
  colors: string[];
  privacy: string;
}

export interface OpponentIdentity {
  player_id: number;
  name: string;
  identity_key?: string | null;
  platform?: string | null;
  platform_username?: string | null;
  title?: string | null;
}

export interface OpponentEvidence {
  game_id: string;
  ply?: number | null;
  move_number?: number | null;
  san?: string | null;
  label?: string | null;
  detail?: string | null;
}

export interface OpponentStatistics {
  total_games: number;
  analyzed_games: number;
  wins: number;
  draws: number;
  losses: number;
  as_white: number;
  as_black: number;
  avg_opponent_rating: number | null;
  first_date: string | null;
  last_date: string | null;
  result_share: Record<string, number>;
}

export interface OpponentOpeningNode {
  san: string;
  uci: string;
  fen: string;
  ply: number;
  move_number: number;
  occurrences: number;
  share: number;
  wins: number;
  draws: number;
  losses: number;
  claim_level: string;
  evidence: OpponentEvidence[];
}

export interface OpponentOpeningProfile {
  color: "white" | "black";
  total_games: number;
  analyzed_games: number;
  coverage: string;
  nodes: OpponentOpeningNode[];
  top_lines: string[];
  opening_families: Record<string, number>;
  policy: Record<string, number>;
  note: string;
  recent?: boolean;
  player_id?: number;
  identity?: OpponentIdentity;
}

export interface OpponentResponseOption {
  uci: string;
  san: string;
  occurrences: number;
  share: number;
  wins: number;
  draws: number;
  losses: number;
  evidence: OpponentEvidence[];
}

export interface OpponentPositionResponse {
  player_id?: number;
  query_fen: string;
  match: "exact" | "normalized_pieces" | "none";
  side_to_move?: string | null;
  occurrences: number;
  responses: OpponentResponseOption[];
  claim_level: string;
  sample_note: string;
  evidence: OpponentEvidence[];
}

export interface OpponentTendency {
  key: string;
  label: string;
  measurement: string;
  value: number | string;
  sample_size: number;
  share: number | null;
  claim_level: string;
  evidence: OpponentEvidence[];
  note: string;
}

export interface OpponentPhaseStat {
  phase: string;
  moves: number;
  games: number;
  significant_errors: number;
  error_rate: number | null;
  avg_centipawn_loss: number | null;
  avg_accuracy_proxy: number | null;
  claim_level: string;
}

export interface OpponentPhaseStatistics {
  player_id?: number;
  color: string | null;
  analyzed_games: number;
  phases: OpponentPhaseStat[];
  tactical_error_share: number | null;
  positional_error_share: number | null;
  sample_note: string;
  policy: Record<string, number>;
}

export interface OpponentInsight {
  key: string;
  title: string;
  statement: string;
  category: string;
  claim_level: string;
  sample_size: number;
  evidence: OpponentEvidence[];
  preparation_hint: string;
}

export interface OpponentPositionPattern {
  key: string;
  label: string;
  fen_signature: string;
  occurrences: number;
  side_to_move: string;
  responses: OpponentResponseOption[];
  wins: number;
  draws: number;
  losses: number;
  claim_level: string;
  evidence: OpponentEvidence[];
}

export interface OpponentProfile {
  player_id?: number;
  identity: OpponentIdentity;
  methodology_version: string;
  generated_at: string;
  statistics: OpponentStatistics;
  coverage: string;
  repertoire: Record<string, OpponentOpeningProfile>;
  phase_statistics: OpponentPhaseStatistics | null;
  tendencies: OpponentTendency[];
  position_patterns: OpponentPositionPattern[];
  policy: Record<string, number>;
  limitations: string[];
  stale?: boolean;
  rebuilt?: boolean;
  source_signature?: string;
}

export interface OpponentGameRow {
  game_id: string;
  color: string;
  opponent_name: string;
  opponent_rating: number | null;
  other_rating: number | null;
  result: string;
  outcome: string;
  date: string | null;
  event: string | null;
  time_control: string | null;
  eco_code: string | null;
  opening_name: string | null;
  move_count: number;
  source: string | null;
  analysis_status: string | null;
  analysis_version: string | null;
}

export interface OpponentGamesResponse {
  player_id: number;
  identity: OpponentIdentity;
  total: number;
  offset: number;
  limit: number;
  statistics: OpponentStatistics;
  games: OpponentGameRow[];
}

export interface OpponentPreparationReport {
  player_id?: number;
  identity: OpponentIdentity;
  methodology_version: string;
  generated_at: string;
  color_to_prepare: string | null;
  statistics: OpponentStatistics;
  coverage: string;
  repertoire: OpponentOpeningProfile | null;
  phase_statistics: OpponentPhaseStatistics | null;
  tendencies: OpponentTendency[];
  insights: OpponentInsight[];
  expected_lines: OpponentOpeningNode[];
  policy: Record<string, number>;
  evidence_count: number;
  limitations: string[];
}

export interface OpponentResponsesResponse {
  player_id: number;
  identity: OpponentIdentity;
  methodology_version: string;
  position_patterns: OpponentPositionPattern[];
  note: string;
}

export interface OpponentTendenciesResponse {
  player_id: number;
  identity: OpponentIdentity;
  methodology_version: string;
  policy: Record<string, number>;
  tendencies: OpponentTendency[];
  note: string;
}

export interface TrainingContinueResult {
  position_id: number;
  type: string;
  outcome: "correct" | "incorrect";
  correct_moves: number;
  expected_moves: number;
  moves: {
    ply: number;
    expected_uci: string;
    expected_san?: string;
    submitted_uci?: string | null;
    submitted_san?: string | null;
    correct: boolean;
    played_engine_move: boolean;
    legal?: boolean;
  }[];
  engine_line: string[];
  solution_uci: string;
  solution_san: string;
  persisted: boolean;
  note: string;
}

// --- Phase 10: decision intelligence (counterfactuals) ------------------------

/** One candidate move, with the engine's own numbers attached. */
export interface ScenarioCandidate {
  uci: string;
  san?: string | null;
  legal: boolean;
  legality_note?: string | null;
  rank?: number | null;
  cp?: number | null;
  mate?: number | null;
  cp_white?: number | null;
  centipawn_loss?: number | null;
  quality: string;
  depth?: number | null;
  pv: string[];
  pv_san: string[];
  is_engine_best: boolean;
  is_played_move: boolean;
  material_consequence?: string | null;
  tactical_consequence: string[];
  resulting_phase?: string | null;
  position_type?: string | null;
  eval_source: string;
}

export interface ScenarioCandidateComparison {
  fen: string;
  side_to_move: string;
  phase: string;
  engine_config: { depth?: number | null; multipv: number; engine: string; engine_version?: string | null };
  candidates: ScenarioCandidate[];
  best_cp?: number | null;
  best_move_uci?: string | null;
  truncated: boolean;
  notes: string[];
  source?: { kind: string; game_id?: string | null; ply?: number | null; played_move_san?: string | null };
  methodology_version: string;
}

export interface ScenarioContinuationPly {
  ply: number;
  move_number: number;
  color: string;
  uci: string;
  san?: string | null;
  cp?: number | null;
  mate?: number | null;
  cp_white?: number | null;
}

export interface ScenarioBranch {
  scenario_type: string;
  source_fen: string;
  alternative_move_uci: string;
  alternative_move_san?: string | null;
  actual_move_uci?: string | null;
  actual_move_san?: string | null;
  engine_config: { depth?: number | null; multipv: number; engine: string };
  actual_continuation: ScenarioContinuationPly[];
  alternative_continuation: ScenarioContinuationPly[];
  actual_eval_cp?: number | null;
  alternative_eval_cp?: number | null;
  actual_eval_source: string;
  alternative_eval_source: string;
  evaluation_change_cp?: number | null;
  comparison?: { engine_difference: Record<string, unknown>; structural_differences: {
    domain: string;
    feature: string;
    value_a?: number | null;
    value_b?: number | null;
    direction: string;
  }[]; notes: string[] } | null;
  plies_requested: number;
  truncated: boolean;
  notes: string[];
}

export interface ScenarioExplanation {
  question: string;
  facts: string[];
  numbers: Record<string, unknown>;
  insufficient: boolean;
  unavailable_reason?: string | null;
}

/** A counterfactual answer: `status` may be a refusal, which is a result too. */
export interface ScenarioOutcome {
  status: string;
  message?: string | null;
  branch?: ScenarioBranch | null;
  explanation?: ScenarioExplanation | null;
  prediction?: { available: boolean; task?: string; reason?: string; detail?: string } | null;
  scenario_id?: number | null;
  source?: { kind: string; game_id?: string | null; ply?: number | null; actual_move_san?: string | null };
  methodology_version?: string;
}

export interface ScenarioWhyNot {
  status: string;
  message?: string | null;
  move?: ScenarioCandidate;
  quality?: string;
  best_response?: string | null;
  critical_issue?: string | null;
  better_alternatives: ScenarioCandidate[];
  explanation: ScenarioExplanation;
  engine_config: { depth?: number | null; multipv: number };
  source?: { kind: string; game_id?: string | null; ply?: number | null; actual_move_uci?: string | null };
}

export interface ScenarioTurningPoint {
  ply: number;
  move_number: number;
  color: string;
  san: string;
  classification?: string | null;
  phase?: string | null;
  swing_cp?: number | null;
  centipawn_loss?: number | null;
  best_move_san?: string | null;
  is_critical: boolean;
  critical_reason?: string | null;
  severity?: string | null;
  alternatives: ScenarioCandidate[];
  what_if_available: boolean;
}

export interface ScenarioExplorer {
  game_id: string;
  plies_analyzed: number;
  moves_total?: number | null;
  evaluation_series: { ply: number; move_number: number; color: string; san: string; eval_white_cp?: number | null; classification?: string | null }[];
  turning_points: ScenarioTurningPoint[];
  critical_moment_count: number;
  branchable_count: number;
  notes: string[];
  methodology_version: string;
}

export interface ScenarioPositionFacts {
  fen: string;
  side_to_move: string;
  phase: string;
  material_balance: number;
  legal_move_count: number;
  is_check: boolean;
  is_terminal: boolean;
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  ready: () => request<ReadyResponse>("/ready"),

  listGames: () => request<{ games: GameListItem[]; count: number }>("/api/games"),

  getGame: (gameId: string) => request<GameDetail>(`/api/games/${gameId}`),

  getGamePositions: (gameId: string) =>
    request<GamePositionsResponse>(`/api/games/${gameId}/positions`),

  getGameStatus: (gameId: string) => request<GameStatus>(`/api/games/${gameId}/status`),

  deleteGame: (gameId: string) =>
    request<void>(`/api/games/${gameId}`, { method: "DELETE" }),

  validatePgn: (body: { pgn_text: string; source?: string }) =>
    request<PgnValidationResponse>("/api/games/validate", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  importGame: (body: {
    pgn_text: string;
    run_analysis: boolean;
    depth?: number;
    multipv?: number;
    source?: string;
  }) =>
    request<GameImportResponse>("/api/games/import", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // File upload uses multipart/form-data, so it bypasses the JSON request helper.
  importGameFile: async (
    file: File,
    options: { run_analysis: boolean; depth?: number } = { run_analysis: false }
  ): Promise<GameImportResponse> => {
    const form = new FormData();
    form.append("file", file);
    const params = new URLSearchParams({
      run_analysis: String(options.run_analysis),
      ...(options.depth ? { depth: String(options.depth) } : {}),
    });
    const response = await fetch(
      `${API_BASE_URL}/api/games/import/file?${params.toString()}`,
      { method: "POST", body: form, cache: "no-store" }
    );
    if (!response.ok) {
      let body: ApiErrorBody;
      try {
        body = (await response.json()) as ApiErrorBody;
      } catch {
        body = { error: { code: "http_error", message: response.statusText } };
      }
      throw new ApiError(response.status, body);
    }
    return (await response.json()) as GameImportResponse;
  },

  analyzeGameAsync: (gameId: string) =>
    request<{ game_id: string; analysis_status: AnalysisStatus; depth: number }>(
      `/api/games/${gameId}/analyze`,
      { method: "POST" }
    ),

  // --- Phase 3: engine analysis ---------------------------------------------

  startAnalysis: (
    gameId: string,
    body: { profile?: string; depth?: number; multipv?: number; movetime_ms?: number; resume?: boolean }
  ) =>
    request<{ game_id: string; analysis_status: AnalysisStatus; config_label?: string }>(
      `/api/analysis/games/${gameId}`,
      { method: "POST", body: JSON.stringify(body) }
    ),

  cancelAnalysis: (gameId: string) =>
    request<{ game_id: string; cancel_requested: boolean }>(
      `/api/analysis/games/${gameId}/cancel`,
      { method: "POST" }
    ),

  getAnalysisProgress: (gameId: string) =>
    request<AnalysisProgress>(`/api/analysis/games/${gameId}/progress`),

  getAnalysisMoves: (gameId: string) =>
    request<GameMoveAnalyses>(`/api/analysis/games/${gameId}/moves`),

  getCriticalMoments: (gameId: string) =>
    request<{ game_id: string; count: number; critical_moments: CriticalMomentRow[] }>(
      `/api/analysis/games/${gameId}/critical-moments`
    ),

  analyzePositionMultipv: (body: { fen: string; multipv: number; depth?: number; movetime_ms?: number }) =>
    request<AnalyzedPositionResponse>("/api/analysis/position/multipv", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  analyzePosition: (body: { fen: string; depth?: number; multipv?: number }) =>
    request<PositionAnalysisResponse>("/api/analysis/position", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // --- Phase 4: game intelligence -------------------------------------------

  getReportStatus: (gameId: string) =>
    request<ReportStatus>(`/api/intelligence/games/${gameId}/report/status`),

  getForecast: (gameId: string) =>
    request<ResultForecast>(`/api/intelligence/games/${gameId}/forecast`),

  // --- Phase 5: player intelligence ----------------------------------------

  listPlayers: () => request<{ players: PlayerListItem[]; count: number }>("/api/players"),

  getPlayerProfile: (playerId: string) =>
    request<PlayerProfilePayload>(`/api/players/${playerId}`),

  /** Forces a full recomputation — the profile is otherwise cached by input. */
  rebuildPlayerProfile: (playerId: string) =>
    request<PlayerProfilePayload>(`/api/players/${playerId}/rebuild`, { method: "POST" }),

  /** The ML-ready feature vector; user-specific and not training-eligible. */
  getPlayerFeatures: (playerId: string) =>
    request<PlayerFeatureSet>(`/api/players/${playerId}/features`),

  // --- external sources: Chess.com, Lichess ---------------------------------

  listSources: () => request<SourceCatalog>("/api/sources"),

  getSourcePlayer: (source: PlatformSourceId, username: string, months?: number) =>
    request<PlatformPlayerResponse>(
      `/api/sources/${sourcePath(source)}/${encodeURIComponent(username)}` +
        (months ? `?months=${months}` : "")
    ),

  getSourceGames: (
    source: PlatformSourceId,
    username: string,
    year: number,
    month: number,
    timeClass?: string
  ) => {
    const params = new URLSearchParams({ year: String(year), month: String(month) });
    if (timeClass) params.set("time_class", timeClass);
    return request<PlatformMonthResponse>(
      `/api/sources/${sourcePath(source)}/${encodeURIComponent(username)}/games?${params.toString()}`
    );
  },

  importSourceGames: (
    source: PlatformSourceId,
    body: {
      username: string;
      year: number;
      month: number;
      game_urls: string[];
      analyze?: boolean;
      depth?: number;
    }
  ) =>
    request<PlatformImportResponse>(`/api/sources/${sourcePath(source)}/import`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  generateReport: (gameId: string) =>
    request<GameReportPayload>(`/api/intelligence/games/${gameId}/report`, {
      method: "POST",
    }),

  getStoredReport: (gameId: string) =>
    request<StoredReportResponse>(`/api/intelligence/games/${gameId}/report`),

  coachStatus: () => request<CoachStatus>("/api/coach/status"),

  coachChat: (body: {
    message: string;
    history?: { role: string; content: string }[];
  }) =>
    request<CoachChatResponse>("/api/coach/chat", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // --- Phase 7: the Caissa agent ---------------------------------------------

  /** One agent turn: plan, tool calls, answer from evidence, then validation. */
  agentAsk: (body: AgentAskRequest) =>
    request<AgentTurnResponse>("/api/coach/ask", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** The agent's declared tool catalogue, with availability and reasons. */
  agentTools: () => request<AgentToolCatalog>("/api/coach/tools"),

  // --- Phase 8: training -----------------------------------------------------

  trainingMeta: () => request<TrainingMeta>("/api/training/meta"),

  generateTraining: (
    gameId: string,
    body: { player_id?: string | null; data_source?: "personalized" | "general"; regenerate?: boolean }
  ) =>
    request<TrainingGenerateResult>(`/api/training/games/${gameId}/generate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getGameTraining: (gameId: string, playerId?: string) =>
    request<{ game_id: string; count: number; positions: TrainingPositionSummary[]; note: string }>(
      `/api/training/games/${gameId}` + (playerId ? `?player_id=${encodeURIComponent(playerId)}` : "")
    ),

  listTrainingPositions: (params: {
    player_id?: string;
    category?: string;
    state?: string;
    include_general?: boolean;
    limit?: number;
  } = {}) => {
    const query = new URLSearchParams();
    if (params.player_id) query.set("player_id", params.player_id);
    if (params.category) query.set("category", params.category);
    if (params.state) query.set("state", params.state);
    if (params.include_general === false) query.set("include_general", "false");
    if (params.limit) query.set("limit", String(params.limit));
    const suffix = query.toString();
    return request<{ count: number; positions: TrainingPositionSummary[] }>(
      `/api/training/positions${suffix ? `?${suffix}` : ""}`
    );
  },

  getTrainingPosition: (positionId: number, playerId?: string) =>
    request<TrainingPositionSummary>(
      `/api/training/positions/${positionId}` +
        (playerId ? `?player_id=${encodeURIComponent(playerId)}` : "")
    ),

  revealTrainingSolution: (positionId: number) =>
    request<TrainingPositionSummary>(`/api/training/positions/${positionId}/solution`),

  getTrainingHints: (positionId: number, hintIndex = 0) =>
    request<TrainingHintResponse>(`/api/training/positions/${positionId}/hints?hint_index=${hintIndex}`),

  explainTrainingPosition: (positionId: number, playerId?: string) =>
    request<TrainingExplanation>(
      `/api/training/positions/${positionId}/explanation` +
        (playerId ? `?player_id=${encodeURIComponent(playerId)}` : "")
    ),

  submitTrainingAttempt: (
    positionId: number,
    body: {
      player_id: string;
      submitted_uci: string;
      session_id?: number;
      hints_used?: number;
      response_time_ms?: number;
      reveal?: boolean;
    }
  ) =>
    request<TrainingAttemptFeedback>(`/api/training/positions/${positionId}/attempt`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  trainingReviewQueue: (playerId?: string, limit = 50) =>
    request<TrainingReviewQueue>(
      `/api/training/review-queue?limit=${limit}` +
        (playerId ? `&player_id=${encodeURIComponent(playerId)}` : "")
    ),

  trainingProgress: (playerId?: string) =>
    request<TrainingProgress>(
      `/api/training/progress` + (playerId ? `?player_id=${encodeURIComponent(playerId)}` : "")
    ),

  trainingRecommendations: (playerId: string) =>
    request<TrainingRecommendations>(`/api/training/recommendations?player_id=${encodeURIComponent(playerId)}`),

  startTrainingSession: (body: {
    player_id: string;
    kind: string;
    target_category?: string;
    game_id?: string;
    custom_position_ids?: number[];
    max_positions?: number;
  }) =>
    request<TrainingSessionSummary>("/api/training/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listTrainingSessions: (playerId: string, status?: string) =>
    request<{ sessions: TrainingSessionSummary[] }>(
      `/api/training/sessions?player_id=${encodeURIComponent(playerId)}` +
        (status ? `&status=${status}` : "")
    ),

  getTrainingSession: (sessionId: number, playerId: string) =>
    request<TrainingSessionSummary>(
      `/api/training/sessions/${sessionId}?player_id=${encodeURIComponent(playerId)}`
    ),

  cancelTrainingSession: (sessionId: number, playerId: string) =>
    request<TrainingSessionSummary>(
      `/api/training/sessions/${sessionId}/cancel?player_id=${encodeURIComponent(playerId)}`,
      { method: "POST" }
    ),

  /** Grade the continuation of a CONTINUE_LINE exercise against the stored line. */
  continueTrainingLine: (positionId: number, body: { player_id: string; moves: string[] }) =>
    request<TrainingContinueResult>(`/api/training/positions/${positionId}/continue`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Generate preparation exercises against an opponent, owned by the given player. */
  prepareForOpponent: (
    opponentPlayerId: string,
    body: { player_id: string; min_occurrences?: number; max_exercises?: number }
  ) =>
    request<{
      opponent_player_id: number;
      player_id: number;
      accepted: number;
      rejected: number;
      seen: number;
      reasons: Record<string, number>;
      created: number[];
      data_source: string;
      note: string;
    }>(`/api/training/opponents/${opponentPlayerId}/prepare`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // --- Phase 9: opponent intelligence ---------------------------------------

  opponentMeta: () => request<OpponentMeta>("/api/players/opponent-meta"),

  getOpponentProfile: (playerId: string, rebuild = false) =>
    request<OpponentProfile>(
      rebuild
        ? `/api/players/${playerId}/opponent-profile/rebuild`
        : `/api/players/${playerId}/opponent-profile`,
      rebuild ? { method: "POST" } : undefined
    ),

  getOpponentGames: (playerId: string, params: { limit?: number; offset?: number; color?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.limit) query.set("limit", String(params.limit));
    if (params.offset) query.set("offset", String(params.offset));
    if (params.color) query.set("color", params.color);
    const suffix = query.toString();
    return request<OpponentGamesResponse>(
      `/api/players/${playerId}/opponent-games${suffix ? `?${suffix}` : ""}`
    );
  },

  getOpponentRepertoire: (playerId: string, color: "white" | "black", recent = false) =>
    request<OpponentOpeningProfile>(
      recent
        ? `/api/players/${playerId}/repertoire/recent?color=${color}`
        : `/api/players/${playerId}/repertoire?color=${color}`
    ),

  getOpponentPositionResponse: (playerId: string, fen: string) =>
    request<OpponentPositionResponse>(
      `/api/players/${playerId}/position-response?fen=${encodeURIComponent(fen)}`
    ),

  getOpponentResponses: (playerId: string) =>
    request<OpponentResponsesResponse>(`/api/players/${playerId}/responses`),

  getOpponentTendencies: (playerId: string) =>
    request<OpponentTendenciesResponse>(`/api/players/${playerId}/tendencies`),

  getOpponentPhaseStatistics: (playerId: string, color?: "white" | "black") =>
    request<OpponentPhaseStatistics>(
      `/api/players/${playerId}/phase-statistics` + (color ? `?color=${color}` : "")
    ),

  getOpponentPreparationReport: (playerId: string, color?: "white" | "black") =>
    request<OpponentPreparationReport>(
      `/api/players/${playerId}/preparation-report` + (color ? `?color=${color}` : "")
    ),

  // --- Phase 10: decision intelligence --------------------------------------

  getScenarioMeta: () => request<Record<string, unknown>>("/api/scenarios/meta"),

  getScenarioMetrics: () => request<Record<string, unknown>>("/api/scenarios/metrics"),

  getScenarioPredictionAvailability: () =>
    request<{ available: string[]; unavailable: string[]; note: string }>(
      "/api/scenarios/predictions"
    ),

  getPositionFacts: (fen: string) =>
    request<ScenarioPositionFacts>("/api/scenarios/position", {
      method: "POST",
      body: JSON.stringify({ fen }),
    }),

  compareCandidateMoves: (body: {
    fen?: string;
    game_id?: string;
    ply?: number;
    moves?: string[];
    include_top?: number;
    depth?: number;
  }) =>
    request<ScenarioCandidateComparison>("/api/scenarios/compare-moves", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  comparePositions: (body: { fen_a: string; fen_b: string; depth?: number }) =>
    request<Record<string, unknown>>("/api/scenarios/compare-positions", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  counterfactual: (body: {
    fen?: string;
    game_id?: string;
    ply?: number;
    alternative_move: string;
    actual_move?: string;
    plies_ahead?: number;
    depth?: number;
    persist?: boolean;
    player_id?: number;
  }) =>
    request<ScenarioOutcome>("/api/scenarios/counterfactual", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  whyNotThisMove: (body: {
    fen?: string;
    game_id?: string;
    ply?: number;
    move: string;
    depth?: number;
  }) =>
    request<ScenarioWhyNot>("/api/scenarios/why-not", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  whatIf: (body: {
    fen?: string;
    game_id?: string;
    ply?: number;
    move: string;
    plies_ahead?: number;
    depth?: number;
  }) =>
    request<Record<string, unknown>>("/api/scenarios/what-if", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getTurningPoints: (gameId: string, limit?: number) =>
    request<ScenarioExplorer>(
      `/api/scenarios/games/${gameId}/explorer` + (limit ? `?limit=${limit}` : "")
    ),

  createTrainingFromScenario: (body: {
    fen?: string;
    game_id?: string;
    ply?: number;
    alternative_move: string;
    player_id: number;
    depth?: number;
  }) =>
    request<{
      status: string;
      message?: string | null;
      position_id?: number;
      position?: { id: number; fen: string; category: string; difficulty: string; data_source: string };
      note?: string;
    }>("/api/scenarios/training", { method: "POST", body: JSON.stringify(body) }),

  listScenarios: (params: { game_id?: string; player_id?: number; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.game_id) query.set("game_id", params.game_id);
    if (params.player_id) query.set("player_id", String(params.player_id));
    if (params.limit) query.set("limit", String(params.limit));
    const suffix = query.toString();
    return request<{ count: number; scenarios: Record<string, unknown>[] }>(
      `/api/scenarios${suffix ? `?${suffix}` : ""}`
    );
  },

  // --- Phase 11: the coaching workspace -------------------------------------

  coachMethod: () => request<CoachMethod>("/api/coaching/method"),

  coachContext: (params: {
    user_id?: number;
    mode?: string;
    game_id?: string;
    fen?: string;
    phase?: string;
    training_position_id?: number;
    opponent_id?: number;
  } = {}) => {
    const query = new URLSearchParams();
    if (params.user_id) query.set("user_id", String(params.user_id));
    if (params.mode) query.set("mode", params.mode);
    if (params.game_id) query.set("game_id", params.game_id);
    if (params.fen) query.set("fen", params.fen);
    if (params.phase) query.set("phase", params.phase);
    if (params.training_position_id)
      query.set("training_position_id", String(params.training_position_id));
    if (params.opponent_id) query.set("opponent_id", String(params.opponent_id));
    const suffix = query.toString();
    return request<CoachContextResponse>(`/api/coaching/context${suffix ? `?${suffix}` : ""}`);
  },

  gameDebrief: (gameId: string, userId?: number) =>
    request<GameDebriefResponse>(
      `/api/coaching/games/${gameId}/debrief` + (userId ? `?user_id=${userId}` : "")
    ),

  coachFeed: (params: {
    user_id?: number;
    game_id?: string;
    opponent_id?: number;
    dismissed?: string[];
  } = {}) => {
    const query = new URLSearchParams();
    if (params.user_id) query.set("user_id", String(params.user_id));
    if (params.game_id) query.set("game_id", params.game_id);
    if (params.opponent_id) query.set("opponent_id", String(params.opponent_id));
    for (const key of params.dismissed ?? []) query.append("dismissed", key);
    const suffix = query.toString();
    return request<CoachFeedResponse>(`/api/coaching/feed${suffix ? `?${suffix}` : ""}`);
  },

  coachFocus: (userId: number, dismissed: string[] = []) => {
    const query = new URLSearchParams({ user_id: String(userId) });
    for (const key of dismissed) query.append("dismissed", key);
    return request<CoachFocusResponse>(`/api/coaching/focus?${query.toString()}`);
  },

  coachPlan: (userId: number, weeks = 1) =>
    request<CoachPlanResponse>(`/api/coaching/plan?user_id=${userId}&weeks=${weeks}`),

  // --- Phase 11 workspace extras: evidence, collections, search, prep, progress

  showMeWhy: (params: {
    claim: string;
    game_id?: string;
    ply?: number;
    player_id?: number;
    insight_key?: string;
  }) => {
    const query = new URLSearchParams({ claim: params.claim });
    if (params.game_id) query.set("game_id", params.game_id);
    if (params.ply !== undefined) query.set("ply", String(params.ply));
    if (params.player_id) query.set("player_id", String(params.player_id));
    if (params.insight_key) query.set("insight_key", params.insight_key);
    return request<EvidencePacketResponse>(`/api/coaching/evidence?${query.toString()}`);
  },

  listCollections: (playerId: number) =>
    request<{ player_id: number; count: number; collections: StudyCollection[] }>(
      `/api/coaching/collections?player_id=${playerId}`
    ),

  createCollection: (body: {
    player_id: string;
    name: string;
    kind?: string;
    description?: string;
  }) =>
    request<StudyCollection>("/api/coaching/collections", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deleteCollection: (collectionId: number, playerId: number) =>
    request<{ deleted: boolean }>(
      `/api/coaching/collections/${collectionId}?player_id=${playerId}`,
      { method: "DELETE" }
    ),

  addCollectionItem: (
    collectionId: number,
    body: {
      player_id: string;
      kind: string;
      ref: string;
      label?: string;
      note?: string;
      game_id?: string;
      ply?: number;
      fen?: string;
    }
  ) =>
    request<StudyCollection>(`/api/coaching/collections/${collectionId}/items`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  removeCollectionItem: (
    collectionId: number,
    params: { kind: string; ref: string; player_id: number }
  ) => {
    const query = new URLSearchParams({
      kind: params.kind,
      ref: params.ref,
      player_id: String(params.player_id),
    });
    return request<StudyCollection>(
      `/api/coaching/collections/${collectionId}/items?${query.toString()}`,
      { method: "DELETE" }
    );
  },

  unifiedSearch: (params: {
    q: string;
    player_id?: number;
    kind?: string[];
    limit?: number;
  }) => {
    const query = new URLSearchParams({ q: params.q });
    if (params.player_id) query.set("player_id", String(params.player_id));
    for (const kind of params.kind ?? []) query.append("kind", kind);
    if (params.limit) query.set("limit", String(params.limit));
    return request<UnifiedSearchResponse>(`/api/coaching/search?${query.toString()}`);
  },

  buildMatchPreparation: (body: {
    preparing_player_id: string;
    opponent_id: number;
    as_white?: boolean | null;
    persist?: boolean;
  }) =>
    request<CoachingMatchPreparationResponse>("/api/coaching/match-preparation", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  compareProgress: (playerId: number, split = 0.5) =>
    request<ProgressComparisonResponse>(
      `/api/coaching/progress/compare?player_id=${playerId}&split=${split}`
    ),
};

// --- Phase 11: the coaching workspace -----------------------------------------

export interface CoachMethod {
  methodology_version: string;
  priority: {
    methodology_version: string;
    factor_weights: Record<string, number>;
    recency_bands_days_to_factor: [number, number][];
    bands: Record<string, number>;
    rules: string[];
  };
  feed: { methodology_version: string; sections: string[]; rules: string[] };
  modes: Record<
    string,
    {
      label: string;
      summary: string;
      explanation_depth: string;
      expose_evaluation: boolean;
      expose_principal_variation: boolean;
      allowed_tool_families: string[];
    }
  >;
}

export interface CoachContextBrief {
  situation: string;
  mode: string;
  mode_label: string;
  explanation_depth: string;
  exposure: {
    evaluation: boolean;
    candidate_moves: boolean;
    principal_variation: boolean;
    statistics: boolean;
  };
  suggest_training: boolean;
  allowed_tool_families: string[];
  lead_with: string | null;
  evidence_available: string[];
  gaps: string[];
  notes: string[];
  methodology_version: string;
}

export interface CoachContextResponse {
  context: Record<string, unknown> & {
    situation: string;
    mode: string;
    gaps: string[];
    available_predictions?: Record<string, unknown> | null;
  };
  brief: CoachContextBrief;
  methodology_version: string;
}

export interface DebriefSection {
  key: string;
  title: string;
  available: boolean;
  reason: string | null;
  observations: Record<string, unknown>[];
  sample_size: number | null;
  evidence: Record<string, unknown>[];
}

export interface GameDebriefResponse {
  game_id: string;
  steps: string[];
  sections: DebriefSection[];
  focus: {
    key: string;
    title: string;
    statement: string;
    priority: string;
    score: number;
    sample_size: number;
    capped_by: string | null;
  }[];
  gaps: string[];
  report_available: boolean;
  methodology_version: string;
  game: Record<string, unknown>;
}

export interface FeedAction {
  label: string;
  href: string;
  kind: string;
}

export interface FeedCard {
  key: string;
  section: string;
  title: string;
  statement: string;
  priority: string;
  score: number | null;
  sample_size: number | null;
  evidence: Record<string, unknown>[];
  actions: FeedAction[];
  dismissible: boolean;
  capped_by: string | null;
}

export interface FeedSection {
  key: string;
  title: string;
  cards: FeedCard[];
  available: boolean;
  reason: string | null;
}

export interface CoachFeedResponse {
  user_id: number | null;
  sections: FeedSection[];
  counts: Record<string, number>;
  dismissed: string[];
  gaps: string[];
  methodology_version: string;
  generated_at: string | null;
}

export interface CoachFocusResponse {
  status: string;
  reason: string | null;
  primary_focus: {
    key: string;
    title: string;
    statement: string;
    priority: string;
    score: number;
    sample_size: number;
    claim_level: string | null;
    evidence: Record<string, unknown>[];
  } | null;
  supporting_focus: CoachFocusResponse["primary_focus"][];
  evidence: Record<string, unknown>[];
  affected_games: { game_id?: string; ply?: number }[];
  training_history: Record<string, unknown> | null;
  recommended_exercises: Record<string, unknown>[];
  how_progress_will_be_measured: string[];
}

export interface CoachPlanResponse {
  plan_id: string;
  player_id: number | null;
  created_at: string;
  focus_areas: Record<string, unknown>[];
  training_sessions: Record<string, unknown>[];
  target_metrics: Record<string, unknown>[];
  review_date: string | null;
  weeks: number;
  limitations: string[];
  methodology_version: string;
}

// --- Phase 11 workspace extras: evidence, collections, search, prep, progress --

export interface EvidenceItem {
  kind: "engine_fact" | "argus_feature" | "interpretation" | "prediction" | "refusal";
  label: string;
  statement: string;
  href: string | null;
  game_id: string | null;
  ply: number | null;
  fen: string | null;
  value: number | null;
  unit: string | null;
  certainty: string | null;
  source_system: string | null;
  followable: boolean;
  unavailable_reason: string | null;
}

export interface EvidencePacketResponse {
  claim: string;
  claim_level: string | null;
  answer_type: string;
  certainty: string | null;
  items: EvidenceItem[];
  counts_by_kind: Record<string, number>;
  gaps: string[];
  methodology_version: string;
  method: { methodology_version: string; kinds: string[]; rules: string[] };
}

export interface StudyItem {
  kind: "game" | "position" | "training" | "scenario" | "insight" | "opening" | "endgame";
  ref: string;
  label: string;
  note: string;
  game_id: string | null;
  ply: number | null;
  fen: string | null;
  added_at: string | null;
}

export interface StudyCollection {
  id: number;
  player_id: number;
  name: string;
  description: string;
  kind: string;
  size: number;
  items: StudyItem[];
  methodology_version: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface SearchHit {
  kind: string;
  id: string;
  title: string;
  subtitle: string;
  href: string | null;
  score: number;
  matched_terms: string[];
  matched_fields: string[];
  explanation: string;
  metadata: Record<string, unknown>;
}

export interface UnifiedSearchResponse {
  query: string;
  hits: SearchHit[];
  total_candidates: number;
  counts_by_kind: Record<string, number>;
  searched_kinds: string[];
  status: "ok" | "empty_query" | "no_candidates" | "no_matches";
  reason: string | null;
  methodology_version: string;
  method: { methodology_version: string; field_weights: Record<string, number>; rules: string[] };
}

export interface PrepScenario {
  key: string;
  kind: string;
  title: string;
  statement: string;
  as_white: boolean | null;
  fen: string | null;
  line_san: string[];
  practice_href: string | null;
  sample_size: number;
  evidence: Record<string, unknown>[];
  status: string;
  reason: string | null;
}

export interface PrepSection {
  key: string;
  title: string;
  status: "available" | "insufficient" | "missing";
  gate: number | null;
  sample_size: number;
  items: PrepScenario[];
  reason: string | null;
}

export interface CoachingMatchPreparationResponse {
  preparation: {
    id: number | null;
    preparing_player_id: number;
    opponent_id: number;
    opponent_name: string;
    as_white: boolean | null;
    opponent_games: number;
    analysed_games: number;
    coverage: string;
    sections: PrepSection[];
    scenarios: PrepScenario[];
    scenario_count: number;
    methodology_version: string;
    created_at: string | null;
  };
  brief: {
    title: string;
    opponent: string;
    as_white: boolean | null;
    headline: string;
    coverage: string;
    priorities: {
      order: number;
      title: string;
      statement: string;
      kind: string;
      sample_size: number;
      practice_href: string | null;
    }[];
    available_sections: string[];
    unavailable_sections: { title: string; reason: string | null }[];
    methodology_version: string;
    disclaimer: string;
  };
  method: { methodology_version: string; sample_gates: Record<string, number>; rules: string[] };
}

export interface ProgressMeasure {
  key: string;
  label: string;
  unit: string;
  direction: string;
  before: number | null;
  after: number | null;
  delta: number | null;
  verdict: "improved" | "declined" | "unchanged" | "insufficient";
  noise_tolerance: number | null;
  sample_before: number;
  sample_after: number;
  note: string | null;
}

export interface ProgressSnapshot {
  label: string;
  start: string | null;
  end: string | null;
  games: number;
  analysed_games: number;
  measures: ProgressMeasure[];
  gaps: string[];
}

export interface ProgressComparisonResponse {
  player_id: number;
  before: ProgressSnapshot;
  after: ProgressSnapshot;
  measures: ProgressMeasure[];
  summary: string;
  causality_note: string;
  limitations: string[];
  status: "ok" | "insufficient_data";
  reason: string | null;
  methodology_version: string;
  method: { methodology_version: string; minimum_games_per_period: number; causality_note: string; rules: string[] };
}


// --- Phase 12: live chess --------------------------------------------------------

export type LiveVisibility = "private" | "unlisted" | "public";
export type LiveMode = "local" | "private_match" | "training" | "sandbox";
export type LiveAnalysisMode =
  | "no_analysis"
  | "post_move_analysis"
  | "training_analysis"
  | "sandbox_analysis";
export type LiveCoachLevel = "off" | "hints" | "conceptual" | "full_analysis";

export interface LiveGameSeat {
  player_id: number | null;
  name: string | null;
  kind: "human" | "open" | "engine";
  connected: boolean;
  rating: number | null;
}

export interface LiveClockSnapshot {
  white_ms: number;
  black_ms: number;
  running: boolean;
  turn_side: "white" | "black" | null;
  turn_started_at: string | null;
  server_time: string;
  display: { white: string; black: string };
}

export interface LivePermissions {
  mode: string;
  analysis_mode: string;
  coach_level: string;
  competitive: boolean;
  may_give_engine_moves: boolean;
  may_show_evaluation: boolean;
  may_show_engine_lines: boolean;
  may_give_hints: boolean;
  may_explain_concepts: boolean;
  may_analyse: boolean;
  refusal: string | null;
}

export interface LiveMoveRow {
  ply: number;
  move_number: number;
  san: string;
  uci: string;
  fen_before: string;
  fen_after: string;
  side: "white" | "black";
  clock_after?: { white_ms: number; black_ms: number } | null;
}

export interface LiveGamePayload {
  game_id: string;
  status: string;
  mode: string;
  analysis_mode: string;
  coach_level: string;
  training_mode: string;
  visibility: LiveVisibility;
  rated: boolean;
  variant: string;
  initial_fen: string;
  current_fen: string;
  side_to_move: "white" | "black";
  move_number: number;
  version: number;
  sequence: number;
  result: string;
  result_reason: string | null;
  draw_offer: "white" | "black" | null;
  clock_config: { base_ms: number; increment_ms: number };
  clock: LiveClockSnapshot;
  seats: Record<"white" | "black", "human" | "open" | "engine">;
  engine: Record<string, unknown>;
  players: { white: LiveGameSeat; black: LiveGameSeat };
  permissions: LivePermissions;
  moves: LiveMoveRow[];
  /** Open sockets per side, so the UI can detect multiple tabs (§18). */
  session_counts: { white: number; black: number };
  pgn_available: boolean;
  library_game_id: string | null;
  viewer: "player" | "owner" | "spectator" | null;
  created_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  methodology_version: string;
  turn_owner: "player" | "engine";
  legal_moves: string[];
  invite_token?: string;
  invite_expires_at?: string;
}

export interface LiveEventEnvelope {
  event_id: string;
  game_id: string;
  event_type: string;
  sequence_number: number;
  game_version: number;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface LiveMutation {
  events: LiveEventEnvelope[];
  state: LiveGamePayload;
  engine_reply?: LiveMoveRow | null;
  engine_error?: { code: string; message: string } | null;
}

export interface LiveSyncResponse {
  game_id: string;
  after_sequence: number;
  server_sequence: number;
  server_version: number;
  count: number;
  events: LiveEventEnvelope[];
  resync: boolean;
  state?: LiveGamePayload;
  clock?: LiveClockSnapshot;
  status?: string;
  version?: number;
}

export interface LiveGameListEntry {
  game_id: string;
  status: string;
  mode: string;
  visibility: LiveVisibility;
  analysis_mode: string;
  training_mode: string;
  white: string | null;
  black: string | null;
  move_number: number;
  result: string;
  result_reason: string | null;
  clock: LiveClockSnapshot;
  clock_config: { base_ms: number; increment_ms: number };
  version: number;
  library_game_id: string | null;
  created_at: string | null;
}

export interface LiveMethod {
  methodology_version: string;
  modes: string[];
  statuses: string[];
  transitions: Record<string, string[]>;
  time_controls: string[];
  analysis_modes: string[];
  coach_levels: string[];
  training_modes: string[];
  fair_play: string;
  limits: Record<string, number>;
  events: string[];
}

export interface LiveMetrics {
  counters: Record<string, number>;
  engine: Record<string, unknown>;
  connections: Record<string, unknown>;
  note: string;
}

export interface LiveCoachAnswer {
  kind: "hint_only" | "coach_off" | "analysis_permitted";
  message: string | null;
  hint: string | null;
  permissions: LivePermissions;
  game_id: string;
  status: string;
  version: number;
  side_to_move: string;
  training_mode: string;
  analysis?: {
    available: boolean;
    reason?: string;
    detail?: string;
    fen?: string;
    best_move_uci?: string | null;
    best_move_san?: string | null;
    lines?: { rank: number; move_uci: string; move_san: string | null; cp: number | null; mate: number | null; pv: string[] }[];
    engine?: string;
    engine_version?: string | null;
  };
}

export interface CreateLiveGameInput {
  player_id: number;
  mode?: LiveMode;
  colour?: "white" | "black";
  time_control?: string;
  visibility?: LiveVisibility;
  rated?: boolean;
  opponent_player_id?: number | null;
  analysis_mode?: LiveAnalysisMode | null;
  coach_level?: LiveCoachLevel | null;
  training_mode?: string;
  opponents?: "engine" | "human";
  engine_depth?: number | null;
  start_fen?: string | null;
}

export function liveMethod() {
  return request<LiveMethod>("/api/live/method");
}

export function liveMetrics() {
  return request<LiveMetrics>("/api/live/metrics");
}

export function createLiveGame(input: CreateLiveGameInput) {
  return request<LiveMutation>("/api/live/games", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listLiveGames(playerId: number, status?: string) {
  const query = new URLSearchParams({ player_id: String(playerId) });
  if (status) query.set("status", status);
  return request<{ count: number; games: LiveGameListEntry[] }>(`/api/live/games?${query}`);
}

export function getLiveGame(liveGameId: string, playerId?: number) {
  const query = playerId ? `?player_id=${playerId}` : "";
  return request<LiveGamePayload>(`/api/live/games/${liveGameId}${query}`);
}

function liveAction(liveGameId: string, action: string, body: Record<string, unknown>) {
  return request<LiveMutation>(`/api/live/games/${liveGameId}/${action}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function liveJoin(liveGameId: string, playerId: number, inviteToken?: string) {
  return liveAction(liveGameId, "join", { player_id: playerId, invite_token: inviteToken });
}

export function liveStart(liveGameId: string, playerId: number) {
  return liveAction(liveGameId, "start", { player_id: playerId });
}

export function liveMove(
  liveGameId: string,
  playerId: number,
  uci: string,
  expectedVersion?: number,
) {
  return liveAction(liveGameId, "move", {
    player_id: playerId,
    uci,
    expected_version: expectedVersion,
  });
}

export function liveResign(liveGameId: string, playerId: number) {
  return liveAction(liveGameId, "resign", { player_id: playerId });
}

export function liveAbort(liveGameId: string, playerId: number) {
  return liveAction(liveGameId, "abort", { player_id: playerId });
}

export function livePause(liveGameId: string, playerId: number) {
  return liveAction(liveGameId, "pause", { player_id: playerId });
}

export function liveResume(liveGameId: string, playerId: number) {
  return liveAction(liveGameId, "resume", { player_id: playerId });
}

export function liveOfferDraw(liveGameId: string, playerId: number) {
  return liveAction(liveGameId, "draw/offer", { player_id: playerId });
}

export function liveAcceptDraw(liveGameId: string, playerId: number) {
  return liveAction(liveGameId, "draw/accept", { player_id: playerId });
}

export function liveDeclineDraw(liveGameId: string, playerId: number) {
  return liveAction(liveGameId, "draw/decline", { player_id: playerId });
}

export function liveSetVisibility(
  liveGameId: string,
  playerId: number,
  visibility: LiveVisibility,
) {
  return request<LiveGamePayload>(`/api/live/games/${liveGameId}/visibility`, {
    method: "POST",
    body: JSON.stringify({ player_id: playerId, visibility }),
  });
}

export function liveInvite(liveGameId: string, playerId: number) {
  return request<{ game_id: string; invite_token: string; invite_expires_at: string | null }>(
    `/api/live/games/${liveGameId}/invite?player_id=${playerId}`,
  );
}

export function liveRotateInvite(liveGameId: string, playerId: number) {
  return request<{ game_id: string; invite_token: string; invite_expires_at: string | null }>(
    `/api/live/games/${liveGameId}/invite/rotate`,
    { method: "POST", body: JSON.stringify({ player_id: playerId }) },
  );
}

export function liveSync(liveGameId: string, afterSequence: number, playerId?: number) {
  const query = new URLSearchParams({ after_sequence: String(afterSequence) });
  if (playerId) query.set("player_id", String(playerId));
  return request<LiveSyncResponse>(`/api/live/games/${liveGameId}/sync?${query}`);
}

export function livePgn(liveGameId: string, playerId?: number) {
  const query = playerId ? `?player_id=${playerId}` : "";
  return request<{ game_id: string; pgn: string }>(`/api/live/games/${liveGameId}/pgn${query}`);
}

export function liveCoach(liveGameId: string, playerId: number, question?: string) {
  return request<LiveCoachAnswer>(`/api/live/games/${liveGameId}/coach`, {
    method: "POST",
    body: JSON.stringify({ player_id: playerId, question }),
  });
}

export function liveAnalysis(liveGameId: string, playerId?: number) {
  const query = playerId ? `?player_id=${playerId}` : "";
  return request<{ game_id: string; analyses: unknown[]; coach_messages: unknown[] }>(
    `/api/live/games/${liveGameId}/analysis${query}`,
  );
}

/** The WebSocket URL for a live game's event stream, authenticated by player id. */
export function liveSocketUrl(
  liveGameId: string,
  playerId: number | null,
  afterSequence = 0,
): string {
  const base = API_BASE_URL.replace(/^http/, "ws");
  const query = new URLSearchParams({ after_sequence: String(afterSequence) });
  if (playerId) query.set("player_id", String(playerId));
  return `${base}/api/live/games/${liveGameId}/ws?${query}`;
}

// --- Phase 13: the intelligence graph (§40–§43) -------------------------------
//
// The explorers are read-only compositions of the graph. Every relationship they
// return carries its type and its evidence, and a section with nothing behind it
// comes back empty with a note — never a plausible default.

export interface GraphMethod {
  schema_version: string;
  methodology_version: string;
  knowledge_version: string;
  storage: string;
  node_types: string[];
  edge_types: string[];
  note: string;
}

/** One row in a similarity bucket: the game, the ply, and the controlled level. */
export interface GraphSimilarPosition {
  game_id: string;
  ply: number;
  level: string;
}

export interface GraphNodeSummary {
  node_type?: string;
  node_key: string;
  label?: string;
  attributes?: Record<string, unknown>;
}

export interface GraphConcept {
  slug: string;
  name?: string;
  category?: string;
  definition?: string;
  how_to_spot?: string;
  typical_mistake?: string;
  source_id?: string | null;
  related_base_concept?: string | null;
}

export interface GraphPositionExplorer {
  fen: string;
  valid: boolean;
  note?: string | null;
  materialized?: boolean;
  position?: {
    fen: string;
    normalized_fen: string;
    position_hash: string;
    side_to_move: string;
    castling: string;
    en_passant: string;
  };
  exact_matches?: Array<{
    game_id: string;
    white: string;
    black: string;
    result: string;
    date: string | null;
    exact: boolean;
  }>;
  exact_match_count?: number;
  similar?: Record<string, GraphSimilarPosition[]>;
  similar_note?: string;
  openings?: string[];
  patterns?: GraphNodeSummary[];
  knowledge_concepts?: GraphConcept[];
  training_positions?: GraphNodeSummary[];
  scenarios?: GraphNodeSummary[];
}

export interface GraphGameExplorer {
  game_id: string;
  found: boolean;
  note?: string;
  game?: {
    white: string;
    black: string;
    result: string;
    date: string | null;
    eco_code: string | null;
    opening_name: string | null;
    analysis_status: string;
  };
  openings?: string[];
  positions?: Array<{ position_hash: string; edge: string }>;
  patterns?: GraphNodeSummary[];
  training?: GraphNodeSummary[];
  related_games?: Array<{ game_id: string; shared_opening: string }>;
}

export interface GraphPlayerExplorer {
  player_id: number;
  found: boolean;
  note?: string;
  player?: { name: string; title: string | null; platform: string | null };
  games?: string[];
  game_count?: number;
  openings?: string[];
  patterns?: GraphNodeSummary[];
  training_positions?: GraphNodeSummary[];
  training_attempts?: number;
  training_correct?: number;
  opponents?: number[];
}

export interface GraphWhyTrace {
  node: string;
  found: boolean;
  note?: string;
  label?: string;
  relationships?: Array<{
    edge_type: string;
    from_node: string;
    to_node: string;
    evidence_count: number;
    sample_size: number | null;
  }>;
  evidence?: Record<string, Array<Record<string, unknown>>>;
  evidence_count?: number;
  sample_size?: number | null;
  methodology_version?: string;
  date_range?: { start: string | null; end: string | null };
  gaps?: string[];
  ranking_methodology?: unknown;
}

export function graphMethod() {
  return request<GraphMethod>("/api/graph/method");
}

export function graphHealth() {
  return request<{ healthy: boolean; schema_version: string; report: unknown; metrics?: Record<string, number> }>(
    "/api/graph/health",
  );
}

export function graphExplorePosition(fen: string) {
  return request<GraphPositionExplorer>(
    `/api/graph/position?fen=${encodeURIComponent(fen)}`,
  );
}

export function graphExploreGame(gameId: string) {
  return request<GraphGameExplorer>(`/api/graph/games/${encodeURIComponent(gameId)}`);
}

export function graphExplorePlayer(playerId: number) {
  return request<GraphPlayerExplorer>(`/api/graph/players/${playerId}`);
}

/** The "Why?" trace. The node key can contain characters a path cannot carry. */
export function graphWhy(nodeType: string, nodeKey: string) {
  return request<GraphWhyTrace>(
    `/api/graph/why/${encodeURIComponent(nodeType)}/${nodeKey
      .split("/")
      .map(encodeURIComponent)
      .join("/")}`,
  );
}

// --- The Intelligence Map (§39) ----------------------------------------------
//
// A bounded, authorized neighbourhood subgraph around one node. Edges carry
// their controlled relationship name and evidence counts, never inlined
// evidence, and the payload states plainly whether the walk was truncated and
// how many nodes were withheld by authorization.

export interface GraphNeighborhoodNode {
  node_type: string;
  node_key: string;
  label: string;
  attributes?: Record<string, unknown>;
  methodology_version?: string;
}

export interface GraphNeighborhoodEdge {
  edge_type: string;
  from: { node_type: string; node_key: string };
  to: { node_type: string; node_key: string };
  sample_size?: number | null;
  evidence_count?: number;
  methodology_version?: string;
}

export interface GraphNeighborhood {
  node: string;
  found: boolean;
  note?: string | null;
  label?: string;
  node_type?: string;
  depth?: number;
  limit?: number;
  truncated?: boolean;
  denied_count?: number;
  nodes?: GraphNeighborhoodNode[];
  edges?: GraphNeighborhoodEdge[];
  hops?: Array<{
    edge_type: string;
    from_node: string;
    to_node: string;
    evidence_count: number;
    sample_size: number | null;
  }>;
  methodology_version?: string;
}

/** A page of stored nodes of one kind, authorized. Powers the map's node picker. */
export function graphListNodes(nodeType: string, limit = 50) {
  return request<{ node_type: string; count: number; nodes: GraphNodeSummary[] }>(
    `/api/graph/nodes/${encodeURIComponent(nodeType)}?limit=${limit}`,
  );
}

/** The neighbourhood subgraph used by the Intelligence Map. */
export function graphNeighborhood(
  nodeType: string,
  nodeKey: string,
  options: { depth?: number; limit?: number } = {},
) {
  const params = new URLSearchParams();
  if (options.depth) params.set("depth", String(options.depth));
  if (options.limit) params.set("limit", String(options.limit));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<GraphNeighborhood>(
    `/api/graph/neighborhood/${encodeURIComponent(nodeType)}/${nodeKey
      .split("/")
      .map(encodeURIComponent)
      .join("/")}${suffix}`,
  );
}
