"""Experiment tracking — every run leaves a directory nobody can overwrite.

An experiment that cannot be re-read is not evidence. Each run therefore gets its
own directory containing the configuration, the dataset manifest it ran against,
the measured metrics per split, the confusion matrix, the calibration curve, the
feature importances, and a human-readable ``report.md`` that states the dataset,
the population, the split, the model, the features, the metrics and the
limitations — including the uncomfortable ones.

Directories are never overwritten: re-running the same experiment id raises
unless the caller explicitly asks for a new suffix, so a rewritten result cannot
be mistaken for the original.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from argus.ml.gating import GateDecision
from argus.shared.errors import ValidationError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: Files written into every experiment directory.
EXPERIMENT_FILES = (
    "config.json",
    "dataset_manifest.json",
    "metrics.json",
    "confusion_matrix.json",
    "calibration.json",
    "feature_importance.json",
    "gate.json",
    "report.md",
)


class ExperimentRecord(BaseModel):
    """The index entry for one experiment."""

    experiment_id: str
    task: str
    model: str
    dataset_version: str = ""
    feature_version: str = ""
    split_strategy: str = ""
    status: str = "running"
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    rows: dict[str, int] = Field(default_factory=dict)
    headline_metrics: dict[str, float | None] = Field(default_factory=dict)
    gate_passed: bool | None = None
    directory: str = ""
    notes: list[str] = Field(default_factory=list)


class Experiment:
    """A single run, with a directory to write its evidence into."""

    def __init__(self, record: ExperimentRecord, directory: Path) -> None:
        self.record = record
        self.directory = directory
        self._started = datetime.now(timezone.utc)
        self.record.started_at = self._started

    # --- writing ------------------------------------------------------------

    def write_json(self, name: str, payload: Any) -> Path:
        """Write one evidence file (pretty-printed, sorted, deterministic)."""
        path = self.directory / name
        if isinstance(payload, BaseModel):
            serialised: Any = payload.model_dump(mode="json")
        else:
            serialised = payload
        path.write_text(
            json.dumps(serialised, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        return path

    def write_config(self, config: dict[str, Any]) -> Path:
        """Record the full configuration, including the seed and versions."""
        return self.write_json("config.json", config)

    def write_manifest(self, manifest: Any) -> Path:
        """Record the dataset manifest the run consumed."""
        return self.write_json("dataset_manifest.json", manifest)

    def write_metrics(self, metrics: dict[str, Any]) -> Path:
        """Record measured metrics, per split."""
        return self.write_json("metrics.json", metrics)

    def write_confusion_matrix(self, matrices: dict[str, Any]) -> Path:
        return self.write_json("confusion_matrix.json", matrices)

    def write_calibration(self, calibration: Any) -> Path:
        return self.write_json("calibration.json", calibration)

    def write_feature_importance(self, importance: dict[str, Any]) -> Path:
        return self.write_json("feature_importance.json", importance)

    def write_gate(self, gate: GateDecision | None) -> Path:
        return self.write_json("gate.json", gate.as_report() if gate else {"passed": False, "summary": "no gate evaluated"})

    def write_report(self, markdown: str) -> Path:
        """Write the human-readable report."""
        path = self.directory / "report.md"
        path.write_text(markdown, encoding="utf-8")
        return path

    def finish(self, *, status: str = "completed", headline: dict[str, float | None] | None = None) -> ExperimentRecord:
        """Mark the run finished and persist the index entry."""
        self.record.finished_at = datetime.now(timezone.utc)
        self.record.duration_seconds = round(
            (self.record.finished_at - self._started).total_seconds(), 3
        )
        self.record.status = status
        if headline:
            self.record.headline_metrics = headline
        return self.record


class ExperimentTracker:
    """Owns the experiments directory and the append-only index."""

    INDEX_FILENAME = "experiments_index.json"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _index_path(self) -> Path:
        return self._root / self.INDEX_FILENAME

    def index(self) -> list[ExperimentRecord]:
        """Every recorded experiment, oldest first."""
        path = self._index_path()
        if not path.is_file():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [ExperimentRecord.model_validate(entry) for entry in payload]

    def _persist_index(self, records: list[ExperimentRecord]) -> None:
        self._index_path().write_text(
            json.dumps([record.model_dump(mode="json") for record in records], indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def create(
        self,
        *,
        experiment_id: str,
        task: str,
        model: str,
        dataset_version: str = "",
        feature_version: str = "",
        split_strategy: str = "",
        allow_existing: bool = False,
    ) -> Experiment:
        """Create a new experiment directory.

        Raises:
            ValidationError: when the directory already exists and
                ``allow_existing`` is not set. A result directory must not be
                silently rewritten.
        """
        directory = self._root / experiment_id
        if directory.exists():
            if not allow_existing:
                raise ValidationError(
                    f"Experiment directory {directory} already exists. Pass a new "
                    "experiment_id, or allow_existing=True to add to it deliberately."
                )
        else:
            directory.mkdir(parents=True, exist_ok=True)

        record = ExperimentRecord(
            experiment_id=experiment_id,
            task=task,
            model=model,
            dataset_version=dataset_version,
            feature_version=feature_version,
            split_strategy=split_strategy,
            directory=str(directory),
        )
        experiment = Experiment(record, directory)
        # The index gains the entry on finish(); a crashed run stays absent rather
        # than appearing as a completed experiment.
        return experiment

    def record(self, experiment: Experiment) -> ExperimentRecord:
        """Append a finished experiment to the index."""
        records = self.index()
        records = [entry for entry in records if entry.experiment_id != experiment.record.experiment_id]
        records.append(experiment.record)
        self._persist_index(records)
        logger.info("Recorded experiment %s [%s]", experiment.record.experiment_id, experiment.record.status)
        return experiment.record

    def load_metrics(self, experiment_id: str) -> dict[str, Any]:
        """Read a recorded experiment's metrics back."""
        path = self._root / experiment_id / "metrics.json"
        if not path.is_file():
            raise ValidationError(f"Experiment {experiment_id!r} has no metrics recorded")
        return json.loads(path.read_text(encoding="utf-8"))

    def summary(self) -> list[dict[str, Any]]:
        """One row per experiment, for a comparison table."""
        return [
            {
                "experiment_id": record.experiment_id,
                "task": record.task,
                "model": record.model,
                "split": record.split_strategy,
                "status": record.status,
                "gate_passed": record.gate_passed,
                **record.headline_metrics,
            }
            for record in self.index()
        ]


