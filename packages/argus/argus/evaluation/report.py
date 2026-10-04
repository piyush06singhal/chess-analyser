"""Render an :class:`EvaluationReport` as text or JSON (§40).

The same report is written two ways: a machine-readable JSON payload that a CI
job or dashboard consumes, and a plain-text summary a person reads. Neither adds
a claim the report does not already hold — the summary is a rendering, not an
interpretation.
"""

from __future__ import annotations

import json
from pathlib import Path

from argus.evaluation.results import CheckStatus, EvaluationReport

_STATUS_MARK = {
    CheckStatus.PASS: "PASS",
    CheckStatus.FAIL: "FAIL",
    CheckStatus.SKIP: "SKIP",
    CheckStatus.WARN: "WARN",
}


def render_text_report(report: EvaluationReport) -> str:
    """A human-readable summary with the reproducibility header."""
    metadata = report.metadata
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Caissa Evaluation Report")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"Commit:        {metadata.git_commit}" + (" (dirty)" if metadata.git_dirty else ""))
    lines.append(f"Date:          {metadata.timestamp}")
    lines.append(f"Methodology:   {metadata.evaluation_methodology_version}")
    lines.append(
        f"Engine:        {metadata.engine_name} {metadata.engine_version or '(unavailable)'}"
    )
    lines.append(f"Model:         {metadata.model_version}")
    lines.append(f"Data:          {metadata.dataset_version}")
    versions = ", ".join(f"{k}={v}" for k, v in sorted(metadata.versions.items()))
    lines.append(f"Versions:      {versions}")
    lines.append(f"Python:        {metadata.python_version}")
    lines.append("")

    for gate in report.gates:
        marker = "PASS" if gate.passed else ("BLOCK" if gate.critical else "WARN")
        lines.append(f"{gate.name:<18} {marker:<6} {gate.description}")
        if not gate.passed and gate.reason:
            lines.append(f"{'':<18}        {gate.reason}")
    lines.append("")

    for suite in report.suites:
        heading = f"{suite.title}"
        lines.append("-" * 72)
        lines.append(
            f"{suite.suite}: {heading} "
            f"[{suite.passed} passed, {suite.failed} failed, "
            f"{suite.skipped} skipped, {suite.warnings} warned]"
        )
        provenance = []
        if suite.dataset_version:
            provenance.append(f"dataset: {suite.dataset_version}")
        if suite.methodology_version:
            provenance.append(f"methodology: {suite.methodology_version}")
        if provenance:
            lines.append("  " + "  ".join(provenance))
        if suite.note:
            lines.append(f"  note: {suite.note}")
        for result in suite.checks:
            mark = _STATUS_MARK[result.status]
            suffix = " [critical]" if result.critical else ""
            detail = f" — {result.detail}" if result.detail else ""
            lines.append(f"  {mark:<5} {result.name}{suffix}{detail}")
    lines.append("-" * 72)
    lines.append("")

    lines.append("=" * 72)
    lines.append(
        f"TOTAL: {report.total_passed} passed, {report.total_failed} failed, "
        f"{report.total_skipped} skipped, {report.total_warnings} warned"
    )
    if report.release_blocked:
        lines.append("RELEASE BLOCKED:")
        for blocker in report.release_blockers:
            lines.append(f"  - {blocker}")
    else:
        lines.append("No release blockers.")
    lines.append("=" * 72)
    return "\n".join(lines)


def write_json_report(report: EvaluationReport, path: str | Path) -> Path:
    """Write the machine-readable report, creating parent directories."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.to_payload(), indent=2, sort_keys=False))
    return target


def write_text_report(report: EvaluationReport, path: str | Path) -> Path:
    """Write the human-readable report, creating parent directories."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_text_report(report))
    return target


__all__ = [
    "render_text_report",
    "write_json_report",
    "write_text_report",
]
