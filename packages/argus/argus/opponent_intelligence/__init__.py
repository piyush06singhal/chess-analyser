"""Opponent Intelligence + Advanced Chess Analytics (Phase 9).

Turns the stored games of *any* tracked player into an evidence-gated answer to
one question:

    What can the available game data tell me about this opponent's chess
    tendencies, repertoire, recurring patterns, and preparation opportunities?

The pipeline mirrors Phase 5's player intelligence:

    stored games + stored move analysis → inputs → repertoire / responses /
    statistics → evidence-gated insights → opponent profile → preparation report

It is analytics, not psychology: every output is a count, a share, or a
centipawn measurement over games that were actually played, each carrying its
sample size and a claim level. When the data cannot support a statement the
claim level is ``insufficient`` and the UI says so.
"""

from argus.opponent_intelligence.models import (
    OPPONENT_METHODOLOGY_VERSION,
    OpponentEvidence,
    OpponentGame,
    OpponentGameInput,
    OpponentInsight,
    OpponentMoveInput,
    OpponentOpeningNode,
    OpponentOpeningProfile,
    OpponentPhaseStat,
    OpponentPhaseStatistics,
    OpponentPositionPattern,
    OpponentPositionResponse,
    OpponentPreparationReport,
    OpponentProfile,
    OpponentResponseOption,
    OpponentStatistics,
    OpponentTendency,
    PlayerIdentity,
)
from argus.opponent_intelligence.policy import (
    DEFAULT_POLICY,
    OPPONENT_COVERAGE_BANDS,
    ClaimLevel,
    Coverage,
    OpponentInsightPolicy,
    coverage_for,
)
from argus.opponent_intelligence.preparation import (
    build_insights,
    build_preparation_report,
    build_profile,
    summarize,
)
from argus.opponent_intelligence.repertoire import build_repertoire
from argus.opponent_intelligence.responses import find_position_patterns, find_position_responses
from argus.opponent_intelligence.service import OpponentIntelligenceService
from argus.opponent_intelligence.statistics import phase_statistics, tendencies

__all__ = [
    "DEFAULT_POLICY",
    "OPPONENT_COVERAGE_BANDS",
    "OPPONENT_METHODOLOGY_VERSION",
    "ClaimLevel",
    "Coverage",
    "OpponentEvidence",
    "OpponentGame",
    "OpponentGameInput",
    "OpponentInsight",
    "OpponentInsightPolicy",
    "OpponentIntelligenceService",
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
    "build_insights",
    "build_preparation_report",
    "build_profile",
    "build_repertoire",
    "coverage_for",
    "find_position_patterns",
    "find_position_responses",
    "phase_statistics",
    "summarize",
    "tendencies",
]
