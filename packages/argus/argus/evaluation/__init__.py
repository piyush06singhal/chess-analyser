"""Caissa evaluation framework (Phase 14).

A repeatable, automated way to answer *whether Caissa is actually correct* rather
than merely working: benchmark suites for each subsystem, quality gates that
decide what may ship, release blockers for the failures that must never pass, a
machine-readable report, and the reproducibility metadata that makes a run
trustworthy.

The framework adds no chess logic and no product features; it measures what the
earlier phases already do. A suite that cannot run (no engine, no data) is
skipped with its reason, never quietly passed — the same honesty rule the rest of
Caissa follows.

    from argus.evaluation import build_framework

    report = build_framework().run()
    report.ok                 # every suite passed and no gate blocked a release
    report.to_payload()       # machine-readable
"""

from argus.evaluation.datasets import (
    EvaluationDataset,
    dataset_registry,
    dataset_version,
)
from argus.evaluation.framework import (
    AUTO_ENGINE,
    CaissaEvaluationFramework,
    EvaluationContext,
    FunctionSuite,
    Suite,
)
from argus.evaluation.gates import (
    GATE_DEFINITIONS,
    GateDefinition,
    GateOutcome,
    evaluate_gates,
    release_decision,
)
from argus.evaluation.metadata import EVALUATION_METHODOLOGY_VERSION, RunMetadata
from argus.evaluation.model_baseline import (
    ModelBaseline,
    compare_candidate_to_baseline,
    load_model_baseline,
    model_baseline_path,
    record_model_baseline,
    save_model_baseline,
)
from argus.evaluation.model_regression import (
    ModelComparison,
    ModelEvaluation,
    compare_against_production,
    compare_models,
)
from argus.evaluation.report import (
    render_text_report,
    write_json_report,
    write_text_report,
)
from argus.evaluation.results import (
    CheckResult,
    CheckStatus,
    EvaluationReport,
    SuiteResult,
    check,
    skipped,
    warning,
)


def build_framework(
    *,
    engine=AUTO_ENGINE,
    repo_root=None,
    configuration: dict | None = None,
) -> CaissaEvaluationFramework:
    """A framework with every default suite registered, ready to run.

    ``engine`` defaults to auto-detection. Pass ``engine=None`` to run with **no**
    engine (engine-required suites then skip with that reason) — which is what
    ``run_evaluation.py --no-engine`` and the offline quality check do.
    """
    from argus.evaluation.suites import register_default_suites

    framework = CaissaEvaluationFramework(
        engine=engine, repo_root=repo_root, configuration=configuration
    )
    return register_default_suites(framework)


__all__ = [
    "EVALUATION_METHODOLOGY_VERSION",
    "GATE_DEFINITIONS",
    "AUTO_ENGINE",
    "CaissaEvaluationFramework",
    "CheckResult",
    "CheckStatus",
    "EvaluationContext",
    "EvaluationDataset",
    "EvaluationReport",
    "FunctionSuite",
    "GateDefinition",
    "GateOutcome",
    "ModelBaseline",
    "ModelComparison",
    "ModelEvaluation",
    "RunMetadata",
    "Suite",
    "SuiteResult",
    "build_framework",
    "check",
    "compare_against_production",
    "compare_candidate_to_baseline",
    "compare_models",
    "dataset_registry",
    "dataset_version",
    "evaluate_gates",
    "load_model_baseline",
    "model_baseline_path",
    "record_model_baseline",
    "release_decision",
    "save_model_baseline",
    "render_text_report",
    "skipped",
    "warning",
    "write_json_report",
    "write_text_report",
]
