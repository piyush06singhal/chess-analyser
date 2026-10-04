"""The automatic game debrief: what happened, what it means, what to do next.

A finished game already has everything a debrief needs, measured in earlier
phases:

* **Game Intelligence** — the game's facts, critical moments, turning points and
  classification counts, each with the ply it came from;
* **Player Intelligence** — the recurring patterns across analysed games, with
  their claim levels and evidence refs;
* **Training** — which of those patterns the player has already tried to fix, and
  how well.

This module assembles those into a ``GameDebrief``: an ordered, plain structure
where every observation carries the evidence that produced it. It performs no
analysis of its own. That is deliberate — a debrief that recomputed anything
would be a second source of truth, and the first chapter of this codebase's rules
is that there is exactly one.

Where a section has no evidence, it says so (``available: false`` with a reason)
instead of padding the report with generic advice.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from argus.coaching.prioritize import (
    InsightCandidate,
    InsightPriority,
    ScoredInsight,
    prioritize,
)

DEBRIEF_METHODOLOGY_VERSION = "11.0"

#: Patterns the coach will point at after a game. Only recurring ones: a single
#: occurrence is not a habit, and saying otherwise is the classic coaching lie.
REVIEW_STEPS: tuple[str, ...] = (
    "summary",
    "critical_moments",
    "biggest_decisions",
    "recurring_patterns",
    "what_went_well",
    "what_to_train",
    "counterfactuals",
    "improvement_tracking",
)

#: Classification labels the engine uses, grouped by what they mean for review.
_ERROR_LABELS = ("blunder", "mistake")
_GOOD_LABELS = ("best", "excellent", "good")


class DebriefSection(BaseModel):
    """One section of a debrief, with its own availability and evidence."""

    key: str
    title: str
    available: bool = True
    reason: str | None = Field(
        default=None, description="Why this section has nothing to show, when it does not"
    )
    observations: list[dict] = Field(default_factory=list)
    sample_size: int | None = None
    evidence: list[dict] = Field(default_factory=list)


class GameDebrief(BaseModel):
    """A finished game, read back."""

    game_id: str
    user_id: int | None = None
    side: str | None = Field(default=None, description="The side the user played")
    result: str | None = None
    opening: str | None = None
    summary: DebriefSection
    critical_moments: DebriefSection
    biggest_decisions: DebriefSection
    recurring_patterns: DebriefSection
    what_went_well: DebriefSection
    what_to_train: DebriefSection
    counterfactuals: DebriefSection
    improvement_tracking: DebriefSection
    steps: list[str] = Field(default_factory=lambda: list(REVIEW_STEPS))
    focus: list[ScoredInsight] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    methodology_version: str = DEBRIEF_METHODOLOGY_VERSION
    generated_at: datetime | None = None

    def sections(self) -> list[DebriefSection]:
        return [
            self.summary,
            self.critical_moments,
            self.biggest_decisions,
            self.recurring_patterns,
            self.what_went_well,
            self.what_to_train,
            self.counterfactuals,
            self.improvement_tracking,
        ]

    def to_payload(self) -> dict[str, Any]:
        """A JSON-ready view, sections in review order."""
        return {
            "game_id": self.game_id,
            "user_id": self.user_id,
            "side": self.side,
            "result": self.result,
            "opening": self.opening,
            "steps": self.steps,
            "sections": [section.model_dump(mode="json") for section in self.sections()],
            "focus": [item.model_dump(mode="json") for item in self.focus],
            "gaps": list(self.gaps),
            "methodology_version": self.methodology_version,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
        }


def _unavailable(key: str, title: str, reason: str) -> DebriefSection:
    return DebriefSection(key=key, title=title, available=False, reason=reason)


def _facts(report: dict | None) -> list[dict]:
    """The report's own fact list, or nothing. Never a paraphrase."""
    if not report:
        return []
    facts = ((report.get("summary") or {}).get("facts")) or []
    return [fact for fact in facts if isinstance(fact, dict)]


