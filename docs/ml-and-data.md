# Caissa — ML and data design

## Caissa — ML Strategy

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
| `sklearn_pipelines.py` | scikit-learn implementation: validation-gated training, held-out evaluation, versioned metadata |
| `build_dataset.py` | PGN corpus → engine-evaluated per-move feature CSV (real Stockfish pipeline, provenance metadata) |
| `train.py` | guarded train/evaluate/persist CLI; refuses under-spec datasets with a clear report |
| `features.py` | raw ↔ derived feature separation (derived ML features over raw position features) |
| `model_store.py` | model versioning (versioned artifacts + metadata) |

## Dataset generation (real, engine-evaluated)

`python -m argus.ml.build_dataset data/raw/<corpus>.pgn --depth 12` analyzes
every game through the same Stockfish pipeline the API uses and writes rows
of raw board features + the engine evaluation at a recorded depth. Provenance
(source files, engine version, depth, row count, timestamp) is stored next to
the CSV. Games without a recorded result are skipped — labels must be real.

## Training gate (verified by tests)

The `move_evaluation_regression` spec declares the *current honest minimums*:
≥ 2,000 training rows, ≥ 300 validation/test rows, 11 declared feature
columns. Training on less raises `InsufficientDataError` — verified against
the Phase 1 smoke corpus (46 rows), which is refused with a readable report.
No model artifact and no metrics are produced from under-spec data.

## Interfaces (contracts for later implementations)

- **`PredictionModel`** — `metadata()` + `predict(features)`; predictions
  always carry the model name/version used.
- **`TrainingPipeline`** — `train(rows)` validates requirements first, then
  delegates to `_train` (implementation-only hook); `train_with_splits`
  additionally performs the deterministic split so held-out metrics cannot
  leak into training.
- **`EvaluationPipeline`** — `evaluate(model, rows, split)` returns
  `EvaluationMetrics` measured on the held-out split (MAE/R² for regression;
  accuracy/macro-F1 for classification).
- **`DatasetValidator`** — `validate_dataset` / `validate_dataset_strict`.

## Stack direction

- **Phase 2 (initial)**: scikit-learn for the first genuinely data-backed
  problems (e.g. move-quality calibration over large engine-evaluated
  datasets, player-style clustering).
- **Later**: XGBoost/PyTorch behind the same interfaces — no application
  rewrites; models stay decoupled from UI and API.

## Phase 6 — dataset engineering and statistical foundation

The candidate problems above are no longer deferred: they are **declared** as five
`PredictionTaskDefinition`s with exact targets, label definitions, data
requirements, leakage risks and evaluation metrics. Declaring a task is not a
promise to ship it — each is evaluated independently and each may end up
permanently unavailable.

What Phase 6 adds, and where it is documented:

| Area | Module | Document |
| --- | --- | --- |
| Ingestion, validation, dedupe, manifests, scope | `argus.datasets.*` | [ml-and-data.md](ml-and-data.md) |
| Tasks, splits, leakage, features, labels | `argus.datasets.{splits,leakage,features,labels}` | [ml-and-data.md](ml-and-data.md) |
| Baselines, metrics, calibration, error analysis, gating, registry | `argus.ml.*` | [ml-and-data.md](ml-and-data.md) |
| Experiment tracking and the first controlled run | `argus.ml.{experiments,experiment_runner}` | [ml-and-data.md](ml-and-data.md) |

The pipeline the phase implements:

```
raw games → validation → dedupe → labels → features → splits
         → leakage checks → baselines → evaluation → calibration
         → error analysis → production gate → (maybe) a served prediction
```

Three disciplines are enforced in code rather than promised in prose:

* **Leakage is tested, not asserted.** A first-class suite covers game,
  position, player, temporal and feature leakage, including negative cases where a
  deliberately mutated split must be caught.
* **A refusal is a result.** When the declared data requirements are not met, no
  model is trained and the missing requirements are recorded. An `exploratory`
  mode can exercise the machinery on under-spec data, but it is labelled as such
  and can never promote a model.
