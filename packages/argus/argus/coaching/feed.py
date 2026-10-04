"""Today's Caissa: the personalised feed, the focus answer, the training plan.

Three product surfaces, one rule: every card is a *pointer to evidence*, not a
motivational slogan. A card that cannot name the games or positions behind it is
not emitted. That constraint is what keeps the feed from turning into the thing
every chess product eventually becomes — a stream of confident advice with
nothing under it.

The feed carries actions that go to real pages (``/game/<id>``, ``/training``,
``/opponents``, ``/scenarios``), so clicking a card always lands on the actual
analysis rather than a description of it. The focus answer follows §10's shape,
and the plan follows §11's: derived from measured categories, with the metric the
progress will be read from stated up front.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from argus.coaching.prioritize import (
    PRIORITY_METHODOLOGY_VERSION,
    InsightCandidate,
    ScoredInsight,
    prioritize,
)

FEED_METHODOLOGY_VERSION = "11.0"

#: The feed's sections, in the order a coach would raise them.
FEED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("recent_game", "Recent game"),
    ("important_mistakes", "Important mistakes"),
    ("recurring_patterns", "Recurring patterns"),
    ("training_recommendations", "Training recommendations"),
    ("opponent_preparation", "Opponent preparation"),
    ("opening_work", "Opening work"),
    ("progress", "Progress"),
)


class FeedAction(BaseModel):
    """A link to the thing the card is talking about."""

    label: str
    href: str
    kind: str = Field(description="page | api")


class FeedCard(BaseModel):
    key: str
    section: str
    title: str
    statement: str
    priority: str = "normal"
    score: float | None = None
    sample_size: int | None = None
    evidence: list[dict] = Field(default_factory=list)
    actions: list[FeedAction] = Field(default_factory=list)
    dismissible: bool = True
    capped_by: str | None = None


class FeedSection(BaseModel):
    key: str
    title: str
    cards: list[FeedCard] = Field(default_factory=list)
    available: bool = True
    reason: str | None = Field(
        default=None, description="Why the section is empty, when it is"
    )


class CoachingFeed(BaseModel):
    """The dashboard payload."""

    user_id: int | None = None
    sections: list[FeedSection] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    dismissed: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    methodology_version: str = FEED_METHODOLOGY_VERSION
    generated_at: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "sections": [section.model_dump(mode="json") for section in self.sections],
            "counts": dict(self.counts),
            "dismissed": list(self.dismissed),
            "gaps": list(self.gaps),
            "methodology_version": self.methodology_version,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
        }


# ---------------------------------------------------------------------------
# candidates from each source
# ---------------------------------------------------------------------------


def mistake_candidates(
    report: dict | None, *, game_id: str, side: str | None = None
) -> list[InsightCandidate]:
    """This game's mistakes as candidates — a real ply, a real loss in centipawns."""
    if not report:
        return []
    candidates: list[InsightCandidate] = []
    timeline = report.get("timeline") or []
    for moment in timeline:
        if not isinstance(moment, dict):
            continue
        classification = str(moment.get("classification") or "").lower()
        if classification not in ("blunder", "mistake"):
            continue
        loss = moment.get("centipawn_loss")
        ply = moment.get("ply")
        san = moment.get("san") or moment.get("played_move_san") or "?"
        if loss is None or ply is None:
            continue
        candidates.append(
            InsightCandidate(
                key=f"game:{game_id}:ply:{ply}",
                title=f"{classification.title()} at move {moment.get('move_number') or '?'} ({san})",
                statement=(
                    f"You played {san} here and lost {abs(int(loss))} centipawns; the "
                    f"engine's choice was {moment.get('best_move_san') or 'a different move'}."
                ),
                source="game_report",
                category="tactical_pattern" if classification == "blunder" else None,
                claim_level="observation",
                coverage="limited",
                games=1,
                occurrences=1,
                value=float(abs(int(loss))),
                unit="cp",
                severity="high" if classification == "blunder" else "medium",
                last_seen=_time(report.get("generated_at")),
                evidence=[
                    {
                        "kind": "game",
                        "game_id": game_id,
                        "ply": ply,
                        "side": side,
                        "classification": classification,
                        "centipawn_loss": loss,
                    }
                ],
            )
        )
    return candidates


