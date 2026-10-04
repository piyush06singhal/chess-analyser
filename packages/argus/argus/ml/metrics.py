"""Evaluation metrics — task-appropriate, and never accuracy alone.

Two conventions are load-bearing:

*Class order is explicit.* Every metric takes the class list. A metric computed
against an inferred class order can silently swap two classes and report a fine
score, and a model's class axis must mean the same thing forever.

*Missing metrics are ``None``, not zero.* ``roc_auc`` for a class that has no
positives is undefined, not 0.0 — reporting 0.0 would be a fabricated bad score,
and reporting 1.0 would be a fabricated good one. ``None`` says "not measurable
here", which is the truth.

Implemented with the standard library and NumPy only, so the numbers are the
same whether or not scikit-learn is installed.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel, Field

#: Probabilities are clipped before a log, so a confident wrong answer yields a
#: large finite loss instead of an infinity.
_EPS = 1e-12


class ClassificationMetrics(BaseModel):
    """Measured classification performance on one held-out split."""

    rows: int = 0
    classes: list[str] = Field(default_factory=list)
    accuracy: float | None = None
    balanced_accuracy: float | None = None
    macro_f1: float | None = None
    micro_f1: float | None = None
    weighted_f1: float | None = None
    log_loss: float | None = None
    brier_score: float | None = None
    per_class: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    confusion_matrix: list[list[int]] = Field(default_factory=list)
    #: Present only for binary framings (one class vs the rest).
    roc_auc: float | None = None
    average_precision: float | None = None
    positive_class: str | None = None
    notes: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict[str, float | None]:
        """The headline metrics, as a flat mapping."""
        return {
            "accuracy": self.accuracy,
            "balanced_accuracy": self.balanced_accuracy,
            "macro_f1": self.macro_f1,
            "log_loss": self.log_loss,
            "brier_score": self.brier_score,
            "roc_auc": self.roc_auc,
            "average_precision": self.average_precision,
        }

    def summary(self) -> dict[str, object]:
        return {
            "rows": self.rows,
            "classes": self.classes,
            **self.as_dict(),
            "per_class_f1": {
                name: values.get("f1") for name, values in self.per_class.items()
            },
        }


def _as_matrix(proba: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
    matrix = np.asarray(proba, dtype=float)
    if matrix.ndim == 1:
        matrix = np.column_stack([1.0 - matrix, matrix])
    return matrix


def confusion_matrix(
    y_true: Sequence[str], y_pred: Sequence[str], classes: Sequence[str]
) -> list[list[int]]:
    """Rows are truth, columns are prediction, in the given class order."""
    index = {name: position for position, name in enumerate(classes)}
    matrix = np.zeros((len(classes), len(classes)), dtype=int)
    for truth, prediction in zip(y_true, y_pred, strict=True):
        if truth in index and prediction in index:
            matrix[index[truth], index[prediction]] += 1
    return matrix.tolist()


def per_class_metrics(
    y_true: Sequence[str], y_pred: Sequence[str], classes: Sequence[str]
) -> dict[str, dict[str, float | None]]:
    """Precision/recall/F1/support per class, with ``None`` where undefined."""
    matrix = np.asarray(confusion_matrix(y_true, y_pred, classes), dtype=float)
    totals = matrix.sum()
    result: dict[str, dict[str, float | None]] = {}
    for position, name in enumerate(classes):
        true_positive = matrix[position, position]
        predicted = matrix[:, position].sum()
        actual = matrix[position, :].sum()
        precision = float(true_positive / predicted) if predicted else None
        recall = float(true_positive / actual) if actual else None
        if precision is None or recall is None or (precision + recall) == 0:
            f1 = None if (precision is None or recall is None) else 0.0
        else:
            f1 = float(2 * precision * recall / (precision + recall))
        result[name] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(actual),
            "share": float(actual / totals) if totals else None,
        }
    return result


def log_loss(
    y_true: Sequence[str], proba: Sequence[Sequence[float]], classes: Sequence[str]
) -> float | None:
    """Multiclass log loss (cross-entropy), lower is better."""
    if not len(y_true):
        return None
    matrix = _as_matrix(proba)
    index = {name: position for position, name in enumerate(classes)}
    picked = np.array(
        [matrix[row, index.get(truth, 0)] if truth in index else _EPS for row, truth in enumerate(y_true)]
    )
    return float(-np.mean(np.log(np.clip(picked, _EPS, 1.0))))


def brier_score(
    y_true: Sequence[str], proba: Sequence[Sequence[float]], classes: Sequence[str]
) -> float | None:
    """Multiclass Brier score: mean squared error of the probability vector.

    Both a scoring rule and a calibration-sensitive measure, which is why it is
    reported next to log loss rather than instead of it.
    """
    if not len(y_true):
        return None
    matrix = _as_matrix(proba)
    index = {name: position for position, name in enumerate(classes)}
    one_hot = np.zeros_like(matrix)
    for row, truth in enumerate(y_true):
        if truth in index:
            one_hot[row, index[truth]] = 1.0
    return float(np.mean(np.sum((matrix - one_hot) ** 2, axis=1)))


def roc_auc_binary(y_true_binary: Sequence[int], scores: Sequence[float]) -> float | None:
    """ROC-AUC via the rank (Mann–Whitney U) identity — no thresholds needed.

    ``None`` when one class is absent: AUC is undefined, not 0.5 and not 1.0.
    """
    labels = np.asarray(y_true_binary, dtype=int)
    values = np.asarray(scores, dtype=float)
    positives = int(labels.sum())
    negatives = int(len(labels) - positives)
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(1, len(values) + 1, dtype=float)
    # Average ranks inside ties, so tied scores cannot inflate or deflate the AUC.
    sorted_values = values[order]
    start = 0
    for position in range(1, len(sorted_values) + 1):
        if position == len(sorted_values) or sorted_values[position] != sorted_values[start]:
            if position - start > 1:
                ranks[order[start:position]] = ranks[order[start:position]].mean()
            start = position
    rank_sum = ranks[labels == 1].sum()
    return float((rank_sum - positives * (positives + 1) / 2) / (positives * negatives))


def average_precision_binary(
    y_true_binary: Sequence[int], scores: Sequence[float]
) -> float | None:
    """PR-AUC as average precision (step-wise, tie-aware).

    Preferred over ROC-AUC whenever the positive class is rare, which is the case
    for every error-risk task here.
    """
    labels = np.asarray(y_true_binary, dtype=int)
    values = np.asarray(scores, dtype=float)
    positives = int(labels.sum())
    if positives == 0:
        return None
    order = np.argsort(-values, kind="mergesort")
    sorted_labels = labels[order]
    sorted_values = values[order]
    cumulative_tp = np.cumsum(sorted_labels)
    ranks = np.arange(1, len(sorted_labels) + 1)
    precision = cumulative_tp / ranks
    total = 0.0
    previous_recall = 0.0
    position = 0
    while position < len(sorted_values):
        end = position
        while end + 1 < len(sorted_values) and sorted_values[end + 1] == sorted_values[position]:
            end += 1
        recall = cumulative_tp[end] / positives
        total += float(precision[end]) * (recall - previous_recall)
        previous_recall = recall
        position = end + 1
    return float(total)


def classification_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    proba: Sequence[Sequence[float]] | None = None,
    *,
    classes: Sequence[str] | None = None,
    positive_class: str | None = None,
    binary: bool = False,
) -> ClassificationMetrics:
    """Compute every metric that is actually defined for this data.

    ``binary`` narrows the framing to ``positive_class`` vs the rest and is what
    the error-risk tasks report, because a probability for "this move will be an
    error" is the thing the product would ever show.
    """
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have the same length")
    resolved = list(classes) if classes else sorted(set(y_true) | set(y_pred))
    metrics = ClassificationMetrics(rows=len(y_true), classes=resolved)

    if not y_true:
        metrics.notes.append("No rows: nothing is measurable on an empty split.")
        return metrics

    matrix = np.asarray(confusion_matrix(y_true, y_pred, resolved), dtype=float)
    metrics.confusion_matrix = matrix.astype(int).tolist()
    metrics.accuracy = float(np.trace(matrix) / matrix.sum())
    per_class = per_class_metrics(y_true, y_pred, resolved)
    metrics.per_class = per_class

    # Macro averages cover every class that is actually present in the split. A
    # class the model never predicts contributes 0 rather than being dropped:
    # dropping it is what makes a majority-class baseline look respectable, and
    # that is precisely the mistake macro F1 exists to prevent.
    present = [values for values in per_class.values() if (values["support"] or 0.0) > 0]
    recalls = [values["recall"] for values in present if values["recall"] is not None]
    f1s = [values["f1"] or 0.0 for values in present]
    supports = [values["support"] or 0.0 for values in per_class.values()]
    metrics.balanced_accuracy = float(np.mean(recalls)) if recalls else None
    metrics.macro_f1 = float(np.mean(f1s)) if f1s else None
    total_support = sum(supports)
    if total_support and f1s:
        metrics.weighted_f1 = float(
            sum(
                (values["f1"] or 0.0) * (values["support"] or 0.0)
                for values in per_class.values()
            )
            / total_support
        )
    metrics.micro_f1 = metrics.accuracy

    missing_classes = [name for name, values in per_class.items() if not values["support"]]
    if missing_classes:
        metrics.notes.append(
            "Classes with no examples in this split (their precision/recall are not "
            f"measurable): {', '.join(sorted(missing_classes))}"
        )
    if metrics.accuracy < 0.5:
        metrics.notes.append(
            "Accuracy is below 0.5: report the majority-class baseline next to this "
            "model, because it may have learned nothing."
        )

    if proba is not None:
        metrics.log_loss = log_loss(y_true, proba, resolved)
        metrics.brier_score = brier_score(y_true, proba, resolved)

    if binary:
        resolved_positive = positive_class or resolved[-1]
        metrics.positive_class = resolved_positive
        binary_truth = [1 if value == resolved_positive else 0 for value in y_true]
        scores = None
        if proba is not None:
            position = resolved.index(resolved_positive) if resolved_positive in resolved else -1
            if position >= 0:
                scores = np.asarray(_as_matrix(proba), dtype=float)[:, position].tolist()
        if scores is None:
            # Fall back to hard predictions: a thresholded score produces a rank
            # ordering too, and it is honest to say which was used.
            scores = [1.0 if value == resolved_positive else 0.0 for value in y_pred]
            metrics.notes.append(
                "ROC-AUC/PR-AUC were computed from hard predictions because no "
                "probabilities were supplied."
            )
        metrics.roc_auc = roc_auc_binary(binary_truth, scores)
        metrics.average_precision = average_precision_binary(binary_truth, scores)
        if metrics.roc_auc is None:
            metrics.notes.append(
                f"ROC-AUC is undefined here (no examples of both classes for "
                f"'{resolved_positive}' vs the rest) and is reported as null."
            )

    return metrics


def majority_class_baseline(y_true: Sequence[str]) -> ClassificationMetrics:
    """The score any model must beat: always predict the commonest class."""
    if not y_true:
        return ClassificationMetrics(rows=0, notes=["No rows to compute a baseline from."])
    counts: dict[str, int] = {}
    for value in y_true:
        counts[value] = counts.get(value, 0) + 1
    majority = max(counts, key=lambda key: (counts[key], key))
    predictions = [majority for _ in y_true]
    metrics = classification_metrics(y_true, predictions, classes=sorted(counts))
    metrics.notes.append(
        f"Always predicts '{majority}' ({counts[majority]}/{len(y_true)} rows). "
        "Macro F1 and balanced accuracy are the honest comparison for an imbalanced "
        "label, because raw accuracy flatters this baseline."
    )
    return metrics


def rating_baseline_reference(y_true: Sequence[str]) -> dict[str, float | None]:
    """The class distribution a rating-only model is trying to improve on."""
    if not y_true:
        return {}
    counts: dict[str, int] = {}
    for value in y_true:
        counts[value] = counts.get(value, 0) + 1
    total = len(y_true)
    return {value: round(count / total, 4) for value, count in sorted(counts.items())}


__all__ = [
    "ClassificationMetrics",
    "average_precision_binary",
    "brier_score",
    "classification_metrics",
    "confusion_matrix",
    "log_loss",
    "majority_class_baseline",
    "per_class_metrics",
    "rating_baseline_reference",
    "roc_auc_binary",
]
