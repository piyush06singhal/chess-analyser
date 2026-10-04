"""Game-intelligence layer: shared primitives.

This layer sits **above** the engine analysis. It never calls Stockfish, never
recomputes an evaluation, and never invents a chess fact: every conclusion it
produces is derived from

1. **engine facts** — evaluations, centipawn loss, classifications, principal
   variations already stored by the Phase 3 pipeline, and
2. **board-state features** — computed deterministically with python-chess.

The evidence model is the whole point of the layer. Every :class:`Insight`
carries:

* ``source`` — is this an engine fact, a Caissa-derived feature (computed from
  the board by Caissa code), or a Caissa interpretation (a rule Caissa applies to
  features, e.g. "this is a conversion failure candidate")?
* ``certainty`` — ``confirmed`` when the statement is structurally verified
  (a piece really is attacked and undefended) or ``candidate`` when Caissa is
  offering a hypothesis (a tactic *may* be the reason the evaluation dropped).
* ``evidence`` — the raw inputs the conclusion was built from, so a later
  natural-language layer (or a human reviewer) can audit it.

Nothing here is natural language. ``statement`` fields are factual templates
("White's largest evaluation drop occurred after move 28."), never commentary.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import chess
from pydantic import BaseModel, Field

from argus.analysis.classification import MoveClassification
from argus.analysis.engine.base import to_cp
from argus.analysis.phase import GamePhase
from argus.analysis.perspective import to_white_perspective
from argus.chess_core.models import Color

#: Bumped when the intelligence semantics change (thresholds, methodology).
#: 4.2 — the forced-exchange detector now requires a real capture of material
#: worth reporting, so quiet moves and routine pawn trades are no longer
#: mislabelled as recaptures.
#: 4.1 — accuracy is computed from a single engine search (best line vs the move
#: actually played) instead of mixing two searches, and gained a phase / error
#: type / material-state breakdown.
REPORT_VERSION = "4.2"


class EvidenceSource(str, Enum):
    """Where a conclusion came from — the audit trail of the whole layer."""

    ENGINE_FACT = "engine_fact"
    ARGUS_DERIVED_FEATURE = "argus_derived_feature"
    ARGUS_INTERPRETATION = "argus_interpretation"


class Certainty(str, Enum):
    """How strong a claim is allowed to be."""

    CONFIRMED = "confirmed"
    CANDIDATE = "candidate"


class GamePhasePolicy(BaseModel):
    """Configurable board-state boundaries for the game-phase detector."""

    endgame_max_phase_material: int = 14
    opening_min_phase_material: int = 52
    opening_min_undeveloped_total: int = 3
    #: Non-pawn material below which undeveloped pieces no longer indicate the
    #: opening: a shattered position with a rook still at home is not "the
    #: opening", it is a transitional position that has been decided elsewhere.
    opening_development_min_phase_material: int = 30
    endgame_max_total_pieces: int = 14
    queens_off_endgame_max_phase_material: int = 26


class MaterialPolicy(BaseModel):
    """Material accounting. Values are pawns (king excluded)."""

    pawn: int = 1
    knight: int = 3
    bishop: int = 3
    rook: int = 5
    queen: int = 9
    #: Minimum |balance change| in pawns to call it a material transition.
    transition_min: int = 2


class TacticPolicy(BaseModel):
    """Thresholds for structural tactical detection."""

    #: Pieces of at least this value count as tactical targets.
    min_target_value: int = 3
    fork_min_targets: int = 2


class PositionalPolicy(BaseModel):
    """Thresholds for positional feature/error-candidate detection."""

    #: Centipawn loss from which a structural change is an *error candidate*.
    error_candidate_min_cp_loss: int = 100
    #: A non-pawn piece with at most this many legal moves counts as restricted.
    restricted_mobility: int = 1
    lost_center_min: int = 2
    undeveloped_after_ply: int = 24


class KingSafetyPolicy(BaseModel):
    """Weights for the king-safety score (ARGS-derived, documented)."""

    missing_shield_pawn_weight: int = 2
    open_file_weight: int = 2
    attacker_weight: int = 2
    check_weight: int = 1
    limited_mobility_weight: int = 1
    medium_score: int = 4
    high_score: int = 8


class AdvantagePolicy(BaseModel):
    """ARGS analytical advantage bands, in centipawns (White perspective)."""

    slight_threshold: int = 60
    advantage_threshold: int = 150
    winning_threshold: int = 300
    decisive_threshold: int = 600


class TurningPointPolicy(BaseModel):
    """Turning-point detection thresholds."""

    min_swing_cp: int = 150
    high_swing_cp: int = 300
    material_swing_pawns: int = 2
    persistence_plies: int = 4
    merge_window_plies: int = 2
    max_turning_points: int = 8
    missed_win_min_cp: int = 250


class ConversionPolicy(BaseModel):
    """Conversion / comeback detection."""

    winning_state_cp: int = 300
    lost_advantage_max_cp: int = 120
    min_winning_plies: int = 2


class AccuracyPolicy(BaseModel):
    """ARGS accuracy methodology parameters (see ``accuracy.py``)."""

    #: Logistic scale (centipawns) mapping an evaluation to win expectation.
    scale_cp: int = 300
    #: Positions already this decided are excluded from the game average.
    decided_win_expectation: float = 0.95
    #: Lower bound on the accuracy denominator, so an already-lost position
    #: cannot inflate or collapse the normalized drop.
    denominator_floor: float = 0.5
    minimum_scored_moves: int = 5
    #: Below this many scored moves, a breakdown slice is flagged as a small
    #: sample so no claim is ever made from a handful of moves.
    minimum_group_moves: int = 5
    #: Material-state bands used by the accuracy breakdown (pawns).
    material_band_minor: int = 1
    material_band_major: int = 3


class PhasePerformancePolicy(BaseModel):
    """Minimum sample sizes before phase statistics are called reliable."""

    reliable_min_moves: int = 10
    reliable_min_scored: int = 8


class IntelligencePolicy(BaseModel):
    """One configurable policy object for the whole intelligence layer."""

    game_phase: GamePhasePolicy = Field(default_factory=GamePhasePolicy)
    material: MaterialPolicy = Field(default_factory=MaterialPolicy)
    tactics: TacticPolicy = Field(default_factory=TacticPolicy)
    positional: PositionalPolicy = Field(default_factory=PositionalPolicy)
    king_safety: KingSafetyPolicy = Field(default_factory=KingSafetyPolicy)
    advantage: AdvantagePolicy = Field(default_factory=AdvantagePolicy)
    turning_points: TurningPointPolicy = Field(default_factory=TurningPointPolicy)
    conversion: ConversionPolicy = Field(default_factory=ConversionPolicy)
    accuracy: AccuracyPolicy = Field(default_factory=AccuracyPolicy)
    phase_performance: PhasePerformancePolicy = Field(default_factory=PhasePerformancePolicy)

    def describe(self) -> dict[str, Any]:
        """Reproducibility record of the exact policy used for a report."""
        return self.model_dump()


#: Piece-value table (pawns) used by board-state checks that need magnitudes.
PIECE_POINTS: dict[str, int] = {
    "p": 1,
    "n": 3,
    "b": 3,
    "r": 5,
    "q": 9,
    "k": 0,
}


class MoveFact(BaseModel):
    """One played move, exactly as the engine analysis layer stored it.

    Evaluations stay in the **mover perspective** (the engine's native form, and
    the form required for centipawn loss). White-perspective values are provided
    as properties, computed through the shared ``analysis.perspective`` module so
    there is exactly one sign convention in the codebase.
    """

    ply: int = Field(ge=1)
    move_number: int = Field(ge=1)
    mover: Color
    san: str
    uci: str
    fen_before: str
    fen_after: str
    eval_before_cp: int | None = None
    eval_before_mate: int | None = None
    eval_after_cp: int | None = None
    eval_after_mate: int | None = None
    eval_change_cp: int | None = Field(
        default=None, description="Change from the mover's perspective (negative = lost ground)"
    )
    centipawn_loss: int | None = None
    #: The played move's own score (mover perspective) exactly as the engine
    #: reported it, plus where that score came from. ``played_eval_source`` is
    #: ``"same_search"`` when it was scored inside the same search as the best
    #: line (exact, no cross-search noise) and ``"resulting_position"`` when it
    #: had to come from a separate search of the position after the move.
    played_eval_cp: int | None = None
    played_eval_mate: int | None = None
    played_eval_source: str | None = None
    classification: MoveClassification | None = None
    best_move_uci: str | None = None
    best_move_san: str | None = None
    is_best_move: bool = False
    phase: GamePhase | None = None
    principal_variation: list[str] = Field(default_factory=list)
    depth: int = 0

    # --- perspective helpers (single conversion point) ------------------------

    @property
    def mover_eval_before(self) -> int | None:
        """Best-line evaluation before the move, in centipawns, mover perspective."""
        return to_cp(self.eval_before_cp, self.eval_before_mate)

    @property
    def mover_eval_after(self) -> int | None:
        """Evaluation after the played move, in centipawns, mover perspective."""
        return to_cp(self.eval_after_cp, self.eval_after_mate)

    @property
    def eval_before_white(self) -> int | None:
        """Best-line evaluation before the move, White perspective (stored convention)."""
        cp, mate = to_white_perspective(
            self.eval_before_cp, self.eval_before_mate, side=self.mover
        )
        return to_cp(cp, mate)

    @property
    def eval_after_white(self) -> int | None:
        """Evaluation after the move, White perspective (stored convention)."""
        cp, mate = to_white_perspective(
            self.eval_after_cp, self.eval_after_mate, side=self.mover
        )
        return to_cp(cp, mate)

    @property
    def mate_after_white(self) -> int | None:
        """Mate distance after the move, White perspective (``None`` when not mate)."""
        _, mate = to_white_perspective(
            self.eval_after_cp, self.eval_after_mate, side=self.mover
        )
        return mate

    @property
    def mate_before_white(self) -> int | None:
        _, mate = to_white_perspective(
            self.eval_before_cp, self.eval_before_mate, side=self.mover
        )
        return mate

    @property
    def evaluated(self) -> bool:
        """True when the engine produced an evaluation for this move."""
        return self.eval_before_cp is not None or self.eval_before_mate is not None

    @property
    def is_problem(self) -> bool:
        """True when the engine classified the move as inaccurate or worse."""
        return self.classification in {
            MoveClassification.INACCURATE,
            MoveClassification.MISTAKE,
            MoveClassification.BLUNDER,
        }

    def side_label(self) -> str:
        return "White" if self.mover == Color.WHITE else "Black"


class CriticalFact(BaseModel):
    """A critical position the engine analysis layer already flagged.

    Carried through unchanged from persistence so the intelligence layer never
    re-derives critical moments from a different rule set.
    """

    ply: int
    move_number: int
    color: Color
    san: str
    reason: str
    severity: str
    severity_score: int = 0
    classification: MoveClassification | None = None
    swing_cp: int | None = None
    evaluation_before_white: int | None = None
    evaluation_after_white: int | None = None
    is_mate_related: bool = False
    detail: str | None = None


class GameContext(BaseModel):
    """Game metadata, carried through so the report can describe the game."""

    game_id: str | None = None
    white_player: str = "Unknown"
    black_player: str = "Unknown"
    white_rating: int | None = None
    black_rating: int | None = None
    result: str = "*"
    date: str | None = None
    event: str | None = None
    site: str | None = None
    time_control: str | None = None
    eco_code: str | None = Field(default=None, description="From PGN headers, when present")
    opening_name: str | None = Field(default=None, description="From PGN headers, when present")
    initial_position: str = ""
    final_position: str = ""
    move_count: int = 0


class Insight(BaseModel):
    """One evidence-carrying conclusion.

    ``statement`` is always a factual statement (counts, evaluations, plies) —
    never subjective commentary. Interpretation is limited to the
    ``insight_type``/``category`` labels and is always tagged as a Caissa
    interpretation when it goes beyond the raw measurement.
    """

    insight_type: str
    category: str | None = None
    source: EvidenceSource
    certainty: Certainty = Certainty.CONFIRMED
    ply: int | None = None
    move_number: int | None = None
    side: Color | None = None
    severity: str | None = None
    statement: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    """A deterministic, factual statement extracted from the analysis."""

    key: str
    statement: str
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE
    ply: int | None = None
    move_number: int | None = None
    side: Color | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class Recommendation(BaseModel):
    """A rule-derived training recommendation (structured, not prose)."""

    key: str
    focus: str
    rationale: str
    evidence_refs: list[int] = Field(
        default_factory=list, description="Plies the recommendation is based on"
    )
    observed_count: int = 0
    source: EvidenceSource = EvidenceSource.ARGUS_INTERPRETATION


class Unavailable(BaseModel):
    """An honest record of something the layer could not compute, and why."""

    section: str
    reason: str
    required: str | None = None


def severity_from_magnitude(magnitude: int, *, medium: int, high: int) -> str:
    """Map a magnitude onto low/medium/high using explicit thresholds."""
    if magnitude >= high:
        return "high"
    if magnitude >= medium:
        return "medium"
    return "low"


def sample_reliability(count: int, minimum: int) -> bool:
    """True when a sample is large enough to state without a caveat."""
    return count >= minimum


def other(color: Color) -> Color:
    return Color.BLACK if color == Color.WHITE else Color.WHITE


def color_label(color: Color) -> str:
    return "White" if color == Color.WHITE else "Black"


def chess_color(color: Color | str) -> chess.Color:
    """Map a Caissa :class:`Color` onto the boolean python-chess expects."""
    return chess.WHITE if Color(color) == Color.WHITE else chess.BLACK
