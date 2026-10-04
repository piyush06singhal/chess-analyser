#!/usr/bin/env python
"""Phase 6 CLI: build a dataset from raw PGN sources and run the first experiment.

Usage::

    # Build the layers and write the manifests + quality report
    python scripts/phase6_experiment.py build

    # Run the controlled baseline experiment (strict: refuses under-spec data)
    python scripts/phase6_experiment.py run

    # Exercise the whole ladder on under-spec data, clearly labelled exploratory
    python scripts/phase6_experiment.py run --exploratory

Nothing here fabricates data. A run that cannot be justified reports the missing
requirements instead of producing a number.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "packages" / "argus") not in sys.path:
    sys.path.insert(0, str(ROOT / "packages" / "argus"))

from argus.datasets.db_source import (  # noqa: E402
    DatabaseGameSource,
    DatasetScope,
)
from argus.datasets.importer import DatasetImporter  # noqa: E402
from argus.datasets.quality import render_quality_report  # noqa: E402
from argus.datasets.records import DatasetLayer  # noqa: E402
from argus.datasets.store import DatasetStore  # noqa: E402
from argus.ml.experiment_runner import (  # noqa: E402
    DATASET_VERSION_FALLBACK,
    build_dataset_from_ingestion,
    build_game_dataset,
    run_controlled_experiment,
)
from argus.ml.experiments import ExperimentTracker  # noqa: E402
from argus.ml.registry import ModelRegistry  # noqa: E402

DEFAULT_DATA_ROOT = ROOT / "data"
DEFAULT_RAW = ("data/raw",)


def _build_from(args: argparse.Namespace, store: DatasetStore):
    """Build the dataset from whichever source kind was asked for.

    Both paths converge on the same ingestion pipeline: only the reader differs.
    """
    if args.source_kind == "db":
        scope = DatasetScope(args.scope)
        source = DatabaseGameSource(
            args.db_url,
            scope=scope,
            owner_key=args.owner or None,
            label=args.source,
            where=args.where or None,
        )
        importer = DatasetImporter(source=args.source, source_version=args.source_version)
        ingestion = source.ingest(importer, allow_user_scope=args.allow_user_scope)
        provenance = source.describe()
        if args.limit:
            provenance["limit_applied"] = args.limit
        return build_dataset_from_ingestion(
            ingestion,
            dataset_id=args.dataset_id,
            source=args.source,
            source_version=args.source_version,
            store=store,
            write=True,
            scope=args.scope,
            provenance=provenance,
            files=[f"{args.db_url.split('@')[-1]}#scope={args.scope}"],
        )
    raw = _raw_paths(args.raw)
    if not raw:
        raise SystemExit(f"No PGN sources found under {args.raw}; add a corpus under data/raw/")
    return build_game_dataset(
        raw,
        dataset_id=args.dataset_id,
        source=args.source,
        source_version=args.source_version,
        store=store,
        write=True,
        scope=args.scope,
        provenance={"kind": "pgn", "paths": [str(path) for path in raw]},
    )


def _raw_paths(pattern: str) -> list[Path]:
    paths: list[Path] = []
    for entry in pattern.split(","):
        candidate = (ROOT / entry.strip()).resolve()
        if candidate.is_dir():
            paths.extend(sorted(candidate.glob("*.pgn")))
        elif candidate.is_file():
            paths.append(candidate)
    return paths


def _build(args: argparse.Namespace):
    store = DatasetStore(ROOT / args.data_root)
    built = _build_from(args, store)
    print(render_quality_report(built.quality))
    print()
    print("Written:")
    for key, value in built.written.items():
        print(f"  {key}: {value}")
    print("\nManifest:")
    print(json.dumps(built.manifests[0].model_dump(mode="json"), indent=2))
    return built


def _run(args: argparse.Namespace) -> int:
    store = DatasetStore(ROOT / args.data_root)
    built = _build_from(args, store)
    print(render_quality_report(built.quality))
    print()

    tracker = ExperimentTracker(store.layer_path(DatasetLayer.EXPERIMENTS))
    registry = ModelRegistry.load(ROOT / "data" / "models")
    result = run_controlled_experiment(
        built,
        tracker=tracker,
        registry=registry,
        dataset_version=args.dataset_version or DATASET_VERSION_FALLBACK,
        experiment_id=args.experiment_id,
        exploratory=args.exploratory,
    )
    print(json.dumps(result, indent=2, default=str))
    print()
    if result["status"] == "refused_insufficient_data":
        print(
            "No model was trained: the dataset does not meet the task's declared "
            "requirements. That refusal is the result — see the report above."
        )
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def shared(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--source-kind",
            choices=("pgn", "db"),
            default="pgn",
            help="Where the games come from (default: pgn files under data/raw)",
        )
        target.add_argument("--raw", default=",".join(DEFAULT_RAW), help="PGN file(s) or directory/directories")
        target.add_argument("--data-root", default="data", help="Dataset root (default: data)")
        target.add_argument("--dataset-id", default="argus_phase6", help="Dataset identifier")
        target.add_argument("--source", default="pgn corpus")
        target.add_argument("--source-version", default=None)
        target.add_argument(
            "--scope",
            choices=("global", "user"),
            default="global",
            help="Dataset scope; 'user' marks an account's own games",
        )
        target.add_argument(
            "--db-url",
            default=os.environ.get("ARGUS_DATABASE_URL", ""),
            help="SQLAlchemy URL for --source-kind db (default: $ARGUS_DATABASE_URL)",
        )
        target.add_argument("--owner", default=None, help="Identity key of the account, for user-scoped sources")
        target.add_argument("--where", default=None, help="Extra SQL predicate on the games table, e.g. 'g.analysis_status = \'analyzed\''")
        target.add_argument("--limit", type=int, default=None, help="Read at most N games from a database source")
        target.add_argument(
            "--allow-user-scope",
            action="store_true",
            help="Required for --scope user: private games never enter a dataset by accident",
        )

    build_parser = sub.add_parser("build", help="Ingest, validate, deduplicate, label and write the layers")
    shared(build_parser)

    run_parser = sub.add_parser("run", help="Run the controlled baseline experiment")
    shared(run_parser)
    run_parser.add_argument("--experiment-id", default="phase6-game-outcome-v1")
    run_parser.add_argument("--dataset-version", default=None)
    run_parser.add_argument(
        "--exploratory",
        action="store_true",
        help="Run the ladder on under-spec data, labelled exploratory (nothing is promoted)",
    )

    args = parser.parse_args(argv)
    if args.command == "build":
        _build(args)
        return 0
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
