"""Training models: the shared vocabulary of the training engine.

These are the *package* models — plain, serializable, engine-free. The ORM layer
(``argus_api.db.models.TrainingPosition``) persists them; nothing here touches a
database, so the whole engine is testable without one.

The category list is deliberately short and every value must be *earned*: the
generator assigns a category only when the stored evidence supports it (spec §2 —
"do not implement all categories as superficial labels"). A move whose analysis
shows a tactical motif is a tactical exercise; a move that is merely bad is
``calculation`` — the honest category when nothing more specific is provable.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

#: Bumped when any methodology in this package changes, so stored exercises can
#: be traced to the rules that produced them.
#: 8.1 adds the CONTINUE_LINE exercise mechanism (a multi-ply line graded against
#: the stored principal variation). 8.2 adds the replay generator: whole-game
#: WHAT_WENT_WRONG and RECONSTRUCTION exercises built from a game's stored
#: analyses (``argus.training.replay``), each carrying a ``replay_context``.
TRAINING_METHODOLOGY_VERSION = "8.2"


class Category(str, Enum):
    """A training category — assigned only when evidence supports it."""

    TACTICAL = "tactical"
    POSITIONAL = "positional"
    CALCULATION = "calculation"
    OPENING = "opening"
    ENDGAME = "endgame"
    CONVERSION = "conversion"
    RECOVERY = "recovery"


#: Categories the generator may assign. CONVERSION and RECOVERY joined in
#: methodology 8.1: they are never assigned from a single move, only from the
#: game's stored evaluation trajectory **and** its result
#: (``argus.training.gamearc``), so the label always rests on whole-game
#: evidence rather than a guess about what happened later.
GENERATOR_CATEGORIES = (
    Category.TACTICAL,
    Category.POSITIONAL,
    Category.CALCULATION,
    Category.OPENING,
    Category.ENDGAME,
    Category.CONVERSION,
    Category.RECOVERY,
)


CATEGORY_LABELS: dict[str, str] = {
    "tactical": "Tactics",
    "positional": "Positional",
    "calculation": "Calculation",
    "opening": "Opening",
    "endgame": "Endgame",
    "conversion": "Conversion",
    "recovery": "Recovery",
}


class Difficulty(str, Enum):
    """Measurable difficulty bands (see ``argus.training.difficulty``)."""

    BEGINNER = "beginner"
    EASY = "easy"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class PositionType(str, Enum):
    """The exercise format (spec §6). Each is answerable from stored evidence."""

    FIND_BEST_MOVE = "find_best_move"
    FIND_TACTICAL_MOVE = "find_tactical_move"
    FIND_DEFENSE = "find_defense"
    #: Continue the engine's line for a few moves. Emitted only when the stored
    #: principal variation is long enough to grade move-by-move (see the
    #: generator): every move checked is a move Stockfish actually played in the
    #: stored PV, never a constructed or guessed continuation.
    CONTINUE_LINE = "continue_line"
    #: Whole-game formats: they need the *sequence* of a game's stored analyses,
    #: not one move. Emitted by ``argus.training.replay``, which reads the game's
    #: real continuation (WHAT_WENT_WRONG) and its stored best moves
    #: (RECONSTRUCTION). Both were declared in Phase 8 and refused emission until
    #: the replay generator existed, rather than being approximated.
    WHAT_WENT_WRONG = "what_went_wrong"
    CHOOSE_BETWEEN_MOVES = "choose_between_moves"
    RECONSTRUCTION = "reconstruction"


#: Types the per-move generator can produce from a single stored move analysis.
#: CONTINUE_LINE joined the set in methodology 8.1 once multi-ply grading against
#: the stored principal variation existed.
GENERATOR_POSITION_TYPES = (
    PositionType.FIND_BEST_MOVE,
    PositionType.FIND_TACTICAL_MOVE,
    PositionType.FIND_DEFENSE,
    PositionType.CHOOSE_BETWEEN_MOVES,
    PositionType.CONTINUE_LINE,
)

#: Types the replay generator produces from a whole game's stored analyses
#: (``argus.training.replay``). Methodology 8.2 added these: each one carries a
#: ``replay_context`` describing the real moves it was built from.
REPLAY_POSITION_TYPES = (
    PositionType.WHAT_WENT_WRONG,
    PositionType.RECONSTRUCTION,
)

#: Everything the training engine as a whole can emit.
ALL_POSITION_TYPES = GENERATOR_POSITION_TYPES + REPLAY_POSITION_TYPES


class TrainingState(str, Enum):
    """The SRS lifecycle of an exercise (spec §18)."""

    NEW = "new"
    LEARNING = "learning"
    REVIEW = "review"
    MASTERED = "mastered"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


#: The state machine. Every transition is explicit; anything not listed is
#: forbidden. "Mastered" requires a *streak* of correct answers (defined in
#: :mod:`argus.training.scheduler`), never a single success.
STATE_TRANSITIONS: dict[TrainingState, tuple[TrainingState, ...]] = {
    TrainingState.NEW: (TrainingState.LEARNING, TrainingState.FAILED),
    TrainingState.LEARNING: (
        TrainingState.LEARNING,
        TrainingState.REVIEW,
        TrainingState.NEEDS_REVIEW,
    ),
    TrainingState.REVIEW: (
        TrainingState.REVIEW,
        TrainingState.MASTERED,
        TrainingState.NEEDS_REVIEW,
    ),
    TrainingState.MASTERED: (TrainingState.MASTERED, TrainingState.NEEDS_REVIEW),
    TrainingState.NEEDS_REVIEW: (
        TrainingState.LEARNING,
        TrainingState.REVIEW,
        TrainingState.NEEDS_REVIEW,
    ),
    TrainingState.FAILED: (TrainingState.LEARNING, TrainingState.FAILED),
}


def can_transition(current: TrainingState, target: TrainingState) -> bool:
    return target in STATE_TRANSITIONS[current]


class TrainingPosition(BaseModel):
    """The package-level exercise record (the ORM mirror adds persistence)."""

    id: int | None = None
    player_id: int | None = None
    source_game_id: str | None = None
    source_ply: int | None = None
    source_position_id: int | None = None
    side_to_move: str
    fen: str
    source_fen_normalized: str = ""
    position_type: PositionType = PositionType.FIND_BEST_MOVE
    category: Category
    difficulty: Difficulty = Difficulty.EASY
    difficulty_factors: dict[str, Any] = Field(default_factory=dict)
    data_source: str = "personalized"
    source_reason: str = ""
    tags: list[str] = Field(default_factory=list)

    solution_uci: str
    solution_san: str
    acceptable_moves: dict[str, str] = Field(default_factory=dict)
    candidate_moves: list[dict[str, Any]] = Field(default_factory=list)
    principal_variation: list[str] = Field(default_factory=list)
    #: The engine's line *after* the solution (UCI), used by CONTINUE_LINE. It is
    #: the stored PV from index 1 on: the opponent's forced reply, then the
    #: solver's expected continuation, alternating. Empty for other types.
    continuation_line: list[str] = Field(default_factory=list)
    solution_eval_cp: int | None = None
    solution_eval_mate: int | None = None
    played_move_uci: str | None = None
    played_move_san: str | None = None
    played_eval_cp: int | None = None
    played_loss_cp: int | None = None

    #: Type-specific provenance for the whole-game formats. For
    #: WHAT_WENT_WRONG it holds the game's *real* continuation and evaluation
    #: trajectory; for RECONSTRUCTION it records how many solver moves the stored
    #: line supplies. Empty for every single-move type. Always cites the
    #: methodology version so a stored exercise can be traced to the rules that
    #: produced it.
    replay_context: dict[str, Any] = Field(default_factory=dict)

    engine: str = "stockfish"
    engine_version: str | None = None
    depth: int = 0
    analysis_version: str = "3.1"
    methodology_version: str = TRAINING_METHODOLOGY_VERSION

    state: TrainingState = TrainingState.NEW
    attempts: int = 0
    correct_attempts: int = 0
    streak: int = 0
    review_interval_days: float = 0.0
    next_review_at: datetime | None = None
    last_attempted_at: datetime | None = None
    created_at: datetime | None = None

    def normalized_fen(self) -> str:
        """The dedupe key: board, side to move, castling, en passant."""
        return " ".join(self.fen.split()[:4])

    @property
    def is_personalized(self) -> bool:
        return self.data_source == "personalized"

    @property
    def is_opponent_preparation(self) -> bool:
        return self.data_source == OPPONENT_PREPARATION_SOURCE

    @property
    def played_the_solution(self) -> bool:
        """True when the source move already was the engine's choice.

        Such a position is not a mistake and must never become a puzzle (spec §5
        generates from mistakes); the generator filters these out.
        """
        return bool(
            self.played_move_uci and self.played_move_uci == self.solution_uci
        )


__all__ = [
    "ALL_POSITION_TYPES",
    "CATEGORIES",
    "CATEGORY_LABELS",
    "DATA_SOURCES",
    "DIFFICULTIES",
    "OPPONENT_PREPARATION_SOURCE",
    "POSITION_TYPES",
    "REPLAY_POSITION_TYPES",
    "STATE_TRANSITIONS",
    "TRAINING_METHODOLOGY_VERSION",
    "Category",
    "Difficulty",
    "PositionType",
    "TrainingPosition",
    "TrainingState",
    "can_transition",
]

#: The source of an exercise. ``personalized`` is the player's own analysed
#: mistakes; ``general`` is a shared, player-agnostic set; and
#: ``opponent_preparation`` is a reply to a move an *opponent* demonstrably plays,
#: built from that opponent's stored games (see ``argus.training.opponent_prep``).
#: It is never labelled personalized: it is not the player's own mistake.
OPPONENT_PREPARATION_SOURCE = "opponent_preparation"

DATA_SOURCES = ("personalized", "general", OPPONENT_PREPARATION_SOURCE)

CATEGORIES = tuple(category.value for category in Category)
DIFFICULTIES = tuple(difficulty.value for difficulty in Difficulty)
POSITION_TYPES = tuple(position_type.value for position_type in PositionType)
