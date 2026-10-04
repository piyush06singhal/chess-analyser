"""Opponent Intelligence service (Phase 9) — the package's public entry point.

The service is a thin, deterministic orchestrator over the four aggregators.
It takes already-assembled :class:`OpponentGameInput` rows (the API layer owns
reading the database) and returns derived documents. It never runs the engine
and never touches a session, so the whole phase is testable with fixtures.
"""

from __future__ import annotations

from argus.opponent_intelligence.models import (
    OpponentGameInput,
    OpponentOpeningProfile,
    OpponentPhaseStatistics,
    OpponentPositionResponse,
    OpponentPreparationReport,
    OpponentProfile,
    OpponentTendency,
    PlayerIdentity,
)
from argus.opponent_intelligence.policy import (
    DEFAULT_POLICY,
    Coverage,
    OpponentInsightPolicy,
    coverage_for,
)
from argus.opponent_intelligence.preparation import (
    build_insights,
    build_preparation_report,
    build_profile,
)
from argus.opponent_intelligence.repertoire import build_repertoire
from argus.opponent_intelligence.responses import find_position_patterns, find_position_responses
from argus.opponent_intelligence.statistics import phase_statistics, tendencies


class OpponentIntelligenceService:
    """Assemble opponent documents from stored game evidence."""

    def __init__(self, policy: OpponentInsightPolicy | None = None) -> None:
        self.policy = policy or DEFAULT_POLICY

    def coverage(self, games: list[OpponentGameInput]) -> Coverage:
        analyzed = sum(1 for game in games if game.analyzed)
        return coverage_for(analyzed)

    def profile(self, games: list[OpponentGameInput], identity: PlayerIdentity) -> OpponentProfile:
        return build_profile(games, identity, policy=self.policy)

    def preparation_report(
        self,
        games: list[OpponentGameInput],
        identity: PlayerIdentity,
        *,
        color_to_prepare: str | None = None,
    ) -> OpponentPreparationReport:
        return build_preparation_report(
            games, identity, policy=self.policy, color_to_prepare=color_to_prepare
        )

    def insights(self, profile: OpponentProfile):
        return build_insights(profile, self.policy)

    def repertoire(self, games: list[OpponentGameInput], color: str) -> OpponentOpeningProfile:
        return build_repertoire(
            games, color=color, policy=self.policy, coverage=self.coverage(games)
        )

    def responses(self, games: list[OpponentGameInput], fen: str) -> OpponentPositionResponse:
        return find_position_responses(games, fen, policy=self.policy)

    def position_patterns(self, games: list[OpponentGameInput]):
        return find_position_patterns(games, policy=self.policy)

    def phase_statistics(
        self, games: list[OpponentGameInput], *, color: str | None = None
    ) -> OpponentPhaseStatistics:
        return phase_statistics(games, color=color, policy=self.policy)

    def tendencies(self, games: list[OpponentGameInput]) -> list[OpponentTendency]:
        phases = phase_statistics(games, policy=self.policy)
        return tendencies(games, policy=self.policy, phase_stats=phases)


__all__ = ["OpponentIntelligenceService"]