def profile_candidates(
    player_profile: dict | None,
    *,
    training_by_tag: dict[str, dict] | None = None,
) -> list[InsightCandidate]:
    """Recurring player patterns. Only patterns and tendencies reach the feed."""
    if not player_profile:
        return []
    trained = training_by_tag or {}
    candidates: list[InsightCandidate] = []
    for insight in player_profile.get("insights") or []:
        if not isinstance(insight, dict):
            continue
        key = str(insight.get("id") or "")
        if not key:
            continue
        history = trained.get(key) or {}
        candidates.append(
            InsightCandidate(
                key=f"profile:{key}",
                title=str(insight.get("title") or key),
                statement=str(insight.get("statement") or ""),
                source="player_profile",
                category=str(insight.get("category")) if insight.get("category") else None,
                claim_level=insight.get("claim_level"),
                coverage=insight.get("coverage") or player_profile.get("coverage"),
                games=int(insight.get("games") or 0),
                occurrences=int(insight.get("occurrences") or 0),
                value=insight.get("value") if isinstance(insight.get("value"), (int, float)) else None,
                unit=insight.get("unit"),
                severity=insight.get("severity"),
                last_seen=_time(insight.get("last_seen")),
                training_attempts=history.get("attempts"),
                training_accuracy=history.get("accuracy"),
                evidence=list(insight.get("evidence") or []),
            )
        )
    return candidates


def opponent_candidates(opponent_profile: dict | None) -> list[InsightCandidate]:
    """What is worth knowing about the selected opponent, with its own sample size."""
    if not opponent_profile:
        return []
    candidates: list[InsightCandidate] = []
    identity = (opponent_profile.get("identity") or {}).get("display_name") or "this opponent"
    for insight in opponent_profile.get("insights") or []:
        if not isinstance(insight, dict):
            continue
        key = str(insight.get("id") or insight.get("title") or "")
        if not key:
            continue
        candidates.append(
            InsightCandidate(
                key=f"opponent:{key}",
                title=str(insight.get("title") or key),
                statement=str(insight.get("statement") or ""),
                source="opponent",
                category="opening_pattern",
                claim_level=insight.get("claim_level"),
                coverage=insight.get("coverage") or (opponent_profile.get("coverage") or {}).get("band"),
                games=int(insight.get("games") or insight.get("sample_size") or 0),
                occurrences=int(insight.get("occurrences") or 0),
                severity=insight.get("severity"),
                last_seen=_time(insight.get("last_seen")),
                evidence=list(insight.get("evidence") or []),
            )
        )
    if not candidates:
        candidates.append(
            InsightCandidate(
                key="opponent:identity",
                title=f"Selected opponent: {identity}",
                statement=(
                    "An opponent is selected. Nothing is claimed about their habits until "
                    "their stored games meet the sample threshold."
                ),
                source="opponent",
                claim_level="insufficient",
                coverage="insufficient",
                games=0,
                occurrences=0,
            )
        )
    return candidates


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _card(
    insight: ScoredInsight,
    section: str,
    *,
    actions: list[FeedAction],
) -> FeedCard:
    return FeedCard(
        key=insight.key,
        section=section,
        title=insight.title,
        statement=insight.statement,
        priority=insight.priority.value,
        score=insight.score,
        sample_size=insight.sample_size,
        evidence=insight.evidence,
        actions=actions,
        dismissible=insight.dismissible,
        capped_by=insight.capped_by,
    )


