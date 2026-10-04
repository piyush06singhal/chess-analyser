"""Player-intelligence data model.

Two halves, deliberately separated:

*Inputs* (``PlayerGameInput`` and its event rows) are the per-game facts the
API layer reads out of the **stored** ``GameReport`` plus the stored moves. The
core never touches the database or the engine: it aggregates structured
evidence, so a profile is deterministic, cheap to rebuild, and testable with
real report fixtures.

*Outputs* (``PlayerProfile`` and its sections) are derived statistics only —
no raw game data is copied — each carrying the sample size that produced it and
a coverage/claim state so the UI can never present a weak signal as a finding.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from argus.player_intelligence.policy import ClaimLevel, Coverage, PlayerInsightPolicy


class TimeClass(str, Enum):
    """Coarse time-control class; only categories with data are reported."""

    BULLET = "bullet"
    BLITZ = "blitz"
    RAPID = "rapid"
    CLASSICAL = "classical"
    UNKNOWN = "unknown"


class GameOutcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"
    UNKNOWN = "unknown"


class InsightCategory(str, Enum):
    """Analytical categories — never ranking or judgement labels."""

    STRENGTH = "strength"
    WEAKNESS_CANDIDATE = "weakness_candidate"
    RECURRING_PATTERN = "recurring_pattern"
    IMPROVEMENT_TREND = "improvement_trend"
    OPENING_PATTERN = "opening_pattern"
    TACTICAL_PATTERN = "tactical_pattern"
    POSITIONAL_PATTERN = "positional_pattern"
    PHASE_PATTERN = "phase_pattern"
    CONVERSION_PATTERN = "conversion_pattern"
    RECOVERY_PATTERN = "recovery_pattern"


class EvidenceRef(BaseModel):
    """A pointer back to the exact game/position behind a claim.

    The UI resolves this to ``/game/{game_id}?ply={ply}`` so every player-level
    statement is one click from the position that produced it.
    """

    game_id: str
    ply: int
    move_number: int | None = None
    san: str | None = None
    label: str | None = None


# --- inputs -------------------------------------------------------------------


class PlayerErrorEvent(BaseModel):
    """One engine-flagged problem move by the player (multi-tag capable)."""

    game_id: str
    ply: int
    move_number: int | None = None
    san: str | None = None
    phase: str | None = None
    classification: str | None = None  # blunder / mistake / inaccurate / best …
    categories: list[str] = Field(default_factory=list)  # tactical, positional, …
    severity: str | None = None
    centipawn_loss: int | None = None
    certainty: str = "confirmed"


class TacticalEventInput(BaseModel):
    game_id: str
    ply: int
    move_number: int | None = None
    san: str | None = None
    event_type: str
    #: ``created`` — the player executed it; ``allowed`` — the opponent did it
    #: against the player. The distinction is the whole point of the section.
    direction: str
    severity: str | None = None
    certainty: str = "confirmed"


class PositionalEventInput(BaseModel):
    game_id: str
    ply: int
    move_number: int | None = None
    san: str | None = None
    feature: str
    #: ``feature`` (objective structure) or ``error_candidate`` (feature the
    #: evaluation context supports as an error).
    kind: str
    direction: str
    severity: str | None = None


class KingSafetyEventInput(BaseModel):
    game_id: str
    ply: int
    move_number: int | None = None
    san: str | None = None
    event_type: str
    direction: str
    severity: str | None = None


class PhasePerformanceInput(BaseModel):
    """Measured per-phase performance for the player in one game."""

    phase: str
    evaluated_moves: int = 0
    average_centipawn_loss: float | None = None
    problem_moves: int = 0
    accuracy: float | None = None
    share_of_loss: float | None = None
    small_sample: bool = False


class MaterialInput(BaseModel):
    """Material facts measured from the board for one game."""

    total_captures: int = 0
    exchanges: int = 0
    promotions: int = 0
    final_balance: int = 0
    imbalance_reached: bool = False
    max_abs_balance: int = 0


class OpeningDeviationInput(BaseModel):
    ply: int
    move_number: int | None = None
    played_san: str | None = None
    expected_san: list[str] = Field(default_factory=list)


class TrajectoryInput(BaseModel):
    """Advantage-band summary of one game, from the stored trajectory.

    Bands are Caissa advantage bands (0 equal … 3 winning, 4 forced mate),
    already computed by the Phase 4 intelligence layer.
    """

    evaluated_plies: int = 0
    peak_band: int | None = None
    worst_band: int | None = None
    final_band: int | None = None


class PlayerGameInput(BaseModel):
    """Everything Phase 5 needs about one analyzed game, per player."""

    game_id: str
    date: str | None = None
    opponent_name: str
    color: str  # "white" | "black"
    player_rating: int | None = None
    opponent_rating: int | None = None
    result_raw: str
    outcome: GameOutcome
    time_class: TimeClass = TimeClass.UNKNOWN
    time_control_raw: str | None = None
    eco_code: str | None = None
    opening_name: str | None = None
    opening_family: str | None = None
    move_count: int = 0
    # --- measured aggregates (from the stored report's accuracy section) ----
    accuracy: float | None = None
    average_centipawn_loss: float | None = None
    scored_moves: int = 0
    best_moves: int = 0
    problem_moves: int = 0
    blunders: int = 0
    mistakes: int = 0
    inaccuracies: int = 0
    # --- evidence-level rows -------------------------------------------------
    errors: list[PlayerErrorEvent] = Field(default_factory=list)
    tactical_events: list[TacticalEventInput] = Field(default_factory=list)
    positional_events: list[PositionalEventInput] = Field(default_factory=list)
    king_safety_events: list[KingSafetyEventInput] = Field(default_factory=list)
    phase_performance: list[PhasePerformanceInput] = Field(default_factory=list)
    material: MaterialInput = Field(default_factory=MaterialInput)
    trajectory: TrajectoryInput = Field(default_factory=TrajectoryInput)
    opening_deviation: OpeningDeviationInput | None = None
    conversion_events: list[str] = Field(default_factory=list)  # event types
    #: Castling, detected from the stored SAN moves for the player's colour.
    castled: bool | None = None
    castling_ply: int | None = None
    #: Provenance of the underlying analysis (used for like-for-like grouping).
    engine: str | None = None
    engine_version: str | None = None
    depth: int | None = None
    analysis_version: str | None = None
    report_version: str | None = None

    @property
    def rating_difference(self) -> int | None:
        if self.player_rating is None or self.opponent_rating is None:
            return None
        return self.player_rating - self.opponent_rating

    def rating_bucket(self) -> str:
        """Opponent-strength context bucket — keeps unlike comparisons apart."""
        difference = self.rating_difference
        if difference is None:
            return "unknown"
        if difference >= 100:
            return "weaker_opponent"
        if difference <= -100:
            return "stronger_opponent"
        return "similar_opponent"


# --- outputs ------------------------------------------------------------------


class SampleNote(BaseModel):
    """Sample-size bookkeeping attached to every derived block."""

    games: int = 0
    events: int = 0
    claim_level: ClaimLevel = ClaimLevel.INSUFFICIENT
    coverage: Coverage = Coverage.INSUFFICIENT
    note: str | None = None


class PlayerGameStatistics(BaseModel):
    analyzed_games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    win_rate: float | None = None
    draw_rate: float | None = None
    loss_rate: float | None = None
    average_accuracy: float | None = None
    median_accuracy: float | None = None
    average_centipawn_loss: float | None = None
    median_centipawn_loss: float | None = None
    blunders_per_game: float | None = None
    mistakes_per_game: float | None = None
    inaccuracies_per_game: float | None = None
    average_game_length: float | None = None
    accuracy_sample: int = 0
    time_span: tuple[str | None, str | None] = (None, None)
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerColorStatistics(BaseModel):
    color: str
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    win_rate: float | None = None
    average_accuracy: float | None = None
    average_centipawn_loss: float | None = None
    blunders: int = 0
    mistakes: int = 0
    inaccuracies: int = 0
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerOpeningEntry(BaseModel):
    key: str
    eco_code: str | None = None
    name: str | None = None
    family: str | None = None
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    win_rate: float | None = None
    average_accuracy: float | None = None
    average_centipawn_loss: float | None = None
    deviation_games: int = 0
    recent_uses: int = 0
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerOpeningStatistics(BaseModel):
    #: Frequency (what the player plays) and performance (how it scores) are
    #: separate lists on purpose — they answer different questions.
    white_repertoire: list[PlayerOpeningEntry] = Field(default_factory=list)
    black_repertoire: list[PlayerOpeningEntry] = Field(default_factory=list)
    most_played: list[PlayerOpeningEntry] = Field(default_factory=list)
    families: dict[str, int] = Field(default_factory=dict)
    distinct_openings: int = 0
    deviation_rate: float | None = None
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerPhaseEntry(BaseModel):
    phase: str
    evaluated_moves: int = 0
    average_centipawn_loss: float | None = None
    problem_moves: int = 0
    accuracy: float | None = None
    share_of_loss: float | None = None
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerPhaseStatistics(BaseModel):
    phases: list[PlayerPhaseEntry] = Field(default_factory=list)
    weakest_phase: str | None = None
    strongest_phase: str | None = None
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerTacticalStatistics(BaseModel):
    created: dict[str, int] = Field(default_factory=dict)
    allowed: dict[str, int] = Field(default_factory=dict)
    created_total: int = 0
    allowed_total: int = 0
    created_per_game: float | None = None
    allowed_per_game: float | None = None
    missed_opportunities: int = 0
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerPositionalStatistics(BaseModel):
    features: dict[str, int] = Field(default_factory=dict)
    error_candidates: dict[str, int] = Field(default_factory=dict)
    features_per_game: float | None = None
    error_candidates_per_game: float | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerKingSafetyStatistics(BaseModel):
    castled_games: int = 0
    uncastled_games: int = 0
    castling_rate: float | None = None
    late_castling_games: int = 0
    events_created: int = 0
    events_allowed: int = 0
    top_event_types: dict[str, int] = Field(default_factory=dict)
    by_color: dict[str, dict[str, float | int | None]] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerMaterialStatistics(BaseModel):
    average_captures: float | None = None
    average_exchanges: float | None = None
    promotions: int = 0
    imbalance_games: int = 0
    imbalance_share: float | None = None
    average_final_balance: float | None = None
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerConversionStatistics(BaseModel):
    opportunities: int = 0
    conversions: int = 0
    conversion_rate: float | None = None
    advantage_lost: int = 0
    maintenance_rate: float | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerRecoveryStatistics(BaseModel):
    situations: int = 0
    improvements: int = 0
    improvement_rate: float | None = None
    saved_games: int = 0
    save_rate: float | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerTimeControlEntry(BaseModel):
    time_class: TimeClass
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    win_rate: float | None = None
    average_accuracy: float | None = None
    average_centipawn_loss: float | None = None
    blunders_per_game: float | None = None
    average_game_length: float | None = None
    conversion_rate: float | None = None
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerTimeControlStatistics(BaseModel):
    entries: list[PlayerTimeControlEntry] = Field(default_factory=list)
    sample: SampleNote = Field(default_factory=SampleNote)


class PlayerTrendEntry(BaseModel):
    window: int
    recent_games: int = 0
    baseline_games: int = 0
    recent_average_cpl: float | None = None
    baseline_average_cpl: float | None = None
    relative_change: float | None = None
    direction: str = "steady"  # lower_cpl | higher_cpl | steady
    supported: bool = False
    recent_wins: int = 0
    recent_draws: int = 0
    recent_losses: int = 0
    note: str | None = None


class PlayerTrendStatistics(BaseModel):
    entries: list[PlayerTrendEntry] = Field(default_factory=list)
    note: str | None = None


class PlayerOpponentContext(BaseModel):
    """Opponent strength preserved as context, never silently averaged away."""

    games_with_rating: int = 0
    average_player_rating: float | None = None
    average_opponent_rating: float | None = None
    average_rating_difference: float | None = None
    by_bucket: dict[str, dict[str, float | int | None]] = Field(default_factory=dict)
    sample: SampleNote = Field(default_factory=SampleNote)


class ChessDnaDimension(BaseModel):
    """One interpretable behavioural dimension.

    No 0–100 score: each dimension exposes the raw measured metric, its unit,
    the sample it came from, and the definition used — a number whose meaning
    the user can check.
    """

    key: str
    label: str
    value: float | None = None
    unit: str = ""
    definition: str
    games: int = 0
    events: int = 0
    coverage: Coverage = Coverage.INSUFFICIENT
    claim_level: ClaimLevel = ClaimLevel.INSUFFICIENT
    evidence: list[EvidenceRef] = Field(default_factory=list)
    note: str | None = None


class ChessDna(BaseModel):
    dimensions: list[ChessDnaDimension] = Field(default_factory=list)
    derived_from_games: int = 0
    notes: list[str] = Field(default_factory=list)


class PlayerInsight(BaseModel):
    """A structured, evidence-backed player-level statement.

    Never prose: the future LLM layer turns this into friendly language, so the
    product can never present a conclusion the evidence does not support.
    """

    id: str
    category: InsightCategory
    claim_level: ClaimLevel
    title: str
    statement: str
    metric: str | None = None
    value: float | None = None
    unit: str | None = None
    severity: str | None = None
    games: int = 0
    occurrences: int = 0
    coverage: Coverage = Coverage.INSUFFICIENT
    evidence: list[EvidenceRef] = Field(default_factory=list)
    methodology_version: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlayerProfile(BaseModel):
    """The stored, versioned player snapshot."""

    player_id: str
    display_name: str
    platform: str | None = None
    platform_username: str | None = None
    profile_version: str
    methodology_version: str
    feature_version: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated_at: datetime | None = None
    imported_games: int = 0
    analyzed_games: int = 0
    excluded_games: int = 0
    #: Games whose analysis failed or that have no stored report; they are
    #: counted and named, never silently dropped.
    excluded_game_ids: list[str] = Field(default_factory=list)
    coverage: Coverage = Coverage.INSUFFICIENT
    sufficient_data: bool = False
    policy: dict[str, Any] = Field(default_factory=dict)
    games: PlayerGameStatistics = Field(default_factory=PlayerGameStatistics)
    by_color: list[PlayerColorStatistics] = Field(default_factory=list)
    openings: PlayerOpeningStatistics = Field(default_factory=PlayerOpeningStatistics)
    phases: PlayerPhaseStatistics = Field(default_factory=PlayerPhaseStatistics)
    tactical: PlayerTacticalStatistics = Field(default_factory=PlayerTacticalStatistics)
    positional: PlayerPositionalStatistics = Field(default_factory=PlayerPositionalStatistics)
    king_safety: PlayerKingSafetyStatistics = Field(default_factory=PlayerKingSafetyStatistics)
    material: PlayerMaterialStatistics = Field(default_factory=PlayerMaterialStatistics)
    conversion: PlayerConversionStatistics = Field(default_factory=PlayerConversionStatistics)
    recovery: PlayerRecoveryStatistics = Field(default_factory=PlayerRecoveryStatistics)
    time_controls: PlayerTimeControlStatistics = Field(default_factory=PlayerTimeControlStatistics)
    opponents: PlayerOpponentContext = Field(default_factory=PlayerOpponentContext)
    trends: PlayerTrendStatistics = Field(default_factory=PlayerTrendStatistics)
    chess_dna: ChessDna = Field(default_factory=ChessDna)
    insights: list[PlayerInsight] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


__all__ = [
    "ChessDna",
    "ChessDnaDimension",
    "EvidenceRef",
    "GameOutcome",
    "InsightCategory",
    "KingSafetyEventInput",
    "MaterialInput",
    "OpeningDeviationInput",
    "PhasePerformanceInput",
    "PlayerColorStatistics",
    "PlayerConversionStatistics",
    "PlayerErrorEvent",
    "PlayerGameInput",
    "PlayerGameStatistics",
    "PlayerInsight",
    "PlayerKingSafetyStatistics",
    "PlayerMaterialStatistics",
    "PlayerOpeningEntry",
    "PlayerOpeningStatistics",
    "PlayerPhaseEntry",
    "PlayerPhaseStatistics",
    "PlayerPositionalStatistics",
    "PlayerProfile",
    "PlayerRecoveryStatistics",
    "PlayerTacticalStatistics",
    "PlayerTimeControlEntry",
    "PlayerTimeControlStatistics",
    "PlayerTrendEntry",
    "PlayerTrendStatistics",
    "PlayerInsightPolicy",
    "PositionalEventInput",
    "SampleNote",
    "TacticalEventInput",
    "TimeClass",
    "TrajectoryInput",
]
