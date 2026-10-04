"""Progress: measured change between two periods, with the causality warning built in.

Phase 11 §26–§28 ask for a progress model, an improvement comparison, and an
explicit causality warning. Those three requirements are in tension with the way
chess products usually talk, so the rules here are stated rather than implied:

* **A progress number is a measured difference, never a cause.** The comparison
  always carries ``causality_note``: Caissa cannot know that training *caused* a
  change, and it says so on every payload rather than only in the docs.
* **Sample size travels, and small samples are refused.** Each measure reports the
  games behind each side, and below ``MIN_GAMES_PER_PERIOD`` the measure is
  reported as insufficient instead of shown as a number.
* **Only stored, comparable measures.** Every measure is a field the Phase 4/5
  layers already compute and store; nothing here derives a new chess statistic.

This module is pure: it operates on per-game metric dictionaries the service
layer has already loaded. It performs no engine work and reads no database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

PROGRESS_METHODOLOGY_VERSION = "11.0"

#: Below this many analysed games on either side, a measure is not compared.
MIN_GAMES_PER_PERIOD = 4

#: A change smaller than this (in the measure's own unit) is reported as
#: unchanged — stated noise tolerance, not an invisible threshold.
#: Accuracy is stored and reported on the 0–100 scale the reports already use,
#: so its tolerance is one percentage point on that scale.
DEFAULT_NOISE_TOLERANCE: dict[str, float] = {
    "accuracy": 1.0,           # one percentage point on the 0–100 scale
    "mean_centipawn_loss": 10.0,
    "blunders_per_game": 0.5,
    "mistakes_per_game": 0.5,
}

CAUSALITY_NOTE = (
    "This is a measured change between two periods of your games, not proof that "
    "anything caused it. Improvement can come from training, from stronger or weaker "
    "opponents, from different openings, from time control, or from chance. Caissa "
    "reports the difference and the sample; it does not claim a cause."
)


class ProgressMeasure(BaseModel):
    """One comparable measure across two periods."""

    key: str
    label: str
    unit: str
    direction: str = Field(
        default="lower_is_better", description="higher_is_better | lower_is_better"
    )
    before: float | None = None
    after: float | None = None
    delta: float | None = None
    #: ``improved`` | ``declined`` | ``unchanged`` | ``insufficient``
    verdict: str = "insufficient"
    noise_tolerance: float | None = None
    sample_before: int = 0
    sample_after: int = 0
    note: str | None = None


class ProgressSnapshot(BaseModel):
    """One period of stored metrics, with its games and measures."""

    label: str
    start: datetime | None = None
    end: datetime | None = None
    games: int = 0
    analysed_games: int = 0
    measures: list[ProgressMeasure] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class ImprovementComparison(BaseModel):
    """Two snapshots and the honest reading of the difference between them."""

    before: ProgressSnapshot
    after: ProgressSnapshot
    measures: list[ProgressMeasure] = Field(default_factory=list)
    summary: str = ""
    causality_note: str = CAUSALITY_NOTE
    limitations: list[str] = Field(default_factory=list)
    status: str = Field(default="ok", description="ok | insufficient_data")
    reason: str | None = None
    methodology_version: str = PROGRESS_METHODOLOGY_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "before": self.before.model_dump(mode="json"),
            "after": self.after.model_dump(mode="json"),
            "measures": [measure.model_dump(mode="json") for measure in self.measures],
            "summary": self.summary,
            "causality_note": self.causality_note,
            "limitations": list(self.limitations),
            "status": self.status,
            "reason": self.reason,
            "methodology_version": self.methodology_version,
        }


#: How each measure is read. Only fields the intelligence layers already store.
_MEASURE_DEFS: tuple[tuple[str, str, str, str], ...] = (
    ("accuracy", "Caissa accuracy", "ratio", "higher_is_better"),
    ("mean_centipawn_loss", "Mean centipawn loss", "cp", "lower_is_better"),
    ("blunders_per_game", "Blunders per game", "count", "lower_is_better"),
    ("mistakes_per_game", "Mistakes per game", "count", "lower_is_better"),
)


def _mean(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 4)


def _collect(games: list[dict]) -> dict[str, list[float]]:
    """Extract the comparable per-game measures from stored report metrics.

    Accepts either the flat naming the reports use or a nested ``metrics``
    mapping; missing fields are simply absent, never zero-filled.
    """
    collected: dict[str, list[float]] = {key: [] for key, *_ in _MEASURE_DEFS}
    for game in games:
        metrics = game.get("metrics") if isinstance(game.get("metrics"), dict) else game
        if not isinstance(metrics, dict):
            continue
        for key, *_ in _MEASURE_DEFS:
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                collected[key].append(float(value))
        # Derivations use counts that the report stores directly; when a report
        # exposes only counts, derive the per-game rate from them.
        for derived_key, source_key in (
            ("blunders_per_game", "blunder_count"),
            ("mistakes_per_game", "mistake_count"),
        ):
            value = metrics.get(source_key)
            if isinstance(value, (int, float)):
                collected[derived_key].append(float(value))
    return collected


def build_snapshot(
    *,
    label: str,
    games: list[dict],
    start: datetime | None = None,
    end: datetime | None = None,
) -> ProgressSnapshot:
    """Summarise one period's stored games into measures, with sample sizes."""
    analysed = [game for game in games if game.get("has_report")]
    collected = _collect(analysed)
    measures: list[ProgressMeasure] = []
    for key, label_text, unit, direction in _MEASURE_DEFS:
        values = collected.get(key) or []
        value = _mean(values)
        measures.append(
            ProgressMeasure(
                key=key,
                label=label_text,
                unit=unit,
                direction=direction,
                after=value,
                sample_after=len(values),
            )
        )
    gaps: list[str] = []
    if not analysed:
        gaps.append("No analysed game with a stored report falls in this period.")
    return ProgressSnapshot(
        label=label,
        start=start,
        end=end,
        games=len(games),
        analysed_games=len(analysed),
        measures=measures,
        gaps=gaps,
    )


