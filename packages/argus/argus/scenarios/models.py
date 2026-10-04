"""Structured shapes for Phase 10 decision intelligence.

Every model here is either a **board fact** (derived from the position with no
engine call), an **engine result** (copied from Stockfish's own output), or an
**evidence reference** (where a number came from). Nothing in this module
computes an evaluation, infers a probability, or writes a sentence.

The distinction matters for the counterfactual product. A comparison has to be
able to say "the evaluation is different" and "the structure is different" as two
separate statements, and an explanation has to be assembled from listed facts
rather than from a model's impression of the position.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from argus.analysis.features.models import RawPositionFeatures
from argus.scenarios.policy import (
    DECISION_METHODOLOGY_VERSION,
    ComparisonDomain,
    MoveQuality,
    ScenarioType,
)


class EvidenceRef(BaseModel):
    """Where a number in a scenario came from.

    A reference that resolves to a stored ply is the only kind that may be
    presented as history; an engine reference names its search so a reader can
    reproduce it.
    """

    kind: str = Field(description="board | engine | stored_analysis | game")
    detail: str
    game_id: str | None = None
    ply: int | None = None
    uci: str | None = None


class EngineConfig(BaseModel):
    """The search limits a result was produced under — the reproducibility key."""

    depth: int | None = None
    multipv: int = 1
    movetime_ms: int | None = None
    engine: str = "stockfish"
    engine_version: str | None = None

    def label(self) -> str:
        if self.movetime_ms:
            return f"{self.engine} {self.engine_version or '?'} movetime={self.movetime_ms}ms pv={self.multipv}"
        return f"{self.engine} {self.engine_version or '?'} depth={self.depth} pv={self.multipv}"


class EngineMetrics(BaseModel):
    """One engine result, exactly as the engine reported it."""

    fen: str
    depth: int
    multipv: int = 1
    cp: int | None = Field(default=None, description="Score from the mover's perspective")
    mate: int | None = None
    cp_white: int | None = Field(
        default=None,
        description="Same score normalised to White's perspective, for comparability",
    )
    best_move_uci: str | None = None
    best_move_san: str | None = None
    pv: list[str] = Field(default_factory=list)
    multipv_rank: int | None = Field(
        default=None, description="Rank of the requested move inside the MultiPV window"
    )
    nodes: int | None = None
    engine: str = "stockfish"
    engine_version: str | None = None
    available: bool = True
    unavailable_reason: str | None = None


class PositionFacts(BaseModel):
    """Everything about a position that needs no engine: the board, itself."""

    fen: str
    side_to_move: str
    phase: str
    material_white: int
    material_black: int
    material_balance: int = Field(description="White minus black, in pawn units x100")
    move_number: int | None = None
    legal_move_count: int
    is_check: bool
    is_terminal: bool
    terminal_reason: str | None = None
    castling_rights: str | None = None
    features: RawPositionFeatures


class FeatureDifference(BaseModel):
    """One measured structural difference between two positions.

    ``basis`` names what produced it so a structural claim is never mistaken for
    an engine verdict.
    """

    domain: ComparisonDomain
    feature: str
    value_a: float | int | None = None
    value_b: float | int | None = None
    delta: float | int | None = None
    direction: str = Field(description="a_higher | b_higher | equal | incomparable")
    higher_is: str | None = Field(
        default=None, description="Which side benefits when the value is higher, when known"
    )
    basis: str = "board_feature"


class PositionComparison(BaseModel):
    """Two positions compared along two explicitly separate axes.

    ``engine_difference`` is a search result. ``structural_differences`` are board
    facts. They are never merged into a single score, because a product that
    merges them invents a relationship the data does not contain.
    """

    fen_a: str
    fen_b: str
    facts_a: PositionFacts | None = None
    facts_b: PositionFacts | None = None
    engine_a: EngineMetrics | None = None
    engine_b: EngineMetrics | None = None
    engine_difference: dict = Field(default_factory=dict)
    structural_differences: list[FeatureDifference] = Field(default_factory=list)
    phase_change: str | None = None
    material_change_cp: int | None = None
    notes: list[str] = Field(default_factory=list)
    methodology_version: str = DECISION_METHODOLOGY_VERSION


class CandidateAssessment(BaseModel):
    """One candidate move, with everything measured about it.

    An illegal move is reported as ``legal=False`` with a reason instead of being
    evaluated: the engine cannot score a move that cannot be played, and inventing
    a score for it would be the exact failure this phase must avoid.
    """

    uci: str
    san: str | None = None
    legal: bool = True
    legality_note: str | None = None
    rank: int | None = None
    cp: int | None = None
    mate: int | None = None
    cp_white: int | None = None
    centipawn_loss: int | None = None
    quality: MoveQuality = MoveQuality.UNKNOWN
    depth: int | None = None
    pv: list[str] = Field(default_factory=list)
    pv_san: list[str] = Field(default_factory=list)
    is_engine_best: bool = False
    is_played_move: bool = False
    material_after: dict = Field(default_factory=dict)
    material_consequence: str | None = None
    tactical_consequence: list[str] = Field(default_factory=list)
    resulting_phase: str | None = None
    position_type: str | None = Field(
        default=None,
        description="A short board-derived descriptor: phase plus pawn-structure shape",
    )
    structural_deltas: list[FeatureDifference] = Field(default_factory=list)
    eval_source: str = Field(
        default="unavailable",
        description="same_search | resulting_position | unavailable",
    )


class CandidateComparison(BaseModel):
    """A set of candidate moves in one position, all from one search."""

    fen: str
    side_to_move: str
    phase: str
    engine_config: EngineConfig
    candidates: list[CandidateAssessment] = Field(default_factory=list)
    best_cp: int | None = None
    best_move_uci: str | None = None
    requested: int = 0
    truncated: bool = False
    notes: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    methodology_version: str = DECISION_METHODOLOGY_VERSION


class ContinuationPly(BaseModel):
    """One ply of a played-out line."""

    ply: int
    move_number: int
    color: str
    uci: str
    san: str | None = None
    fen_before: str
    fen_after: str
    cp: int | None = Field(default=None, description="Mover's perspective")
    mate: int | None = None
    cp_white: int | None = None
    is_best_in_search: bool = True


class ScenarioBranch(BaseModel):
    """One immutable alternative future for a position.

    A branch never modifies the game it came from; it is a value object that can
    be stored, replayed and compared.
    """

    scenario_type: ScenarioType
    source_fen: str
    alternative_move_uci: str
    alternative_move_san: str | None = None
    actual_move_uci: str | None = None
    actual_move_san: str | None = None
    engine_config: EngineConfig
    actual_continuation: list[ContinuationPly] = Field(default_factory=list)
    alternative_continuation: list[ContinuationPly] = Field(default_factory=list)
    actual_eval_cp: int | None = None
    alternative_eval_cp: int | None = None
    actual_eval_source: str = "unavailable"
    alternative_eval_source: str = "unavailable"
    evaluation_change_cp: int | None = Field(
        default=None, description="alternative minus actual, mover perspective"
    )
    comparison: PositionComparison | None = None
    moves_played: int = 0
    plies_requested: int = 0
    truncated: bool = False
    #: Evidence that belongs to this *type's* question and not to the branch
    #: mechanics: the opponent's stored replies for an opponent-response
    #: scenario, or the phase facts that made the question askable. Empty for
    #: types that need nothing beyond the two lines.
    type_context: dict = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ExplanationBundle(BaseModel):
    """Structured facts an explanation may be built from — and nothing else.

    Handing the agent this bundle instead of the raw position is what keeps an
    explanation grounded: every sentence it can write traces to one of these
    entries, each of which carries the number and its source.
    """

    question: str
    facts: list[str] = Field(default_factory=list)
    numbers: dict = Field(default_factory=dict)
    engine_config: EngineConfig | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    insufficient: bool = False
    unavailable_reason: str | None = None


class ScenarioOutcome(BaseModel):
    """The result of a counterfactual request, including the honest refusals.

    ``status`` is one of ``ok``, ``illegal_move``, ``analysis_pending``,
    ``insufficient_evidence`` or ``unavailable``. A refusal is a first-class
    result, not an error: "that move is not legal here" is an answer.
    """

    status: str = "ok"
    message: str | None = None
    branch: ScenarioBranch | None = None
    comparison: PositionComparison | None = None
    explanation: ExplanationBundle | None = None
    prediction: dict | None = None
    scenario_id: int | None = None
    methodology_version: str = DECISION_METHODOLOGY_VERSION


class ScenarioRecord(BaseModel):
    """A persisted scenario, as stored in the database."""

    id: int
    scenario_type: ScenarioType
    source_fen: str
    resulting_fen: str | None = None
    game_id: str | None = None
    ply: int | None = None
    owner_player_id: int | None = None
    methodology_version: str = DECISION_METHODOLOGY_VERSION
    engine_config: EngineConfig
    branch: dict = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime | None = None


class TurningPoint(BaseModel):
    """One moment in a game an explorer can branch from.

    Built **only** from stored analysis: an explorer must be able to list what-if
    opportunities without running the engine for each one.
    """

    ply: int
    move_number: int
    color: str
    san: str
    uci: str
    fen_before: str
    classification: str | None = None
    phase: str | None = None
    evaluation_before_cp: int | None = None
    evaluation_after_cp: int | None = None
    swing_cp: int | None = None
    centipawn_loss: int | None = None
    best_move_uci: str | None = None
    best_move_san: str | None = None
    is_best_move: bool = False
    is_critical: bool = False
    critical_reason: str | None = None
    severity: str | None = None
    alternatives: list[CandidateAssessment] = Field(default_factory=list)
    alternative_count: int = 0
    what_if_available: bool = False
    branchable: bool = True


class TurningPointExplorer(BaseModel):
    """The stored, engine-free map of where a game could have gone differently."""

    game_id: str
    analysis_version: str | None = None
    plies_analyzed: int = 0
    moves_total: int | None = None
    evaluation_series: list[dict] = Field(default_factory=list)
    turning_points: list[TurningPoint] = Field(default_factory=list)
    critical_moment_count: int = 0
    branchable_count: int = 0
    notes: list[str] = Field(default_factory=list)
    methodology_version: str = DECISION_METHODOLOGY_VERSION


__all__ = [
    "CandidateAssessment",
    "CandidateComparison",
    "ContinuationPly",
    "EngineConfig",
    "EngineMetrics",
    "EvidenceRef",
    "ExplanationBundle",
    "FeatureDifference",
    "PositionComparison",
    "PositionFacts",
    "ScenarioBranch",
    "ScenarioOutcome",
    "ScenarioRecord",
    "TurningPoint",
    "TurningPointExplorer",
]
