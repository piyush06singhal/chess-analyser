# ARGUS Chess — Data Strategy

This directory holds all datasets used by ARGUS Chess. **Nothing is downloaded
or fabricated automatically in Phase 1** — datasets are selected deliberately
(see `docs/data-strategy.md`), and every dataset must be documented before use.

## Layout

| Path        | Purpose |
| ----------- | ------- |
| `raw/`      | Source data exactly as obtained (PGN corpora, position files). Immutable — never edited in place. |
| `processed/`| Cleaned/validated data derived deterministically from `raw/` (parsers normalize, filter, deduplicate). |
| `features/` | Extracted feature datasets (raw position features, derived ML features) ready for model consumption. |
| `models/`   | Trained model artifacts with versioned metadata (written only by `argus.ml` pipelines). |

## Intended future datasets

1. **Large PGN game corpus** — master-level and online games with reliable
   metadata (results, ratings, time controls, ECO). Source for game/player
   intelligence and future predictive analytics.
2. **Engine-evaluated positions** — positions evaluated by Stockfish at a fixed
   depth with MultiPV, generated from the corpus by our own pipeline
   (deterministic, reproducible, engine-versioned).
3. **Tactical positions** — curated puzzles with verified solutions.
4. **Opening/game metadata** — ECO book mappings for opening detection.
5. **Player historical games** — per-player game collections for longitudinal
   profiling and personalized training.
6. **Generated feature datasets** — features extracted from 1–5, split and
   versioned by the ML pipelines.

## Data categories (strictly distinguished)

- **Training data** — used to fit models.
- **Validation data** — used for hyperparameter selection and early stopping.
  Never used for training or final reporting.
- **Test data** — held out until final evaluation. Never touched during
  development. Metrics on test data are reported as measured, once.
- **User-specific historical data** — per-user games and analyses stored in the
  application database. Never mixed into global training/test sets without
  explicit, documented leakage-prevention steps.

## Rules

- Every dataset in `processed/` and `features/` must record: source, generation
  date, generating script/version, row count, and engine version where relevant.
- The ML module refuses training when a dataset does not meet its declared
  requirements (`argus.ml.dataset.validate_dataset_strict`).
- No dataset may be fabricated, duplicated from other datasets, or presented
  with unverified labels.
