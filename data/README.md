# Caissa — Data Strategy

This directory holds all datasets used by Caissa. **Nothing is downloaded
or fabricated automatically in Phase 1** — datasets are selected deliberately
(see `docs/ml-and-data.md`), and every dataset must be documented before use.

## Layout

| Path        | Purpose |
| ----------- | ------- |
| `raw/`      | Source data exactly as obtained (PGN corpora, position files). Immutable — never edited in place. |
| `validated/`| Records that passed dataset validation, with their validation report. |
| `normalized/`| **The canonical layer** (Phase 6): one record per game, plus a `.manifest.json` per dataset. |
| `positions/`| Position rows sampled from games, with the sampling spec that produced them. |
| `engine_analysis/`| Stockfish outputs tagged with engine version, depth, MultiPV and configuration. |
| `features/` | Extracted feature datasets (raw position features, derived ML features) ready for model consumption. |
| `labels/`   | Generated labels, with their label-definition version. |
| `train/` `validation/` `test/` | Split layers, written per split strategy. |
| `experiments/`| One directory per experiment — never overwritten. See `docs/ml-and-data.md`. |
| `models/`   | Trained model artifacts with versioned metadata (written only by `argus.ml` pipelines). |
| `processed/`| Legacy Phase 1/2 engine-evaluated position dataset, kept for continuity. |

Raw is read-only by convention **and by design**: every processed layer is
regenerable from raw plus the processing version, so a pipeline bug can never
destroy the thing you would re-run against. See `docs/ml-and-data.md`
for the full pipeline.

### Manifests

Every dataset written to a Phase 6 layer carries a sibling `*.manifest.json`
recording source, scope (`global` / `user`), collection date, game and player
counts, rating and time-control coverage, result and opening distributions, and
how many records were **rejected**, **invalid** or **duplicated**. Those last
three are recorded rather than hidden: a dataset's rejections are a property of
the source.

## Building a Phase 6 dataset

```bash
# From a PGN corpus
python scripts/phase6_experiment.py build --source-kind pgn --raw data/raw

# From the games already stored in the ARGUS database (user-scoped)
python scripts/phase6_experiment.py build --source-kind db --scope user \
    --allow-user-scope --db-url "$ARGUS_DATABASE_URL" --dataset-id argus_user_games
```

A user-scoped build **refuses without `--allow-user-scope`**. That is deliberate:
private games must not enter a dataset by accident.

## Intended future datasets

1. **Large PGN game corpus** — master-level and online games with reliable
   metadata (results, ratings, time controls, ECO). Source for game/player
   intelligence and future predictive analytics. Deliberate candidates:
   Lichess Open Database (monthly dumps), chessandsim, KWABS chess data.
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
  explicit, documented leakage-prevention steps. Phase 6 enforces this with a
  dataset *scope*: a user-scoped source requires an explicit opt-in and is
  recorded in the manifest, so the rule is checkable after the fact rather than
  merely promised.

## Rules

- Every dataset in `processed/` and `features/` must record: source, generation
  date, generating script/version, row count, and engine version where relevant.
- The ML module refuses training when a dataset does not meet its declared
  requirements (`argus.ml.dataset.validate_dataset_strict`).
- No dataset may be fabricated, duplicated from other datasets, or presented
  with unverified labels.

## Current contents (Phase 6)

| File | What it is | Status |
| ---- | ---------- | ------ |
| `normalized/argus_phase6.jsonl` + `.manifest.json` | Phase 6 dataset built from `raw/` by `scripts/phase6_experiment.py build`. | generated |
| `normalized/argus_phase6_quality.jsonl` | Quality report for that dataset (validation, duplicates, labels, bias). | generated |
| `experiments/phase6-game-outcome-v1/` | The first controlled experiment — a recorded **refusal**, with the missing requirements. | generated |
| `models/model_registry.json` | The model registry. Empty of production entries, honestly. | generated |

## Current contents (Phase 1)

| File | What it is | Status |
| ---- | ---------- | ------ |
| `raw/classic_miniatures.pgn` | Two famous, historically documented miniature games (Morphy Opera Game 1858; Légal–Saint Brie, Legal's mate pattern). Every move is verified legal by the ARGUS parser. **Smoke-test corpus only — not training-scale.** | curated, in place |
| `processed/positions_dataset.csv` | Per-move engine-evaluated feature rows generated by `python -m argus.ml.build_dataset` from the raw corpus above. Regenerated, never hand-edited. `.meta.json` holds provenance (engine version, depth, row count, timestamp). | generated |

## Generating the feature dataset

```bash
python -m argus.ml.build_dataset data/raw/classic_miniatures.pgn \
    --output data/processed/positions_dataset.csv --depth 12
```

Every row is produced by the real Stockfish pipeline (same code path as the
API). Rows whose games lack a recorded result are skipped and reported.

## Training (guarded)

```bash
python -m argus.ml.train --dataset data/processed/positions_dataset.csv
```

The declared spec for `move_evaluation_regression` requires ≥ 2,000 training
rows and ≥ 300 validation/test rows. The smoke-test corpus has ~40 rows, so the
trainer **refuses with `InsufficientDataError` by design** — this demonstrates
the guardrail, it is not a failure. To actually train, bring a real corpus
(several thousand games minimum), drop it in `data/raw/`, and re-run both
commands. Never present metrics from a toy dataset as model accuracy.
