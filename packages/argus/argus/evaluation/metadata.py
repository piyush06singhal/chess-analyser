"""Reproducibility metadata for an evaluation run (§46).

A benchmark result is only meaningful if it can be reproduced. Every run records
the exact inputs that produced it: the commit, each subsystem's version, the
engine build, the model and feature versions, the effective configuration and the
timestamp. Where a value genuinely cannot be determined, it is recorded as
``unknown`` rather than guessed — the same rule the rest of Caissa follows.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: The evaluation framework's own methodology version. Bumped when the way a
#: benchmark decides pass/fail changes, so an old report can be recognised.
EVALUATION_METHODOLOGY_VERSION = "14.0"


def _git_commit() -> str:
    """The current commit hash, or ``unknown`` outside a git checkout."""
    for env_var in ("ARGUS_GIT_COMMIT", "GIT_COMMIT", "SOURCE_COMMIT"):
        value = os.environ.get(env_var)
        if value:
            return value
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _git_dirty() -> bool:
    """Whether the working tree has uncommitted changes (a reproducibility caveat)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(result.stdout.strip())


def _versions() -> dict[str, str]:
    """Each subsystem's version, read from the single place that owns it."""
    versions: dict[str, str] = {}
    try:
        from argus.analysis.pipeline import ANALYSIS_VERSION

        versions["analysis"] = ANALYSIS_VERSION
    except Exception:  # noqa: BLE001 — a missing version is recorded, not faked
        versions["analysis"] = "unknown"
    try:
        from argus.intelligence.report import REPORT_VERSION

        versions["game_intelligence"] = REPORT_VERSION
    except Exception:  # noqa: BLE001
        versions["game_intelligence"] = "unknown"
    try:
        from argus.datasets.features import FEATURE_VERSION

        versions["features"] = FEATURE_VERSION
    except Exception:  # noqa: BLE001
        versions["features"] = "unknown"
    try:
        from argus.datasets.labels import LABEL_VERSION

        versions["labels"] = LABEL_VERSION
    except Exception:  # noqa: BLE001
        versions["labels"] = "unknown"
    try:
        from argus.intelligence_graph.taxonomy import (
            GRAPH_METHODOLOGY_VERSION,
            GRAPH_SCHEMA_VERSION,
        )

        versions["graph_schema"] = GRAPH_SCHEMA_VERSION
        versions["graph_methodology"] = GRAPH_METHODOLOGY_VERSION
    except Exception:  # noqa: BLE001
        versions["graph_schema"] = "unknown"
        versions["graph_methodology"] = "unknown"
    try:
        from argus.intelligence_graph.knowledge import KNOWLEDGE_GRAPH_VERSION

        versions["knowledge"] = KNOWLEDGE_GRAPH_VERSION
    except Exception:  # noqa: BLE001
        versions["knowledge"] = "unknown"
    return versions


@dataclass
class RunMetadata:
    """Everything needed to reproduce (or invalidate) an evaluation run."""

    git_commit: str = ""
    git_dirty: bool = False
    evaluation_methodology_version: str = EVALUATION_METHODOLOGY_VERSION
    engine_name: str = "unknown"
    engine_version: str | None = None
    engine_configuration: dict[str, Any] = field(default_factory=dict)
    model_version: str = "none"
    dataset_version: str = "unknown"
    versions: dict[str, str] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    python_version: str = ""
    platform: str = ""

    @classmethod
    def capture(
        cls,
        *,
        engine: Any | None = None,
        configuration: dict[str, Any] | None = None,
        dataset_version: str = "unknown",
        model_version: str = "none",
    ) -> "RunMetadata":
        """Build a metadata record from the live environment."""
        engine_name = "stockfish"
        engine_version: str | None = None
        engine_configuration: dict[str, Any] = {}
        if engine is not None:
            info = engine.info() if hasattr(engine, "info") else {}
            engine_name = info.get("engine", engine_name)
            engine_version = info.get("version")
            settings = getattr(engine, "_settings", None)
            if settings is not None and hasattr(settings, "describe"):
                engine_configuration = settings.describe()
            engine_configuration["available"] = info.get("available")
        return cls(
            git_commit=_git_commit(),
            git_dirty=_git_dirty(),
            engine_name=engine_name,
            engine_version=engine_version,
            engine_configuration=engine_configuration,
            model_version=model_version,
            dataset_version=dataset_version,
            versions=_versions(),
            configuration=configuration or {},
            timestamp=datetime.now(timezone.utc).isoformat(),
            python_version=sys.version.split()[0],
            platform=platform.platform(),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "git_commit": self.git_commit,
            "git_dirty": self.git_dirty,
            "evaluation_methodology_version": self.evaluation_methodology_version,
            "engine_name": self.engine_name,
            "engine_version": self.engine_version,
            "engine_configuration": self.engine_configuration,
            "model_version": self.model_version,
            "dataset_version": self.dataset_version,
            "versions": self.versions,
            "configuration": self.configuration,
            "timestamp": self.timestamp,
            "python_version": self.python_version,
            "platform": self.platform,
        }


__all__ = [
    "EVALUATION_METHODOLOGY_VERSION",
    "RunMetadata",
]
