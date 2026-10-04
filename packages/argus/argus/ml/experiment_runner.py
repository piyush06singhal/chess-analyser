"""Phase 6 orchestration: build a dataset, run the controlled baseline experiment.

This is the module the CLI calls, and it is written so that the honest outcome is
a first-class result. Two things can happen when it runs:

``refused``
    The task's declared data requirements are not met, so **no model is
    trained** and no metric is produced. The experiment records exactly which
    requirements were missing. This is the expected outcome on a small corpus and
    it is reported as a finding, not hidden.
``completed``
    The requirements are met and the baseline ladder runs on the declared splits,
    with calibration, error analysis, leakage checks and production gating. Even
    then, a model is only promoted to production if every gate passes.

An ``exploratory`` run additionally executes the ladder on data that is below the
declared minimum, so the machinery can be exercised end to end. Its results are
labelled ``exploratory_insufficient_data``, its gate always fails (an unmeasured
requirement cannot unlock production), and it never registers a production model.
The distinction is the whole point: numbers may be *obtained* on small data, they
may not be *presented* as validation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.datasets.dedupe import DuplicateReport, deduplicate
from argus.datasets.features import (
    FEATURE_VERSION,
    GAME_OUTCOME_FEATURES,
    build_game_features,
)
from argus.datasets.importer import DatasetImporter
from argus.datasets.labels import LabelledDataset, label_games
from argus.datasets.leakage import LeakageReport, LeakageValidator
from argus.datasets.manifest import DatasetManifest, build_manifest
from argus.datasets.quality import DatasetQualityReport, quality_report
from argus.datasets.records import Availability, DatasetLayer, IngestedGame
from argus.datasets.splits import SplitPlan, SplitStrategy, make_split
from argus.datasets.store import DatasetStore
from argus.datasets.validation import ValidatedDataset, validate_records
from argus.ml.baselines import BASELINE_LADDER, BasePredictionModel, make_model
from argus.ml.calibration import (
    CalibrationReport,
    evaluate_multiclass_calibration,
)
from argus.ml.error_analysis import (
    ErrorAnalysis,
    misclassified_examples,
    permutation_importance,
    slice_metrics,
)
from argus.ml.experiments import Experiment, ExperimentTracker, render_experiment_report
from argus.ml.gating import evaluate_gates
from argus.ml.metrics import ClassificationMetrics, majority_class_baseline
from argus.ml.models import ModelStatus
from argus.ml.registry import ModelRegistry, RegisteredModel, model_id_for
from argus.ml.tasks import get_task
from argus.shared.errors import ValidationError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: The task the first controlled experiment targets (spec §42).
DEFAULT_TASK = "game_outcome"

#: Dataset version recorded when the caller does not supply one. It is explicit
#: rather than empty so a stored result can still be traced to a build.
DATASET_VERSION_FALLBACK = "phase6-local"

#: Floor below which even an *exploratory* run produces noise rather than
#: information: with fewer rows than this, a test-split metric is dominated by a
#: handful of games and no honest reading exists. Below it, exploratory mode also
#: refuses — an exploratory label licenses weak evidence, not meaningless numbers.
MIN_EXPLORATORY_ROWS = 60


@dataclass
class BuiltDataset:
    """Everything a dataset build produced, ready to write or experiment on."""

    dataset_id: str
    records: list[IngestedGame]
    raw: dict[str, Any]
    validation: ValidatedDataset
    duplicates: DuplicateReport
    labelled: LabelledDataset
    manifests: list[DatasetManifest] = field(default_factory=list)
    quality: DatasetQualityReport | None = None
    written: dict[str, str] = field(default_factory=dict)

    @property
    def games(self) -> int:
        return len(self.records)


def build_game_dataset(
    raw_paths: Sequence[str | Path],
    *,
    dataset_id: str,
    source: str = "pgn corpus",
    source_version: str | None = None,
    store: DatasetStore | None = None,
    write: bool = True,
    scope: str = "global",
    provenance: dict[str, Any] | None = None,
) -> BuiltDataset:
    """Ingest a PGN corpus → validate → deduplicate → label → measure → write.

    The deduplicated set is what everything downstream sees, and the duplicate
    report travels with it so a reader can see how much duplication the source
    contained.
    """
    importer = DatasetImporter(source=source, source_version=source_version)
    result = importer.ingest_paths(sorted(str(path) for path in raw_paths))
    return build_dataset_from_ingestion(
        result,
        dataset_id=dataset_id,
        source=source,
        source_version=source_version,
        store=store,
        write=write,
        scope=scope,
        provenance=provenance,
        files=[str(path) for path in raw_paths],
    )


def build_dataset_from_ingestion(
    result: Any,
    *,
    dataset_id: str,
    source: str,
    source_version: str | None = None,
    store: DatasetStore | None = None,
    write: bool = True,
    scope: str = "global",
    provenance: dict[str, Any] | None = None,
    files: Sequence[str] | None = None,
) -> BuiltDataset:
    """Everything downstream of ingestion, for any source.

    Splitting this out is what lets a database or API source reuse the exact same
    validation, deduplication, labelling and manifest machinery as a PGN file —
    with no second path through the pipeline to keep honest.
    """
    records = list(result.records)

    validation = validate_records(records)
    kept, duplicates = deduplicate(records)
    validation_after = validate_records(kept) if len(kept) != len(records) else validation
    labelled = label_games(kept)

    manifest = build_manifest(
        kept,
        dataset_id=dataset_id,
        source=source,
        layer=DatasetLayer.NORMALIZED,
        source_version=source_version,
        duplicate_count=duplicates.duplicate_records,
        invalid_game_count=validation_after.report.invalid_records,
        rejected_game_count=result.stats.games_rejected,
        label_version=labelled.definition.label_version,
        scope=scope,
        provenance=provenance,
        files=list(files or []),
        notes=[
            f"Ingestion read {result.stats.games_seen} game(s)"
            + (
                f" from {result.stats.files_read} source file(s)"
                if result.stats.files_read
                else f" from {source} (no files read)"
            )
            + f"; {result.stats.games_rejected} were rejected.",
            duplicates.policy.rationale,
            (
                "Dataset scope 'user': these are an account's own games and are "
                "never merged into a global training set automatically."
                if scope == "user"
                else "Dataset scope 'global': public/neutral corpus."
            ),
        ],
    )

    quality = quality_report(
        kept,
        dataset_id=dataset_id,
        validation=validation_after.report,
        duplicates=duplicates,
        labelled=labelled,
        rejected_games=result.stats.games_rejected,
    )

    built = BuiltDataset(
        dataset_id=dataset_id,
        records=kept,
        raw=result.stats.model_dump(mode="json"),
        validation=validation_after,
        duplicates=duplicates,
        labelled=labelled,
        manifests=[manifest],
        quality=quality,
    )

    if write and store is not None:
        store.ensure_layers()
        paths = store.write_games(
            kept, dataset_id=dataset_id, layer=DatasetLayer.NORMALIZED, manifest=manifest
        )
        built.written = {key: str(value) for key, value in paths.items()}
        built.written["quality"] = str(
            store.write_table(
                [quality.model_dump(mode="json")], name=f"{dataset_id}_quality",
                layer=DatasetLayer.NORMALIZED,
            )
        )
    return built


def _feature_rows(
    records: Sequence[IngestedGame], labels: dict[str, str]
) -> tuple[list[dict[str, Any]], list[str], list[IngestedGame]]:
    """Build the pre-game feature rows for the labelled games, in split order."""
    rows: list[dict[str, Any]] = []
    targets: list[str] = []
    used: list[IngestedGame] = []
    for record in records:
        label = labels.get(record.game_id)
        if label is None:
            continue
        rows.append(build_game_features(record))
        targets.append(label)
        used.append(record)
    return rows, targets, used


def _select(records: Sequence[IngestedGame], plan: SplitPlan, split: str) -> list[IngestedGame]:
    wanted = set(plan.ids_in(split))
    return [record for record in records if record.game_id in wanted]


def _rating_band_of(record: IngestedGame) -> str:
    from argus.datasets.quality import rating_band

    ratings = [value for value in (record.white_rating, record.black_rating) if value is not None]
    if not ratings:
        return "unrated"
    return rating_band(int(sum(ratings) / len(ratings)))


@dataclass
class StrategyOutcome:
    """The result of evaluating one split strategy."""

    strategy: str
    split: SplitPlan
    leakage: LeakageReport
    readiness: dict[str, Any]
    metrics: dict[str, ClassificationMetrics] = field(default_factory=dict)
    calibration: CalibrationReport | None = None
    #: The primary breakdown (by rating band); every slicer's result is kept in
    #: ``slice_analyses`` so a limitation in one slice cannot be dropped.
    error_analysis: ErrorAnalysis | None = None
    slice_analyses: dict[str, ErrorAnalysis] = field(default_factory=dict)
    importance: dict[str, float] = field(default_factory=dict)
    gate: Any = None
    models: dict[str, BasePredictionModel] = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""

    @property
    def headline(self) -> dict[str, float | None]:
        best = self.metrics.get("tree_ensemble") or self.metrics.get("logistic_regression")
        if best is None:
            best = self.metrics.get("rating_based") or self.metrics.get("majority_class")
        return best.as_dict() if best else {}


def evaluate_strategy(
    records: Sequence[IngestedGame],
    *,
    strategy: SplitStrategy,
    task_name: str = DEFAULT_TASK,
    seed: int = 42,
    exploratory: bool = False,
    artifact_dir: str | Path | None = None,
) -> StrategyOutcome:
    """Run the baseline ladder for one split strategy."""
    task = get_task(task_name)
    plan = make_split(records, strategy=strategy, seed=seed)
    leakage = LeakageValidator(plan, records=list(records)).validate(
        feature_names=GAME_OUTCOME_FEATURES,
        label_column=task.target,
        task_availability=Availability.PRE_GAME,
    )

    labels = label_games(records).as_map()
    train = _select(records, plan, "train")
    validation = _select(records, plan, "validation")
    test = _select(records, plan, "test")
    train_rows, train_y, train_used = _feature_rows(train, labels)
    validation_rows, validation_y, _ = _feature_rows(validation, labels)
    test_rows, test_y, test_used = _feature_rows(test, labels)

    outcome = StrategyOutcome(strategy=strategy.value, split=plan, leakage=leakage, readiness={})
    class_shares = label_games(records).shares()
    readiness = task.readiness(
        games=len(records),
        train_rows=len(train_rows),
        validation_rows=len(validation_rows),
        test_rows=len(test_rows),
        players=len({key for record in records for key in record.player_keys()}),
        class_shares=class_shares,
        has_ratings=any(record.white_rating is not None for record in records),
    )
    outcome.readiness = readiness

    if not readiness["attemptable"] and not exploratory:
        outcome.skipped = True
        outcome.skip_reason = (
            "Refused: the task's declared data requirements are not met, so no model was "
            "trained and no metric was produced. Missing: "
            + "; ".join(task.missing_requirements(
                games=len(records),
                train_rows=len(train_rows),
                validation_rows=len(validation_rows),
                test_rows=len(test_rows),
                players=len({key for record in records for key in record.player_keys()}),
                class_shares=class_shares,
                has_ratings=any(record.white_rating is not None for record in records),
            ))
        )
        return outcome

    if not train_rows:
        outcome.skipped = True
        outcome.skip_reason = "Refused: the training split is empty."
        return outcome

    if exploratory and (len(train_rows) + len(test_rows)) < MIN_EXPLORATORY_ROWS:
        outcome.skipped = True
        outcome.skip_reason = (
            f"Refused: {len(train_rows) + len(test_rows)} usable row(s) is below the "
            f"{MIN_EXPLORATORY_ROWS}-row floor where even an exploratory metric carries "
            "any information. No metric was produced."
        )
        return outcome

    classes = list(task.target_values)
    majority_reference = majority_class_baseline(train_y)

    fit_failures: list[str] = []
    for kind in BASELINE_LADDER:
        model = make_model(kind, classes=classes)
        try:
            model.fit(
                train_rows,
                train_y,
                feature_names=["rating_diff"] if kind == "rating_based" else GAME_OUTCOME_FEATURES,
                feature_version=FEATURE_VERSION,
            )
        except (ValidationError, ValueError, ImportError) as exc:
            # A model that cannot be built here (no scikit-learn, or too few rows to
            # fit) is reported as unavailable rather than substituted with a fake
            # score or allowed to crash the whole experiment.
            message = getattr(exc, "message", str(exc))
            logger.warning("Baseline %s unavailable: %s", kind, message)
            fit_failures.append(f"{kind}: {message}")
            continue
        outcome.models[kind] = model

    if not outcome.models:
        outcome.skipped = True
        outcome.skip_reason = (
            "Refused: no baseline in the ladder could be fitted — " + "; ".join(fit_failures)
        )
        return outcome

    if test_rows:
        for kind, model in outcome.models.items():
            outcome.metrics[kind] = model.evaluate(
                test_rows, test_y, split="test", binary=True, positive_class="draw"
            )
    if not outcome.metrics and outcome.models:
        # No test rows at all: metrics would be measured on training data, which is
        # not evidence. Say so instead.
        outcome.skipped = True
        outcome.skip_reason = "Refused: the test split is empty, so nothing can be measured."
        return outcome

    if validation_rows and test_rows:
        best = outcome.models.get("logistic_regression") or outcome.models.get("rating_based")
        if best is not None:
            outcome.calibration = evaluate_multiclass_calibration(
                test_y, best.predict_proba(test_rows), best.card.classes
            )
        # Feature importance from the model that exposes least: permutation
        # importance is model-agnostic, so it is comparable across the ladder.
        importance_model = outcome.models.get("tree_ensemble") or outcome.models.get("logistic_regression")
        if importance_model is not None:
            try:
                outcome.importance = permutation_importance(
                    importance_model,
                    test_rows,
                    test_y,
                    feature_names=GAME_OUTCOME_FEATURES,
                    repeats=3,
                    seed=seed,
                )
            except Exception as exc:  # noqa: BLE001 — importance must never fail a run
                logger.warning("Permutation importance failed: %s", exc)

    if test_rows:
        importance_model = outcome.models.get("tree_ensemble") or outcome.models.get("logistic_regression")
        if importance_model is not None:
            predicted = importance_model.predict(test_rows)
            probabilities = importance_model.predict_proba(test_rows)
            slices = {
                "rating_band": [_rating_band_of(record) for record in test_used],
                "time_class": [record.time_class or "unknown" for record in test_used],
                "colour": ["white" for _ in test_used],
                "opening_family": [
                    (record.opening_name or record.eco or "unknown") for record in test_used
                ],
                "player_seen": [
                    "unseen"
                    if not (
                        {record.white.identity_key, record.black.identity_key}
                        & {key for entry in train_used for key in entry.player_keys()}
                    )
                    else "seen"
                    for record in test_used
                ],
            }
            for name, values in slices.items():
                analysis = slice_metrics(
                    test_y, predicted, probabilities, values,
                    classes=importance_model.card.classes,
                    model=importance_model.name, slice_by=name,
                )
                outcome.slice_analyses[name] = analysis
            outcome.error_analysis = outcome.slice_analyses.get("rating_band")
            if outcome.error_analysis is not None:
                # The individual mistakes, most confident first: a confidently wrong
                # prediction is a systematic misunderstanding, not noise.
                outcome.error_analysis.misclassified_examples = misclassified_examples(
                    test_y,
                    predicted,
                    probabilities,
                    classes=importance_model.card.classes,
                    limit=20,
                )

    if artifact_dir is not None:
        for kind, model in outcome.models.items():
            model.save(Path(artifact_dir) / kind)

    # The candidate being gated is the strongest model that actually trained, and
    # a missing measurement is passed through as an empty (not positive) result so
    # the gate fails rather than passing on someone else's numbers.
    candidate_kind = "tree_ensemble" if "tree_ensemble" in outcome.metrics else "logistic_regression"
    candidate = outcome.metrics.get(candidate_kind) or ClassificationMetrics(
        rows=0, classes=classes,
        notes=["no candidate model produced test metrics, so no gate can pass"],
    )
    outcome.gate = evaluate_gates(
        task,
        model_name=f"{candidate_kind} ({outcome.strategy})",
        test_metrics=candidate,
        majority_metrics=majority_reference,
        rating_metrics=outcome.metrics.get("rating_based"),
        calibration_ece=(
            outcome.calibration.expected_calibration_error if outcome.calibration else None
        ),
        leakage_passed=leakage.passed,
        # Every slicer feeds the stability gate, so a group the model fails cannot
        # hide behind a good aggregate: rating band, time class and — the one that
        # matters most for a new user — seen vs unseen players.
        subgroup_scores={
            f"{name}:{slice_name}": score
            for name, analysis in outcome.slice_analyses.items()
            for slice_name, score in analysis.metrics_by_slice().items()
        }
        or None,
        reproducibility={"seed": seed, "dataset_version": "", "feature_version": FEATURE_VERSION},
        dataset_stats={
            "games": len(records),
            "train_rows": len(train_rows),
            "test_rows": len(test_rows),
            "players": len({key for record in records for key in record.player_keys()}),
        },
    )
    return outcome


def run_controlled_experiment(
    built: BuiltDataset,
    *,
    tracker: ExperimentTracker,
    registry: ModelRegistry,
    dataset_version: str,
    experiment_id: str,
    task_name: str = DEFAULT_TASK,
    strategies: Sequence[SplitStrategy] = (
        SplitStrategy.GAME_GROUP,
        SplitStrategy.TEMPORAL,
    ),
    seed: int = 42,
    exploratory: bool = False,
) -> dict[str, Any]:
    """The first controlled baseline experiment (spec §42).

    Reports honestly whichever way it goes; never promotes anything to production.
    """
    task = get_task(task_name)
    models_dir = Path(tracker.root).parent / "models"
    experiment: Experiment = tracker.create(
        experiment_id=experiment_id,
        task=task_name,
        model="baseline_ladder",
        dataset_version=dataset_version,
        feature_version=FEATURE_VERSION,
        split_strategy=" + ".join(strategy.value for strategy in strategies),
    )

    outcomes = [
        evaluate_strategy(
            built.records,
            strategy=strategy,
            task_name=task_name,
            seed=seed,
            exploratory=exploratory,
            artifact_dir=models_dir / experiment_id / strategy.value if exploratory else None,
        )
        for strategy in strategies
    ]

    config = {
        "experiment_id": experiment_id,
        "task": task_name,
        "task_version": task.task_version,
        "seed": seed,
        "feature_version": FEATURE_VERSION,
        "dataset_version": dataset_version,
        "strategies": [strategy.value for strategy in strategies],
        "baseline_ladder": BASELINE_LADDER,
        "exploratory": exploratory,
        "target_values": task.target_values,
        "input_features": GAME_OUTCOME_FEATURES,
        "metrics": task.evaluation_metrics,
        "data_requirements": task.data_requirements.model_dump(),
    }
    experiment.write_config(config)
    experiment.write_manifest(
        [manifest.model_dump(mode="json") for manifest in built.manifests]
    )
    experiment.write_metrics(
        {
            outcome.strategy: {
                "split": outcome.split.counts(),
                "readiness": outcome.readiness,
                "skipped": outcome.skipped,
                "skip_reason": outcome.skip_reason,
                "models": {
                    kind: metrics.summary() for kind, metrics in outcome.metrics.items()
                },
                "leakage": outcome.leakage.summary(),
            }
            for outcome in outcomes
        }
    )
    experiment.write_confusion_matrix(
        {
            outcome.strategy: {
                kind: metrics.confusion_matrix for kind, metrics in outcome.metrics.items()
            }
            for outcome in outcomes
        }
    )
    experiment.write_calibration(
        {
            outcome.strategy: outcome.calibration.summary() if outcome.calibration else None
            for outcome in outcomes
        }
    )
    experiment.write_feature_importance(
        {outcome.strategy: outcome.importance for outcome in outcomes}
    )

    primary = outcomes[0]
    experiment.write_gate(primary.gate)

    # --- register the candidates as EXPERIMENTAL, never PRODUCTION ------------
    registered: list[str] = []
    if exploratory:
        for outcome in outcomes:
            for kind, model in outcome.models.items():
                version = f"{dataset_version}-{outcome.strategy}"
                model_id = model_id_for(task_name, kind, version)
                try:
                    registry.register(
                        RegisteredModel(
                            model_id=model_id,
                            task=task_name,
                            model_type=kind,
                            version=version,
                            status=ModelStatus.EXPERIMENTAL,
                            dataset_version=dataset_version,
                            feature_version=FEATURE_VERSION,
                            split_strategy=outcome.strategy,
                            trained_rows=len(outcome.split.train),
                            metrics=(
                                outcome.metrics[kind].as_dict() if kind in outcome.metrics else {}
                            ),
                            calibration_metrics=(
                                {"expected_calibration_error": outcome.calibration.expected_calibration_error}
                                if outcome.calibration
                                else {}
                            ),
                            experiment_id=experiment_id,
                            hyperparameters=model.card.hyperparameters,
                            gate=outcome.gate.as_report() if outcome.gate else None,
                            notes=[
                                "Registered EXPLORATORY: the dataset is below the task's "
                                "declared minimum, so these metrics are not validation.",
                            ],
                        )
                    )
                    registered.append(model_id)
                except ValidationError:
                    logger.info("Model %s already registered; leaving the original entry", model_id)
        registry.save()

    limitations = list(task.limitations)
    if not exploratory:
        limitations.insert(
            0,
            "No model was trained: the declared data requirements are not met, and "
            "training below them would produce a number that means nothing.",
        )
    else:
        limitations.insert(
            0,
            "EXPLORATORY: the dataset is far below the task's declared minimum. These "
            "metrics demonstrate that the pipeline works end to end; they are not "
            "evidence about chess, and no model was promoted to production.",
        )

    report = render_experiment_report(
        experiment_id=experiment_id,
        task=task_name,
        hypothesis=task.hypothesis,
        dataset_summary={
            "dataset_id": built.dataset_id,
            "games (after deduplication)": built.games,
            "games rejected at ingestion": built.raw.get("games_rejected", 0),
            "duplicate records": built.duplicates.duplicate_records,
            "invalid games": built.validation.report.invalid_records,
            "players": built.quality.players if built.quality else "unknown",
            "rating coverage": f"{(built.quality.rating_coverage if built.quality else 0):.1%}",
            "label distribution": (
                built.labelled.distribution() if built.labelled else "unavailable"
            ),
            "population": built.quality.bias.population_statement if built.quality else "",
            "class balance": built.quality.class_balance_warning
            if built.quality
            else "not assessed",
        },
        split_summary={
            outcome.strategy: outcome.split.counts() for outcome in outcomes
        },
        model_summaries=[
            {
                "model": f"{kind} ({outcome.strategy})",
                "rows": metrics.rows,
                **metrics.as_dict(),
            }
            for outcome in outcomes
            for kind, metrics in outcome.metrics.items()
        ],
        calibration={
            outcome.strategy: outcome.calibration.summary() if outcome.calibration else "not evaluated"
            for outcome in outcomes
        },
        error_analysis=(
            primary.error_analysis.summary() if primary.error_analysis else None
        ),
        leakage={outcome.strategy: outcome.leakage.passed for outcome in outcomes},
        gate=primary.gate.as_report() if primary.gate else None,
        limitations=limitations,
        conclusion=_conclusion(exploratory, outcomes, primary.gate),
    )
    experiment.write_report(report)

    status = (
        "refused_insufficient_data"
        if all(outcome.skipped for outcome in outcomes)
        else ("exploratory_insufficient_data" if exploratory else "completed")
    )
    record = experiment.finish(status=status, headline=primary.headline)
    record.gate_passed = bool(primary.gate.passed) if primary.gate else False
    tracker.record(experiment)

    return {
        "experiment_id": experiment_id,
        "status": status,
        "directory": str(experiment.directory),
        "registry": registry.summary(),
        "registered": registered,
        "outcomes": {
            outcome.strategy: {
                "skipped": outcome.skipped,
                "skip_reason": outcome.skip_reason,
                "split": outcome.split.counts(),
                "leakage_passed": outcome.leakage.passed,
                "gate_passed": bool(outcome.gate.passed) if outcome.gate else False,
                "readiness": outcome.readiness,
                "metrics": {
                    kind: {k: v for k, v in metrics.as_dict().items() if v is not None}
                    for kind, metrics in outcome.metrics.items()
                },
            }
            for outcome in outcomes
        },
        "files": sorted(path.name for path in experiment.directory.iterdir()),
    }


def _conclusion(exploratory: bool, outcomes: Sequence[StrategyOutcome], gate: Any) -> str:
    if all(outcome.skipped for outcome in outcomes):
        return (
            "No model was trained. The dataset does not meet the declared requirements for "
            "this task, and training below them would produce a number that cannot be "
            "interpreted. The requirement list above is the deliverable: it states exactly "
            "what data this task needs before a result may be reported."
        )
    passed = bool(gate.passed) if gate else False
    prefix = (
        "Exploratory run on data far below the declared minimum. "
        if exploratory
        else ""
    )
    if passed:
        return prefix + (
            "Every gate passed. This model may be promoted deliberately; promotion is a "
            "separate, recorded decision."
        )
    return prefix + (
        "The ladder ran, and no model passed the production gates, so no prediction is "
        "exposed. The failures above are the finding: they say which requirement is "
        "missing rather than inviting a bigger model to paper over it."
    )


__all__ = [
    "DATASET_VERSION_FALLBACK",
    "DEFAULT_TASK",
    "BuiltDataset",
    "StrategyOutcome",
    "build_game_dataset",
    "evaluate_strategy",
    "run_controlled_experiment",
]