* **Privacy is structural.** User games are a distinct dataset scope that requires
  an explicit opt-in and is never merged into global training data.

### Current status: nothing is served

No model has passed production gating, because the corpora available here are 2–4
games against a declared floor of 20,000 for the first task. `PredictionService`
returns `unavailable` with the missing requirements for every task, and the
`/api/predictions/*` routes expose that as a normal response. This is the phase
working correctly: the alternative — a number produced from two games — is the
failure mode the phase was built to prevent.

## Versioning

Models are versioned artifacts: name, version, problem type, trained_at,
dataset name, feature columns, label column (`ModelMetadata`). The store keeps
artifacts alongside their metadata so every prediction is traceable to the
exact model version and dataset that produced it.

Phase 6 extends the version set that travels with a result:

| Version | Constant | Meaning |
| --- | --- | --- |
| Processing | `PROCESSING_VERSION` | how raw games became records |
| Feature | `FEATURE_VERSION` | the feature definitions in force |
| Label | `LABEL_VERSION` | how labels were generated |
| Dataset | `dataset_version` | the specific dataset build |
| Model | `model_id` / `version` | the artifact that produced a prediction |

A prediction that cannot name all six is not reproducible, and an experiment that
cannot name all six is not evidence.

## Model evaluation

Phase 6 exists to answer one question honestly: *is there useful predictive
signal in Caissa's features?* Everything in this document is machinery for
answering it without inflating the answer.

The rules the phase enforces:

1. No metric is reported without its split, its dataset version and its baseline.
2. Accuracy alone is never reported; class imbalance makes it a lie by omission.
3. A score is not called a probability until its calibration has been measured.
4. A model is not exposed unless every gate passes — and a task with no passing
   model returns "unavailable", with the reason.

## Task definitions

`PredictionTaskDefinition` (`argus.ml.tasks`) declares, before any experiment:

| Field | Meaning |
| --- | --- |
| `task_name` | stable identifier |
| `target` / `target_values` | what is predicted, and the exact label space |
| `unit_of_prediction` | game, position or move — the row a prediction is about |
| `input_features` | the declared feature groups this task may use |
| `label_definition` | how labels are generated, and from what |
| `data_requirements` | the declared floor for a meaningful evaluation |
| `evaluation_metrics` | which metrics are meaningful here, and why |
| `leakage_risks` | the specific ways this task leaks, written down |
| `recommended_split` | the split the task's claims are valid under |
| `production_status` | `experimental` until gates pass |
| `hypothesis` / `limitations` | what the task would and would not show |

Five candidate tasks are declared:

| Task | Unit | Target | Notes |
| --- | --- | --- | --- |
| `game_outcome` | game | white win / draw / black win | pre-game features only |
| `position_outcome` | position | white advantage / equal / black advantage | engine-derived |
| `position_difficulty` | position | complexity label from evaluation volatility | engine-derived |
| `move_error_risk` | move | error / not, using Caissa move classification | the user-relevant one |
| `player_performance` | player-game | future accuracy/CPL band, given history | needs long history |

Declaring five is not a promise to ship five. Each is evaluated independently and
each may end up permanently unavailable.

## Splitting

`argus.datasets.splits` implements four strategies, all of which keep a game's
positions together:

* **Random game split** — baselines. Fast, honest about nothing else.
* **Player-holdout** — test players never appear in training. The right split for
  "will this work for a new user?".
* **Temporal** — train on older games, validate later, test latest. The right
  split for anything that claims to predict the future.
* **Game-group** — the invariant the other three build on: positions from one game
  never cross a split boundary.

`TASK_SPLIT_GUIDANCE` states which split each task's claims are valid under, and
`SplitPlan` records the actual assignment so it can be re-derived.

## Leakage

The leakage suite is a first-class part of the phase, not a checklist item.
`LeakageValidator` returns a `LeakageReport` and `assert_no_leakage` fails a run.

Checks performed:

