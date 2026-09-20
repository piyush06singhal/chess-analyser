"""Model versioning structure.

Trained models are recorded as :class:`ModelMetadata` JSON files under the
model store directory so every prediction can be traced to the exact model
version and dataset that produced it.
"""

from __future__ import annotations

import json
from pathlib import Path

from argus.ml.models import ModelMetadata
from argus.shared.errors import NotFoundError


class ModelStore:
    """Filesystem-backed store of model metadata."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def save(self, metadata: ModelMetadata) -> Path:
        """Persist model metadata; returns the written path."""
        directory = self._root / metadata.name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{metadata.version}.json"
        path.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load(self, name: str, version: str) -> ModelMetadata:
        """Load model metadata.

        Raises:
            NotFoundError: when the metadata file does not exist.
        """
        path = self._root / name / f"{version}.json"
        if not path.is_file():
            raise NotFoundError(f"Model metadata not found: {path}")
        return ModelMetadata.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def list_models(self, name: str | None = None) -> list[ModelMetadata]:
        """List stored model metadata (optionally filtered by model name)."""
        results: list[ModelMetadata] = []
        directories = [self._root / name] if name else sorted(p for p in self._root.iterdir() if p.is_dir())
        for directory in directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                results.append(
                    ModelMetadata.model_validate(json.loads(path.read_text(encoding="utf-8")))
                )
        return results
