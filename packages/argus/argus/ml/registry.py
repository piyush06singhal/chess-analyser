"""Model registry — every model, with its status and the evidence behind it.

The registry is the single place that answers "what models exist, and which of
them is allowed to answer a user?" Two rules make it trustworthy:

``Only PRODUCTION serves users``
    :meth:`ModelRegistry.production` is the only method the prediction service is
    allowed to consult, and a model can only reach ``PRODUCTION`` through
    :meth:`ModelRegistry.promote`, which requires a *passing* gate decision.
``Registration is append-only per version``
    Re-registering the same model and version is refused unless overwriting is
    explicitly requested, because history that can be quietly rewritten is not
    history.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from argus.ml.gating import GateDecision
from argus.ml.models import ModelStatus
from argus.shared.errors import NotFoundError, ValidationError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: The registry file name inside the registry directory.
REGISTRY_FILENAME = "model_registry.json"


class RegisteredModel(BaseModel):
    """One registered model version and its measured evidence."""

    model_id: str = Field(description="Stable id: task + model name + version")
    task: str
    model_type: str
    version: str
    status: ModelStatus = ModelStatus.EXPERIMENTAL

    dataset_version: str = ""
    feature_version: str = ""
    split_strategy: str = ""
    problem_type: str = "classification"

    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trained_at: datetime | None = None
    trained_rows: int = 0

    #: Measured on the held-out test split. Never filled from training data.
    metrics: dict[str, float | None] = Field(default_factory=dict)
    calibration_metrics: dict[str, float | None] = Field(default_factory=dict)
    baseline_metrics: dict[str, float | None] = Field(default_factory=dict)

    artifact: str = ""
    experiment_id: str = ""
    hyperparameters: dict[str, object] = Field(default_factory=dict)
    gate: dict[str, object] | None = None
    status_history: list[dict[str, str]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def is_servable(self) -> bool:
        """Whether this model may answer a user-facing request."""
        return self.status is ModelStatus.PRODUCTION


class ModelRegistry(BaseModel):
    """A filesystem-backed, JSON-serialised model registry."""

    models: list[RegisteredModel] = Field(default_factory=list)
    path: str = ""

    # --- persistence --------------------------------------------------------

    @classmethod
    def load(cls, directory: str | Path) -> "ModelRegistry":
        """Load a registry, or return an empty one when none exists yet."""
        target = Path(directory)
        file_path = target / REGISTRY_FILENAME
        if not file_path.is_file():
            return cls(models=[], path=str(file_path))
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        registry = cls.model_validate(payload)
        registry.path = str(file_path)
        return registry

    def save(self, directory: str | Path | None = None) -> Path:
        """Persist the registry; returns the file path."""
        file_path = Path(directory) / REGISTRY_FILENAME if directory else Path(self.path or REGISTRY_FILENAME)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(file_path)
        file_path.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8"
        )
        return file_path

    # --- mutation -----------------------------------------------------------

    def register(self, model: RegisteredModel, *, overwrite: bool = False) -> RegisteredModel:
        """Add a model, refusing to silently replace an existing version."""
        existing = self.get(model.model_id)
        if existing is not None:
            if not overwrite:
                raise ValidationError(
                    f"Model '{model.model_id}' is already registered. Registration is "
                    "append-only: bump the version, or pass overwrite=True explicitly."
                )
            self.models = [entry for entry in self.models if entry.model_id != model.model_id]
            model.status_history.append(
                {
                    "status": "reregistered",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "reason": "explicitly overwritten",
                }
            )
        if not model.status_history:
            model.status_history.append(
                {
                    "status": model.status.value,
                    "at": model.registered_at.isoformat(),
                    "reason": "registered",
                }
            )
        self.models.append(model)
        logger.info("Registered model %s [%s]", model.model_id, model.status.value)
        return model

    def promote(
        self,
        model_id: str,
        status: ModelStatus,
        *,
        gate: GateDecision | None = None,
        reason: str = "",
    ) -> RegisteredModel:
        """Change a model's status, refusing PRODUCTION without a passing gate.

        Raises:
            NotFoundError: when the model is not registered.
            ValidationError: when production is requested without evidence, or
                when the gate supplied did not pass.
        """
        model = self.get(model_id)
        if model is None:
            raise NotFoundError(f"No registered model with id {model_id!r}")

        if status is ModelStatus.PRODUCTION:
            decision = gate
            if decision is None:
                if model.gate is None:
                    raise ValidationError(
                        f"Refusing to promote {model_id} to production: no production-gate "
                        "decision is recorded. Run gating first — a model is not promoted "
                        "because it looks good."
                    )
                passed = bool(model.gate.get("passed"))
            else:
                passed = decision.passed
                model.gate = decision.as_report()
            if not passed:
                raise ValidationError(
                    f"Refusing to promote {model_id} to production: the gate decision did "
                    f"not pass. {model.gate.get('summary', '') if model.gate else ''}"
                )

        model.status = status
        model.status_history.append(
            {
                "status": status.value,
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": reason or "status changed",
            }
        )
        self.save()
        return model

    # --- queries ------------------------------------------------------------

    def get(self, model_id: str) -> RegisteredModel | None:
        for model in self.models:
            if model.model_id == model_id:
                return model
        return None

    def list_models(
        self, *, task: str | None = None, status: ModelStatus | None = None
    ) -> list[RegisteredModel]:
        """Registered models, newest first, optionally filtered."""
        found = [
            model
            for model in self.models
            if (task is None or model.task == task) and (status is None or model.status is status)
        ]
        return sorted(found, key=lambda model: model.registered_at, reverse=True)

    def production(self, task: str) -> list[RegisteredModel]:
        """Models a user-facing prediction may use. The only servable set."""
        return [
            model
            for model in self.list_models(task=task, status=ModelStatus.PRODUCTION)
            if model.is_servable()
        ]

    def summary(self) -> dict[str, object]:
        """Counts per task and status, for an admin view."""
        by_task: dict[str, dict[str, int]] = {}
        for model in self.models:
            entry = by_task.setdefault(model.task, {})
            entry[model.status.value] = entry.get(model.status.value, 0) + 1
        return {
            "registered": len(self.models),
            "by_task": dict(sorted(by_task.items())),
            "production_models": [
                model.model_id for model in self.models if model.is_servable()
            ],
        }


def model_id_for(task: str, model_type: str, version: str) -> str:
    """The stable identifier for one model version."""
    return f"{task}:{model_type}:{version}"


__all__ = [
    "REGISTRY_FILENAME",
    "ModelRegistry",
    "RegisteredModel",
    "model_id_for",
]