1. **Game leakage** — the same `game_id` cannot appear in two splits. Sets are
   derived from the actual records in each split, not from a cached summary, so a
   mutated plan cannot slip past.
2. **Position leakage** — positions from one game cannot cross splits.
3. **Temporal leakage** — no training record is dated after a validation or test
   record.
4. **Feature leakage** — every feature declares its `availability`
   (`pre_game`, `in_game`, `post_game`). A model for `game_outcome` may not use a
   `post_game` feature, which is the concrete case of "do not predict the result
   using the final evaluation".
5. **Player leakage** — under a player-holdout split, no test player appears in
   training.
6. **Label isolation** — test labels are never used to fit or select anything.

`tests/test_leakage.py` asserts each of these, including the negative cases: a
plan with a game deliberately moved across splits must be caught, and a feature
that is unavailable at prediction time must be rejected by name.

## Metrics

Implemented from first principles in `argus.ml.metrics` so evaluation does not
depend on an optional library, and verified against hand-computable cases.

Multiclass (game outcome) — accuracy, balanced accuracy, macro F1, per-class
precision/recall/F1, log loss, Brier score, confusion matrix.

Binary (error risk) — precision, recall, F1, ROC-AUC, PR-AUC, log loss.

Two deliberate details:

* **Macro F1 counts classes the model never predicts.** A baseline that always
  answers "white wins" scores 0.9 accuracy on a 90/10 corpus and a macro F1 of
  **0.4737** (one class perfect, the other unreachable) — which is the diagnostic
  that exposes it. An earlier version excluded unobserved classes and inflated
  this to 0.58; that was a bug, and it is now pinned by a test with the exact
  expected value.
* **Undefined is `None`, not `0.0`.** ROC-AUC with only one class present is
  `None`. Reporting `0.0` there would look like a catastrophic result instead of
  an unmeasurable one.

`ClassificationMetrics.notes` carries the reading, e.g. that accuracy flatters a
majority-class model.

## Calibration

`argus.ml.calibration` provides reliability curves, expected calibration error
(ECE), maximum calibration error, and Brier score, plus two calibrators
implemented without optional dependencies: **isotonic regression** (pool-adjacent
violators) and **Platt scaling** (gradient descent on log loss).

The question calibration answers is the only one that matters for a stated
probability: *when this model says 0.70, does the event happen about 70% of the
time?* Until that is measured, Caissa does not describe an output as a
probability, and the gate treats an unmeasured ECE as a **failure**, not as
"unknown but probably fine".

## Probability versus coverage

The prediction service keeps two concepts apart, and the API returns them as
separate fields:

* **`probabilities`** — the model's output, calibrated on held-out games, with its
  measured calibration error.
* **`data_coverage`** — how much data the model rests on: trained rows, dataset
  version, split strategy, test balanced accuracy, ECE, and a label such as
  `insufficient sample`.

There is no combined "confidence" number, because such a number is a fabrication:
it would be the product of two quantities that are not commensurable. A high
probability from a model trained on 300 rows is reported as a high probability
*with* `insufficient sample` coverage beside it, and the reader draws the
conclusion.

## Error analysis

`argus.ml.error_analysis` provides:

* **Slice metrics** — balanced accuracy per rating band, time class, opening,
  colour, seen/unseen players and game phase. An aggregate score can hide a model
  that is useless for exactly the players who would use it.
* **Subgroup spread** — the worst-minus-best slice gap, which the gate caps.
* **Misclassified examples** — the individual games the model got wrong, with its
  probability, so failures can be read rather than counted.
* **Permutation importance** — which features the model actually relies on, and
  which declared features contribute nothing.

## Production gating

`argus.ml.gating.evaluate_gates` runs ten checks and returns a `GateDecision` with
every individual result — including the ones that passed and the ones that could
not be measured.