def build_feed(
    *,
    user_id: int | None = None,
    game_id: str | None = None,
    game_report: dict | None = None,
    side: str | None = None,
    player_profile: dict | None = None,
    training_by_tag: dict[str, dict] | None = None,
    training_recommendations: dict | None = None,
    training_progress: dict | None = None,
    training_queue: dict | None = None,
    opponent_id: int | None = None,
    opponent_profile: dict | None = None,
    opening_summary: dict | None = None,
    dismissed: set[str] | None = None,
    now: datetime | None = None,
    limit: int = 12,
) -> CoachingFeed:
    """Compose the dashboard. Empty sections explain themselves."""
    now = now or datetime.now(timezone.utc)
    dismissed = dismissed or set()
    gaps: list[str] = []
    sections: list[FeedSection] = []

    # --- recent game ---------------------------------------------------------
    if game_id and game_report:
        facts = ((game_report.get("summary") or {}).get("facts")) or []
        result_fact = next((f for f in facts if f.get("key") == "result"), None)
        opening_fact = next((f for f in facts if f.get("key") == "opening"), None)
        cards = [
            FeedCard(
                key=f"recent_game:{game_id}",
                section="recent_game",
                title="Your most recent analysed game",
                statement=" ".join(
                    part
                    for part in (
                        (result_fact or {}).get("statement"),
                        (opening_fact or {}).get("statement"),
                    )
                    if part
                )
                or "The game has an analysis report.",
                priority="normal",
                sample_size=len(facts),
                evidence=[{"kind": "game_report", "game_id": game_id, "detail": "summary.facts"}],
                actions=[
                    FeedAction(label="Open game", href=f"/game/{game_id}", kind="page"),
                    FeedAction(label="Full review", href=f"/game/{game_id}/report", kind="page"),
                ],
            )
        ]
        sections.append(FeedSection(key="recent_game", title="Recent game", cards=cards))
    else:
        gaps.append("No analysed game is available to lead the feed with.")
        sections.append(
            FeedSection(
                key="recent_game",
                title="Recent game",
                available=False,
                reason="No analysed game is available yet — import one to start the loop.",
            )
        )

    # --- important mistakes --------------------------------------------------
    mistakes = prioritize(
        mistake_candidates(game_report, game_id=game_id or "", side=side),
        now=now,
        dismissed=dismissed,
        limit=4,
    )
    if mistakes:
        sections.append(
            FeedSection(
                key="important_mistakes",
                title="Important mistakes",
                cards=[
                    _card(
                        item,
                        "important_mistakes",
                        actions=[
                            FeedAction(label="Show the position", href=f"/game/{game_id}", kind="page"),
                            FeedAction(label="What if?", href=f"/scenarios?game_id={game_id}", kind="page"),
                        ],
                    )
                    for item in mistakes
                ],
            )
        )
    else:
        sections.append(
            FeedSection(
                key="important_mistakes",
                title="Important mistakes",
                available=False,
                reason="This game's report has no classified mistake or blunder stored.",
            )
        )

    # --- recurring patterns --------------------------------------------------
    patterns = [
        item
        for item in prioritize(
            profile_candidates(player_profile, training_by_tag=training_by_tag),
            now=now,
            dismissed=dismissed,
            limit=6,
        )
        if (item.claim_level or "") in ("pattern", "tendency")
    ]
    if patterns:
        sections.append(
            FeedSection(
                key="recurring_patterns",
                title="Recurring patterns",
                cards=[
                    _card(
                        item,
                        "recurring_patterns",
                        actions=[
                            FeedAction(label="View evidence", href=f"/players/{user_id}", kind="page"),
                            FeedAction(label="Train this", href="/training", kind="page"),
                        ],
                    )
                    for item in patterns
                ],
            )
        )
    else:
        sections.append(
            FeedSection(
                key="recurring_patterns",
                title="Recurring patterns",
                available=False,
                reason=(
                    "No pattern in the profile has met its repetition threshold yet."
                    if player_profile
                    else "No player profile has been computed yet."
                ),
            )
        )

    # --- training recommendations --------------------------------------------
    opportunities = (training_recommendations or {}).get("opportunities") or []
    if opportunities:
        sections.append(
            FeedSection(
                key="training_recommendations",
                title="Training recommendations",
                cards=[
                    FeedCard(
                        key=f"training:{item.get('category')}",
                        section="training_recommendations",
                        title=str(item.get("label") or item.get("category") or "training"),
                        statement=str(item.get("reason") or item.get("statement") or ""),
                        # The training engine scores its own opportunities; that score
                        # travels as a measured value rather than being re-banded here.
                        priority="normal",
                        score=item.get("priority") if isinstance(item.get("priority"), (int, float)) else None,
                        sample_size=item.get("sample_size"),
                        evidence=[
                            {"kind": "training_history", "detail": entry}
                            for entry in (item.get("evidence") or [])
                        ],
                        actions=[
                            FeedAction(label="Start training", href="/training", kind="page"),
                        ],
                    )
                    for item in opportunities
                ],
            )
        )
    else:
        sections.append(
            FeedSection(
                key="training_recommendations",
                title="Training recommendations",
                available=False,
                reason=(
                    "Recommendations need measured attempt history; there is not enough "
                    "yet, so Caissa points at nothing."
                ),
            )
        )

    # --- opponent preparation ------------------------------------------------
    if opponent_profile or opponent_id:
        cards = [
            _card(
                item,
                "opponent_preparation",
                actions=[
                    FeedAction(label="Preparation", href="/opponents", kind="page"),
                    FeedAction(label="Scenarios", href="/scenarios", kind="page"),
                ],
            )
            for item in prioritize(opponent_candidates(opponent_profile), now=now, limit=4)
        ]
        sections.append(
            FeedSection(
                key="opponent_preparation",
                title="Opponent preparation",
                cards=cards,
                available=bool(cards),
                reason=None if cards else "No opponent profile is available.",
            )
        )
    else:
        sections.append(
            FeedSection(
                key="opponent_preparation",
                title="Opponent preparation",
                available=False,
                reason="No opponent is selected.",
            )
        )

    # --- opening work --------------------------------------------------------
    if opening_summary:
        sections.append(
            FeedSection(
                key="opening_work",
                title="Opening work",
                cards=[
                    FeedCard(
                        key="opening:summary",
                        section="opening_work",
                        title=str(opening_summary.get("title") or "Your openings"),
                        statement=str(opening_summary.get("statement") or ""),
                        priority=str(opening_summary.get("priority") or "normal"),
                        sample_size=opening_summary.get("games"),
                        evidence=list(opening_summary.get("evidence") or []),
                        actions=[FeedAction(label="Openings", href="/players", kind="page")],
                    )
                ],
            )
        )
    else:
        sections.append(
            FeedSection(
                key="opening_work",
                title="Opening work",
                available=False,
                reason=(
                    "No opening breakdown met its per-opening sample threshold "
                    "(4 games with that opening) yet."
                ),
            )
        )

    # --- progress ------------------------------------------------------------
    if training_progress:
        measured = {
            key: training_progress.get(key)
            for key in ("library_size", "attempts_total", "correct_total", "due_count")
            if training_progress.get(key) is not None
        }
        sections.append(
            FeedSection(
                key="progress",
                title="Progress",
                cards=[
                    FeedCard(
                        key="progress:training",
                        section="progress",
                        title="Training progress",
                        statement=(
                            f"{measured.get('attempts_total', 0)} attempt(s) on "
                            f"{measured.get('library_size', 0)} stored exercise(s); "
                            f"{measured.get('due_count', 0)} review(s) due."
                        ),
                        priority="normal",
                        sample_size=measured.get("attempts_total"),
                        evidence=[{"kind": "training_history", "detail": measured}],
                        actions=[
                            FeedAction(label="Progress", href="/training", kind="page"),
                            FeedAction(label="Compare periods", href="/players", kind="page"),
                        ],
                    )
                ],
            )
        )
    else:
        sections.append(
            FeedSection(
                key="progress",
                title="Progress",
                available=False,
                reason="No training history has been recorded yet.",
            )
        )

    ordered = [section for key, _ in FEED_SECTIONS for section in sections if section.key == key]
    counts = {band: 0 for band in ("critical", "high", "normal", "low")}
    for section in ordered:
        for card in section.cards:
            counts[card.priority] = counts.get(card.priority, 0) + 1
    return CoachingFeed(
        user_id=user_id,
        sections=ordered,
        counts=counts,
        dismissed=sorted(dismissed),
        gaps=gaps,
        generated_at=now,
    )


