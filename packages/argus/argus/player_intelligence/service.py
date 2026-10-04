"""Structured player-intelligence services.

The same accessor pattern the Phase 4 intelligence layer uses: every question
an agent (or a route, or a script) may ask has a named method returning
structured data. The conversational agent itself is a later phase and is *not*
implemented here — this module only guarantees that when it arrives it can only
ever consume measured, evidenced values.

Method names match the tool surface the phase objective specifies, so wiring
them into the agent registry later is a mapping, not a redesign.
"""

from __future__ import annotations

from typing import Any

from argus.player_intelligence.features import PlayerFeatureSet, extract_features
from argus.player_intelligence.models import PlayerGameInput, PlayerProfile


class PlayerIntelligenceService:
    """Read-only accessors over one built player profile."""

    def __init__(self, profile: PlayerProfile, inputs: list[PlayerGameInput] | None = None) -> None:
        self._profile = profile
        self._inputs = inputs or []

    @property
    def profile(self) -> PlayerProfile:
        return self._profile

    def _dump(self, value: Any) -> Any:
        from pydantic import BaseModel

        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return value

    def get_player_profile(self) -> dict[str, Any]:
        """The full versioned profile (sections + insights + coverage)."""
        return self._dump(self._profile)

    def get_player_statistics(self) -> dict[str, Any]:
        """Record, move quality and the colour split."""
        return {
            "scope": "player",
            "profile_version": self._profile.profile_version,
            "methodology_version": self._profile.methodology_version,
            "analyzed_games": self._profile.analyzed_games,
            "coverage": self._profile.coverage.value,
            "games": self._dump(self._profile.games),
            "by_color": [self._dump(entry) for entry in self._profile.by_color],
            "time_controls": self._dump(self._profile.time_controls),
            "opponents": self._dump(self._profile.opponents),
        }

    def get_player_opening_profile(self) -> dict[str, Any]:
        return self._dump(self._profile.openings)

    def get_player_phase_statistics(self) -> dict[str, Any]:
        return self._dump(self._profile.phases)

    def get_player_tactical_profile(self) -> dict[str, Any]:
        return {
            "tactical": self._dump(self._profile.tactical),
            "king_safety": self._dump(self._profile.king_safety),
        }

    def get_player_positional_profile(self) -> dict[str, Any]:
        return {
            "positional": self._dump(self._profile.positional),
            "material": self._dump(self._profile.material),
        }

    def get_player_trends(self) -> dict[str, Any]:
        return {
            "trends": self._dump(self._profile.trends),
            "conversion": self._dump(self._profile.conversion),
            "recovery": self._dump(self._profile.recovery),
        }

    def get_player_insights(self) -> dict[str, Any]:
        """Every insight with its claim level, sample and evidence refs."""
        return {
            "player_id": self._profile.player_id,
            "coverage": self._profile.coverage.value,
            "sufficient_data": self._profile.sufficient_data,
            "insights": [self._dump(insight) for insight in self._profile.insights],
        }

    def get_player_evidence(self, insight_id: str | None = None) -> dict[str, Any]:
        """Evidence refs for one insight (or all of them)."""
        selected = [
            insight for insight in self._profile.insights
            if insight_id is None or insight.id == insight_id
        ]
        return {
            "player_id": self._profile.player_id,
            "insights": [
                {
                    "id": insight.id,
                    "claim_level": insight.claim_level.value,
                    "games": insight.games,
                    "occurrences": insight.occurrences,
                    "evidence": [self._dump(ref) for ref in insight.evidence],
                }
                for insight in selected
            ],
        }

    def get_player_features(self) -> PlayerFeatureSet:
        """The ML-ready feature set (user-specific; not training-eligible)."""
        return extract_features(self._profile, self._inputs, games_analyzed=self._profile.analyzed_games)

    def available_tools(self) -> dict[str, Any]:
        """The named tool surface a future AI agent will call."""
        return {
            "get_player_profile": self.get_player_profile,
            "get_player_statistics": self.get_player_statistics,
            "get_player_opening_profile": self.get_player_opening_profile,
            "get_player_phase_statistics": self.get_player_phase_statistics,
            "get_player_tactical_profile": self.get_player_tactical_profile,
            "get_player_positional_profile": self.get_player_positional_profile,
            "get_player_trends": self.get_player_trends,
            "get_player_insights": self.get_player_insights,
            "get_player_evidence": self.get_player_evidence,
            "get_player_features": self.get_player_features,
        }


__all__ = ["PlayerIntelligenceService"]
