"""Phase 8: the personalized chess training engine.

Training content comes from **verified chess data** — never from random
generation dressed up as personalization. Every exercise carries its origin
chain (game → position → analysis → mistake → exercise) and an engine-verified
solution, and the module refuses to build exercises it cannot justify.

Layout:

===========================  ==================================================
``models``                    TrainingPosition (pydantic), difficulty levels,
                              categories, position types, states
``eligibility``               which analysed moves may become exercises
``generator``                 the deterministic game → exercises pipeline
``acceptance``                MoveAcceptancePolicy: correct / near_best / incorrect
``difficulty``                measurable difficulty from position characteristics
``dedupe``                    exact-FEN duplicate detection
``scheduler``                 deterministic spaced repetition + state machine
``sessions``                  resumable training sessions and their kinds
``recommendations``           evidence-backed prioritized training opportunities
``progress``                  measured statistics with sample sizes
``hints``                     progressive hints derived from stored evidence
===========================  ==================================================

Design rules shared by every module:

* **Refusal is a result.** A position that cannot be justified reports why
  (``Rejected`` with its reason) instead of producing a weaker exercise.
* **Measurements carry sample sizes.** "Tactical accuracy: 78% across 42
  attempts" is a statement this package can make; "you are 78% tactical" is not.
* **No engine at read time.** Solutions are verified when an exercise is
  created; dashboards and sessions only read stored results (spec §44).
* **Privacy is structural.** Personalized exercises carry their owning player
  id; GENERAL exercises are the only shareable ones (spec §34).
"""

from __future__ import annotations

from argus.training.acceptance import AcceptanceOutcome, MoveAcceptancePolicy, default_policy
from argus.training.difficulty import DifficultyAssessment, DifficultyMethodologyVersion, assess_difficulty
from argus.training.eligibility import (
    EligibilityReport,
    TrainingEligibilityService,
    default_eligibility_service,
)
from argus.training.generator import (
    GenerationReport,
    TrainingPositionCandidate,
    TrainingPositionGenerator,
    generate_from_move_analysis,
)
from argus.training.hints import HINT_POLICY_VERSION, build_hints
from argus.training.models import (
    CATEGORIES,
    CATEGORY_LABELS,
    DIFFICULTIES,
    POSITION_TYPES,
    STATE_TRANSITIONS,
    TRAINING_METHODOLOGY_VERSION,
    TrainingPosition,
    TrainingState,
    can_transition,
)
from argus.training.progress import ProgressReport, compute_progress
from argus.training.recommendations import (
    RecommendationEngine,
    TrainingOpportunity,
    recommend,
)
from argus.training.scheduler import (
    DEFAULT_SCHEDULE,
    INITIAL_INTERVAL_DAYS,
    MultiplierSchedule,
    SchedulerDecision,
    apply_attempt,
    due_positions,
)
from argus.training.sessions import SESSION_KINDS, SessionPlan, plan_session

__all__ = [
    "DEFAULT_SCHEDULE",
    "CATEGORIES",
    "CATEGORY_LABELS",
    "DIFFICULTIES",
    "DifficultyAssessment",
    "DifficultyMethodologyVersion",
    "assess_difficulty",
    "AcceptanceOutcome",
    "EligibilityReport",
    "GenerationReport",
    "HINT_POLICY_VERSION",
    "INITIAL_INTERVAL_DAYS",
    "MoveAcceptancePolicy",
    "MultiplierSchedule",
    "POSITION_TYPES",
    "ProgressReport",
    "RecommendationEngine",
    "SESSION_KINDS",
    "STATE_TRANSITIONS",
    "TRAINING_METHODOLOGY_VERSION",
    "SchedulerDecision",
    "TrainingEligibilityService",
    "TrainingOpportunity",
    "TrainingPosition",
    "TrainingPositionCandidate",
    "TrainingPositionGenerator",
    "TrainingState",
    "SessionPlan",
    "apply_attempt",
    "build_hints",
    "compute_progress",
    "default_eligibility_service",
    "default_policy",
    "due_positions",
    "generate_from_move_analysis",
    "plan_session",
    "recommend",
    "can_transition",
]