# ---------------------------------------------------------------------------
# "What should I work on?"
# ---------------------------------------------------------------------------


class FocusAnswer(BaseModel):
    """§10's answer shape — every field measured, or explicitly absent."""

    question: str = "What should I work on?"
    primary_focus: ScoredInsight | None = None
    evidence: list[dict] = Field(default_factory=list)
    affected_games: list[dict] = Field(default_factory=list)
    training_history: dict | None = None
    recommended_exercises: list[dict] = Field(default_factory=list)
    how_progress_will_be_measured: list[str] = Field(default_factory=list)
    supporting_focus: list[ScoredInsight] = Field(default_factory=list)
    status: str = "ok"
    reason: str | None = None
    methodology_version: str = FEED_METHODOLOGY_VERSION

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


#: How each focus area's progress is read. Only measures Caissa already stores.
PROGRESS_MEASURES: dict[str, str] = {
    "tactical_pattern": "centipawn loss on tactical mistakes in future analysed games",
    "positional_pattern": "centipawn loss on positional moves in future analysed games",
    "phase_pattern": "mean centipawn loss in that phase across future analysed games",
    "opening_pattern": "results and deviations in that opening across future games",
    "conversion_pattern": "conversion outcomes in games where the evaluation reached winning",
    "recovery_pattern": "outcomes in games where the evaluation fell to clearly worse",
    "recurring_pattern": "occurrences of the pattern per analysed game",
}