| Gate | Purpose |
| --- | --- |
| `data_sufficiency` | the task's declared requirements, in full |
| `balanced_accuracy` | above threshold on the untouched test split |
| `macro_f1` | above threshold |
| `log_loss` | not worse than the three-class uniform value (1.099) |
| `lift_over_majority` | better than guessing the majority class |
| `lift_over_rating_baseline` | better than a rating lookup, by a real margin |
| `calibration` | measured ECE within threshold |
| `subgroup_stability` | no slice collapse |
| `leakage_checks` | the leakage suite passed |
| `reproducibility` | seed, dataset version and feature version recorded |

The data floor is `max(task requirement, policy floor)`: an operator may tighten a
requirement before shipping, and may never loosen it. (An earlier version wrapped
the counts in `bool()` and compared `True >= 20000`, which could never pass; that
was a real bug and the test suite caught it.)

Beating the majority baseline is explicitly **not** sufficient. A rating lookup is
a strong baseline for chess outcome, and a model that merely matches it is not a
product.

## Registry statuses

`EXPERIMENTAL` → `VALIDATED` → `PRODUCTION` → `RETIRED`.

Only `PRODUCTION` entries are readable by `PredictionService`. A registered
experimental model is not served — that case is tested.

## Prediction service

`PredictionService` is the only door between a model and a user. It can list task
availability and serve a prediction from a `PRODUCTION` model. It has no fallback
path to a number: with no production model, it returns
`PredictionUnavailable(reason=..., requirements=...)`.

The API surface is `apps/api/argus_api/routes/predictions.py`:

| Route | Behaviour |
| --- | --- |
| `GET /api/predictions/tasks` | per-task availability, reason and declared requirements |
| `GET /api/predictions/models` | the registry: registered models, their tasks and statuses |
| `GET /api/predictions/datasets` | dataset-layer inventory with byte counts and manifests |
| `GET /api/predictions/datasets/{id}/quality` | the dataset quality and bias report (Phase 6 §32/§33) |
| `GET /api/predictions/experiments` | the experiment index, including refusals |
| `POST /api/predictions/{task}` | a prediction, or a 200-level "unavailable" body with the reason |

Unavailability is returned as a normal response, not an error: "we do not have a
validated model for this" is information, not a fault.

## Current state

**No model has passed production gating. No prediction is served.** Every task
returns unavailable with its missing requirements. That is the honest outcome of
Phase 6 at the current corpus size, and the system is built so that it is a
first-class result rather than an embarrassment.

## Dataset engineering

Phase 6 turns chess games into datasets that can be *audited*. Nothing here
trains anything; this document describes how raw games become rows a model can
legitimately use, and how every claim about those rows is measured rather than
assumed.

The governing rule of the phase:

> A number that cannot be traced to a source, a version and a validation result
> is not a dataset statistic. It is a guess.

## Directory layout

```
data/
├── raw/               # source corpora, exactly as obtained (never written to)
├── validated/         # records that passed validation
├── normalized/        # the canonical layer: one record per game (+ manifests)
├── positions/         # position rows, sampled from games
├── engine_analysis/   # Stockfish outputs, tagged with their configuration
├── features/          # feature tables, versioned
├── labels/            # label tables, versioned
├── train/             # split layers
├── validation/
├── test/
├── experiments/       # one directory per experiment, never overwritten
└── README.md
```

The layers are distinct on purpose. Raw data is read-only: a pipeline bug must
never be able to destroy the thing you would re-run against. Processed layers are
derived and reproducible from raw plus the processing version.

## Records

`IngestedGame` (`argus.datasets.records`) is the single output format of every
importer. Key properties:

| Field | Meaning |
| --- | --- |
| `game_id` | content-derived: `sha256(moves)` plus a metadata suffix, so re-ingesting the same game yields the same id and two metadata variants stay distinct |
| `white` / `black` | `PlayerIdentity` — the original spelling **and** the normalized identity |
| `result` | the PGN result token, unmodified |
| `ply_count` | number of plies actually parsed |
| `moves_hash` | hash of the move sequence only |
| `metadata_hash` | hash of the metadata only |
| `time_class` | derived from `TimeControl` (`bullet`/`blitz`/`rapid`/`classical`/`unknown`) |
| `source_file`, `source_index` | where the record came from, for every error message |

