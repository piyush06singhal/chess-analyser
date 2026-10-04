"""Model regression: compare a candidate against the current production model (§44).

Promoting a model is a decision, not a side effect. This module takes the two
evaluations of a candidate and the production model **on the same benchmark** and
reports, per metric, what changed — overall, per subgroup, over time, and in
calibration — plus whether the candidate leaked or is not reproducible. It never
promotes: :func:`compare_models` returns a *recommendation* and the reasons for
it, and the caller decides.

Two rules make the comparison trustworthy:

* **Direction is explicit.** ``log_loss``, ``brier_score`` and calibration error
  are lower-is-better; everything else is higher-is-better. A comparison that
  guesses the direction would call a worse log loss an improvement.
* **A subgroup regression is a regression.** A candidate that improves the average
  while getting worse for one time control or rating band is not an improvement;
  it is a model that moved its errors onto the people least able to notice.

The mechanism is always available. When no model has been promoted, the caller is
told that there is nothing to compare rather than being handed a green light.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Metrics where a *lower* value is better. Anything not listed is higher-is-better.
LOWER_IS_BETTER = frozenset(
    {
        "log_loss",
        "brier_score",
        "expected_calibration_error",
        "ece",
        "mean_absolute_error",
        "rmse",
    }
)


def _direction(metric: str) -> int:
    """+1 when larger is better, -1 when smaller is better."""
    return -1 if metric in LOWER_IS_BETTER else 1


@dataclass
class ModelEvaluation:
    """One model's measured performance on a named benchmark."""

    model_version: str
    dataset_version: str
    feature_version: str = "unknown"
    #: Headline metrics on the held-out test split.
    metrics: dict[str, float] = field(default_factory=dict)
    #: Per-subgroup metrics, keyed by subgroup name (time control, rating band …).
    subgroups: dict[str, dict[str, float]] = field(default_factory=dict)
    #: The same metrics over time windows, keyed by window (e.g. ``"2026-Q1"``).
    temporal: dict[str, dict[str, float]] = field(default_factory=dict)
    #: Whether the leakage check passed for this model.
    leakage_passed: bool | None = None
    #: Whether the training run was reproducible from its recorded inputs.
    reproducible: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "dataset_version": self.dataset_version,
            "feature_version": self.feature_version,
            "metrics": dict(self.metrics),
            "subgroups": {k: dict(v) for k, v in self.subgroups.items()},
            "temporal": {k: dict(v) for k, v in self.temporal.items()},
            "leakage_passed": self.leakage_passed,
            "reproducible": self.reproducible,
        }


@dataclass
class MetricDelta:
    """One metric's change from baseline to candidate."""

    metric: str
    baseline: float | None
    candidate: float | None
    change: float | None
    improved: bool
    regressed: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "change": self.change,
            "improved": self.improved,
            "regressed": self.regressed,
        }


@dataclass
class ModelComparison:
    """The outcome of comparing a candidate against production."""

    baseline_version: str
    candidate_version: str
    dataset_version: str
    deltas: list[MetricDelta] = field(default_factory=list)
    subgroup_regressions: list[str] = field(default_factory=list)
    temporal_regressions: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    #: "candidate", "baseline", or "insufficient-data".
    recommendation: str = "insufficient-data"
    note: str = ""

    @property
    def promotable(self) -> bool:
        """Whether the evidence supports promoting the candidate.

        A recommendation of ``baseline`` on a *real* comparison means no. This is
        the field a caller must check; it is never true by default.
        """
        return self.recommendation == "candidate"

    def to_payload(self) -> dict[str, Any]:
        return {
            "baseline_version": self.baseline_version,
            "candidate_version": self.candidate_version,
            "dataset_version": self.dataset_version,
            "deltas": [delta.to_payload() for delta in self.deltas],
            "subgroup_regressions": self.subgroup_regressions,
            "temporal_regressions": self.temporal_regressions,
            "blocking_reasons": self.blocking_reasons,
            "recommendation": self.recommendation,
            "promotable": self.promotable,
            "note": self.note,
        }


def _delta(metric: str, baseline: float | None, candidate: float | None, tolerance: float) -> MetricDelta:
    if baseline is None or candidate is None:
        return MetricDelta(metric, baseline, candidate, None, False, False)
    change = candidate - baseline
    better = change * _direction(metric)
    return MetricDelta(
        metric=metric,
        baseline=baseline,
        candidate=candidate,
        change=round(change, 6),
        improved=better > 0,
        regressed=better < -abs(tolerance),
    )