def _fact(facts: list[dict], key: str) -> dict | None:
    return next((fact for fact in facts if fact.get("key") == key), None)


def _moments(report: dict | None) -> list[dict]:
    """Every moment the report stored, from both of its containers.

    Game Intelligence keeps turning points as a list and engine critical positions
    as a dict of insight lists. Both describe the same game at the same plies, so a
    debrief reads the union — de-duplicated on (ply, statement), because the same
    fact can legitimately appear in both — rather than picking one and losing the
    other. Nothing is recomputed or merged into a new claim.
    """
    if not report:
        return []
    collected: list[dict] = []
    turning_points = report.get("turning_points")
    if isinstance(turning_points, list):
        collected.extend(item for item in turning_points if isinstance(item, dict))
    critical = report.get("critical_moments")
    if isinstance(critical, list):
        collected.extend(item for item in critical if isinstance(item, dict))
    elif isinstance(critical, dict):
        for value in critical.values():
            if isinstance(value, list):
                collected.extend(item for item in value if isinstance(item, dict))
    seen: set[tuple[int | None, str | None]] = set()
    unique: list[dict] = []
    for moment in collected:
        key = (_moment_ply(moment), str(moment.get("statement") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(moment)
    return unique


def _moment_ply(moment: dict) -> int | None:
    ply = moment.get("ply")
    return int(ply) if isinstance(ply, int) else None


def _moment_san(moment: dict, san_by_ply: dict[int, str] | None) -> str | None:
    """The move's SAN: from the moment when it stored one, else the game's own ply.

    The stored move list is the same fact the report was built from, so filling a
    missing SAN from it adds no claim — it just saves the reader a lookup.
    """
    san = moment.get("san")
    if san:
        return str(san)
    if not san_by_ply:
        return None
    ply = _moment_ply(moment)
    return san_by_ply.get(ply) if ply is not None else None


def _moment_classification(moment: dict) -> str | None:
    """The engine's classification of the move at this moment, when it stored one."""
    evidence = moment.get("evidence") if isinstance(moment.get("evidence"), dict) else {}
    for candidate in (moment.get("classification"), evidence.get("classification")):
        if candidate:
            return str(candidate).lower()
    statement = str(moment.get("statement") or "").lower()
    for label in _ERROR_LABELS:
        if f"classified the move a {label}" in statement:
            return label
    return None


def _moment_magnitude(moment: dict) -> int:
    """The measured size of the moment, in centipawns, from whichever field exists."""
    evidence = moment.get("evidence") if isinstance(moment.get("evidence"), dict) else {}
    for key in ("centipawn_loss", "severity_score"):
        value = moment.get(key) or evidence.get(key)
        if isinstance(value, (int, float)):
            return abs(int(value))
    swing = evidence.get("swing_cp")
    if isinstance(swing, (int, float)):
        return abs(int(swing))
    return 0


def _decision_rows(
    report: dict | None, san_by_ply: dict[int, str] | None = None
) -> list[dict]:
    """The moves worth a second look — genuine mistakes, largest first."""
    rows: list[dict] = []
    seen: set[tuple[int | None, str | None]] = set()
    for moment in _moments(report):
        classification = _moment_classification(moment)
        if classification not in _ERROR_LABELS:
            continue
        key = (_moment_ply(moment), classification)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "ply": _moment_ply(moment),
                "san": _moment_san(moment, san_by_ply),
                "side": moment.get("side"),
                "classification": classification,
                "centipawn_loss": _moment_magnitude(moment),
                "statement": moment.get("statement"),
            }
        )
    rows.sort(key=lambda item: item.get("centipawn_loss") or 0, reverse=True)
    return rows