The `game_id` deliberately derives from *moves*, not from the source row: the same
game imported twice, or from two sources, must collapse. Occurrence numbering
distinguishes genuinely repeated move sequences.

## Ingestion

Two source kinds feed the same pipeline. Only the reader differs.

### PGN files

```bash
python scripts/phase6_experiment.py build --source-kind pgn --raw data/raw
```

`DatasetImporter.ingest_file` / `ingest_directory` / `ingest_paths` split a file
into individual games before handing each to python-chess. Splitting matters: a
concatenated PGN with no blank line between games makes python-chess read the
*second* game's headers as belonging to the first, which silently corrupts
metadata. `iter_game_texts` performs that split once, explicitly.

### The Caissa database

```bash
python scripts/phase6_experiment.py build \
  --source-kind db --scope user --allow-user-scope \
  --db-url "$ARGUS_DATABASE_URL" --dataset-id argus_user_games
```

`DatabaseGameSource` streams rows from `games` joined to `game_moves` under a
server-side cursor, ordered by game id then ply, so one game is completed at a
time and nothing beyond the current game is held in memory. That is what allows
the same code to handle 2 games today and 2 million later.

Games are re-serialised from their **normalised columns** (headers plus the stored
SAN list) rather than reused from the stored `pgn_text`. Stored `pgn_text` may
contain many games in one blob and would ingest as duplicates; the normalised
columns are exactly one game, with one result, one rating pair and one date.

The move list is rendered with the colour carried explicitly
(`1. e4 e5 2. Nf3`), not inferred from position, so a game starting from a custom
FEN with Black to move renders `1... d5` instead of shifting every move number.
`tests/test_db_source.py` asserts the round trip move by move: the re-rendered PGN
must re-parse to exactly the stored SAN sequence.

### Adding another source

Implement an iterator of `(pgn_text, label)` — or of `SourcedGame` — and call
`build_dataset_from_ingestion`. Validation, deduplication, labelling, manifests
and quality reporting are then already done, identically.

## Validation

`validate_records` produces a `ValidationReport` with counts and per-record
issues. Checks include malformed PGN, empty/no-move games, illegal move
sequences, missing result, invalid rating values, missing player names, invalid
or impossible dates, impossible lengths, and duplicate detection.

Two rules:

* **Nothing is silently discarded.** Every rejection has a reason code, a source
  file and an index, and the count reaches the manifest.
* **Warnings are not failures.** A missing date does not invalidate a game — it
  makes the dataset less complete, and that is reported as missing metadata.

## Duplicate policy

Documented in `argus.datasets.dedupe` and printed in every manifest. The policy
is deliberately conservative:

* **Exact duplicate** (same moves, same metadata): dropped. It is the same record
  twice.
* **Metadata variant** (same moves, different metadata): **kept** and reported.
  Choosing between two records is a judgement about which source is more
  trustworthy, and a kept duplicate is visible while a wrongly deleted game is
  not.
* **Short or repetitive games** (e.g. the same miniature played twice): never
  dropped. Identical openings are not identical games, and deduplicating on
  length would quietly erase legitimate repetition.

## Manifests

Every written dataset carries a sibling `*.manifest.json`:

```json
{
  "dataset_id": "argus_user_games",
  "layer": "normalized",
  "source": "Caissa database",
  "scope": "user",
  "provenance": { "kind": "database", "label": "argus-db", "scope": "user" },
  "collection_date": "2026-09-30T19:23:36Z",
  "number_of_games": 2,
  "players": 3,
  "rating_coverage": 1.0,
  "time_control_coverage": 1.0,
  "time_control_distribution": { "rapid": 2 },
  "result_distribution": { "1-0": 2 },
  "duplicate_count": 0,
  "invalid_game_count": 0,
  "rejected_game_count": 0,
  "missing_metadata": { "date": 0, "rating": 0, "time_control": 0, "opening": 2 },
  "date_range": ["2026-09-13", "2026-09-21"],
  "processing_version": "6.0",
  "label_version": "6.0",
  "notes": ["..."]
}
```

