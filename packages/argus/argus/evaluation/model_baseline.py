"""Stored model-version baselines (§44).

Analysis numbers are pinned to a Stockfish build by an engine baseline (§45); a
*model's* numbers need the same treatment for the same reason. Once a model is
promoted, the metrics it qualified on become the thing every future candidate is
measured against — and a baseline that lives only in a chat log is not a
baseline.

This is the runnable half of §44. :mod:`argus.evaluation.model_regression`
already decides whether a candidate beats production; this module *records what
production is*, so that decision has something real to compare against.

Two facts are kept apart, exactly as in the engine baseline:

* **No production model.** There is nothing to baseline, and the honest answer is
  ``None`` — not an empty record, and never a fabricated one. The recorder script
  says so and exits non-zero.

* **A stored baseline that a measurement disagrees with.** That is a real
  disagreement, and it is reported, never rounded away.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.evaluation.engine_baseline import DEFAULT_BASELINE_DIR
from argus.evaluation.model_regression import (
    ModelComparison,
    ModelEvaluation,
    compare_models,
)
from argus.ml.registry import ModelRegistry, RegisteredModel
from argus.shared.errors import ValidationError

#: The metric keys a model baseline pins, in the order they are written. Both
#: directions live here on purpose: the lower-is-better keys are exactly the ones
#: a promotion argument is most likely to leave out.
BASELINE_METRICS: tuple[str, ...] = (
    "balanced_accuracy",
    "macro_f1",
    "accuracy",
    "log_loss",
    "expected_calibration_error",
)


def _slug(value: str) -> str:
    """A filesystem-safe form of a task or dataset version."""
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "unknown"


@dataclass
class ModelBaseline:
    """The metrics a model was baselined at, on a named benchmark.

    Every value here was measured. A missing metric stays missing — it is never
    imputed with a zero, which for a lower-is-better metric would read as a
    perfect score.
    """

    task: str
    model_id: str
    dataset_version: str
    feature_version: str = "unknown"
    metrics: dict[str, float] = field(default_factory=dict)
    subgroups: dict[str, dict[str, float]] = field(default_factory=dict)
    recorded_at: str = ""
    note: str = ""

    def to_evaluation(self) -> ModelEvaluation:
        """The baseline as the comparison's :class:`ModelEvaluation` input."""
        return ModelEvaluation(
            model_version=self.model_id,
            dataset_version=self.dataset_version,
            feature_version=self.feature_version,
            metrics=dict(self.metrics),
            subgroups={group: dict(values) for group, values in self.subgroups.items()},
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "model_id": self.model_id,
            "dataset_version": self.dataset_version,
            "feature_version": self.feature_version,
            "metrics": dict(self.metrics),
            "subgroups": {group: dict(values) for group, values in self.subgroups.items()},
            "recorded_at": self.recorded_at,
            "note": self.note,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ModelBaseline":
        return cls(
            task=str(payload["task"]),
            model_id=str(payload["model_id"]),
            dataset_version=str(payload["dataset_version"]),
            feature_version=str(payload.get("feature_version", "unknown")),
            metrics={key: float(value) for key, value in dict(payload.get("metrics") or {}).items()},
            subgroups={
                str(group): {key: float(value) for key, value in dict(values).items()}
                for group, values in dict(payload.get("subgroups") or {}).items()
            },
            recorded_at=str(payload.get("recorded_at", "")),
            note=str(payload.get("note", "")),
        )


def model_baseline_path(task: str, dataset_version: str, *, directory: Path | None = None) -> Path:
    """Where the baseline for a task and dataset version lives.

    The dataset version is part of the filename because a comparison between
    models measured on different benchmarks is not valid, and the path makes
    that visible before anything is loaded.
    """
    base = directory if directory is not None else DEFAULT_BASELINE_DIR
    return base / f"model-{_slug(task)}-{_slug(dataset_version)}.json"


def load_model_baseline(
    task: str, dataset_version: str, *, directory: Path | None = None
) -> ModelBaseline | None:
    """The stored baseline for a task, or ``None`` when none is recorded."""
    path = model_baseline_path(task, dataset_version, directory=directory)
    if not path.is_file():
        return None
    return ModelBaseline.from_payload(json.loads(path.read_text(encoding="utf-8")))


def save_model_baseline(
    baseline: ModelBaseline, *, directory: Path | None = None, force: bool = False
) -> Path:
    """Write a baseline, refusing to replace an existing one by accident.

    Raises:
        ValidationError: when a baseline already exists and ``force`` is not set.
    """
    path = model_baseline_path(baseline.task, baseline.dataset_version, directory=directory)
    if path.exists() and not force:
        raise ValidationError(
            f"A baseline already exists at {path}. A baseline is a commitment: "
            "review the change and re-run with force=True if it is deliberate."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(baseline.to_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def metrics_from_registered(model: RegisteredModel) -> dict[str, float]:
    """The measured metrics of a registered model, with nothing invented.

    Only the keys in :data:`BASELINE_METRICS` are kept: those are the numbers the
    comparison is defined over, and restricting to them keeps a boolean flag such
    as ``calibrated`` (which the registry stores as ``1.0``) out of a numeric
    baseline. A metric that was not measured (``None``) is left out rather than
    coerced to ``0.0``, which for a lower-is-better metric would read as perfect.
    """
    values: dict[str, float] = {}
    for source in (model.metrics, model.calibration_metrics):
        for key, value in source.items():
            if str(key) not in BASELINE_METRICS:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            values[str(key)] = float(value)
    return values


def _leakage_from_gate(model: RegisteredModel) -> bool | None:
    """Whether the recorded gate decision passed its leakage check.

    ``None`` when no gate was recorded: an unmeasured property must not be read
    as a pass, the same rule the production gate itself applies.
    """
    gate = model.gate or {}
    for check in gate.get("checks") or []:
        if isinstance(check, dict) and check.get("name") == "leakage_checks":
            return bool(check.get("passed"))
    return None


def evaluation_from_registered(model: RegisteredModel) -> ModelEvaluation:
    """Turn a registered model's recorded evidence into a comparison input."""
    return ModelEvaluation(
        model_version=model.model_id,
        dataset_version=model.dataset_version or "unknown",
        feature_version=model.feature_version or "unknown",
        metrics=metrics_from_registered(model),
        leakage_passed=_leakage_from_gate(model),
        reproducible=bool(model.feature_version and model.dataset_version),
    )


def production_model(registry: ModelRegistry, task: str) -> RegisteredModel | None:
    """The task's production model, or ``None`` when none is promoted."""
    for model in registry.production(task):
        return model
    return None


def record_model_baseline(
    registry: ModelRegistry,
    task: str,
    *,
    directory: Path | None = None,
    force: bool = False,
) -> ModelBaseline:
    """Baseline the task's production model from its recorded evidence.

    This is the entry point the recorder script calls. It refuses — with a
    reason, never a placeholder — when there is no model in production for the
    task.

    Raises:
        ValidationError: when no model is in production for the task, or the
            production model carries no measured metrics to baseline.
    """
    model = production_model(registry, task)
    if model is None:
        raise ValidationError(
            f"No model is in production for '{task}', so there is nothing to "
            "baseline. Promote a model that passed its gate first — a baseline "
            "is not invented."
        )

    metrics = metrics_from_registered(model)
    if not metrics:
        raise ValidationError(
            f"The production model {model.model_id} has no measured metrics "
            "recorded, so a baseline would be empty. Measure it before baselining."
        )

    baseline = ModelBaseline(
        task=task,
        model_id=model.model_id,
        dataset_version=model.dataset_version or "unknown",
        feature_version=model.feature_version or "unknown",
        metrics=metrics,
        recorded_at=datetime.now(timezone.utc).isoformat(),
        note=(
            "Recorded from the production model's registered evidence. "
            "Future candidates are compared against these numbers."
        ),
    )
    save_model_baseline(baseline, directory=directory, force=force)
    return baseline


def compare_candidate_to_baseline(
    baseline: ModelBaseline, candidate: ModelEvaluation, *, tolerance: float = 0.0
) -> ModelComparison:
    """Compare a candidate against a stored baseline on the same benchmark."""
    return compare_models(baseline.to_evaluation(), candidate, tolerance=tolerance)


__all__ = [
    "BASELINE_METRICS",
    "ModelBaseline",
    "compare_candidate_to_baseline",
    "evaluation_from_registered",
    "load_model_baseline",
    "metrics_from_registered",
    "model_baseline_path",
    "production_model",
    "record_model_baseline",
    "save_model_baseline",
]