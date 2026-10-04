"""``CaissaEvaluationFramework`` (§3): register benchmark suites and run them.

The framework owns three things and nothing else:

* **A registry of suites.** A suite is a named unit of evaluation with a
  ``run(context)`` that returns a :class:`~argus.evaluation.results.SuiteResult`.
  Registration is explicit, so the full set of benchmarks is one readable list.
* **The evaluation context.** Whatever a suite may need — a live engine, a
  repository root, the dataset registry — is handed to it, so a suite never
  reaches out and creates global state. A suite that needs the engine declares
  ``requires_engine``, and when no engine is available the suite is *skipped with
  the reason*, never silently passed.
* **The run.** ``run()`` runs the registered suites in order, times each one, and
  assembles an :class:`EvaluationReport` with the reproducibility metadata and the
  quality-gate outcomes.

The framework imposes no policy on what a suite checks. Policy lives in the
suites and in ``gates.py``, so a check's reason for blocking a release is visible
where the check is written.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from argus.evaluation.datasets import EvaluationDataset, dataset_registry, dataset_version
from argus.evaluation.gates import evaluate_gates, release_decision
from argus.evaluation.metadata import RunMetadata
from argus.evaluation.results import EvaluationReport, SuiteResult


#: Sentinel meaning "find and use an engine automatically". Passing ``None``
#: explicitly means the opposite — *no* engine — so ``run_evaluation.py
#: --no-engine`` and the offline quality check really are offline. (Before this
#: distinction existed, ``engine=None`` fell through to auto-detection and started
#: Stockfish anyway, which made the offline check anything but.)
AUTO_ENGINE = object()


class Suite(Protocol):
    """What the framework requires of a benchmark suite."""

    name: str
    title: str
    requires_engine: bool

    def run(self, context: "EvaluationContext") -> SuiteResult: ...


@dataclass
class EvaluationContext:
    """Everything a suite may use, assembled once per run."""

    engine: Any | None = None
    engine_available: bool = False
    engine_reason: str = ""
    repo_root: Path = field(default_factory=lambda: Path.cwd())
    datasets: dict[str, EvaluationDataset] = field(default_factory=dataset_registry)
    strict: bool = False

    def dataset_version(self, dataset_id: str) -> str:
        return dataset_version(dataset_id)


@dataclass
class FunctionSuite:
    """A suite backed by a plain function — the common case."""

    name: str
    title: str
    function: Callable[[EvaluationContext], SuiteResult]
    requires_engine: bool = False
    dataset_id: str | None = None

    def run(self, context: EvaluationContext) -> SuiteResult:
        return self.function(context)


class CaissaEvaluationFramework:
    """Run the registered benchmark suites and produce one report."""

    def __init__(
        self,
        *,
        engine: Any = AUTO_ENGINE,
        repo_root: Path | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> None:
        self._engine = engine
        self._auto_engine = engine is AUTO_ENGINE
        self._repo_root = repo_root or Path.cwd()
        self._configuration = configuration or {}
        self._suites: list[Suite] = []

    # --- registry -------------------------------------------------------------

    def register(self, suite: Suite) -> None:
        """Add a suite. Registering the same name twice replaces it."""
        self._suites = [s for s in self._suites if s.name != suite.name]
        self._suites.append(suite)

    def register_function(
        self,
        name: str,
        title: str,
        function: Callable[[EvaluationContext], SuiteResult],
        *,
        requires_engine: bool = False,
        dataset_id: str | None = None,
    ) -> None:
        self.register(
            FunctionSuite(
                name=name,
                title=title,
                function=function,
                requires_engine=requires_engine,
                dataset_id=dataset_id,
            )
        )

    @property
    def suites(self) -> list[Suite]:
        return list(self._suites)

    # --- engine ---------------------------------------------------------------

    def _resolve_engine(self) -> tuple[Any | None, bool, str]:
        """The engine to use, whether it is available, and the honest reason."""
        if not self._auto_engine:
            if self._engine is None:
                return None, False, "no engine requested for this run"
            info = self._engine.info() if hasattr(self._engine, "info") else {"available": True}
            if info.get("available", True):
                return self._engine, True, ""
            return self._engine, False, info.get("reason", "engine unavailable")
        try:
            from argus.analysis.engine.stockfish import StockfishEngine, locate_stockfish

            if locate_stockfish() is None:
                return None, False, (
                    "Stockfish binary not found; set ARGUS_STOCKFISH_PATH or install Stockfish"
                )
            engine = StockfishEngine()
            return engine, True, ""
        except Exception as exc:  # noqa: BLE001 — an unavailable engine is a skip, not a crash
            return None, False, f"engine unavailable: {exc}"

    # --- run ------------------------------------------------------------------

    def run(self, *, only: list[str] | None = None) -> EvaluationReport:
        """Run the suites, in registration order, and assemble the report."""
        engine, available, reason = self._resolve_engine()
        context = EvaluationContext(
            engine=engine,
            engine_available=available,
            engine_reason=reason,
            repo_root=self._repo_root,
            strict=False,
        )
        selected = self._suites
        if only:
            wanted = set(only)
            selected = [s for s in self._suites if s.name in wanted]

        suites: list[SuiteResult] = []
        for suite in selected:
            if suite.requires_engine and not available:
                suites.append(
                    SuiteResult(
                        suite=suite.name,
                        title=suite.title,
                        checks=[],
                        note=f"skipped — {reason}",
                    )
                )
                continue
            started = time.perf_counter()
            try:
                result = suite.run(context)
            except Exception as exc:  # noqa: BLE001 — a crashing suite is a recorded failure
                from argus.evaluation.results import CheckStatus, CheckResult

                result = SuiteResult(
                    suite=suite.name,
                    title=suite.title,
                    checks=[
                        CheckResult(
                            name=f"{suite.name}.crashed",
                            status=CheckStatus.FAIL,
                            detail=f"{type(exc).__name__}: {exc}",
                            critical=True,
                        )
                    ],
                )
            result.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            # Provenance is stamped from the registered dataset, once, here — a
            # suite does not write its own version string, so a report cannot
            # disagree with the registry about which data was used.
            dataset_id = getattr(suite, "dataset_id", None)
            if dataset_id:
                version = context.dataset_version(dataset_id)
                result.dataset_version = (
                    f"{dataset_id}@{version}" if version != "unknown" else dataset_id
                )
            suites.append(result)

        used_datasets = sorted({s.dataset_version for s in suites if s.dataset_version})
        metadata = RunMetadata.capture(
            engine=engine if available else None,
            configuration=self._configuration,
            dataset_version="+".join(used_datasets) if used_datasets else "none",
        )
        gates = evaluate_gates(suites, scoped={s.name for s in selected} if only else None)
        blocked, blockers = release_decision(gates, suites)
        report = EvaluationReport(
            metadata=metadata,
            suites=suites,
            gates=gates,
            release_blocked=blocked,
            release_blockers=blockers,
        )
        if engine is not None and self._auto_engine and hasattr(engine, "close"):
            engine.close()
        return report


__all__ = [
    "CaissaEvaluationFramework",
    "AUTO_ENGINE",
    "EvaluationContext",
    "FunctionSuite",
    "Suite",
]