`rating_coverage: 1.0` and `missing_metadata.opening: 2` are measurements, not
quality judgements: they say exactly which predictions this dataset can and
cannot support. A task requiring ratings cannot be attempted on a dataset with
`rating_coverage: 0.0`, and the readiness report says so by name.

The manifest never contains a database URL (which may hold credentials) — only the
dialect and an operator-supplied label.

## Scope: user data versus training data

`DatasetScope` separates:

* `GLOBAL` — public or neutral corpora.
* `USER` — an account's own games.

A user-scoped source raises `ValidationError` unless the caller passes
`allow_user_scope=True`. That is enforced in code
(`assert_scope_allowed`), not in documentation, and it is tested. The reasoning is
that the dangerous failure is not a deliberate decision to train on private games
— it is an accidental one.

The scope is recorded in the manifest, so the rule is checkable after the fact.
Phase 6 does **not** merge user games into a global training set, and the
architecture keeps `global_model` and `user_specific_statistics` separate
concepts for exactly this reason.

## Scaling

The pipeline is written to grow, and is explicit about where it currently is:

* **Streaming ingestion.** Files are split into games one at a time; the database
  is read under a streaming cursor. Games are never all loaded at once.
* **Chunked writes.** `DatasetStore.write_table` writes JSONL, one record per
  line, so a layer can be appended to and read back incrementally.
* **Columnar path reserved.** `data/processed/` already holds a Parquet-shaped
  position dataset from earlier phases. Parquet is the intended format at scale,
  but `pandas`/`pyarrow` are optional dependencies and the Phase 6 layers do not
  require them — a missing optional dependency must not break a run.
* **Honest current scale.** The largest corpus available here is measured in
  *dozens* of games. Every throughput claim in this document is about the design,
  not about an observed million-game run. No such run has happened.

## Reproducibility

Each build records `processing_version`, `source_version`, the source label, the
collection timestamp, and the label version. Experiments additionally record the
feature version, the split strategy, the random seed, dependency versions and the
full hyperparameters. See [ml-and-data.md](ml-and-data.md).

## Caissa — Data Strategy

See `data/README.md` for the concrete directory layout and rules. This
document explains the strategy behind it.

## Principles

1. **Do not fabricate data.** No synthetic datasets presented as real; no
   unverified labels.
2. **Deliberate selection.** Datasets are chosen for a specific prediction
   problem, documented, and versioned — nothing is downloaded randomly.
3. **Strict splits.** Training, validation, and test data are physically
   separate; test data is touched once, at final evaluation.
4. **Leakage prevention.** User-specific historical data is never mixed into
   global training/test sets without explicit, documented steps (e.g.
   splitting by player, not by row, for player-level predictions).

## Intended datasets (selected deliberately in a later phase)

| # | Dataset | Purpose | Requirements sketch |
| - | ------- | ------- | ------------------- |
| 1 | Large PGN corpus (master/online) | game + player intelligence, future prediction | reliable results/ratings/ECO metadata; ≥ 100k games |
| 2 | Engine-evaluated positions | move-quality datasets, ML training | generated by our pipeline; fixed depth; engine version recorded |
| 3 | Tactical positions | tactics detection, training generation | verified solutions from curated sources |
| 4 | Opening/ECO metadata | opening detection from move sequences | standard ECO book mapping |
| 5 | Player historical games | longitudinal profiling | per-player, consented sources |
| 6 | Generated feature datasets | model consumption | produced from 1–5 by `argus.ml.features` |

## Categories

- **Training data** — model fitting only.
- **Validation data** — hyperparameter selection only.
- **Test data** — final, one-time evaluation; metrics reported as measured.
- **User-specific historical data** — application database (per-user games and
  analyses); excluded from global datasets unless leakage is explicitly handled.

## Governance

- Every processed/features dataset records: source, generation date,
  generating script + version, row count, engine version (where relevant).
