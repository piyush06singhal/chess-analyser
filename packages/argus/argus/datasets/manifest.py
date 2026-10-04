"""Dataset manifests — machine-readable provenance for every dataset.

A dataset without a manifest is an anonymous pile of rows: nothing about it can
be verified later, and no result computed from it can be reproduced. Every
written dataset therefore carries a sibling ``*.manifest.json``, and the metric
that matter most for honesty — how many games were *rejected* and how many were
*duplicates* — is recorded rather than quietly dropped.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from argus.datasets.records import PROCESSING_VERSION, DatasetLayer, IngestedGame
from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: Suffix used for every manifest file so a directory listing makes them obvious.
MANIFEST_SUFFIX = ".manifest.json"


class DatasetManifest(BaseModel):
    """Everything needed to explain, audit and reproduce a dataset."""

    dataset_id: str
    layer: DatasetLayer = DatasetLayer.NORMALIZED
    source: str
    source_version: str | None = None
    #: ``global`` for a public/neutral corpus, ``user`` for an account's own
    #: games. A user-scoped dataset is never folded into global training by
    #: default; the field exists so that rule is checkable, not just a promise.
    scope: str = "global"
    #: Where the rows came from — source URL/path, upstream ids, extraction
    #: command. Kept free-form and small; it is the audit trail, not the data.
    provenance: dict[str, Any] = Field(default_factory=dict)
    collection_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    number_of_games: int = 0
    number_of_positions: int = 0
    players: int = 0

    #: Share of games with both ratings present (0.0–1.0). Missing metadata is a
    #: measured fact about the dataset, not something to fill in.
    rating_coverage: float = 0.0
    #: Share of games with a parseable time control.
    time_control_coverage: float = 0.0
    #: time class → game count.
    time_control_distribution: dict[str, int] = Field(default_factory=dict)
    #: PGN result token → game count.
    result_distribution: dict[str, int] = Field(default_factory=dict)
    #: Most frequent openings (name → count), bounded so the manifest stays small.
    opening_distribution: dict[str, int] = Field(default_factory=dict)

    duplicate_count: int = 0
    invalid_game_count: int = 0
    rejected_game_count: int = 0
    #: Fields the source simply did not provide, and how often.
    missing_metadata: dict[str, int] = Field(default_factory=dict)

    date_range: tuple[str | None, str | None] = (None, None)
    processing_version: str = PROCESSING_VERSION
    feature_version: str | None = None
    label_version: str | None = None
    #: Which split strategy produced this layer, when it is a split layer.
    split_strategy: str | None = None

    files: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def filename(self) -> str:
        return f"{self.dataset_id}{MANIFEST_SUFFIX}"

    def write(self, directory: str | Path) -> Path:
        """Write the manifest next to its dataset; returns the path written."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        path = target / self.filename
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Wrote dataset manifest [%s]", path)
        return path

    @classmethod
    def read(cls, path: str | Path) -> "DatasetManifest":
        """Read a manifest from a file path."""
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def find(cls, directory: str | Path) -> list["DatasetManifest"]:
        """Every manifest in a directory (sorted by dataset id)."""
        root = Path(directory)
        if not root.is_dir():
            return []
        return [
            cls.read(path) for path in sorted(root.glob(f"*{MANIFEST_SUFFIX}"))
        ]


def _share(present: int, total: int) -> float:
    return round(present / total, 4) if total else 0.0


def build_manifest(
    games: list[IngestedGame],
    *,
    dataset_id: str,
    source: str,
    layer: DatasetLayer = DatasetLayer.NORMALIZED,
    source_version: str | None = None,
    scope: str = "global",
    provenance: dict[str, Any] | None = None,
    number_of_positions: int = 0,
    duplicate_count: int = 0,
    invalid_game_count: int = 0,
    rejected_game_count: int = 0,
    feature_version: str | None = None,
    label_version: str | None = None,
    split_strategy: str | None = None,
    files: list[str] | None = None,
    notes: list[str] | None = None,
    opening_limit: int = 25,
) -> DatasetManifest:
    """Measure a manifest from records.

    Every number here is counted from the records (or carried in from the run
    that produced them). Nothing is estimated.
    """
    rated = [g for g in games if g.white_rating is not None and g.black_rating is not None]
    timed = [g for g in games if g.time_class]
    time_controls: dict[str, int] = {}
    results: dict[str, int] = {}
    openings: dict[str, int] = {}
    missing: dict[str, int] = {"date": 0, "rating": 0, "time_control": 0, "opening": 0}

    for game in games:
        if game.time_class:
            time_controls[game.time_class] = time_controls.get(game.time_class, 0) + 1
        results[game.result] = results.get(game.result, 0) + 1
        if game.opening_name:
            openings[game.opening_name] = openings.get(game.opening_name, 0) + 1
        if not game.date_iso:
            missing["date"] += 1
        if game.white_rating is None or game.black_rating is None:
            missing["rating"] += 1
        if not game.time_control_raw:
            missing["time_control"] += 1
        if not game.opening_name:
            missing["opening"] += 1

    players = {key for game in games for key in game.player_keys()}
    dates = sorted(g.date_iso for g in games if g.date_iso)

    return DatasetManifest(
        dataset_id=dataset_id,
        layer=layer,
        source=source,
        source_version=source_version,
        scope=scope,
        provenance=dict(provenance or {}),
        number_of_games=len(games),
        number_of_positions=number_of_positions,
        players=len(players),
        rating_coverage=_share(len(rated), len(games)),
        time_control_coverage=_share(len(timed), len(games)),
        time_control_distribution=dict(sorted(time_controls.items())),
        result_distribution=dict(sorted(results.items())),
        opening_distribution=dict(
            sorted(openings.items(), key=lambda item: -item[1])[:opening_limit]
        ),
        duplicate_count=duplicate_count,
        invalid_game_count=invalid_game_count,
        rejected_game_count=rejected_game_count,
        missing_metadata=missing,
        date_range=(dates[0] if dates else None, dates[-1] if dates else None),
        feature_version=feature_version,
        label_version=label_version,
        split_strategy=split_strategy,
        files=list(files or []),
        notes=list(notes or []),
    )


__all__ = ["MANIFEST_SUFFIX", "DatasetManifest", "build_manifest"]