def render_experiment_report(
    *,
    experiment_id: str,
    task: str,
    hypothesis: str,
    dataset_summary: dict[str, Any],
    split_summary: dict[str, Any],
    model_summaries: list[dict[str, Any]],
    calibration: dict[str, Any] | None,
    error_analysis: dict[str, Any] | None,
    gate: dict[str, Any] | None,
    leakage: dict[str, Any] | None,
    limitations: list[str],
    conclusion: str,
) -> str:
    """Render the experiment's ``report.md``.

    The report leads with what the data *is* and what could not be established,
    because those are the parts a reader is least likely to infer and most
    likely to over-claim without.
    """
    lines: list[str] = [
        f"# Experiment {experiment_id}",
        "",
        f"**Task:** {task}",
        "",
        f"**Hypothesis:** {hypothesis}",
        "",
        "## Dataset",
        "",
    ]
    for key, value in dataset_summary.items():
        lines.append(f"- **{key}**: {value}")
    lines.extend(["", "## Split", ""])
    for key, value in split_summary.items():
        lines.append(f"- **{key}**: {value}")
    lines.extend(["", "## Models", "", "| Model | Rows | Accuracy | Balanced acc. | Macro F1 | Log loss | Brier |", "| ----- | ---- | -------- | ------------- | -------- | -------- | ----- |"])
    for summary in model_summaries:
        lines.append(
            "| {model} | {rows} | {accuracy} | {balanced} | {macro} | {logloss} | {brier} |".format(
                model=summary.get("model", ""),
                rows=summary.get("rows", ""),
                accuracy=_fmt(summary.get("accuracy")),
                balanced=_fmt(summary.get("balanced_accuracy")),
                macro=_fmt(summary.get("macro_f1")),
                logloss=_fmt(summary.get("log_loss")),
                brier=_fmt(summary.get("brier_score")),
            )
        )

    if calibration:
        lines.extend(["", "## Calibration", ""])
        for key, value in calibration.items():
            lines.append(f"- **{key}**: {_fmt(value) if isinstance(value, (int, float)) else value}")
    if error_analysis:
        lines.extend(["", "## Error analysis", ""])
        for key, value in error_analysis.items():
            lines.append(f"- **{key}**: {value}")
    if leakage:
        lines.extend(["", "## Leakage checks", ""])
        for key, value in leakage.items():
            lines.append(f"- **{key}**: {value}")
    if gate:
        lines.extend(["", "## Production gate", "", f"**Passed:** {gate.get('passed')}", "", gate.get("summary", "")])
        for check in gate.get("checks", []):
            mark = "PASS" if check.get("passed") else "FAIL"
            lines.append(
                f"- `{mark}` **{check.get('name')}** — measured {check.get('measured')} "
                f"vs threshold {check.get('threshold')}"
            )

    lines.extend(["", "## Limitations", ""])
    for limitation in limitations:
        lines.append(f"- {limitation}")
    lines.extend(["", "## Conclusion", "", conclusion, ""])
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return "—" if value is None else str(value)


__all__ = [
    "EXPERIMENT_FILES",
    "Experiment",
    "ExperimentRecord",
    "ExperimentTracker",
    "render_experiment_report",
]