- `argus.ml.DatasetSpec` declares per-problem requirements (minimum samples,
  class balance, required columns); `validate_dataset_strict` refuses training
  when unmet.
- Dataset lineage: `raw/` → (deterministic scripts) → `processed/` →
  (feature extraction) → `features/` → (training pipelines) → `models/`.
- No claim of ML prediction accuracy is made without a held-out evaluation
  recorded in `EvaluationMetrics`.

## Phase 5 — player data (separate from any global dataset)

Phase 5 introduced a second kind of stored data, and the separation is
deliberate:

- **Per-player analysis snapshots** (`player_profiles`) are *derived* documents
  built from that player's own games. They are versioned
  (`profile_version` / `methodology_version` / `feature_version`) and cached by
  input signature.
- **Feature sets** (`GET /api/players/{id}/features`) are the interface a later
  phase could consume. Every feature is flagged `user_specific=True` and
  `training_eligible=False`, and carries its own definition, version and sample
  size.
- **Nothing is pooled automatically.** User games never enter `data/` and are
  never combined across players into a shared dataset. Any future use of user
  data for training requires an explicit, documented decision — and, for
  player-level prediction, splitting by *player* rather than by row to prevent
  leakage.
- **Sample size travels with every value.** A feature without a sample size is
  `None` by design, because a value without its sample is indistinguishable
  from a guess.

## Phase 6 — scope is a property of the dataset, not a convention

Phase 6 built the dataset layer the earlier phases were holding the door open
for. Its privacy position is structural rather than procedural:

