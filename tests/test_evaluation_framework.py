"""Tests for the Phase 14 evaluation framework itself.

The framework is the thing that decides whether Caissa is correct, so it is held
to the same standard: it must run, its deterministic suites must pass, a failing
suite must block a release, and a report must be renderable and reproducible. The
engine suite is excluded here (it is marked ``engine`` and run separately) so the
module stays fast and offline.
"""

from __future__ import annotations

import pytest

from argus.evaluation import build_framework
from argus.evaluation.gates import GATE_DEFINITIONS, evaluate_gates, release_decision
from argus.evaluation.report import render_text_report
from argus.evaluation.results import (
    CheckResult,
    CheckStatus,
    SuiteResult,
    check,
)

#: Suites that need no network, no engine and no database.
OFFLINE_SUITES = [
    "chess_rules",
    "fen_benchmark",
    "pgn_benchmark",
    "move_classification",
    "accuracy",
    "game_intelligence",
    "player_intelligence",
    "opponent",
    "training",
    "ml_evaluation",
    "agent",
    "knowledge",
    "graph",
    "realtime",
    "security",
    "privacy",
]


def test_offline_suites_all_pass() -> None:
    report = build_framework(engine=None).run(only=OFFLINE_SUITES)
    failures = [
        f"{suite.suite}:{result.name} — {result.detail}"
        for suite in report.suites
        for result in suite.checks
        if result.status is CheckStatus.FAIL
    ]
    assert failures == [], "\n".join(failures)
    assert report.total_passed > 0
    assert not report.release_blocked, report.release_blockers


def test_no_engine_means_no_engine() -> None:
    """``engine=None`` must not silently auto-start Stockfish (it used to)."""
    report = build_framework(engine=None).run(only=["engine"])
    assert len(report.suites) == 1
    suite = report.suites[0]
    assert suite.checks == []
    assert "no engine requested" in suite.note


def test_unknown_suite_name_is_ignored() -> None:
    report = build_framework(engine=None).run(only=["not_a_suite"])
    assert report.suites == []


def test_every_gate_names_suites_that_exist() -> None:
    framework = build_framework(engine=None)
    registered = {suite.name for suite in framework.suites}
    for gate in GATE_DEFINITIONS:
        missing = set(gate.suites) - registered
        assert not missing, f"{gate.name} references unknown suites: {missing}"


def test_a_failing_suite_blocks_its_gate() -> None:
    failing = SuiteResult(
        suite="chess_rules",
        title="Chess rule correctness",
        checks=[
            CheckResult(
                name="illegal move accepted",
                status=CheckStatus.FAIL,
                detail="a king was captured",
                critical=True,
            )
        ],
    )
    gates = evaluate_gates([failing])
    chess_gate = next(g for g in gates if g.name == "CHESS_GATE")
    assert chess_gate.passed is False
    blocked, blockers = release_decision(gates, [failing])
    assert blocked is True
    assert any("illegal chess state" in b for b in blockers)


def test_a_passing_suite_unblocks_its_gate() -> None:
    passing = SuiteResult(
        suite="chess_rules",
        title="Chess rule correctness",
        checks=[check("legal moves all validate", True)],
    )
    gates = evaluate_gates([passing])
    chess_gate = next(g for g in gates if g.name == "CHESS_GATE")
    assert chess_gate.passed is True


def test_an_all_skipped_suite_does_not_pass_its_gate() -> None:
    skipped = SuiteResult(
        suite="engine",
        title="Engine correctness",
        checks=[
            CheckResult(name="x", status=CheckStatus.SKIP),
            CheckResult(name="y", status=CheckStatus.SKIP),
        ],
    )
    gates = evaluate_gates([skipped])
    engine_gate = next(g for g in gates if g.name == "ENGINE_GATE")
    assert engine_gate.passed is False


def test_report_is_serialisable_and_renders() -> None:
    report = build_framework(engine=None).run(only=["chess_rules", "fen_benchmark"])
    payload = report.to_payload()
    assert payload["summary"]["suites"] == 2
    assert payload["metadata"]["evaluation_methodology_version"]
    text = render_text_report(report)
    assert "Caissa Evaluation Report" in text
    assert "RESULT" not in text  # the header is not a fabricated claim
    assert "commit" in text.lower()


def test_reproducibility_metadata_captures_versions() -> None:
    report = build_framework(engine=None).run(only=["fen_benchmark"])
    metadata = report.metadata.to_payload()
    assert metadata["git_commit"]
    assert metadata["timestamp"]
    assert metadata["evaluation_methodology_version"]
    # Every subsystem version is recorded, or honestly "unknown".
    assert metadata["versions"]
    assert all(isinstance(value, str) for value in metadata["versions"].values())


@pytest.mark.parametrize("suite_name", OFFLINE_SUITES)
def test_each_suite_runs_and_produces_checks(suite_name: str) -> None:
    report = build_framework(engine=None).run(only=[suite_name])
    assert len(report.suites) == 1
    assert report.suites[0].checks, f"{suite_name} produced no checks"
    # Provenance is never a bare "unknown": it is a dataset id, or nothing.
    version = report.suites[0].dataset_version
    assert version != "unknown"
    if version is not None:
        assert "@" in version, f"{suite_name} dataset version is not id-tagged: {version!r}"


def test_dataset_provenance_is_stamped_from_the_registry() -> None:
    """A suite that declares a dataset reports its id@version, from one source."""
    report = build_framework(engine=None).run(only=["chess_rules", "fen_benchmark"])
    versions = {suite.suite: suite.dataset_version for suite in report.suites}
    assert versions["chess_rules"] == "chess-positions@14.0"
    assert versions["fen_benchmark"] == "fen-cases@14.0"
    metadata_version = report.metadata.to_payload()["dataset_version"]
    assert "chess-positions@14.0" in metadata_version
    assert "unknown" not in metadata_version


def test_a_run_with_no_datasets_says_none_not_unknown() -> None:
    """"No dataset" and "unknown" are different facts, and are reported as such."""
    report = build_framework(engine=None).run(only=["privacy"])
    assert report.suites[0].dataset_version is None
    assert report.metadata.to_payload()["dataset_version"] == "none"
