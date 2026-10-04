"""Opponent-intelligence data model (Phase 9).

Two halves, separated exactly as Phase 5's player intelligence is:

*Inputs* (``OpponentGameInput`` and its ``OpponentMoveInput`` rows) are the
per-game facts the API layer reads out of the **stored** engine analysis and
game metadata. The core never touches the database or the engine: it aggregates
structured evidence, so a report is deterministic, cheap to rebuild, and
testable with real fixtures.

*Outputs* are derived statistics only, each carrying the sample size that
produced it and a coverage / claim level, so the UI can never present a weak
signal as a finding.

This is analytics, not psychology. Nothing here models intent, emotion, or
state of mind; every field is a count, a share, or a centipawn measurement over
games that were actually played.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from argus.player_intelligence.models import GameOutcome
from argus.opponent_intelligence.policy import ClaimLevel, Coverage

#: Bumped when any aggregation in this package changes, so a stored report can
#: be traced back to the rules that produced it.
OPPONENT_METHODOLOGY_VERSION = "9.0"


# ---------------------------------------------------------------------------
# Inputs (API layer → core; no DB, no engine)
# ---------------------------------------------------------------------------


class OpponentMoveInput(BaseModel):
    """One stored move of one game, from the mover's perspective."""

    ply: int
    move_number: int
    color: str
    san: str
    uci: str
    fen_before: str
    phase: str | None = None
    classification: str | None = None
    centipawn_loss: int | None = None
    is_best_move: bool = False
    best_move_uci: str | None = None
    best_move_san: str | None = None
    evaluation_before_cp: int | None = None
    evaluation_before_mate: int | None = None
    candidate_moves: list[dict] = Field(default_factory=list)
    principal_variation: list[str] = Field(default_factory=list)


class OpponentGameInput(BaseModel):
    """One game the opponent played, with its metadata and stored moves."""

    game_id: str
    #: The colour the *opponent* (the subject) played in this game.
    color: str
    opponent_name: str
    opponent_rating: int | None = None
    other_rating: int | None = None
    opponent_of: str = ""
    result: str = "*"
    #: Outcome from the subject's perspective.
    outcome: GameOutcome = GameOutcome.UNKNOWN
    date: str | None = None
    event: str | None = None
    time_control: str | None = None
    eco_code: str | None = None
    opening_name: str | None = None
    move_count: int = 0
    source: str | None = None
    analysis_status: str | None = None
    analysis_version: str | None = None
    engine: str | None = None
    engine_version: str | None = None
    depth: int | None = None
    moves: list[OpponentMoveInput] = Field(default_factory=list)

    @property
    def analyzed(self) -> bool:
        return self.analysis_status == "analyzed" and bool(self.moves)


# ---------------------------------------------------------------------------
# Evidence + identity
# ---------------------------------------------------------------------------


class OpponentEvidence(BaseModel):
    """A pointer back to the exact game/position behind a claim.

    The UI resolves it to ``/game/{game_id}?ply={ply}`` so every opponent-level
    statement is one click from the position that produced it.
    """

    game_id: str
    ply: int | None = None
    move_number: int | None = None
    san: str | None = None
    label: str | None = None
    detail: str | None = None


class PlayerIdentity(BaseModel):
    """Who the opponent is in Caissa's data model — never inferred, always stored."""

    player_id: int
    name: str
    identity_key: str | None = None
    platform: str | None = None
    platform_username: str | None = None
    title: str | None = None


class OpponentGame(BaseModel):
    """One game in the opponent's history, as the API renders it."""

    game_id: str
    color: str
    opponent_name: str
    opponent_rating: int | None = None
    other_rating: int | None = None
    result: str
    outcome: GameOutcome
    date: str | None = None
    event: str | None = None
    time_control: str | None = None
    eco_code: str | None = None
    opening_name: str | None = None
    move_count: int = 0
    source: str | None = None
    analysis_status: str | None = None
    analysis_version: str | None = None


# ---------------------------------------------------------------------------
# Repertoire
# ---------------------------------------------------------------------------


class OpponentOpeningNode(BaseModel):
    """One move the opponent actually played, with the position before it.

    This is a *tree node keyed by position*: the same node aggregates every game
    in which the opponent reached that position and chose that move. Occurrences
    and outcomes are measured, never estimated.
    """

    san: str
    uci: str
    fen: str
    ply: int
    move_number: int
    occurrences: int
    share: float
    wins: int = 0
    draws: int = 0
    losses: int = 0
    claim_level: ClaimLevel = ClaimLevel.OBSERVATION
    evidence: list[OpponentEvidence] = Field(default_factory=list)


class OpponentOpeningProfile(BaseModel):
    """The opponent's repertoire as one colour, evidence-gated."""

    color: str
    total_games: int
    analyzed_games: int
    coverage: Coverage
    #: Root choices (the first move the opponent played in the opening) and the
    #: most frequent continuation nodes, ordered by occurrences.
    nodes: list[OpponentOpeningNode] = Field(default_factory=list)
    top_lines: list[str] = Field(default_factory=list)
    #: Per-opening-family counts measured from stored game metadata.
    opening_families: dict[str, int] = Field(default_factory=dict)
    policy: dict = Field(default_factory=dict)
    note: str = ""


# ---------------------------------------------------------------------------
# Position responses
# ---------------------------------------------------------------------------