- **Every dataset carries a scope.** `GLOBAL` (public/neutral corpora) or `USER`
  (an account's own games). The scope is written into the dataset manifest, so it
  survives the build and can be checked afterwards.
- **Private games require an explicit opt-in.** `DatabaseGameSource` *refuses*
  to ingest without `allow_user_scope=True`. The dangerous failure is not a
  deliberate decision to train on user games; it is an accidental one, and this
  makes the accidental case impossible.
- **`global_model` and `user_specific_statistics` stay separate.** Phase 6 does
  not pool user games into a shared training set, and the architecture has no
  path that does so implicitly.
- **Artifacts hold what analysis needs, not what the row happened to contain.**
  The database source reads a fixed set of normalised columns; a dataset is not a
  copy of a user's database. A database URL (which may carry credentials) is never
  written into a manifest — only the dialect and an operator-supplied label.
- **Labels are generated, never hand-edited.** Every label table records its
  label-definition version, and engine-generated labels additionally record the
  engine version, depth, MultiPV and configuration, because engine outputs from
  incompatible configurations are not comparable.
- **Refusal over a number.** Where the declared data requirements are not met,
  the honest output is a recorded refusal naming what is missing. Phase 6
  currently refuses every prediction task, so **no prediction is served and no
  accuracy number exists anywhere in the product**.

Full pipeline: `docs/ml-and-data.md`. Evaluation and gating:
`docs/ml-and-data.md`. Experiment records: `docs/ml-and-data.md`.

## Experiments

An experiment is the unit of evidence in Phase 6. It is a directory, it is never
overwritten, and it records a refusal as readily as a result.

## Layout

```
data/experiments/
├── experiments_index.json          # append-only index of every run
└── phase6-game-outcome-v1/
    ├── config.json                 # seeds, versions, model, hyperparameters
    ├── dataset_manifest.json       # which dataset, how complete, from where
    ├── metrics.json                # per-split metrics
    ├── confusion_matrix.json
    ├── calibration.json            # reliability curve, ECE, Brier
    ├── feature_importance.json     # permutation importance on the test split
    ├── gate.json                   # the production decision and every check
    └── report.md                   # the human-readable account
```

`ExperimentTracker.create` refuses to reuse an id, so a result can never be
silently replaced. `tracker.index()` returns every run, including refused ones —
a refusal with a reason is a finding, and hiding it would make the record
dishonest.

## What a run records

| Category | Fields |
| --- | --- |
| Identity | experiment id, task, dataset id and version |
| Provenance | processing version, feature version, label version |
| Design | split strategy, random seed, model name and hyperparameters |
| Cost | start time, duration, rows trained |
| Results | validation metrics, test metrics, calibration metrics |
| Judgement | gate decision and every individual check |
| Notes | free-text, human-written |

## Statuses

| Status | Meaning |
| --- | --- |
| `completed` | the ladder ran and metrics were measured |
| `refused_insufficient_data` | the task's declared requirements were not met; **no model was trained** |
| `exploratory_insufficient_data` | metrics were obtained on under-spec data, clearly labelled, nothing promoted |
| `failed` | the run errored; the error is recorded |

`exploratory` exists so the machinery can be exercised end to end on a small
corpus without turning a small number into a validation claim. An exploratory run
always fails the production gate, because an unmeasured requirement cannot unlock
production.

## The first controlled experiment

Task: **game outcome** (`white_win` / `draw` / `black_win`), pre-game features
only. Ladder: majority class → rating baseline → logistic regression →
gradient-boosted trees. Split: game-group (all positions of a game stay together)
and temporal, where dates permit.

Reproduce with:

```bash
# from a PGN corpus under data/raw
python scripts/phase6_experiment.py run --experiment-id phase6-game-outcome-v2

# from the games already stored in Caissa (user-scoped; opt-in required)
python scripts/phase6_experiment.py run --source-kind db --scope user \
  --allow-user-scope --db-url "$ARGUS_DATABASE_URL" \
  --experiment-id phase6-game-outcome-user-v1
```

### Result: refused

Both runs **refuse**. The refusal is the result, and it is recorded as such.

The available corpora are:

| Corpus | Games | Ratings | Notes |
| --- | --- | --- | --- |
| `data/raw/classic_miniatures.pgn` | 4 | none | short historical games |
| Caissa database (the two stored games) | 2 | 100% coverage | user-scoped, one player |

Against `game_outcome`'s declared requirement of 20,000 games / 14,000 training
rows / 3,000 test rows / 500 players with ratings, the readiness report names
exactly what is missing:

```
games: 2 available, 20000 required
train rows: 2 available, 14000 required
validation rows: 0 available, 3000 required
test rows: 0 available, 3000 required
players: 4 available, 500 required
ratings are required and the dataset carries none   (PGN corpus)
```

and the exploratory path additionally refuses below its own 60-row floor:

> Refused: 2 usable row(s) is below the 60-row floor where even an exploratory
> metric carries any information. No metric was produced.

That floor exists because a test-split metric computed on a handful of games is
not weak evidence — it is noise presented in the shape of evidence. Refusing is
the correct behaviour at this scale, and it is exactly what the phase was built to
do.

### Why the requirement is 20,000 games

The floor was set from the structure of the problem, before any data was seen:

* Three classes, and draws are the hard, underrepresented one. Distinguishing
  draws from decisive games needs enough draws to measure per-class recall at all.
* The production gate demands a *macro F1 lift over the rating baseline*. Ratings
  already predict chess results well, so the bar is a real lift over a strong
  baseline — which needs both a large training set and a large enough test set for
  the lift to be distinguishable from split noise.
* The gate also requires no subgroup collapse across rating bands, time classes
  and seen/unseen players. Each subgroup needs its own sample.

The number is a declared requirement, not a fit: it is written in
`PredictionTaskDefinition.data_requirements` before the experiment and is not
adjusted afterwards. Lowering it to make a run "succeed" would destroy the only
meaning it has.

## Reproducing a run

Every experiment is reproducible from its directory:

1. `config.json` pins the seed, split strategy, feature version and
   hyperparameters.
2. `dataset_manifest.json` identifies the dataset and its versions.
3. The dataset itself is rebuilt by the `build` command against the same raw
   source, and its `moves_hash`-derived ids make the rows stable across rebuilds.

No experiment result in this project has been produced by a run whose
configuration is unavailable.

