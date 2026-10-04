"""Prediction-service construction, shared by the prediction routes and the agent.

Phase 6's :class:`PredictionService` is the only door to a served prediction, and
both callers must go through the *same* service — built over the same model
directory and the same registry file. Two construction paths would be two chances
to disagree about which models count as production, which is exactly the mistake
that would let an unvalidated model reach a user.

The service is cheap to build (it reads a small JSON registry), so it is built per
request rather than cached: a model promoted to production mid-session is then
visible immediately, with no restart and no stale in-memory copy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.ml.registry import ModelRegistry
from argus.ml.service import PredictionService

#: Default model directory, relative to the API's working directory.
DEFAULT_MODELS_DIR = "data/models"


def models_dir_for(settings: Any | None) -> Path:
    """The configured model store, or the default."""
    configured = getattr(settings, "models_dir", None) or DEFAULT_MODELS_DIR
    return Path(configured)


def prediction_service_for(settings: Any | None) -> PredictionService:
    """Build the prediction service over the configured model store."""
    models_dir = models_dir_for(settings)
    registry = ModelRegistry.load(models_dir)
    return PredictionService(registry, models_dir=models_dir)


__all__ = ["DEFAULT_MODELS_DIR", "models_dir_for", "prediction_service_for"]