def compare_models(
    baseline: ModelEvaluation,
    candidate: ModelEvaluation,
    *,
    tolerance: float = 0.0,
) -> ModelComparison:
    """Compare a candidate against production on the same benchmark.

    ``tolerance`` is the amount a metric may worsen before it counts as a
    regression (use a small value for metrics with run-to-run noise). Subgroup and
    temporal regressions are always counted, at the same tolerance.
    """
    comparison = ModelComparison(
        baseline_version=baseline.model_version,
        candidate_version=candidate.model_version,
        dataset_version=candidate.dataset_version,
    )

    if baseline.dataset_version != candidate.dataset_version:
        comparison.blocking_reasons.append(
            "The two models were measured on different datasets "
            f"({baseline.dataset_version} vs {candidate.dataset_version}); the "
            "comparison is not valid."
        )
        comparison.recommendation = "insufficient-data"
        comparison.note = "Cannot compare models measured on different benchmarks."
        return comparison

    if not baseline.metrics or not candidate.metrics:
        comparison.recommendation = "insufficient-data"
        comparison.note = "One side has no measured metrics; nothing to compare."
        return comparison

    for metric in sorted(set(baseline.metrics) | set(candidate.metrics)):
        comparison.deltas.append(
            _delta(metric, baseline.metrics.get(metric), candidate.metrics.get(metric), tolerance)
        )

    for group, base_metrics in baseline.subgroups.items():
        cand_metrics = candidate.subgroups.get(group)
        if not cand_metrics:
            continue
        for metric, base_value in base_metrics.items():
            delta = _delta(metric, base_value, cand_metrics.get(metric), tolerance)
            if delta.regressed:
                comparison.subgroup_regressions.append(f"{group}.{metric}")

    for window, base_metrics in baseline.temporal.items():
        cand_metrics = candidate.temporal.get(window)
        if not cand_metrics:
            continue
        for metric, base_value in base_metrics.items():
            delta = _delta(metric, base_value, cand_metrics.get(metric), tolerance)
            if delta.regressed:
                comparison.temporal_regressions.append(f"{window}.{metric}")

    if candidate.leakage_passed is False:
        comparison.blocking_reasons.append("The candidate failed its leakage check.")
    if candidate.reproducible is False:
        comparison.blocking_reasons.append("The candidate's training run is not reproducible.")
    if comparison.subgroup_regressions:
        comparison.blocking_reasons.append(
            "Subgroup regressions: " + ", ".join(sorted(comparison.subgroup_regressions))
        )
    if comparison.temporal_regressions:
        comparison.blocking_reasons.append(
            "Temporal regressions: " + ", ".join(sorted(comparison.temporal_regressions))
        )

    improved = [delta.metric for delta in comparison.deltas if delta.improved]
    regressed = [delta.metric for delta in comparison.deltas if delta.regressed]
    if regressed:
        comparison.blocking_reasons.append("Overall regressions: " + ", ".join(sorted(regressed)))

    if comparison.blocking_reasons:
        comparison.recommendation = "baseline"
        comparison.note = (
            "Keep the production model: the candidate does not clear every check "
            "above. A candidate is never promoted automatically."
        )
    elif improved:
        comparison.recommendation = "candidate"
        comparison.note = (
            "The candidate is not worse anywhere measured and improves at least one "
            "metric. Promotion is still the caller's decision."
        )
    else:
        comparison.recommendation = "baseline"
        comparison.note = "No measured improvement; there is no reason to promote."

    return comparison


def compare_against_production(
    candidate: ModelEvaluation,
    *,
    production: ModelEvaluation | None = None,
    tolerance: float = 0.0,
) -> ModelComparison:
    """Compare against the current production model, or say there is none.

    This is the entry point a promotion job should call: when no production model
    exists there is nothing to regress against, and the honest answer is
    ``insufficient-data`` rather than a promotion.
    """
    if production is None:
        return ModelComparison(
            baseline_version="none",
            candidate_version=candidate.model_version,
            dataset_version=candidate.dataset_version,
            recommendation="insufficient-data",
            note=(
                "No production model is promoted, so there is no baseline to regress "
                "against. This is a first model, and it must pass the production gate "
                "on its own merits."
            ),
        )
    return compare_models(production, candidate, tolerance=tolerance)


__all__ = [
    "LOWER_IS_BETTER",
    "MetricDelta",
    "ModelComparison",
    "ModelEvaluation",
    "compare_against_production",
    "compare_models",
]
