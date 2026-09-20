# ARGUS Chess — ML Strategy

Deterministic chess analysis is kept strictly separate from probabilistic AI
functionality. The ML module introduces learning **only where sufficient data
exists**, with measurable validation/test performance.

## Non-negotiable rules

1. **No fake predictive models.** No "87% win probability" from arbitrary
   calculations — ever.
2. **No accuracy claims without evaluation.** Metrics come from held-out
   validation/test data, measured and reported as-is.
3. **No training on tiny datasets.** `TrainingPipeline.train` validates the
   dataset against its declared `DatasetSpec` and raises
   `InsufficientDataError` when unmet (minimum samples, class balance,
   required columns).
4. **Deterministic vs probabilistic separation.** Engine evaluations remain
   the authoritative strength signal; ML supplements, never replaces.

## Module layout (`argus.ml`)

| Piece | Role |
| ----- | ---- |
| `dataset.py` | CSV loading, validation against `DatasetSpec`, strict guard, deterministic train/validation/test splitting |
| `models.py` | `DatasetSpec`, `DatasetValidationResult`, `ProblemType`, `ModelMetadata`, `EvaluationMetrics` |
| `pipelines.py` | `PredictionModel`, `TrainingPipeline`, `EvaluationPipeline` interfaces + `PredictionResult` |
| `features.py` | raw ↔ derived feature separation (derived ML features over raw position features) |
| `model_store.py` | model versioning structure (versioned artifacts + metadata) |

## Interfaces (contracts for later implementations)

- **`PredictionModel`** — `metadata()` + `predict(features)`; predictions
  always carry the model name/version used.
- **`TrainingPipeline`** — `train(rows)` validates requirements first, then
  delegates to `_train` (implementation-only hook).
- **`EvaluationPipeline`** — `evaluate(model, rows, split)` returns
  `EvaluationMetrics` measured on the held-out split.
- **`DatasetValidator`** — `validate_dataset` / `validate_dataset_strict`.

## Stack direction

- **Phase 2 (initial)**: scikit-learn for the first genuinely data-backed
  problems (e.g. move-quality calibration over large engine-evaluated
  datasets, player-style clustering).
- **Later**: XGBoost/PyTorch behind the same interfaces — no application
  rewrites; models stay decoupled from UI and API.

## Future predictive analytics (deliberately deferred)

Candidate problems (to be defined with exact dataset requirements later):

- outcome prediction from position/game features (requires large corpus + engine-evaluated positions)
- move-quality modeling calibrated to engine labels
- player rating-trajectory modeling (requires per-player history)

Each will get: a `DatasetSpec` with measured requirements, a
`TrainingPipeline`, an `EvaluationPipeline`, and documented test performance
before any result is shown in the product.

## Versioning

Models are versioned artifacts: name, version, problem type, trained_at,
dataset name, feature columns, label column (`ModelMetadata`). The store keeps
artifacts alongside their metadata so every prediction is traceable to the
exact model version and dataset that produced it.
