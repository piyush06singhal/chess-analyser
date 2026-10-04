"""Phase 10 decision intelligence: counterfactuals, comparison and scenarios.

Caissa answers "what could have happened if I had played differently?" without ever
inventing an answer. The package is built on three rules:

**Stockfish is the calculation.** Every evaluation, every principal variation and
every continuation ply in a branch is a real engine result. Nothing here
estimates, interpolates or extrapolates a chess score.

**Comparison keeps two axes apart.** An engine difference is a search result; a
structural difference is a board fact. They are reported separately, because
merging them is how an invented relationship creeps into a product.

**Refusal is a result.** An illegal move, a missing engine or an insufficient
line is returned as a status with its reason — never replaced by a plausible
number.

The module layout:

``policy``
    versions, the closed set of scenario types, and the resource limits.
``models``
    the structured shapes: facts, engine metrics, comparisons, branches, scenarios.
``positions``
    board facts and the position-comparison service (engine axis vs board axis).
``candidates``
    candidate move comparison — one search, several moves, measured consequences.
``counterfactual``
    the branch builder: actual line vs alternative line, ply by ply.
``whatif``
    "why not this move?" and "what if I had played ...?" as structured evidence.
``explorer``
    the engine-free turning-point explorer over a stored analysis.
``service``
    the single entry point: caching, limits, refusals and prediction attachment.
"""

from argus.scenarios.candidates import CandidateMoveComparison, resolve_move
from argus.scenarios.counterfactual import CounterfactualAnalyzer
from argus.scenarios.explorer import explore
from argus.scenarios.models import (
    CandidateAssessment,
    CandidateComparison,
    ContinuationPly,
    EngineConfig,
    EngineMetrics,
    EvidenceRef,
    ExplanationBundle,
    FeatureDifference,
    PositionComparison,
    PositionFacts,
    ScenarioBranch,
    ScenarioOutcome,
    ScenarioRecord,
    TurningPoint,
    TurningPointExplorer,
)
from argus.scenarios.policy import (
    DECISION_METHODOLOGY_VERSION,
    MAX_CANDIDATE_MOVES,
    MAX_CONTINUATION_PLIES,
    ComparisonDomain,
    MoveQuality,
    ScenarioType,
    classify_move_quality,
)
from argus.scenarios.positions import (
    PositionComparisonService,
    position_facts,
    structural_differences,
)
from argus.scenarios.service import (
    ScenarioResultCache,
    ScenarioService,
    scenario_payload,
)
from argus.scenarios.whatif import (
    explanation_from_branch,
    legal_move_refusal,
    what_if_analysis,
    why_not_analysis,
)

__all__ = [
    "CandidateAssessment",
    "CandidateComparison",
    "CandidateMoveComparison",
    "ComparisonDomain",
    "ContinuationPly",
    "CounterfactualAnalyzer",
    "DECISION_METHODOLOGY_VERSION",
    "EngineConfig",
    "EngineMetrics",
    "EvidenceRef",
    "ExplanationBundle",
    "FeatureDifference",
    "MAX_CANDIDATE_MOVES",
    "MAX_CONTINUATION_PLIES",
    "MoveQuality",
    "PositionComparison",
    "PositionComparisonService",
    "PositionFacts",
    "ScenarioBranch",
    "ScenarioOutcome",
    "ScenarioRecord",
    "ScenarioResultCache",
    "ScenarioService",
    "ScenarioType",
    "TurningPoint",
    "TurningPointExplorer",
    "classify_move_quality",
    "explanation_from_branch",
    "explore",
    "legal_move_refusal",
    "position_facts",
    "resolve_move",
    "scenario_payload",
    "structural_differences",
    "what_if_analysis",
    "why_not_analysis",
]
