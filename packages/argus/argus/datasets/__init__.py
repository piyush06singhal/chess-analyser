"""Dataset engineering (Phase 6).

Turns raw chess sources into datasets that can be *trusted* before any model
sees them:

    raw source → ingestion → validation → deduplication → identity → splitting
    → positions → features → labels → manifests

The package is deliberately model-free. Its job is to make the answer to "what
data is this, where did it come from, and is it safe to evaluate on?" a
mechanical one, so that a model result is a statement about the data rather
than about the pipeline's luck.

Nothing here fabricates a value: a missing header stays missing, an unlabelled
game is counted and named, a rejected game is recorded with a reason, and a
duplicate is classified rather than quietly deleted.
"""

from argus.datasets.db_source import (
    DatabaseGameSource,
    DatasetScope,
    SourcedGame,
    assert_scope_allowed,
    local_sqlite_source,
    render_pgn,
)
from argus.datasets.dedupe import (
    DEFAULT_DEDUPE_POLICY,
    DedupeGroup,
    DedupePolicy,
    DuplicateKind,
    DuplicateReport,
    apply_dedupe,
    deduplicate,
    find_duplicates,
)
from argus.datasets.features import (
    FEATURE_VERSION,
    GAME_OUTCOME_FEATURES,
    FeatureDefinition,
    FeatureGroup,
    build_game_features,
    build_position_features,
    definitions_for,
    get_definition,
    names_for_availability,
    registry,
)
from argus.datasets.identity import (
    IdentityResolution,
    SimilarNamePair,
    build_identity,
    cluster_identities,
    find_similar_names,
    identity_key_for,
    normalize_player_name,
    resolve_identities,
)
from argus.datasets.importer import (
    DatasetImporter,
    IngestionResult,
    game_to_record,
    iter_game_texts,
)
from argus.datasets.labels import (
    GAME_OUTCOME_LABEL,
    LABEL_REGISTRY,
    LABEL_VERSION,
    MOVE_ERROR_LABEL,
    OUTCOME_CLASSES,
    POSITION_DIFFICULTY_LABEL,
    POSITION_OUTCOME_LABEL,
    LabelDefinition,
    LabelledDataset,
    game_outcome_label,
    label_games,
    move_error_label,
    position_difficulty_label,
)
from argus.datasets.leakage import (
    LeakageCheck,
    LeakageFinding,
    LeakageReport,
    LeakageValidator,
    assert_no_leakage,
)
from argus.datasets.positions import (
    DEFAULT_SAMPLING,
    STRATEGY_MEANINGS,
    SamplingSpec,
    SamplingStrategy,
    build_positions,
    iter_positions,
    positions_for_game,
)
from argus.datasets.manifest import MANIFEST_SUFFIX, DatasetManifest, build_manifest
from argus.datasets.quality import (
    RATING_BANDS,
    BiasAnalysis,
    DatasetQualityReport,
    analyse_bias,
    quality_report,
    render_quality_report,
)
from argus.datasets.records import (
    LAYER_DIRECTORIES,
    PROCESSING_VERSION,
    Availability,
    DatasetLayer,
    IngestedGame,
    IngestionStats,
    PlayerIdentity,
    PositionRecord,
    RejectedGame,
)
from argus.datasets.splits import (
    TASK_SPLIT_GUIDANCE,
    SplitPlan,
    SplitStrategy,
    make_split,
    recommended_strategy,
)
from argus.datasets.store import DatasetStore, parquet_available
from argus.datasets.validation import (
    DatasetIssue,
    IssueSeverity,
    ValidatedDataset,
    ValidationReport,
    validate_record,
    validate_records,
)

__all__ = [
    "FEATURE_VERSION",
    "GAME_OUTCOME_FEATURES",
    "GAME_OUTCOME_LABEL",
    "LABEL_REGISTRY",
    "LABEL_VERSION",
    "LAYER_DIRECTORIES",
    "MANIFEST_SUFFIX",
    "MOVE_ERROR_LABEL",
    "OUTCOME_CLASSES",
    "POSITION_DIFFICULTY_LABEL",
    "POSITION_OUTCOME_LABEL",
    "PROCESSING_VERSION",
    "RATING_BANDS",
    "TASK_SPLIT_GUIDANCE",
    "Availability",
    "BiasAnalysis",
    "DEFAULT_DEDUPE_POLICY",
    "DEFAULT_SAMPLING",
    "STRATEGY_MEANINGS",
    "SamplingSpec",
    "SamplingStrategy",
    "DatabaseGameSource",
    "DatasetImporter",
    "DatasetIssue",
    "DatasetLayer",
    "DatasetManifest",
    "DatasetQualityReport",
    "DatasetScope",
    "DatasetStore",
    "SourcedGame",
    "assert_scope_allowed",
    "local_sqlite_source",
    "render_pgn",
    "DedupeGroup",
    "DedupePolicy",
    "DuplicateKind",
    "DuplicateReport",
    "FeatureDefinition",
    "FeatureGroup",
    "IdentityResolution",
    "IngestedGame",
    "IngestionResult",
    "IngestionStats",
    "IssueSeverity",
    "LabelDefinition",
    "LabelledDataset",
    "LeakageCheck",
    "LeakageFinding",
    "LeakageReport",
    "LeakageValidator",
    "PlayerIdentity",
    "PositionRecord",
    "RejectedGame",
    "SimilarNamePair",
    "SplitPlan",
    "SplitStrategy",
    "ValidatedDataset",
    "ValidationReport",
    "analyse_bias",
    "apply_dedupe",
    "assert_no_leakage",
    "build_game_features",
    "build_identity",
    "build_manifest",
    "build_position_features",
    "build_positions",
    "cluster_identities",
    "iter_positions",
    "positions_for_game",
    "deduplicate",
    "definitions_for",
    "find_duplicates",
    "find_similar_names",
    "game_outcome_label",
    "game_to_record",
    "get_definition",
    "identity_key_for",
    "iter_game_texts",
    "label_games",
    "make_split",
    "move_error_label",
    "names_for_availability",
    "normalize_player_name",
    "parquet_available",
    "position_difficulty_label",
    "quality_report",
    "recommended_strategy",
    "registry",
    "render_quality_report",
    "resolve_identities",
    "validate_record",
    "validate_records",
]