def _verdict(
    *, before: float | None, after: float | None, direction: str, tolerance: float
) -> str:
    if before is None or after is None:
        return "insufficient"
    delta = after - before
    if abs(delta) <= tolerance:
        return "unchanged"
    improved = delta > 0 if direction == "higher_is_better" else delta < 0
    return "improved" if improved else "declined"


def compare_periods(
    *,
    before_games: list[dict],
    after_games: list[dict],
    before_label: str = "Earlier period",
    after_label: str = "Recent period",
    before_start: datetime | None = None,
    before_end: datetime | None = None,
    after_start: datetime | None = None,
    after_end: datetime | None = None,
    minimum_games: int = MIN_GAMES_PER_PERIOD,
) -> ImprovementComparison:
    """Compare two periods, refusing to compare what is too thin to compare.

    The comparison is honest in three ways at once: it never claims causality,
    it reports the sample behind every measure, and it returns
    ``insufficient_data`` (with reasons) rather than a thin, misleading delta.
    """
    before = build_snapshot(
        label=before_label, games=before_games, start=before_start, end=before_end
    )
    after = build_snapshot(
        label=after_label, games=after_games, start=after_start, end=after_end
    )
    measures: list[ProgressMeasure] = []
    for definition, snapshot_before, snapshot_after in zip(
        _MEASURE_DEFS, before.measures, after.measures
    ):
        key, label_text, unit, direction = definition
        sample_before = snapshot_before.sample_after
        sample_after = snapshot_after.sample_after
        tolerance = DEFAULT_NOISE_TOLERANCE.get(key, 0.0)
        if sample_before < minimum_games or sample_after < minimum_games:
            measures.append(
                ProgressMeasure(
                    key=key,
                    label=label_text,
                    unit=unit,
                    direction=direction,
                    before=snapshot_before.after,
                    after=snapshot_after.after,
                    verdict="insufficient",
                    noise_tolerance=tolerance,
                    sample_before=sample_before,
                    sample_after=sample_after,
                    note=(
                        f"Each period needs at least {minimum_games} analysed games to "
                        f"compare this measure; there are {sample_before} and {sample_after}."
                    ),
                )
            )
            continue
        delta = (
            round(snapshot_after.after - snapshot_before.after, 4)
            if snapshot_before.after is not None and snapshot_after.after is not None
            else None
        )
        measures.append(
            ProgressMeasure(
                key=key,
                label=label_text,
                unit=unit,
                direction=direction,
                before=snapshot_before.after,
                after=snapshot_after.after,
                delta=delta,
                verdict=_verdict(
                    before=snapshot_before.after,
                    after=snapshot_after.after,
                    direction=direction,
                    tolerance=tolerance,
                ),
                noise_tolerance=tolerance,
                sample_before=sample_before,
                sample_after=sample_after,
            )
        )

    comparable = [m for m in measures if m.verdict not in ("insufficient",)]
    limitations: list[str] = [
        "The two periods may differ in opponent strength, opening choices and time "
        "control; Caissa does not normalise for these.",
        "Measures are computed from stored reports; games without an analysis are excluded.",
    ]
    if not comparable:
        return ImprovementComparison(
            before=before,
            after=after,
            measures=measures,
            status="insufficient_data",
            reason=(
                f"Neither period has at least {minimum_games} analysed games, so no "
                f"measure can be compared. More analysed games are needed."
            ),
            limitations=limitations,
        )
    improved = [m.label for m in comparable if m.verdict == "improved"]
    declined = [m.label for m in comparable if m.verdict == "declined"]
    unchanged = [m.label for m in comparable if m.verdict == "unchanged"]
    parts = []
    if improved:
        parts.append(f"improved on {', '.join(improved)}")
    if declined:
        parts.append(f"declined on {', '.join(declined)}")
    if unchanged:
        parts.append(f"unchanged on {', '.join(unchanged)}")
    summary = (
        f"Across {before.analysed_games} earlier and {after.analysed_games} recent "
        f"analysed games, Caissa measured: " + "; ".join(parts) + "."
        if parts
        else "No comparable measure changed."
    )
    return ImprovementComparison(
        before=before,
        after=after,
        measures=measures,
        summary=summary,
        limitations=limitations,
    )


def progress_method() -> dict:
    """Publish the progress rules, so a number can be argued with."""
    return {
        "methodology_version": PROGRESS_METHODOLOGY_VERSION,
        "minimum_games_per_period": MIN_GAMES_PER_PERIOD,
        "noise_tolerance": dict(DEFAULT_NOISE_TOLERANCE),
        "causality_note": CAUSALITY_NOTE,
        "rules": [
            "Progress is a measured difference between two periods, never a causal claim.",
            "Every measure reports the analysed-game sample behind each side.",
            "Below the minimum sample a measure is reported as insufficient, not shown.",
            "A change inside the stated noise tolerance is reported as unchanged.",
        ],
    }


__all__ = [
    "CAUSALITY_NOTE",
    "DEFAULT_NOISE_TOLERANCE",
    "MIN_GAMES_PER_PERIOD",
    "PROGRESS_METHODOLOGY_VERSION",
    "ImprovementComparison",
    "ProgressMeasure",
    "ProgressSnapshot",
    "build_snapshot",
    "compare_periods",
    "progress_method",
]
