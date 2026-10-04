"""Probability calibration — is a stated probability actually a probability?

A model that says "0.70" and is right 70% of the time is calibrated; a model
that says "0.70" and is right 40% of the time is *confidently wrong*, and its
number must not be shown to a person as a probability. So every probabilistic
model is checked against observed frequencies, and the result is reported as
measured: reliability curve, expected calibration error, maximum calibration
error, and Brier score.

Two calibrators are provided, and the choice is recorded with the result because
they behave differently at the extremes:

``IsotonicCalibrator``
    Pool-adjacent-violators: monotone, non-parametric, effectively unlimited
    flexibility. Needs enough data per bin, or it overfits.
``PlattCalibrator``
    Logistic rescaling of the score: two parameters, far more stable on small
    validation sets, but it can only fix a sigmoid-shaped miscalibration.

Neither is applied automatically: calibration is fitted on validation data and
evaluated on test data, never fitted on the test data it is measured against.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel, Field

_EPS = 1e-12


class ReliabilityBin(BaseModel):
    """One bin of a reliability diagram."""

    lower: float
    upper: float
    midpoint: float
    count: int
    predicted_mean: float | None = None
    observed_rate: float | None = None

    @property
    def gap(self) -> float | None:
        """Signed miscalibration for this bin (observed − predicted)."""
        if self.predicted_mean is None or self.observed_rate is None:
            return None
        return round(self.observed_rate - self.predicted_mean, 6)


class CalibrationReport(BaseModel):
    """Measured calibration for one probabilistic model."""

    rows: int = 0
    bins: int = 10
    brier_score: float | None = None
    expected_calibration_error: float | None = None
    maximum_calibration_error: float | None = None
    #: Observed positive rate, i.e. the base rate the score must be compared with.
    base_rate: float | None = None
    reliability: list[ReliabilityBin] = Field(default_factory=list)
    calibrator: str | None = None
    calibrated: bool = False
    notes: list[str] = Field(default_factory=list)

    @property
    def is_calibrated(self) -> bool:
        """Below the conventional 0.05 ECE bar, and only when it is measurable."""
        return (
            self.expected_calibration_error is not None
            and self.expected_calibration_error <= 0.05
        )

    def summary(self) -> dict[str, object]:
        return {
            "rows": self.rows,
            "brier_score": self.brier_score,
            "expected_calibration_error": self.expected_calibration_error,
            "maximum_calibration_error": self.maximum_calibration_error,
            "base_rate": self.base_rate,
            "calibrated": self.calibrated,
            "calibrator": self.calibrator,
            "within_0.05_ece": self.is_calibrated,
        }


def reliability_curve(
    y_true_binary: Sequence[int], probabilities: Sequence[float], *, bins: int = 10
) -> list[ReliabilityBin]:
    """Bin predictions by confidence and measure the observed rate in each bin."""
    if bins <= 0:
        raise ValueError("bins must be positive")
    labels = np.asarray(y_true_binary, dtype=float)
    scores = np.asarray(probabilities, dtype=float)
    if len(labels) != len(scores):
        raise ValueError("y_true_binary and probabilities must have the same length")

    edges = np.linspace(0.0, 1.0, bins + 1)
    curve: list[ReliabilityBin] = []
    for position in range(bins):
        lower, upper = float(edges[position]), float(edges[position + 1])
        if position == bins - 1:
            mask = (scores >= lower) & (scores <= upper)
        else:
            mask = (scores >= lower) & (scores < upper)
        count = int(mask.sum())
        curve.append(
            ReliabilityBin(
                lower=round(lower, 6),
                upper=round(upper, 6),
                midpoint=round((lower + upper) / 2, 6),
                count=count,
                predicted_mean=round(float(scores[mask].mean()), 6) if count else None,
                observed_rate=round(float(labels[mask].mean()), 6) if count else None,
            )
        )
    return curve


def expected_calibration_error(curve: Sequence[ReliabilityBin], total: int) -> float | None:
    """Sample-weighted mean |observed − predicted| across the bins."""
    if not total:
        return None
    weighted = 0.0
    used = 0
    for bin_data in curve:
        if bin_data.count and bin_data.gap is not None:
            weighted += bin_data.count * abs(bin_data.gap)
            used += bin_data.count
    if not used:
        return None
    return round(weighted / used, 6)


def maximum_calibration_error(curve: Sequence[ReliabilityBin]) -> float | None:
    """The worst bin gap — what a user would notice most."""
    gaps = [abs(bin_data.gap) for bin_data in curve if bin_data.gap is not None]
    return round(max(gaps), 6) if gaps else None


def brier_score_binary(y_true_binary: Sequence[int], probabilities: Sequence[float]) -> float | None:
    """Mean squared error of a binary probability."""
    if not len(y_true_binary):
        return None
    labels = np.asarray(y_true_binary, dtype=float)
    scores = np.asarray(probabilities, dtype=float)
    return float(np.mean((scores - labels) ** 2))


def evaluate_calibration(
    y_true_binary: Sequence[int],
    probabilities: Sequence[float],
    *,
    bins: int = 10,
    calibrator: str | None = None,
    calibrated: bool = False,
) -> CalibrationReport:
    """The full calibration verdict for a binary score."""
    labels = np.asarray(y_true_binary, dtype=int)
    scores = np.asarray(probabilities, dtype=float)
    if len(labels) != len(scores):
        raise ValueError("y_true_binary and probabilities must have the same length")

    report = CalibrationReport(rows=int(len(labels)), bins=bins, calibrator=calibrator, calibrated=calibrated)
    if not len(labels):
        report.notes.append("No rows: calibration is not measurable on an empty split.")
        return report

    report.base_rate = round(float(labels.mean()), 6)
    report.brier_score = round(brier_score_binary(labels, scores), 6) if report.brier_score is None else report.brier_score
    curve = reliability_curve(labels, scores, bins=bins)
    report.reliability = curve
    report.expected_calibration_error = expected_calibration_error(curve, len(labels))
    report.maximum_calibration_error = maximum_calibration_error(curve)

    positives = int(labels.sum())
    negatives = int(len(labels) - positives)
    if positives == 0 or negatives == 0:
        report.notes.append(
            "Only one class is present, so calibration cannot be established: a "
            "reliability curve needs both outcomes to compare against."
        )
    thin = [bin_data for bin_data in curve if bin_data.count and bin_data.count < 20]
    if thin:
        report.notes.append(
            f"{len(thin)} of {bins} bins hold fewer than 20 predictions, so the curve is "
            "noisy there and ECE should be read with that in mind."
        )
    if report.maximum_calibration_error is not None and report.maximum_calibration_error > 0.15:
        report.notes.append(
            f"Worst bin is off by {report.maximum_calibration_error:.2f}: at that "
            "confidence the stated probability should not be shown to a user as a "
            "probability without calibration."
        )
    return report


def evaluate_multiclass_calibration(
    y_true: Sequence[str],
    probabilities: Sequence[Sequence[float]],
    classes: Sequence[str],
    *,
    bins: int = 10,
    calibrated: bool = False,
    calibrator: str | None = None,
) -> CalibrationReport:
    """Top-label (confidence) calibration for a multiclass model.

    The standard multiclass reading: for each prediction, take the model's
    confidence in its top class, and check whether predictions made at that
    confidence are right that often. A well-calibrated 3-class model says 0.6
    and is right about 60% of the time.

    Class-wise one-vs-rest calibration is a different (also valid) question and
    is not silently substituted for this one.
    """
    if len(y_true) != len(probabilities):
        raise ValueError("y_true and probabilities must have the same length")
    confidence: list[float] = []
    correct: list[int] = []
    for truth, row in zip(y_true, probabilities, strict=True):
        if not len(row):
            continue
        index = int(np.argmax(row))
        confidence.append(float(row[index]))
        correct.append(1 if classes[index] == truth else 0)

    report = evaluate_calibration(
        correct, confidence, bins=bins, calibrator=calibrator, calibrated=calibrated
    )
    report.notes.insert(
        0,
        "Top-label calibration: the x-axis is the model's confidence in its own "
        "prediction, not the probability of a named class.",
    )
    return report


# --- calibrators --------------------------------------------------------------


class IsotonicCalibrator(BaseModel):
    """Pool-adjacent-violators isotonic regression, implemented directly."""

    x: list[float] = Field(default_factory=list)
    y: list[float] = Field(default_factory=list)
    fitted: bool = False

    def fit(self, scores: Sequence[float], y_true_binary: Sequence[int]) -> "IsotonicCalibrator":
        values = np.asarray(scores, dtype=float)
        labels = np.asarray(y_true_binary, dtype=float)
        if len(values) != len(labels):
            raise ValueError("scores and y_true_binary must have the same length")
        if not len(values):
            raise ValueError("Cannot fit an isotonic calibrator on zero rows")

        order = np.argsort(values, kind="mergesort")
        sorted_values = values[order]
        sorted_labels = labels[order]

        # Pool adjacent violators, block form: append the next observation as its
        # own block, then keep merging the two rightmost blocks while the mean
        # would decrease. Each block records where it starts so the fitted
        # x-value is the mean score over the observations it pooled.
        weights: list[float] = []
        totals: list[float] = []
        starts: list[int] = []
        for position, label in enumerate(sorted_labels):
            weights.append(1.0)
            totals.append(float(label))
            starts.append(position)
            while len(weights) > 1 and (totals[-2] / weights[-2]) > (totals[-1] / weights[-1]):
                merged_weight = weights.pop() + weights.pop()
                merged_total = totals.pop() + totals.pop()
                starts.pop()  # the right block's start is subsumed
                starts.append(starts.pop())  # keep the left block's start
                weights.append(merged_weight)
                totals.append(merged_total)

        fitted_x: list[float] = []
        fitted_y: list[float] = []
        for index, (weight, total, start) in enumerate(zip(weights, totals, starts, strict=True)):
            end = starts[index + 1] if index + 1 < len(starts) else len(sorted_values)
            fitted_x.append(float(sorted_values[start:end].mean()))
            fitted_y.append(float(total / weight))

        self.x = fitted_x
        self.y = fitted_y
        self.fitted = True
        return self

    def transform(self, scores: Sequence[float]) -> list[float]:
        """Interpolate the fitted monotone mapping (clamped at both ends)."""
        if not self.fitted:
            raise ValueError("IsotonicCalibrator must be fitted before transform")
        values = np.asarray(scores, dtype=float)
        knots_x = np.asarray(self.x, dtype=float)
        knots_y = np.asarray(self.y, dtype=float)
        if len(knots_x) == 1:
            return [float(knots_y[0])] * len(values)
        interpolated = np.interp(values, knots_x, knots_y, left=float(knots_y[0]), right=float(knots_y[-1]))
        return [float(np.clip(value, _EPS, 1 - _EPS)) for value in interpolated]

    def fit_transform(self, scores: Sequence[float], y_true_binary: Sequence[int]) -> list[float]:
        return self.fit(scores, y_true_binary).transform(scores)


class PlattCalibrator(BaseModel):
    """Logistic (Platt) rescaling: ``y = sigmoid(a * logit(p) + b)``."""

    a: float = 1.0
    b: float = 0.0
    fitted: bool = False
    iterations: int = 0

    @staticmethod
    def _logit(scores: np.ndarray) -> np.ndarray:
        clipped = np.clip(scores, _EPS, 1 - _EPS)
        return np.log(clipped / (1 - clipped))

    def fit(
        self,
        scores: Sequence[float],
        y_true_binary: Sequence[int],
        *,
        iterations: int = 200,
        learning_rate: float = 0.5,
    ) -> "PlattCalibrator":
        values = np.asarray(scores, dtype=float)
        labels = np.asarray(y_true_binary, dtype=float)
        if len(values) != len(labels):
            raise ValueError("scores and y_true_binary must have the same length")
        if not len(values):
            raise ValueError("Cannot fit a Platt calibrator on zero rows")

        logits = self._logit(values)
        a, b = 1.0, 0.0
        # Plain gradient descent on the log loss: two parameters, so this is
        # stable, dependency-free and deterministic.
        for step in range(iterations):
            linear = a * logits + b
            predicted = 1.0 / (1.0 + np.exp(-np.clip(linear, -30, 30)))
            error = predicted - labels
            grad_a = float(np.mean(error * logits))
            grad_b = float(np.mean(error))
            a -= learning_rate * grad_a
            b -= learning_rate * grad_b
            self.iterations = step + 1
        self.a, self.b, self.fitted = float(a), float(b), True
        return self

    def transform(self, scores: Sequence[float]) -> list[float]:
        if not self.fitted:
            raise ValueError("PlattCalibrator must be fitted before transform")
        logits = self._logit(np.asarray(scores, dtype=float))
        linear = np.clip(self.a * logits + self.b, -30, 30)
        return [float(value) for value in 1.0 / (1.0 + np.exp(-linear))]

    def fit_transform(self, scores: Sequence[float], y_true_binary: Sequence[int]) -> list[float]:
        return self.fit(scores, y_true_binary).transform(scores)


def calibrate_and_evaluate(
    probabilities_validation: Sequence[float],
    y_validation: Sequence[int],
    probabilities_test: Sequence[float],
    y_test: Sequence[int],
    *,
    method: str = "isotonic",
    bins: int = 10,
) -> tuple[CalibrationReport, list[float]]:
    """Fit a calibrator on validation data and measure it on test data.

    Fitting and measuring on the same rows would report a calibration that does
    not exist, so the two sets are separate arguments and cannot be confused.
    """
    if method == "isotonic":
        calibrator: IsotonicCalibrator | PlattCalibrator = IsotonicCalibrator().fit(
            probabilities_validation, y_validation
        )
    elif method == "platt":
        calibrator = PlattCalibrator().fit(probabilities_validation, y_validation)
    else:
        raise ValueError(f"Unknown calibration method {method!r}; use 'isotonic' or 'platt'")

    calibrated_test = calibrator.transform(probabilities_test)
    report = evaluate_calibration(
        y_test, calibrated_test, bins=bins, calibrator=method, calibrated=True
    )
    report.notes.append(
        f"The {method} calibrator was fitted on {len(probabilities_validation)} validation "
        f"prediction(s) and evaluated on {len(probabilities_test)} test prediction(s)."
    )
    return report, calibrated_test


__all__ = [
    "CalibrationReport",
    "IsotonicCalibrator",
    "PlattCalibrator",
    "ReliabilityBin",
    "brier_score_binary",
    "calibrate_and_evaluate",
    "evaluate_calibration",
    "evaluate_multiclass_calibration",
    "expected_calibration_error",
    "maximum_calibration_error",
    "reliability_curve",
]