def answer_focus(
    *,
    player_profile: dict | None,
    training_by_tag: dict[str, dict] | None = None,
    training_recommendations: dict | None = None,
    recent_games: list[dict] | None = None,
    dismissed: set[str] | None = None,
    now: datetime | None = None,
) -> FocusAnswer:
    """Answer §10 from measured evidence, or decline to."""
    now = now or datetime.now(timezone.utc)
    candidates = profile_candidates(player_profile, training_by_tag=training_by_tag)
    ranked = prioritize(candidates, now=now, dismissed=dismissed, limit=4)
    if not ranked:
        return FocusAnswer(
            status="insufficient_evidence",
            reason=(
                "Caissa has no measured, repetition-threshold-meeting pattern to point at "
                "yet. More analysed games are needed before naming a focus would be "
                "anything other than a guess."
                if player_profile is None or not (player_profile.get("insights") or [])
                else "Every stored pattern is already mastered or dismissed, so nothing is "
                "prioritised."
            ),
        )
    primary = ranked[0]
    category = primary.category or "recurring_pattern"
    history = (training_by_tag or {}).get(primary.key.removeprefix("profile:"))
    games = [
        {"game_id": ref.get("game_id"), "ply": ref.get("ply")}
        for ref in primary.evidence
        if isinstance(ref, dict) and ref.get("game_id")
    ]
    exercises = _recommended_exercises(training_recommendations, category)
    return FocusAnswer(
        primary_focus=primary,
        supporting_focus=ranked[1:],
        evidence=primary.evidence,
        affected_games=games,
        training_history=history,
        recommended_exercises=exercises,
        how_progress_will_be_measured=[PROGRESS_MEASURES.get(category, PROGRESS_MEASURES["recurring_pattern"])],
    )


