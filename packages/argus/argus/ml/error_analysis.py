"""Error analysis — where a model fails, not just how often.

An aggregate score is a summary over slices that may behave completely
differently. A model can be excellent on 1400-rated blitz games and useless on
2200-rated classical ones, and the aggregate will happily hide that. So every
evaluated model is broken down by the slices that matter for chess, and the
worst slice is treated as a finding rather than a footnote:

* rating band (from the Phase 6 quality module's bands, so they match the data reports)
* time class
* colour
* opening family
* game phase (from the stored analysis, when positions are available)
* seen vs unseen players (the generalisation question)

Slice metrics are only computed where the slice has enough rows to mean
anything; a slice below the floor is reported as *unmeasured* rather than as a
score that happens to be terrible (or great) from three rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from argus.ml.metrics import ClassificationMetrics, classification_metrics

Row = dict[str, Any]

#: Minimum rows for a slice's metrics to be reported. Below this the slice is
#: named and marked unmeasured — reporting a metric from five rows would be a
#: fabricated precision.
MIN_SLICE_ROWS = 30


class SliceResult(BaseModel):
    """Measured performance inside one slice of the data."""

    slice_name: str
    values: list[str] = Field(default_factory=list, description="The slice's categories")
    rows: int = 0
    accuracy: float | None = None
    balanced_accuracy: float | None = None
    macro_f1: float | None = None
    log_loss: float | None = None
    measured: bool = True
    note: str = ""


class ErrorAnalysis(BaseModel):
    """The full slice breakdown for one model on one split."""

    model: str = ""
    slice_by: str = ""
    rows: int = 0
    slices: list[SliceResult] = Field(default_factory=list)
    worst_slice: str | None = None
    best_slice: str | None = None
    spread: float | None = None
    misclassified_examples: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def metrics_by_slice(self) -> dict[str, float]:
        """Slice name → balanced accuracy, for the gating subgroup check."""
        return {
            item.slice_name: item.balanced_accuracy  # type: ignore[misc]
            for item in self.slices
            if item.measured and item.balanced_accuracy is not None
        }

    def summary(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "slice_by": self.slice_by,
            "rows": self.rows,
            "worst_slice": self.worst_slice,
            "best_slice": self.best_slice,
            "balanced_accuracy_spread": self.spread,
            "unmeasured_slices": [item.slice_name for item in self.slices if not item.measured],
        }


def slice_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    probabilities: Sequence[Sequence[float]] | None,
    slices: Sequence[str],
    *,
    classes: Sequence[str],
    model: str = "",
    slice_by: str = "slice",
    min_rows: int = MIN_SLICE_ROWS,
) -> ErrorAnalysis:
    """Measure a model inside each category of one slicer."""
    if not (len(y_true) == len(y_pred) == len(slices)):
        raise ValueError("y_true, y_pred and slices must have the same length")

    analysis = ErrorAnalysis(model=model, slice_by=slice_by, rows=len(y_true))
    grouped: dict[str, list[int]] = {}
    for position, name in enumerate(slices):
        grouped.setdefault(name or "unknown", []).append(position)

    for name in sorted(grouped):
        indices = grouped[name]
        truth = [y_true[index] for index in indices]
        predicted = [y_pred[index] for index in indices]
        probability_rows = (
            [probabilities[index] for index in indices] if probabilities is not None else None
        )

        if len(indices) < min_rows:
            analysis.slices.append(
                SliceResult(
                    slice_name=name,
                    values=truth[:0],
                    rows=len(indices),
                    measured=False,
                    note=(
                        f"{len(indices)} row(s) is below the {min_rows}-row floor, so this "
                        "slice is reported as unmeasured rather than scored."
                    ),
                )
            )
            continue

        metrics: ClassificationMetrics = classification_metrics(
            truth, predicted, probability_rows, classes=classes
        )
        analysis.slices.append(
            SliceResult(
                slice_name=name,
                rows=len(indices),
                accuracy=metrics.accuracy,
                balanced_accuracy=metrics.balanced_accuracy,
                macro_f1=metrics.macro_f1,
                log_loss=metrics.log_loss,
            )
        )

    measured = [item for item in analysis.slices if item.measured and item.balanced_accuracy is not None]
    if measured:
        worst = min(measured, key=lambda item: item.balanced_accuracy or 0.0)
        best = max(measured, key=lambda item: item.balanced_accuracy or 0.0)
        analysis.worst_slice = worst.slice_name
        analysis.best_slice = best.slice_name
        analysis.spread = round((best.balanced_accuracy or 0.0) - (worst.balanced_accuracy or 0.0), 4)
        if analysis.spread > 0.15:
            analysis.notes.append(
                f"Balanced accuracy varies by {analysis.spread:.3f} across '{slice_by}' slices: "
                "the aggregate score does not describe every group."
            )
    unmeasured = [item.slice_name for item in analysis.slices if not item.measured]
    if unmeasured:
        analysis.notes.append(
            f"{len(unmeasured)} slice(s) could not be scored for lack of rows: "
            + ", ".join(sorted(unmeasured)[:10])
        )
    return analysis


def misclassified_examples(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    probabilities: Sequence[Sequence[float]] | None,
    *,
    metadata: Sequence[Row] | None = None,
    classes: Sequence[str] | None = None,
    limit: int = 20,
    most_confident_first: bool = True,
) -> list[dict[str, Any]]:
    """The individual mistakes, worst first.

    Ordering by confidence surfaces the *interesting* errors: a confidently wrong
    prediction is a systematic misunderstanding, while a barely-wrong one is the
    noise floor. Reading a confusion matrix cannot distinguish the two.
    """
    resolved_classes = list(classes or sorted(set(y_true) | set(y_pred)))
    order = list(range(len(y_true)))
    if probabilities is not None and most_confident_first:
        def confidence(position: int) -> float:
            predicted = y_pred[position]
            if predicted not in resolved_classes:
                return 0.0
            return float(probabilities[position][resolved_classes.index(predicted)])

        order = sorted(order, key=confidence, reverse=True)

    examples: list[dict[str, Any]] = []
    for position in order:
        if y_true[position] == y_pred[position]:
            continue
        example: dict[str, Any] = {
            "index": position,
            "true": y_true[position],
            "predicted": y_pred[position],
        }
        if probabilities is not None:
            example["probability_of_prediction"] = round(
                float(probabilities[position][resolved_classes.index(y_pred[position])]), 4
            ) if y_pred[position] in resolved_classes else None
        if metadata is not None and position < len(metadata):
            for key, value in metadata[position].items():
                if key not in example:
                    example[key] = value
        examples.append(example)
        if len(examples) >= limit:
            break
    return examples


def permutation_importance(
    model: Any,
    rows: Sequence[Row],
    labels: Sequence[str],
    *,
    feature_names: Sequence[str],
    metric: str = "balanced_accuracy",
    repeats: int = 5,
    seed: int = 42,
) -> dict[str, float]:
    """Model-agnostic importance: how much the score drops when a feature is shuffled.

    Preferred over a model's built-in importances because it is defined for every
    model, including the ones that expose nothing, and because it measures the
    effect on the *task metric* rather than on an internal split criterion.

    Returns *normalised drops*, so 0.0 means "this feature contributes nothing
    measurable" — an honest zero rather than a missing value. Permutations use a
    fixed seed, so the numbers are reproducible.
    """
    if not rows:
        return {}
    rng = np.random.default_rng(seed)
    baseline = _score(model, rows, labels, metric=metric)
    importances: dict[str, float] = {}
    for name in feature_names:
        drops: list[float] = []
        for _ in range(repeats):
            shuffled = list(rows)
            order = rng.permutation(len(shuffled))
            for target, source in enumerate(order):
                row = dict(shuffled[target])
                row[name] = shuffled[int(source)].get(name)
                shuffled[target] = row
            drops.append(baseline - _score(model, shuffled, labels, metric=metric))
        importances[name] = round(float(np.mean(drops)), 6)
    total = sum(abs(value) for value in importances.values())
    if total:
        importances = {key: round(value / total, 6) for key, value in importances.items()}
    return dict(sorted(importances.items(), key=lambda item: -abs(item[1])))


def _score(model: Any, rows: Sequence[Row], labels: Sequence[str], *, metric: str) -> float:
    metrics = model.evaluate(rows, labels)
    value = getattr(metrics, metric, None)
    return float(value) if value is not None else 0.0


__all__ = [
    "MIN_SLICE_ROWS",
    "ErrorAnalysis",
    "SliceResult",
    "misclassified_examples",
    "permutation_importance",
    "slice_metrics",
]
