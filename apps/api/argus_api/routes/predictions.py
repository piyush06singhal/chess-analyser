"""Phase 6 routes: prediction tasks, the model registry, and dataset admin.

The design rule for this surface is that **it can say no**. Every endpoint that
could return a prediction can instead return ``available: false`` with the reason,
and that is the expected response until a model has passed its production gate.
There is no endpoint that fabricates a probability, and no endpoint that falls
back to a "best effort" model.

The prediction endpoints return HTTP 200 with ``available: false`` rather than an
error status: "this is not validated yet" is a legitimate, stable answer, not a
failure of the request. A client can render it honestly instead of treating it as
a bug.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from argus.datasets.manifest import DatasetManifest
from argus.datasets.quality import render_quality_report
from argus.datasets.records import DatasetLayer
from argus.datasets.store import DatasetStore, parquet_available
from argus.ml.models import ModelStatus
from argus.ml.service import PredictionService
from argus.ml.tasks import TASK_REGISTRY
from argus.shared.errors import ValidationError
from argus.shared.logging import get_logger

from argus_api.services.prediction_service import prediction_service_for

logger = get_logger(__name__)
router = APIRouter(prefix="/api/predictions", tags=["predictions"])

#: Dataset layers that hold data (as opposed to the experiment log).
DATA_LAYERS = (
    DatasetLayer.RAW,
    DatasetLayer.VALIDATED,
    DatasetLayer.NORMALIZED,
    DatasetLayer.POSITIONS,
    DatasetLayer.ENGINE_ANALYSIS,
    DatasetLayer.FEATURES,
    DatasetLayer.LABELS,
    DatasetLayer.TRAIN,
    DatasetLayer.VALIDATION,
    DatasetLayer.TEST,
)


def _data_root(request: Request) -> Path:
    """The dataset root: next to the model store, so both are configured together."""
    settings = getattr(request.app.state, "settings", None)
    models_dir = Path(getattr(settings, "models_dir", "data/models"))
    return models_dir.parent


def _service(request: Request) -> PredictionService:
    """The prediction service for this request.

    Delegates to the shared builder so the prediction routes and the Phase 7 agent
    cannot disagree about which models are in production.
    """
    settings = getattr(request.app.state, "settings", None)
    return prediction_service_for(settings)


class PredictionRequest(BaseModel):
    """Feature rows for a prediction, in the task's declared feature space."""

    rows: list[dict[str, object]] = Field(default_factory=list)
    #: Optional: a game id the caller is asking about, recorded in the response
    #: for traceability. It is never used to look up information that would not be
    #: available at prediction time.
    reference: str | None = None


@router.get("/tasks")
def list_tasks(request: Request) -> dict:
    """Every declared prediction task, with its availability and requirements.

    This is the honest capability list: a task is ``available: false`` until a
    model for it has passed production gating.
    """
    service = _service(request)
    tasks = service.available_tasks()
    return {
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "available": [task.task for task in tasks if task.available],
        "unavailable": [task.task for task in tasks if not task.available],
        "note": (
            "An unavailable task is not a missing feature: it is a task whose model has "
            "not been validated. Caissa does not serve unvalidated predictions."
        ),
    }


@router.get("/models")
def list_models(request: Request, task: str | None = None) -> dict:
    """Registered models with status, measured metrics and gate outcome."""
    service = _service(request)
    registry = service.registry
    entries = registry.list_models(task=task)
    return {
        "registry": registry.summary(),
        "models": [
            {
                "model_id": model.model_id,
                "task": model.task,
                "model_type": model.model_type,
                "version": model.version,
                "status": model.status.value,
                "servable": model.is_servable(),
                "split_strategy": model.split_strategy,
                "dataset_version": model.dataset_version,
                "feature_version": model.feature_version,
                "trained_rows": model.trained_rows,
                "metrics": model.metrics,
                "calibration_metrics": model.calibration_metrics,
                "gate_passed": (model.gate or {}).get("passed"),
                "experiment_id": model.experiment_id,
                "notes": model.notes,
            }
            for model in entries
        ],
        "statuses": [status.value for status in ModelStatus],
    }


