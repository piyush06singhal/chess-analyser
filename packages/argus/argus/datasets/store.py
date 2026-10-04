"""The dataset store — layers, manifests and IO that scales past memory.

The store owns two guarantees that make a dataset pipeline trustworthy:

``Layers are enforced``
    Raw is immutable. The store refuses to write into it, so a "processed" run
    can never quietly overwrite the source data it was derived from.
``Rows stream``
    Records are written in batches and read back as an iterator, so a corpus
    does not have to fit in memory. The storage format is JSON Lines by default
    (one record per line, streamable, diffable, no dependency), with Parquet
    used automatically when ``pyarrow`` is installed because it is columnar and
    an order of magnitude smaller for numeric feature tables.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from argus.datasets.manifest import MANIFEST_SUFFIX, DatasetManifest
from argus.datasets.records import LAYER_DIRECTORIES, DatasetLayer
from argus.shared.errors import ValidationError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: Default batch size when streaming records to disk.
WRITE_BATCH_SIZE = 5_000


def parquet_available() -> bool:
    """Whether the optional columnar backend is installed."""
    try:
        import pyarrow  # noqa: F401
    except Exception:  # noqa: BLE001 — any import failure means "not available"
        return False
    return True


class DatasetStore:
    """Filesystem store for the dataset layers."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def layer_path(self, layer: DatasetLayer) -> Path:
        """The directory for a layer (created on demand by writers)."""
        return self._root / LAYER_DIRECTORIES[layer]

    def ensure_layers(self) -> list[Path]:
        """Create every layer directory and the layer README; returns them."""
        created: list[Path] = []
        for layer in DatasetLayer:
            path = self.layer_path(layer)
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
        readme = self._root / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Caissa dataset root\n\n"
                "Layers, from the outside in:\n\n"
                "| Layer | Meaning | Writable |\n| ----- | ------- | -------- |\n"
                "| `raw/` | Source data exactly as obtained. | **never** |\n"
                "| `validated/` | Records that passed validation, with the report. | yes |\n"
                "| `normalized/` | Deduplicated, canonical game records. | yes |\n"
                "| `positions/` | Position rows derived from games. | yes |\n"
                "| `engine_analysis/` | Engine evaluations with provenance. | yes |\n"
                "| `features/` | Versioned feature tables. | yes |\n"
                "| `labels/` | Versioned label tables. | yes |\n"
                "| `train/`, `validation/`, `test/` | Split layers. | yes |\n"
                "| `experiments/` | Experiment configs, metrics and reports. | yes |\n\n"
                "Nothing in this tree is fabricated: every number traces to a manifest\n"
                "and every manifest to a source. `raw/` is never written by tooling.\n",
                encoding="utf-8",
            )
        return created

    def _check_writable(self, layer: DatasetLayer) -> Path:
        if layer is DatasetLayer.RAW:
            raise ValidationError(
                "The raw layer is immutable: no tool writes into raw/. Put new source "
                "data there yourself and re-run the build."
            )
        directory = self.layer_path(layer)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    # --- game records -------------------------------------------------------

    def write_games(
        self,
        records: list[Any],
        *,
        dataset_id: str,
        layer: DatasetLayer = DatasetLayer.NORMALIZED,
        manifest: DatasetManifest | None = None,
        batch_size: int = WRITE_BATCH_SIZE,
    ) -> dict[str, Path]:
        """Write game records to a layer in batches, plus their manifest.

        Returns the paths written (``records`` and, when provided, ``manifest``).
        """
        directory = self._check_writable(layer)
        records_path = directory / f"{dataset_id}.jsonl"
        written = 0
        with records_path.open("w", encoding="utf-8") as handle:
            for start in range(0, len(records), batch_size):
                batch = records[start : start + batch_size]
                for record in batch:
                    handle.write(json.dumps(_dump(record), sort_keys=True) + "\n")
                written += len(batch)
        logger.info("Wrote %d game record(s) to %s", written, records_path)

        paths = {"records": records_path}
        if manifest is not None:
            if manifest.layer is not layer:
                manifest = manifest.model_copy(update={"layer": layer})
            paths["manifest"] = manifest.write(directory)
        return paths

    def read_games(
        self,
        dataset_id: str,
        *,
        layer: DatasetLayer = DatasetLayer.NORMALIZED,
        model: type | None = None,
    ) -> Iterator[Any]:
        """Stream game records back from a layer (one at a time)."""
        path = self.layer_path(layer) / f"{dataset_id}.jsonl"
        if not path.is_file():
            raise FileNotFoundError(f"No dataset records at {path}")
        from argus.datasets.records import IngestedGame

        target = model or IngestedGame
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield target.model_validate(json.loads(line))

    def count_games(self, dataset_id: str, *, layer: DatasetLayer = DatasetLayer.NORMALIZED) -> int:
        """Count records without materialising them."""
        path = self.layer_path(layer) / f"{dataset_id}.jsonl"
        if not path.is_file():
            raise FileNotFoundError(f"No dataset records at {path}")
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    # --- tables (features / labels) ----------------------------------------

    def write_table(
        self,
        rows: list[dict[str, Any]],
        *,
        name: str,
        layer: DatasetLayer = DatasetLayer.FEATURES,
        columns: list[str] | None = None,
    ) -> Path:
        """Write a feature/label table.

        Parquet is used when ``pyarrow`` is installed (columnar, compact, typed);
        otherwise JSON Lines, which streams and needs no dependency. Both are
        readable by :meth:`read_table`, so the choice is an environment detail
        rather than a format the rest of the code has to know about.
        """
        directory = self._check_writable(layer)
        ordered = columns or (sorted({key for row in rows for key in row}) if rows else [])
        if parquet_available():
            import pyarrow as pa
            import pyarrow.parquet as pq

            path = directory / f"{name}.parquet"
            table = pa.table({column: [row.get(column) for row in rows] for column in ordered})
            pq.write_table(table, path)
            logger.info("Wrote %d row(s) to %s (parquet)", len(rows), path)
            return path

        path = directory / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps({column: row.get(column) for column in ordered}) + "\n")
        logger.info("Wrote %d row(s) to %s (jsonl)", len(rows), path)
        return path

    def read_table(
        self, name: str, *, layer: DatasetLayer = DatasetLayer.FEATURES
    ) -> list[dict[str, Any]]:
        """Read a table back, whichever backend wrote it."""
        directory = self.layer_path(layer)
        parquet_path = directory / f"{name}.parquet"
        if parquet_path.is_file():
            import pyarrow.parquet as pq

            return pq.read_table(parquet_path).to_pylist()
        jsonl_path = directory / f"{name}.jsonl"
        if jsonl_path.is_file():
            with jsonl_path.open("r", encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        raise FileNotFoundError(f"No table named {name!r} in {directory}")

    def table_exists(self, name: str, *, layer: DatasetLayer = DatasetLayer.FEATURES) -> bool:
        """Whether a table is present under either backend."""
        directory = self.layer_path(layer)
        return (directory / f"{name}.parquet").is_file() or (directory / f"{name}.jsonl").is_file()

    # --- manifests ----------------------------------------------------------

    def manifests(self, layer: DatasetLayer | None = None) -> list[DatasetManifest]:
        """Every manifest in a layer (or in the whole store)."""
        if layer is not None:
            return DatasetManifest.find(self.layer_path(layer))
        found: list[DatasetManifest] = []
        for candidate in DatasetLayer:
            found.extend(DatasetManifest.find(self.layer_path(candidate)))
        return found

    def manifest(self, dataset_id: str) -> DatasetManifest | None:
        """Find one dataset's manifest by id, wherever it lives."""
        for candidate in self.manifests():
            if candidate.dataset_id == dataset_id:
                return candidate
        return None

    def inventory(self) -> list[dict[str, Any]]:
        """A listing of everything present, for a CLI or an admin endpoint."""
        inventory: list[dict[str, Any]] = []
        for layer in DatasetLayer:
            directory = self.layer_path(layer)
            if not directory.is_dir():
                continue
            files = [
                path
                for path in sorted(directory.iterdir())
                if path.is_file() and path.name != MANIFEST_SUFFIX
            ]
            inventory.append(
                {
                    "layer": layer.value,
                    "path": str(directory),
                    "files": [path.name for path in files],
                    "bytes": sum(path.stat().st_size for path in files),
                    "manifests": [path.name for path in sorted(directory.glob(f"*{MANIFEST_SUFFIX}"))],
                }
            )
        return inventory


def _dump(value: Any) -> dict[str, Any]:
    """Serialize a pydantic model without losing the JSON-safe shape."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    raise ValidationError(f"Cannot serialize {type(value).__name__} into the dataset store")


__all__ = ["WRITE_BATCH_SIZE", "DatasetStore", "parquet_available"]