class OpponentResponseOption(BaseModel):
    uci: str
    san: str
    occurrences: int
    share: float
    wins: int = 0
    draws: int = 0
    losses: int = 0
    evidence: list[OpponentEvidence] = Field(default_factory=list)


class OpponentPositionResponse(BaseModel):
    """How the opponent answered one position (exact or normalized match)."""

    query_fen: str
    match: str  # "exact" | "normalized_pieces" | "none"
    side_to_move: str | None = None
    occurrences: int = 0
    responses: list[OpponentResponseOption] = Field(default_factory=list)
    claim_level: ClaimLevel = ClaimLevel.OBSERVATION
    sample_note: str = ""
    evidence: list[OpponentEvidence] = Field(default_factory=list)


class OpponentPositionPattern(BaseModel):
    """A recurring structural situation the opponent reached, with their answer."""

    key: str
    label: str
    fen_signature: str
    occurrences: int
    side_to_move: str
    responses: list[OpponentResponseOption] = Field(default_factory=list)
    wins: int = 0
    draws: int = 0
    losses: int = 0
    claim_level: ClaimLevel = ClaimLevel.OBSERVATION
    evidence: list[OpponentEvidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tendencies
# ---------------------------------------------------------------------------


class OpponentTendency(BaseModel):
    """One measured, evidence-gated behavioural regularity.

    ``measurement`` names exactly what was counted (e.g. "share of games in
    which the opponent castled kingside by move 10"); ``value`` is the measured
    result; ``sample_size`` is how many observations it rests on.
    """

    key: str
    label: str
    measurement: str
    value: float | int | str
    sample_size: int
    share: float | None = None
    claim_level: ClaimLevel = ClaimLevel.INSUFFICIENT
    evidence: list[OpponentEvidence] = Field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------------------
# Phase / tactical / positional statistics
# ---------------------------------------------------------------------------


class OpponentPhaseStat(BaseModel):
    phase: str
    moves: int
    games: int
    significant_errors: int
    error_rate: float | None = None
    avg_centipawn_loss: float | None = None
    avg_accuracy_proxy: float | None = None
    claim_level: ClaimLevel = ClaimLevel.OBSERVATION


class OpponentPhaseStatistics(BaseModel):
    """Measured performance by phase, from stored move analysis only."""

    color: str | None = None
    analyzed_games: int = 0
    phases: list[OpponentPhaseStat] = Field(default_factory=list)
    tactical_error_share: float | None = None
    positional_error_share: float | None = None
    sample_note: str = ""
    policy: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Aggregated profile + insights + report
# ---------------------------------------------------------------------------


class OpponentStatistics(BaseModel):
    total_games: int = 0
    analyzed_games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    as_white: int = 0
    as_black: int = 0
    avg_opponent_rating: float | None = None
    first_date: str | None = None
    last_date: str | None = None
    result_share: dict[str, float] = Field(default_factory=dict)


class OpponentProfile(BaseModel):
    """The top-level opponent document: identity, history, repertoire, stats."""

    identity: PlayerIdentity
    methodology_version: str = OPPONENT_METHODOLOGY_VERSION
    generated_at: datetime
    statistics: OpponentStatistics
    coverage: Coverage
    repertoire: dict[str, OpponentOpeningProfile] = Field(default_factory=dict)
    phase_statistics: OpponentPhaseStatistics | None = None
    tendencies: list[OpponentTendency] = Field(default_factory=list)
    position_patterns: list[OpponentPositionPattern] = Field(default_factory=list)
    policy: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class OpponentInsight(BaseModel):
    """One evidence-gated finding, ready to render with its sample size."""

    key: str
    title: str
    statement: str
    category: str
    claim_level: ClaimLevel
    sample_size: int
    evidence: list[OpponentEvidence] = Field(default_factory=list)
    preparation_hint: str = ""


class OpponentPreparationReport(BaseModel):
    """The preparation document: what to expect and what to prepare against."""

    identity: PlayerIdentity
    methodology_version: str = OPPONENT_METHODOLOGY_VERSION
    generated_at: datetime
    color_to_prepare: str | None = None
    statistics: OpponentStatistics
    coverage: Coverage
    repertoire: OpponentOpeningProfile | None = None
    phase_statistics: OpponentPhaseStatistics | None = None
    tendencies: list[OpponentTendency] = Field(default_factory=list)
    insights: list[OpponentInsight] = Field(default_factory=list)
    expected_lines: list[OpponentOpeningNode] = Field(default_factory=list)
    policy: dict = Field(default_factory=dict)
    evidence_count: int = 0
    limitations: list[str] = Field(default_factory=list)


__all__ = [
    "OPPONENT_METHODOLOGY_VERSION",
    "OpponentEvidence",
    "OpponentGame",
    "OpponentGameInput",
    "OpponentInsight",
    "OpponentMoveInput",
    "OpponentOpeningNode",
    "OpponentOpeningProfile",
    "OpponentPhaseStat",
    "OpponentPhaseStatistics",
    "OpponentPositionPattern",
    "OpponentPositionResponse",
    "OpponentPreparationReport",
    "OpponentProfile",
    "OpponentResponseOption",
    "OpponentStatistics",
    "OpponentTendency",
    "PlayerIdentity",
]
