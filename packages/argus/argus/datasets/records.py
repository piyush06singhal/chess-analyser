"""Canonical dataset records and dataset layers.

Everything downstream of ingestion (validation, deduplication, splitting,
feature building, labelling) consumes these records and nothing else. The
record is deliberately *normalized*: one shape for PGN files, for a future
database source, and for a future API source, so no consumer has to know where
a game came from.

Two properties are load-bearing:

``Stable identifiers``
    ``game_id`` is derived from the move sequence only, so the same game
    ingested twice (from two files, two exports, or two platforms) gets the
    same id. That is what makes duplicate detection and leakage checking
    *possible* rather than heuristic.
``Declared availability``
    Every feature is declared as available before the game, at the position, or
    only after the game. Leakage is a property of the *feature*, not of the
    model, so the declaration lives here and the leakage validator reads it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

#: Bumped whenever the normalized record shape or its normalization rules
#: change. Stored in every manifest so a dataset can always be explained.
PROCESSING_VERSION = "6.0"


class DatasetLayer(str, Enum):
    """The layers of the dataset architecture (see ``docs/ml-and-data.md``).

    Raw is immutable; every other layer is derived and regenerable. The
    distinction is enforced by :class:`~argus.datasets.store.DatasetStore`,
    which refuses to write into the raw layer.
    """

    RAW = "raw"
    VALIDATED = "validated"
    NORMALIZED = "normalized"
    POSITIONS = "positions"
    ENGINE_ANALYSIS = "engine_analysis"
    FEATURES = "features"
    LABELS = "labels"
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    EXPERIMENTS = "experiments"


#: Layer → directory name under the data root. Fixed so docs, scripts and the
#: store cannot disagree about where a layer lives.
LAYER_DIRECTORIES: dict[DatasetLayer, str] = {
    DatasetLayer.RAW: "raw",
    DatasetLayer.VALIDATED: "validated",
    DatasetLayer.NORMALIZED: "normalized",
    DatasetLayer.POSITIONS: "positions",
    DatasetLayer.ENGINE_ANALYSIS: "engine_analysis",
    DatasetLayer.FEATURES: "features",
    DatasetLayer.LABELS: "labels",
    DatasetLayer.TRAIN: "train",
    DatasetLayer.VALIDATION: "validation",
    DatasetLayer.TEST: "test",
    DatasetLayer.EXPERIMENTS: "experiments",
}


class Availability(str, Enum):
    """When a feature's value becomes knowable.

    This is the leakage control. A feature declared ``POST_GAME`` can never be
    an input to a pre-game prediction, however predictive it looks.
    """

    #: Known before the first move (ratings, colours, time control, opening, history).
    PRE_GAME = "pre_game"
    #: Knowable from the position itself, at prediction time.
    AT_POSITION = "at_position"
    #: Only knowable once the game is over (result, final evaluation, later moves).
    POST_GAME = "post_game"


class PlayerIdentity(BaseModel):
    """A player as written in the source, plus its normalized identity.

    The original spelling is always preserved — normalization is additive, never
    destructive, because "same person?" is a judgement that must stay auditable.
    """

    original_name: str
    normalized: str
    identity_key: str
    platform: str | None = None
    platform_username: str | None = None


class IngestedGame(BaseModel):
    """One normalized game — the single output format of every importer."""

    #: Content-derived (hash of the move sequence): stable across re-ingestion.
    game_id: str
    source: str
    source_version: str | None = None
    source_file: str | None = None
    source_index: int | None = Field(
        default=None, description="Position of the game within its source file (0-based)"
    )

    white: PlayerIdentity
    black: PlayerIdentity
    white_rating: int | None = None
    black_rating: int | None = None

    result: str = Field(description="PGN result token: 1-0, 0-1, 1/2-1/2 or *")
    date_raw: str | None = None
    date_iso: str | None = None
    event: str | None = None
    site: str | None = None
    time_control_raw: str | None = None
    time_control_initial_seconds: int | None = None
    time_control_increment_seconds: int | None = None
    time_class: str | None = Field(
        default=None, description="Caissa time class, from the shared classifier"
    )
    eco: str | None = None
    opening_name: str | None = None

    ply_count: int = 0
    #: Hash of the UCI move sequence — two records with equal values are the
    #: same game as played, whatever the metadata says.
    moves_hash: str = ""
    #: Hash of the SAN move sequence (the notation actually published).
    movetext_hash: str = ""
    #: Hash of the identifying metadata (players, date, result).
    metadata_hash: str = ""
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    processing_version: str = PROCESSING_VERSION

    def player_keys(self) -> tuple[str, str]:
        """Both players' normalized identity keys."""
        return (self.white.identity_key, self.black.identity_key)


class PositionRecord(BaseModel):
    """One position of one game, ready for sampling, features and engine labelling."""

    position_id: str = Field(description="Stable id: game_id + ply")
    game_id: str
    ply: int = Field(ge=0, description="0 is the initial position")
    move_number: int = Field(ge=1)
    side_to_move: str
    fen: str
    phase: str | None = None
    move_san: str | None = None
    #: Hash of the position itself (FEN + side to move) — equal values are the
    #: same position, possibly reached by different games. Used to *report*
    #: cross-game repetition rather than to hide it.
    position_hash: str = ""
    processing_version: str = PROCESSING_VERSION


class IngestionStats(BaseModel):
    """What an ingestion run actually did — counts, never estimates."""

    files_read: int = 0
    games_seen: int = 0
    games_accepted: int = 0
    games_rejected: int = 0
    #: reason → count. A rejected game is always attributed to a reason.
    rejections: dict[str, int] = Field(default_factory=dict)
    duplicates_exact: int = 0
    duplicates_metadata_variant: int = 0
    bytes_read: int = 0
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    def reject(self, reason: str) -> None:
        """Record one rejected game under a stable reason key."""
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        self.games_rejected += 1


class RejectedGame(BaseModel):
    """A game that could not be ingested, kept so nothing is silently lost."""

    source_file: str | None = None
    source_index: int | None = None
    reason: str
    message: str = ""