def _pattern_candidates(
    player_profile: dict | None,
    *,
    training_attempts_by_tag: dict[str, dict] | None = None,
) -> list[InsightCandidate]:
    """Player-level patterns as priority candidates, with training history attached."""
    if not player_profile:
        return []
    attempts = training_attempts_by_tag or {}
    candidates: list[InsightCandidate] = []
    for insight in player_profile.get("insights") or []:
        if not isinstance(insight, dict):
            continue
        key = str(insight.get("id") or "")
        if not key:
            continue
        trained = attempts.get(key)
        category = insight.get("category")
        candidates.append(
            InsightCandidate(
                key=f"profile:{key}",
                title=str(insight.get("title") or key),
                statement=str(insight.get("statement") or ""),
                source="player_profile",
                category=str(category) if category else None,
                claim_level=insight.get("claim_level"),
                coverage=insight.get("coverage") or player_profile.get("coverage"),
                games=int(insight.get("games") or 0),
                occurrences=int(insight.get("occurrences") or 0),
                value=insight.get("value") if isinstance(insight.get("value"), (int, float)) else None,
                unit=insight.get("unit"),
                severity=insight.get("severity"),
                last_seen=_parse_time(insight.get("last_seen")),
                training_attempts=(trained or {}).get("attempts"),
                training_accuracy=(trained or {}).get("accuracy"),
                evidence=list(insight.get("evidence") or []),
            )
        )
    return candidates


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def build_debrief(
    *,
    game_id: str,
    report: dict | None,
    user_id: int | None = None,
    side: str | None = None,
    result: str | None = None,
    opening: str | None = None,
    player_profile: dict | None = None,
    san_by_ply: dict[int, str] | None = None,
    training_attempts_by_tag: dict[str, dict] | None = None,
    training_recommendations: dict | None = None,
    training_queue: dict | None = None,
    counterfactual_summary: dict | None = None,
    now: datetime | None = None,
) -> GameDebrief:
    """Assemble a debrief from already-measured inputs. Nothing is recomputed."""
    now = now or datetime.now(timezone.utc)
    gaps: list[str] = []
    facts = _facts(report)
    if not report:
        gaps.append(
            "This game has no Game Intelligence report yet, so the debrief has no "
            "measured facts to read."
        )

    # --- summary -------------------------------------------------------------
    if facts:
        summary = DebriefSection(
            key="summary",
            title="Summary",
            observations=[
                {"statement": fact.get("statement"), "source": fact.get("source"), "ply": fact.get("ply")}
                for fact in facts
            ],
            evidence=[{"kind": "game_report", "game_id": game_id, "detail": fact.get("key")} for fact in facts],
        )
    else:
        summary = _unavailable(
            "summary", "Summary", "No analysed facts were stored for this game."
        )

    # --- critical moments ----------------------------------------------------
    moments = _moments(report)
    if moments:
        critical = DebriefSection(
            key="critical_moments",
            title="Critical moments",
            observations=[
                {
                    "ply": _moment_ply(moment),
                    "move_number": moment.get("move_number"),
                    "san": _moment_san(moment, san_by_ply),
                    "side": moment.get("side"),
                    "kind": moment.get("kind") or moment.get("category"),
                    "severity": moment.get("severity"),
                    "classification": _moment_classification(moment),
                    "statement": moment.get("statement"),
                    "magnitude_cp": _moment_magnitude(moment),
                    "source": moment.get("source"),
                }
                for moment in moments[:12]
            ],
            sample_size=len(moments),
            evidence=[
                {"kind": "game_report", "game_id": game_id, "ply": _moment_ply(moment)}
                for moment in moments[:12]
            ],
        )
    else:
        critical = _unavailable(
            "critical_moments",
            "Critical moments",
            "No critical moment was stored for this game.",
        )

    # --- biggest decisions ---------------------------------------------------
    decisions = _decision_rows(report, san_by_ply)
    if decisions:
        decisions_section = DebriefSection(
            key="biggest_decisions",
            title="Biggest decisions",
            observations=[
                {
                    "ply": row.get("ply"),
                    "san": row.get("san"),
                    "side": row.get("side"),
                    "classification": row.get("classification"),
                    "loss_cp": row.get("centipawn_loss"),
                    "statement": row.get("statement"),
                }
                for row in decisions[:8]
            ],
            sample_size=len(decisions),
            evidence=[{"kind": "game_report", "game_id": game_id, "ply": row.get("ply")} for row in decisions[:8]],
        )
    else:
        decisions_section = _unavailable(
            "biggest_decisions",
            "Biggest decisions",
            "No classified mistake or blunder was stored for this game — either the "
            "game was clean or the timeline is unavailable.",
        )

    # --- recurring patterns --------------------------------------------------
    candidates = _pattern_candidates(player_profile, training_attempts_by_tag=training_attempts_by_tag)
    context_categories = _situation_categories(side, facts)
    ranked = prioritize(candidates, now=now, context_categories=context_categories)
    recurring = [item for item in ranked if (item.claim_level or "") in ("pattern", "tendency")]
    if recurring:
        patterns_section = DebriefSection(
            key="recurring_patterns",
            title="Recurring patterns",
            observations=[
                {
                    "key": item.key,
                    "title": item.title,
                    "statement": item.statement,
                    "priority": item.priority.value,
                    "score": item.score,
                    "games": item.sample_size,
                    "claim_level": item.claim_level,
                    "trained": item.factors.get("training_need") is not None,
                    "capped_by": item.capped_by,
                }
                for item in recurring
            ],
            sample_size=len(recurring),
            evidence=[
                {"kind": "player_profile", "detail": item.key, "refs": item.evidence} for item in recurring
            ],
        )
    else:
        patterns_section = _unavailable(
            "recurring_patterns",
            "Recurring patterns",
            (
                "No pattern in the player profile has yet met its repetition threshold, so "
                "nothing is called recurring."
                if player_profile
                else "No player profile is computed yet, so no pattern can be read."
            ),
        )

    # --- what went well ------------------------------------------------------
    good = []
    for label in _GOOD_LABELS:
        fact = _fact(facts, f"flagged_moves_{label}")
        if fact:
            good.append({"classification": label, "statement": fact.get("statement")})
    counts_white = _fact(facts, "flagged_moves_white")
    counts_black = _fact(facts, "flagged_moves_black")
    well_section = DebriefSection(
        key="what_went_well",
        title="What went well",
        available=bool(good or counts_white or counts_black),
        reason=None
        if (good or counts_white or counts_black)
        else "No classification counts were stored for this game.",
        observations=[
            {"counts": (counts_white or {}).get("evidence", {}).get("counts"), "side": "white"},
            {"counts": (counts_black or {}).get("evidence", {}).get("counts"), "side": "black"},
        ]
        if (counts_white or counts_black)
        else good,
        evidence=[{"kind": "game_report", "game_id": game_id, "detail": "classification_counts"}]
        if (counts_white or counts_black)
        else [],
    )

    # --- what to train -------------------------------------------------------
    opportunities = (training_recommendations or {}).get("opportunities") or []
    if opportunities:
        train_section = DebriefSection(
            key="what_to_train",
            title="What to train",
            # The training engine's own measured score is passed through as-is and
            # labelled as such: it is not re-derived into a coaching priority.
            observations=[
                {
                    "category": item.get("category"),
                    "statement": item.get("reason") or item.get("statement"),
                    "training_priority_score": item.get("priority"),
                    "factors": item.get("factors"),
                    "evidence": item.get("evidence"),
                }
                for item in opportunities
            ],
            sample_size=len(opportunities),
            evidence=[{"kind": "training_recommendations", "detail": item.get("category")} for item in opportunities],
        )
    else:
        train_section = _unavailable(
            "what_to_train",
            "What to train",
            (
                "Training recommendations need measured attempt history on this pattern; "
                "there is not enough yet, so Caissa will not point at a category."
            ),
        )

    # --- counterfactuals -----------------------------------------------------
    if counterfactual_summary and counterfactual_summary.get("turning_points"):
        moments_available = [
            moment
            for moment in counterfactual_summary["turning_points"]
            if moment.get("what_if_available")
        ]
        cf_section = DebriefSection(
            key="counterfactuals",
            title="Counterfactuals",
            available=bool(moments_available),
            reason=None
            if moments_available
            else (
                "The stored analysis of this game carries no alternative moves "
                "(candidate storage), so no what-if can be built without re-analysing it."
            ),
            observations=[
                {
                    "ply": moment.get("ply"),
                    "san": moment.get("san"),
                    "alternatives": moment.get("alternative_count"),
                    "branchable": moment.get("branchable"),
                    "what_if_available": moment.get("what_if_available"),
                    "critical_reason": moment.get("critical_reason"),
                }
                for moment in (moments_available or counterfactual_summary["turning_points"])[:6]
            ],
            sample_size=len(counterfactual_summary["turning_points"]),
            evidence=[{"kind": "stored_analysis", "game_id": game_id, "ply": moment.get("ply")} for moment in moments_available[:6]],
        )
    else:
        cf_section = _unavailable(
            "counterfactuals",
            "Counterfactuals",
            "No turning-point data is available for this game yet.",
        )

    # --- improvement tracking ------------------------------------------------
    attempts = (training_recommendations or {}).get("training") or {}
    if training_queue is not None or attempts:
        observations: list[dict] = []
        if training_queue is not None:
            observations.append(
                {"metric": "due_reviews", "value": training_queue.get("count")}
            )
        if attempts:
            observations.append(
                {
                    "metric": "attempts_on_these_patterns",
                    "value": attempts.get("attempts_total"),
                }
            )
        tracking = DebriefSection(
            key="improvement_tracking",
            title="Improvement tracking",
            observations=observations,
            reason=(
                "Improvement is reported as measured change after training, never as a "
                "claim that training caused it."
            ),
            sample_size=attempts.get("attempts_total") if attempts else None,
            evidence=[{"kind": "training_history", "detail": "attempts and review schedule"}],
        )
    else:
        tracking = _unavailable(
            "improvement_tracking",
            "Improvement tracking",
            "No training attempts have been recorded for this player yet.",
        )

    focus = prioritize(
        candidates,
        now=now,
        context_categories=context_categories,
        limit=3,
    )
    if not focus:
        gaps.append(
            "No prioritised focus could be derived: the player profile has no "
            "measurable insight yet."
        )

    return GameDebrief(
        game_id=game_id,
        user_id=user_id,
        side=side,
        result=result,
        opening=opening,
        summary=summary,
        critical_moments=critical,
        biggest_decisions=decisions_section,
        recurring_patterns=patterns_section,
        what_went_well=well_section,
        what_to_train=train_section,
        counterfactuals=cf_section,
        improvement_tracking=tracking,
        focus=focus,
        gaps=gaps,
        generated_at=now,
    )


def _situation_categories(side: str | None, facts: list[dict]) -> set[str]:
    """Which insight categories are most relevant to *this* game.

    Read from the game's own measured facts — the phase it went worst in, whether
    a conversion or recovery moment exists — so relevance nudges the ranking
    towards what this game actually showed.
    """
    categories: set[str] = set()
    for fact in facts:
        key = str(fact.get("key") or "")
        if key.startswith("worst_phase"):
            categories.add("phase_pattern")
        if key.startswith("conversion"):
            categories.add("conversion_pattern")
        if key.startswith("tactical"):
            categories.add("tactical_pattern")
    return categories


__all__ = [
    "DEBRIEF_METHODOLOGY_VERSION",
    "InsightPriority",
    "REVIEW_STEPS",
    "DebriefSection",
    "GameDebrief",
    "build_debrief",
]