def _recommended_exercises(recommendations: dict | None, category: str) -> list[dict]:
    if not recommendations:
        return []
    for item in recommendations.get("opportunities") or []:
        if item.get("category") == category:
            return [
                {
                    "kind": "targeted_session",
                    "category": category,
                    "statement": item.get("statement") or item.get("reason"),
                    "sample_size": item.get("sample_size"),
                }
            ]
    return []


# ---------------------------------------------------------------------------
# §11 plan
# ---------------------------------------------------------------------------


class TrainingPlan(BaseModel):
    """A short, measured plan — not a curriculum someone invented."""

    plan_id: str
    player_id: int | None = None
    created_at: datetime
    methodology_version: str = FEED_METHODOLOGY_VERSION
    focus_areas: list[dict] = Field(default_factory=list)
    training_sessions: list[dict] = Field(default_factory=list)
    target_metrics: list[dict] = Field(default_factory=list)
    review_date: datetime | None = None
    weeks: int = 1
    limitations: list[str] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def build_training_plan(
    *,
    player_id: int | None,
    focus: list[ScoredInsight],
    training_recommendations: dict | None = None,
    now: datetime | None = None,
    weeks: int = 1,
) -> TrainingPlan:
    """Turn prioritised focus areas into a plan with a review date.

    The plan names only categories that already carry measured evidence, and it
    states the metric each is read from — so "review next week" means something
    checkable rather than a vibe.
    """
    now = now or datetime.now(timezone.utc)
    areas: list[dict] = []
    sessions: list[dict] = []
    metrics: list[dict] = []
    limitations: list[str] = []
    for index, item in enumerate(focus[:3], start=1):
        category = item.category or "recurring_pattern"
        areas.append(
            {
                "order": index,
                "key": item.key,
                "category": category,
                "statement": item.statement,
                "priority": item.priority.value,
                "score": item.score,
                "sample_size": item.sample_size,
                "claim_level": item.claim_level,
            }
        )
        sessions.append(
            {
                "kind": "targeted_session",
                "category": category,
                "suggested_length": 10,
                "note": "Drawn from your stored exercises in this category; nothing is invented.",
            }
        )
        metrics.append(
            {
                "category": category,
                "measure": PROGRESS_MEASURES.get(category),
                "baseline": {"occurrences": item.evidence and len(item.evidence) or None},
            }
        )
    if not areas:
        limitations.append(
            "No focus area had measurable evidence, so no plan was produced. This is a "
            "data limitation, not a training verdict."
        )
    if training_recommendations is None:
        limitations.append(
            "No training history was available, so the plan suggests categories without "
            "knowing whether they have been trained before."
        )
    return TrainingPlan(
        plan_id=f"plan:{player_id or 'anon'}:{now.date().isoformat()}",
        player_id=player_id,
        created_at=now,
        focus_areas=areas,
        training_sessions=sessions,
        target_metrics=metrics,
        review_date=now + timedelta(days=7 * weeks),
        weeks=weeks,
        limitations=limitations,
    )


def feed_method() -> dict:
    """Publish the feed's rules, so a card can be argued with."""
    return {
        "methodology_version": FEED_METHODOLOGY_VERSION,
        "priority_methodology_version": PRIORITY_METHODOLOGY_VERSION,
        "sections": [key for key, _ in FEED_SECTIONS],
        "rules": [
            "A card exists only if the finding behind it names its evidence.",
            "Empty sections say why they are empty; they are never filled with advice.",
            "Recurring patterns require the profile's repetition threshold to have been met.",
            "Predictions are shown only where a production model exists.",
            "Progress is reported as measured change after training, never as a causal claim.",
        ],
    }


__all__ = [
    "FEED_METHODOLOGY_VERSION",
    "FEED_SECTIONS",
    "PROGRESS_MEASURES",
    "CoachingFeed",
    "FeedAction",
    "FeedCard",
    "FeedSection",
    "FocusAnswer",
    "TrainingPlan",
    "answer_focus",
    "build_feed",
    "build_training_plan",
    "feed_method",
    "mistake_candidates",
    "opponent_candidates",
    "profile_candidates",
]