@router.get("/datasets")
def list_datasets(request: Request) -> dict:
    """The developer-facing dataset inventory: layers, manifests and quality.

    This is the Phase 6 dataset-quality dashboard's data source. It reads what is
    actually on disk — an empty store reports an empty store rather than a
    placeholder dataset.
    """
    store = DatasetStore(_data_root(request))
    manifests = store.manifests()
    return {
        "root": str(store.root),
        "parquet_available": parquet_available(),
        "inventory": store.inventory(),
        "manifests": [manifest.model_dump(mode="json") for manifest in manifests],
        "datasets": [
            {
                "dataset_id": manifest.dataset_id,
                "layer": manifest.layer.value,
                "source": manifest.source,
                "games": manifest.number_of_games,
                "positions": manifest.number_of_positions,
                "players": manifest.players,
                "rating_coverage": manifest.rating_coverage,
                "duplicate_count": manifest.duplicate_count,
                "invalid_game_count": manifest.invalid_game_count,
                "rejected_game_count": manifest.rejected_game_count,
                "result_distribution": manifest.result_distribution,
                "missing_metadata": manifest.missing_metadata,
                "date_range": list(manifest.date_range),
                "processing_version": manifest.processing_version,
                "label_version": manifest.label_version,
            }
            for manifest in manifests
        ],
        "summary": _dataset_summary(manifests),
    }


def _dataset_summary(manifests: list[DatasetManifest]) -> dict:
    """Aggregate numbers across datasets, for the top of a dashboard."""
    return {
        "datasets": len(manifests),
        "games": sum(manifest.number_of_games for manifest in manifests),
        "positions": sum(manifest.number_of_positions for manifest in manifests),
        "players": max((manifest.players for manifest in manifests), default=0),
        "duplicates": sum(manifest.duplicate_count for manifest in manifests),
        "invalid_games": sum(manifest.invalid_game_count for manifest in manifests),
        "rejected_games": sum(manifest.rejected_game_count for manifest in manifests),
    }


@router.get("/datasets/{dataset_id}/quality")
def dataset_quality(dataset_id: str, request: Request) -> dict:
    """The stored quality report for one dataset, rendered as text and raw JSON.

    Raises:
        ValidationError: when no quality report exists for the dataset, rather than
            returning an empty report that would read as "the data is fine".
    """
    store = DatasetStore(_data_root(request))
    report = store.manifest(dataset_id)
    if report is None:
        raise ValidationError(f"No dataset named {dataset_id!r} has been built here")

    quality_path = store.layer_path(report.layer) / f"{dataset_id}_quality.jsonl"
    rendered = None
    payload: dict = {}
    if quality_path.is_file():
        rows = store.read_table(f"{dataset_id}_quality", layer=report.layer)
        if rows:
            payload = rows[0]
            from argus.datasets.quality import DatasetQualityReport

            rendered = render_quality_report(DatasetQualityReport.model_validate(payload))
    return {
        "dataset_id": dataset_id,
        "manifest": report.model_dump(mode="json"),
        "quality": payload,
        "rendered": rendered,
    }


@router.get("/experiments")
def list_experiments(request: Request) -> dict:
    """Every recorded experiment, with its headline metrics and gate outcome."""
    from argus.ml.experiments import ExperimentTracker

    tracker = ExperimentTracker(_data_root(request) / "experiments")
    return {"experiments": tracker.summary(), "root": str(tracker.root)}


# --- prediction endpoints (gated) --------------------------------------------

_PREDICTORS = {
    "game_outcome": "predict_game_outcome",
    "position_outcome": "predict_position_outcome",
    "position_difficulty": "predict_position_difficulty",
    "move_error_risk": "predict_error_risk",
}


@router.post("/{task_name}")
def predict(task_name: str, payload: PredictionRequest, request: Request) -> dict:
    """Serve a prediction **only** from a production model, otherwise say why not.

    Raises:
        ValidationError: for a task name that is not declared. An undeclared task
            has no target definition, so it cannot be predicted at all.
    """
    if task_name not in TASK_REGISTRY:
        raise ValidationError(
            f"Unknown prediction task {task_name!r}; declared tasks: "
            + ", ".join(sorted(TASK_REGISTRY))
        )
    service = _service(request)
    method = _PREDICTORS[task_name]

    rows = [dict(row) for row in payload.rows]
    if not rows:
        # No features means nothing to predict — and inventing defaults would be
        # fabricating an input.
        return service.unavailable(task_name).model_dump(mode="json")

    result = getattr(service, method)(rows)
    return result.model_dump(mode="json") | {
        "reference": payload.reference,
        "task_definition": {
            "target": TASK_REGISTRY[task_name].target,
            "unit_of_prediction": TASK_REGISTRY[task_name].unit_of_prediction,
            "target_values": TASK_REGISTRY[task_name].target_values,
        },
    }
