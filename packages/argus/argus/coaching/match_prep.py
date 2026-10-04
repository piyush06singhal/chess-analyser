"""Match preparation: a persisted, evidence-gated brief for one opponent.

Phase 11 §14–§17 ask for a Match Preparation object, an ``Caissa MATCH BRIEF``
report, and preparation scenarios. Opponent Intelligence (Phase 9) already
computes the *inputs* — repertoire, tendencies, position responses, preparation
opportunities. This module composes them into the object the workspace persists
and renders, and it keeps three disciplines from the earlier phases:

* **Observed play is not predicted play.** Everything here is what the opponent
  *has done* in stored games; nothing is a forecast. A section that cannot clear
  its sample gate is reported as unavailable with the gate it missed.
* **Preparation is scoped to the preparing player.** The brief is owned by the
  player preparing; it never confuses the opponent's habits with the user's.
* **Every line resolves to a real game.** Repertoire lines and danger zones carry
  the games and plies they came from, so a prep item can be opened and checked.

The object is a pure model; persistence lives in the repository.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

MATCH_PREP_METHODOLOGY_VERSION = "11.0"

#: A section is only promoted above "observation" when its sample clears the
#: matching gate. These mirror the Phase 9 gates so the two layers agree.
SAMPLE_GATES: dict[str, int] = {
    "repertoire": 4,
    "tendency": 3,
    "structure": 5,
    "phase": 4,
}


class PrepSectionStatus(str, Enum):
    AVAILABLE = "available"
    INSUFFICIENT = "insufficient"
    MISSING = "missing"


class PrepScenario(BaseModel):
    """One concrete thing to prepare, with the evidence that justifies it."""

    key: str
    kind: str = Field(description="opening_line | position_response | tendency | phase")
    title: str
    statement: str
    as_white: bool | None = None
    fen: str | None = None
    line_san: list[str] = Field(default_factory=list)
    practice_href: str | None = None
    sample_size: int = 0
    evidence: list[dict] = Field(default_factory=list)
    status: PrepSectionStatus = PrepSectionStatus.AVAILABLE
    reason: str | None = None


class PrepSection(BaseModel):
    key: str
    title: str
    status: PrepSectionStatus = PrepSectionStatus.AVAILABLE
    gate: int | None = None
    sample_size: int = 0
    items: list[PrepScenario] = Field(default_factory=list)
    reason: str | None = None


class MatchPreparation(BaseModel):
    """The object the workspace stores: who, when, and what to prepare."""

    id: int | None = None
    preparing_player_id: int
    opponent_id: int
    opponent_name: str = ""
    as_white: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    opponent_games: int = 0
    analysed_games: int = 0
    coverage: str = "insufficient"
    sections: list[PrepSection] = Field(default_factory=list)
    scenarios: list[PrepScenario] = Field(default_factory=list)
    opponent_profile_version: str | None = None
    methodology_version: str = MATCH_PREP_METHODOLOGY_VERSION

    @property
    def scenario_count(self) -> int:
        return len(self.scenarios)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "preparing_player_id": self.preparing_player_id,
            "opponent_id": self.opponent_id,
            "opponent_name": self.opponent_name,
            "as_white": self.as_white,
            "opponent_games": self.opponent_games,
            "analysed_games": self.analysed_games,
            "coverage": self.coverage,
            "sections": [section.model_dump(mode="json") for section in self.sections],
            "scenarios": [scenario.model_dump(mode="json") for scenario in self.scenarios],
            "scenario_count": self.scenario_count,
            "opponent_profile_version": self.opponent_profile_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "methodology_version": self.methodology_version,
        }


def _gate(name: str) -> int:
    return SAMPLE_GATES[name]


# ---------------------------------------------------------------------------
# section builders — each takes an already-computed opponent payload
# ---------------------------------------------------------------------------


def _opening_lines(
    repertoire: dict | None, *, as_white: bool | None
) -> PrepSection:
    gate = _gate("repertoire")
    if not repertoire:
        return PrepSection(
            key="opening_lines",
            title="Likely opening lines",
            status=PrepSectionStatus.MISSING,
            gate=gate,
            reason="No stored games are available for this opponent.",
        )
    colour = "white" if as_white else "black" if as_white is False else None
    lines: list[PrepScenario] = []
    branches = repertoire.get("by_colour") or repertoire.get("branches") or []
    if isinstance(branches, dict):
        branches = [dict(branches, colour=key) for key in (colour or branches)]
    for branch in branches if isinstance(branches, list) else []:
        if not isinstance(branch, dict):
            continue
        branch_colour = str(branch.get("colour") or branch.get("color") or "").lower()
        if colour and branch_colour and branch_colour != colour:
            continue
        line = branch.get("line_san") or branch.get("moves") or branch.get("line") or []
        games = int(branch.get("games") or branch.get("sample_size") or 0)
        if games < gate:
            continue
        lines.append(
            PrepScenario(
                key=f"opening:{branch.get('opening') or branch.get('name') or len(lines)}",
                kind="opening_line",
                title=str(branch.get("opening") or branch.get("name") or "Line"),
                statement=(
                    f"They played this line in {games} stored game(s)"
                    + (
                        f", scoring {branch.get('score')}"
                        if branch.get("score") is not None
                        else ""
                    )
                    + "."
                ),
                as_white=branch_colour == "white" if branch_colour else as_white,
                line_san=list(line) if isinstance(line, (list, tuple)) else [],
                sample_size=games,
                evidence=list(branch.get("evidence") or []),
            )
        )
    lines.sort(key=lambda item: (-item.sample_size, item.key))
    if not lines:
        return PrepSection(
            key="opening_lines",
            title="Likely opening lines",
            status=PrepSectionStatus.INSUFFICIENT,
            gate=gate,
            sample_size=0,
            reason=(
                f"No opening line has been played at least {gate} times, so nothing is "
                f"claimed about their repertoire."
            ),
        )
    return PrepSection(
        key="opening_lines",
        title="Likely opening lines",
        status=PrepSectionStatus.AVAILABLE,
        gate=gate,
        sample_size=max(item.sample_size for item in lines),
        items=lines,
    )


def _tendencies(profile: dict | None) -> PrepSection:
    gate = _gate("tendency")
    insights = (profile or {}).get("insights") or []
    scenarios: list[PrepScenario] = []
    for insight in insights:
        if not isinstance(insight, dict):
            continue
        occurrences = int(insight.get("occurrences") or insight.get("sample_size") or 0)
        if occurrences < gate:
            continue
        scenarios.append(
            PrepScenario(
                key=f"tendency:{insight.get('id') or len(scenarios)}",
                kind="tendency",
                title=str(insight.get("title") or "Tendency"),
                statement=str(insight.get("statement") or ""),
                sample_size=occurrences,
                evidence=list(insight.get("evidence") or []),
            )
        )
    if not scenarios:
        return PrepSection(
            key="tendencies",
            title="Measured tendencies",
            status=PrepSectionStatus.INSUFFICIENT,
            gate=gate,
            reason=(
                f"No tendency has at least {gate} observed occurrences, so nothing is "
                f"claimed about their habits."
            ),
        )
    scenarios.sort(key=lambda item: -item.sample_size)
    return PrepSection(
        key="tendencies",
        title="Measured tendencies",
        status=PrepSectionStatus.AVAILABLE,
        gate=gate,
        sample_size=max(item.sample_size for item in scenarios),
        items=scenarios,
    )


def _danger_zones(profile: dict | None) -> PrepSection:
    """Where the opponent repeatedly lands — positions worth having a plan for."""
    gate = _gate("structure")
    responses = (profile or {}).get("recurring_positions") or (profile or {}).get(
        "position_responses"
    ) or []
    scenarios: list[PrepScenario] = []
    for response in responses if isinstance(responses, list) else []:
        if not isinstance(response, dict):
            continue
        count = int(response.get("games") or response.get("occurrences") or response.get("count") or 0)
        if count < gate:
            continue
        scenarios.append(
            PrepScenario(
                key=f"position:{response.get('fen') or len(scenarios)}",
                kind="position_response",
                title=str(response.get("label") or "Recurring position"),
                statement=(
                    f"This position recurred in {count} stored game(s)"
                    + (
                        f"; they scored {response.get('score')}"
                        if response.get("score") is not None
                        else ""
                    )
                    + "."
                ),
                fen=response.get("fen"),
                sample_size=count,
                evidence=list(response.get("evidence") or []),
            )
        )
    if not scenarios:
        return PrepSection(
            key="danger_zones",
            title="Recurring positions",
            status=PrepSectionStatus.INSUFFICIENT,
            gate=gate,
            reason=(
                f"No position has recurred at least {gate} times, so no danger zone is "
                f"named."
            ),
        )
    scenarios.sort(key=lambda item: -item.sample_size)
    return PrepSection(
        key="danger_zones",
        title="Recurring positions",
        status=PrepSectionStatus.AVAILABLE,
        gate=gate,
        sample_size=max(item.sample_size for item in scenarios),
        items=scenarios,
    )


def _phase_weakness(profile: dict | None) -> PrepSection:
    """Where they perform worst, from stored phase results — a measurement."""
    gate = _gate("phase")
    phases = (profile or {}).get("phase_performance") or (profile or {}).get("phases") or {}
    if not isinstance(phases, dict):
        return PrepSection(
            key="phase_weakness",
            title="Phase performance",
            status=PrepSectionStatus.MISSING,
            gate=gate,
            reason="No phase performance is stored for this opponent.",
        )
    scored: list[tuple[float, str, dict]] = []
    for name, entry in phases.items():
        if not isinstance(entry, dict):
            continue
        games = int(entry.get("games") or entry.get("sample_size") or 0)
        score = entry.get("score") if isinstance(entry.get("score"), (int, float)) else None
        if games >= gate and score is not None:
            scored.append((float(score), str(name), entry))
    if not scored:
        return PrepSection(
            key="phase_weakness",
            title="Phase performance",
            status=PrepSectionStatus.INSUFFICIENT,
            gate=gate,
            reason=f"No phase has at least {gate} stored games to compare.",
        )
    scored.sort(key=lambda item: item[0])
    worst_score, worst_name, worst_entry = scored[0]
    scenario = PrepScenario(
        key=f"phase:{worst_name}",
        kind="phase",
        title=f"{worst_name.title()} performance",
        statement=(
            f"Across {worst_entry.get('games')} stored games their score in the "
            f"{worst_name} is {worst_score}. This is a measured result, not a claim "
            f"about how they will play."
        ),
        sample_size=int(worst_entry.get("games") or 0),
        evidence=list(worst_entry.get("evidence") or []),
    )
    return PrepSection(
        key="phase_weakness",
        title="Phase performance",
        status=PrepSectionStatus.AVAILABLE,
        gate=gate,
        sample_size=scenario.sample_size,
        items=[scenario],
    )


def build_match_preparation(
    *,
    preparing_player_id: int,
    opponent_id: int,
    opponent_profile: dict | None,
    as_white: bool | None = None,
    now: datetime | None = None,
) -> MatchPreparation:
    """Compose the match preparation object from an opponent profile.

    Every section reports either its evidence or the gate it failed; a brief with
    no available section is still returned, because "I have nothing on this
    opponent yet" is a useful, honest answer before a game.
    """
    now = now or datetime.now(timezone.utc)
    profile = opponent_profile or {}
    identity = (profile.get("identity") or {}) if isinstance(profile.get("identity"), dict) else {}
    opponent_name = (
        identity.get("display_name")
        or profile.get("display_name")
        or profile.get("name")
        or ""
    )
    coverage_obj = profile.get("coverage")
    coverage = (
        coverage_obj.get("band")
        if isinstance(coverage_obj, dict)
        else coverage_obj or profile.get("coverage_band") or "insufficient"
    )
    repertoire = profile.get("repertoire")
    sections = [
        _opening_lines(repertoire if isinstance(repertoire, dict) else None, as_white=as_white),
        _tendencies(profile),
        _danger_zones(profile),
        _phase_weakness(profile),
    ]
    scenarios = [scenario for section in sections if section.status is PrepSectionStatus.AVAILABLE for scenario in section.items]
    for scenario in scenarios:
        if scenario.fen:
            scenario.practice_href = f"/opponents?fen={scenario.fen}"
    return MatchPreparation(
        preparing_player_id=preparing_player_id,
        opponent_id=opponent_id,
        opponent_name=str(opponent_name),
        as_white=as_white,
        created_at=now,
        updated_at=now,
        opponent_games=int(profile.get("imported_games") or profile.get("games") or 0),
        analysed_games=int(profile.get("analyzed_games") or profile.get("analysed_games") or 0),
        coverage=str(coverage),
        sections=sections,
        scenarios=scenarios,
        opponent_profile_version=str(profile.get("profile_version") or "") or None,
    )


def match_brief(preparation: MatchPreparation) -> dict[str, Any]:
    """The ``Caissa MATCH BRIEF`` document: what to prepare, honestly sourced."""
    available = [section for section in preparation.sections if section.status is PrepSectionStatus.AVAILABLE]
    unavailable = [section for section in preparation.sections if section.status is not PrepSectionStatus.AVAILABLE]
    headline = (
        f"Caissa has {preparation.analysed_games} analysed game(s) for "
        f"{preparation.opponent_name or 'this opponent'}."
        if preparation.analysed_games
        else f"Caissa has no analysed games for {preparation.opponent_name or 'this opponent'} yet."
    )
    priorities = [
        {
            "order": index,
            "title": scenario.title,
            "statement": scenario.statement,
            "kind": scenario.kind,
            "sample_size": scenario.sample_size,
            "practice_href": scenario.practice_href,
        }
        for index, scenario in enumerate(preparation.scenarios[:5], start=1)
    ]
    return {
        "title": "Caissa MATCH BRIEF",
        "opponent": preparation.opponent_name or str(preparation.opponent_id),
        "as_white": preparation.as_white,
        "headline": headline,
        "coverage": preparation.coverage,
        "preparing_player_id": preparation.preparing_player_id,
        "priorities": priorities,
        "available_sections": [section.title for section in available],
        "unavailable_sections": [
            {"title": section.title, "reason": section.reason} for section in unavailable
        ],
        "methodology_version": preparation.methodology_version,
        "disclaimer": (
            "This brief describes what the opponent has done in stored games. It does not "
            "predict what they will play, and it is not psychology."
        ),
    }


def parse_preparation_row(row: object) -> MatchPreparation:
    """Rehydrate a stored row into the object without re-reading the opponent."""
    sections = getattr(row, "sections", None) or []
    scenarios = getattr(row, "scenarios", None) or []
    return MatchPreparation(
        id=getattr(row, "id", None),
        preparing_player_id=getattr(row, "preparing_player_id"),
        opponent_id=getattr(row, "opponent_id"),
        opponent_name=getattr(row, "opponent_name", "") or "",
        as_white=getattr(row, "as_white", None),
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
        opponent_games=getattr(row, "opponent_games", 0) or 0,
        analysed_games=getattr(row, "analysed_games", 0) or 0,
        coverage=getattr(row, "coverage", "insufficient") or "insufficient",
        sections=[PrepSection.model_validate(section) for section in sections],
        scenarios=[PrepScenario.model_validate(scenario) for scenario in scenarios],
        opponent_profile_version=getattr(row, "opponent_profile_version", None),
        methodology_version=getattr(row, "methodology_version", MATCH_PREP_METHODOLOGY_VERSION),
    )


def match_prep_method() -> dict:
    """Publish the preparation rules, so a brief can be argued with."""
    return {
        "methodology_version": MATCH_PREP_METHODOLOGY_VERSION,
        "sample_gates": dict(SAMPLE_GATES),
        "rules": [
            "A brief describes stored opponent play; it never predicts a move.",
            "Every section either shows its sample-clearing evidence or the gate it missed.",
            "Preparation items resolve to the real games and plies they came from.",
            "A brief with nothing available still renders, and says so.",
        ],
    }


__all__ = [
    "MATCH_PREP_METHODOLOGY_VERSION",
    "SAMPLE_GATES",
    "MatchPreparation",
    "PrepScenario",
    "PrepSection",
    "PrepSectionStatus",
    "build_match_preparation",
    "match_brief",
    "match_prep_method",
    "parse_preparation_row",
]
