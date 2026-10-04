"""Prediction task definitions — targets stated before models are attempted.

The rule this module enforces is simple: **a task is defined before it is
modelled**, not after. Each definition names the unit of prediction, the exact
label and where its ground truth comes from, the inputs and their availability,
the data the task needs before it may be attempted at all, the metrics that must
be reported, the leakage risks specific to it, and its production status.

Five candidate tasks are declared, and they are *not* equally plausible. Their
``production_status`` says so honestly: everything starts ``EXPERIMENTAL``, and
``player_performance`` is explicitly marked as not attempted, because the
corpora available here have nowhere near enough per-player history.

A task whose declared data requirements are not met is not "a weak result" — it
is a task that must not be reported at all. :meth:`PredictionTaskDefinition.readiness`
is what production gating consults.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from argus.ml.models import ModelStatus, ProblemType

#: Bumped when a task's definition, label or requirements change. A result is
#: only interpretable against the definition version that produced it.
TASK_VERSION = "6.0"


class DataRequirement(BaseModel):
    """What a task needs before it may be attempted or reported at all."""

    min_games: int = 0
    min_train_rows: int = 0
    min_validation_rows: int = 0
    min_test_rows: int = 0
    min_players: int = 0
    min_distinct_openings: int = 0
    #: Every class must hold at least this share of the data.
    min_class_share: float = 0.05
    requires_engine: bool = False
    requires_ratings: bool = False
    requires_per_player_history: int = 0
    rationale: str = ""


class PredictionTaskDefinition(BaseModel):
    """One prediction problem, fully specified."""

    task_name: str
    description: str
    problem_type: ProblemType = ProblemType.CLASSIFICATION

    target: str
    target_values: list[str] = Field(default_factory=list)
    unit_of_prediction: str

    input_features: list[str] = Field(default_factory=list)
    #: Features the task would *like* but that are not available in this phase.
    unavailable_features: list[str] = Field(default_factory=list)

    label_definition: str
    label_source: str
    label_version: str = ""

    data_requirements: DataRequirement = Field(default_factory=DataRequirement)
    evaluation_metrics: list[str] = Field(default_factory=list)
    required_metrics: list[str] = Field(default_factory=list)
    leakage_risks: list[str] = Field(default_factory=list)
    baseline_models: list[str] = Field(default_factory=list)

    production_status: ModelStatus = ModelStatus.EXPERIMENTAL
    recommended_split: str = ""
    hypothesis: str = ""
    limitations: list[str] = Field(default_factory=list)
    task_version: str = TASK_VERSION

    def missing_requirements(
        self,
        *,
        games: int = 0,
        train_rows: int = 0,
        validation_rows: int = 0,
        test_rows: int = 0,
        players: int = 0,
        class_shares: dict[str, float] | None = None,
        has_engine_labels: bool = False,
        has_ratings: bool = False,
        per_player_games: int = 0,
    ) -> list[str]:
        """Which declared requirements this data does not meet.

        Returned as human-readable statements rather than a boolean, because the
        reason a task cannot be reported is the useful part.
        """
        requirement = self.data_requirements
        missing: list[str] = []
        for label, actual, needed in (
            ("games", games, requirement.min_games),
            ("train rows", train_rows, requirement.min_train_rows),
            ("validation rows", validation_rows, requirement.min_validation_rows),
            ("test rows", test_rows, requirement.min_test_rows),
            ("players", players, requirement.min_players),
        ):
            if needed and actual < needed:
                missing.append(f"{label}: {actual} available, {needed} required")
        if requirement.requires_engine and not has_engine_labels:
            missing.append("engine-labelled positions are required and none are available")
        if requirement.requires_ratings and not has_ratings:
            missing.append("ratings are required and the dataset carries none")
        if requirement.requires_per_player_history and per_player_games < requirement.requires_per_player_history:
            missing.append(
                f"per-player history: {per_player_games} games per player available, "
                f"{requirement.requires_per_player_history} required"
            )
        if class_shares is not None and requirement.min_class_share:
            for value, share in sorted(class_shares.items()):
                if 0 < share < requirement.min_class_share:
                    missing.append(
                        f"class '{value}' holds {share:.1%} of the data, below the "
                        f"{requirement.min_class_share:.0%} floor"
                    )
        return missing

    def readiness(self, **data: object) -> dict[str, object]:
        """Whether the task may be attempted/reported on this data."""
        missing = self.missing_requirements(**data)  # type: ignore[arg-type]
        return {
            "task": self.task_name,
            "production_status": self.production_status.value,
            "attemptable": not missing,
            "missing_requirements": missing,
        }


# --- TASK A ------------------------------------------------------------------

GAME_OUTCOME = PredictionTaskDefinition(
    task_name="game_outcome",
    description=(
        "Predict the result of a game (White win / draw / Black win) from "
        "information that exists before the first move."
    ),
    target="Game result from White's perspective",
    target_values=["white_win", "draw", "black_win"],
    unit_of_prediction="one game",
    input_features=[
        "rating_diff",
        "rating_mean",
        "time_control_initial_seconds",
        "time_control_increment_seconds",
        "time_class_index",
    ],
    unavailable_features=[
        "opening_choice (known only once the game has begun in most corpora)",
        "per-player historical performance at scale (needs a player history corpus)",
    ],
    label_definition="The PGN Result header: 1-0 → white_win, 1/2-1/2 → draw, 0-1 → black_win.",
    label_source="The recorded result; never inferred from the moves",
    label_version="6.0",
    data_requirements=DataRequirement(
        min_games=20_000,
        min_train_rows=14_000,
        min_validation_rows=3_000,
        min_test_rows=3_000,
        min_players=500,
        min_class_share=0.05,
        requires_ratings=True,
        rationale=(
            "Chess outcomes are driven mainly by rating difference, which is a weak "
            "signal over a narrow rating range. A corpus smaller than tens of "
            "thousands of rated games cannot separate a real effect from noise, so "
            "these floors are set where a measurable lift over the rating baseline "
            "becomes meaningful."
        ),
    ),
    evaluation_metrics=[
        "accuracy",
        "balanced_accuracy",
        "macro_f1",
        "log_loss",
        "brier_score",
        "confusion_matrix",
        "roc_auc_ovr_draw_vs_rest",
    ],
    required_metrics=["balanced_accuracy", "macro_f1", "log_loss", "brier_score"],
    leakage_risks=[
        "using the final evaluation or any later position to predict the result",
        "using the opening actually played (not knowable before the first move)",
        "using ply_count (a consequence of the game, not a precursor)",
        "splitting positions rather than games, which memorises the game",
        "a random rather than temporal split, which predicts the past with hindsight",
    ],
    baseline_models=[
        "majority_class",
        "rating_based",
        "logistic_regression",
        "tree_ensemble",
    ],
    production_status=ModelStatus.EXPERIMENTAL,
    recommended_split="temporal",
    hypothesis=(
        "Rating difference carries real signal; open question whether Caissa "
        "features add lift over the rating-only baseline rather than restating it."
    ),
    limitations=[
        "Draws are the hardest class and the least frequent in decisive corpora.",
        "No rating normalisation across platforms: the rating scale is source-specific.",
        "A pre-game prediction with only ratings available is close to a rating lookup.",
    ],
)

# --- TASK B ------------------------------------------------------------------

POSITION_OUTCOME = PredictionTaskDefinition(
    task_name="position_outcome",
    description=(
        "Predict the final result of the game a position belongs to, from the "
        "position and the pre-game context."
    ),
    target="Final result of the containing game, from White's perspective",
    target_values=["white_win", "draw", "black_win"],
    unit_of_prediction="one position",
    input_features=[
        "material_balance",
        "mobility_diff",
        "king_safety_diff",
        "isolated_pawns_diff",
        "doubled_pawns_diff",
        "passed_pawns_diff",
        "center_occupied_diff",
        "center_attacked_diff",
        "undeveloped_diff",
        "hanging_diff",
        "total_pieces",
        "rating_diff",
    ],
    label_definition=(
        "The final result of the game containing the position — a game-level label "
        "attached to a position. It answers 'how did this game end', not 'who stands "
        "better'; the latter is an evaluation question, not a prediction question."
    ),
    label_source="The PGN Result header of the containing game",
    label_version="6.0",
    data_requirements=DataRequirement(
        min_games=2_000,
        min_train_rows=50_000,
        min_validation_rows=8_000,
        min_test_rows=8_000,
        min_players=200,
        min_class_share=0.05,
        rationale=(
            "Positions are highly correlated within a game, so the effective sample "
            "size is the number of games, not the number of rows. Floors are set on "
            "games first and rows second."
        ),
    ),
    evaluation_metrics=[
        "accuracy",
        "balanced_accuracy",
        "macro_f1",
        "log_loss",
        "brier_score",
        "confusion_matrix",
    ],
    required_metrics=["balanced_accuracy", "macro_f1", "log_loss"],
    leakage_risks=[
        "splitting positions instead of games — the single most damaging leak here",
        "using the engine evaluation of the position (which is a much stronger "
        "predictor than board features, and would make the task an evaluation lookup)",
        "using total plies remaining or the move number as a proxy for the outcome",
    ],
    baseline_models=["majority_class", "rating_based", "logistic_regression", "tree_ensemble"],
    production_status=ModelStatus.EXPERIMENTAL,
    recommended_split="random_game",
    hypothesis=(
        "Board features retained while the game is still open carry a little signal "
        "about the eventual result; the interesting question is whether that survives "
        "a game-group split."
    ),
    limitations=[
        "Board features at ply N are far weaker than an engine evaluation at ply N.",
        "The label is constant within a game, so per-position metrics overstate "
        "independence and must be read alongside a per-game view.",
    ],
)

# --- TASK C ------------------------------------------------------------------

POSITION_DIFFICULTY = PredictionTaskDefinition(
    task_name="position_difficulty",
    description=(
        "Classify whether a position is objectively difficult to play (tactical, "
        "high evaluation volatility, or error-prone) before committing to a move."
    ),
    target="Whether the position is difficult or ordinary",
    target_values=["difficult", "ordinary"],
    unit_of_prediction="one position",
    input_features=[
        "material_balance",
        "mobility_diff",
        "king_safety_diff",
        "hanging_diff",
        "checkers",
        "total_pieces",
        "undeveloped_diff",
    ],
    label_definition=(
        "difficult when the evaluation swings by at least the declared centipawn "
        "threshold within the declared lookahead, or the position is tactical; else "
        "ordinary."
    ),
    label_source="Engine evaluations at a recorded depth and MultiPV setting",
    label_version="6.0",
    data_requirements=DataRequirement(
        min_games=1_000,
        min_train_rows=20_000,
        min_validation_rows=4_000,
        min_test_rows=4_000,
        min_class_share=0.10,
        requires_engine=True,
        rationale=(
            "The label is engine-derived, so the corpus must actually have engine "
            "evaluations at a recorded depth. Without them there is no label at all."
        ),
    ),
    evaluation_metrics=[
        "accuracy",
        "balanced_accuracy",
        "precision",
        "recall",
        "f1",
        "roc_auc",
        "pr_auc",
        "log_loss",
        "calibration",
    ],
    required_metrics=["balanced_accuracy", "pr_auc", "log_loss", "ece"],
    leakage_risks=[
        "labelling from a heuristic when no engine evaluation exists",
        "using the evaluation itself (rather than board features) as an input",
        "mixing engine configurations (depth/MultiPV) without recording the difference",
    ],
    baseline_models=["majority_class", "logistic_regression", "tree_ensemble"],
    production_status=ModelStatus.EXPERIMENTAL,
    recommended_split="random_game",
    hypothesis=(
        "Board characteristics alone may predict where the evaluation is volatile — "
        "which would be useful for choosing where to spend analysis effort."
    ),
    limitations=[
        "The label depends on the engine configuration, so it is only comparable "
        "within one configuration.",
        "'Difficult' is not the same as 'blundered' — this task is about the position, "
        "not about the player.",
    ],
)

# --- TASK D ------------------------------------------------------------------

PLAYER_PERFORMANCE = PredictionTaskDefinition(
    task_name="player_performance",
    description=(
        "Estimate a player's future performance metric (e.g. average centipawn loss) "
        "from their history."
    ),
    problem_type=ProblemType.REGRESSION,
    target="Mean centipawn loss over a future window of games",
    target_values=["future_average_cpl"],
    unit_of_prediction="one player-window",
    input_features=[
        "player_average_cpl",
        "player_average_accuracy",
        "player_blunders_per_game",
        "player_mistakes_per_game",
        "player_tactical_error_rate",
        "player_opening_diversity",
        "player_games_analyzed",
    ],
    label_definition=(
        "The player's mean centipawn loss over the declared future window, measured "
        "from stored analyses."
    ),
    label_source="Stored per-game Caissa analysis for that player",
    label_version="6.0",
    data_requirements=DataRequirement(
        min_games=5_000,
        min_train_rows=1_000,
        min_test_rows=200,
        min_players=100,
        requires_per_player_history=20,
        rationale=(
            "A per-player temporal prediction needs many consecutive games per player "
            "and a following window to predict into. The corpora available in this "
            "phase have a handful of games per player, which is not a dataset."
        ),
    ),
    evaluation_metrics=["mae", "rmse", "r2", "prediction_interval_coverage"],
    required_metrics=["mae", "rmse"],
    leakage_risks=[
        "any non-temporal split: predicting a past game from a future one",
        "computing the player's historical statistics over the whole corpus "
        "including the prediction window",
        "using the games being predicted to fit the player's baseline",
    ],
    baseline_models=["majority_class", "logistic_regression"],
    production_status=ModelStatus.EXPERIMENTAL,
    recommended_split="temporal",
    hypothesis=(
        "Performance is persistent enough over a following window to beat a "
        "player-mean baseline — plausible, but untestable at this corpus size."
    ),
    limitations=[
        "Not attempted in this phase: the available data is orders of magnitude short.",
        "Player performance drifts (form, tilt, time control), so a persistent-model "
        "assumption may simply be wrong over long windows.",
    ],
)

# --- TASK E ------------------------------------------------------------------

MOVE_ERROR_RISK = PredictionTaskDefinition(
    task_name="move_error_risk",
    description=(
        "Estimate whether a position is associated with elevated risk of a mistake by "
        "the player to move — the basis for 'where should I have thought harder?'."
    ),
    target="Whether the played move in this position was classified as an error",
    target_values=["error", "acceptable"],
    unit_of_prediction="one move (position + side to move)",
    input_features=[
        "material_balance",
        "mobility_diff",
        "king_safety_diff",
        "hanging_diff",
        "checkers",
        "total_pieces",
        "center_occupied_diff",
        "undeveloped_diff",
        "player_average_cpl",
        "player_blunders_per_game",
        "player_games_analyzed",
    ],
    label_definition=(
        "Caissa move classification of the played move: blunder/mistake/inaccurate → "
        "error, otherwise acceptable. Reused from the analysis layer so there is one "
        "definition of 'error' in the product."
    ),
    label_source="Caissa move classification (engine-derived)",
    label_version="6.0",
    data_requirements=DataRequirement(
        min_games=1_000,
        min_train_rows=30_000,
        min_validation_rows=5_000,
        min_test_rows=5_000,
        min_players=100,
        min_class_share=0.05,
        requires_engine=True,
        requires_per_player_history=10,
        rationale=(
            "The label is engine-derived and player-dependent: without enough games "
            "per player, a model would learn 'who is playing' rather than 'which "
            "positions are dangerous'."
        ),
    ),
    evaluation_metrics=[
        "accuracy",
        "balanced_accuracy",
        "precision",
        "recall",
        "f1",
        "roc_auc",
        "pr_auc",
        "log_loss",
        "calibration",
    ],
    required_metrics=["balanced_accuracy", "pr_auc", "log_loss", "ece"],
    leakage_risks=[
        "player leakage: a random split lets the model memorise a player's error rate "
        "instead of the position's difficulty — hence a player-holdout split is required",
        "using the engine evaluation of the position (the label's own input)",
        "using the centipawn loss of the played move anywhere in the inputs",
    ],
    baseline_models=["majority_class", "logistic_regression", "tree_ensemble"],
    production_status=ModelStatus.EXPERIMENTAL,
    recommended_split="player_holdout",
    hypothesis=(
        "Some positions are intrinsically error-prone for most players, and that "
        "component is separable from the player-specific component."
    ),
    limitations=[
        "Error rates are player-specific, so an aggregate model can look good while "
        "being useless for any individual user.",
        "Requires per-player history for the player-context features to be honest.",
    ],
)


TASK_REGISTRY: dict[str, PredictionTaskDefinition] = {
    task.task_name: task
    for task in (
        GAME_OUTCOME,
        POSITION_OUTCOME,
        POSITION_DIFFICULTY,
        PLAYER_PERFORMANCE,
        MOVE_ERROR_RISK,
    )
}


def get_task(task_name: str) -> PredictionTaskDefinition:
    """Fetch a task definition.

    Raises:
        KeyError: when the task is not declared — an undeclared task may not be
            modelled, because it has no target definition to be measured against.
    """
    return TASK_REGISTRY[task_name]


def task_names() -> list[str]:
    return sorted(TASK_REGISTRY)


def production_ready_tasks() -> list[str]:
    """Tasks whose status is PRODUCTION (none, until a model passes gating)."""
    return sorted(
        name
        for name, task in TASK_REGISTRY.items()
        if task.production_status is ModelStatus.PRODUCTION
    )


__all__ = [
    "GAME_OUTCOME",
    "MOVE_ERROR_RISK",
    "PLAYER_PERFORMANCE",
    "POSITION_DIFFICULTY",
    "POSITION_OUTCOME",
    "TASK_REGISTRY",
    "TASK_VERSION",
    "DataRequirement",
    "PredictionTaskDefinition",
    "get_task",
    "production_ready_tasks",
    "task_names",
]
