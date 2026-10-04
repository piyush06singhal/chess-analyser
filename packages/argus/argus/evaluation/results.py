"""Evaluation results: the machine-readable shapes every suite produces (§39/§40).

A benchmark is only useful if its outcome is unambiguous. So a check is one of
four states and nothing else:

``pass``  the assertion held;
``fail``  the assertion did not hold — a real defect;
``skip``  the check could not run, with the reason (a missing engine, no data);
``warn``  the check held but something is worth recording.

A *critical* check is a release blocker when it fails. ``critical`` is set on the
check itself, not inferred, so the reason a check can block a release is visible
in the suite that owns it (§42).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    WARN = "warn"


@dataclass
class CheckResult:
    """One assertion: what was checked, and what happened."""

    name: str
    status: CheckStatus
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    critical: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "metrics": self.metrics,
            "critical": self.critical,
        }


def check(
    name: str,
    condition: bool,
    *,
    detail: str = "",
    metrics: dict[str, Any] | None = None,
    critical: bool = False,
) -> CheckResult:
    """A pass/fail check from a boolean condition."""
    return CheckResult(
        name=name,
        status=CheckStatus.PASS if condition else CheckStatus.FAIL,
        detail=detail,
        metrics=metrics or {},
        critical=critical,
    )


def skipped(name: str, reason: str) -> CheckResult:
    """A check that could not run, with the honest reason."""
    return CheckResult(name=name, status=CheckStatus.SKIP, detail=reason)


def warning(name: str, detail: str, *, metrics: dict[str, Any] | None = None) -> CheckResult:
    """A check that held but is worth recording."""
    return CheckResult(name=name, status=CheckStatus.WARN, detail=detail, metrics=metrics or {})


@dataclass
class SuiteResult:
    """The checks one benchmark suite produced."""

    suite: str
    title: str
    checks: list[CheckResult] = field(default_factory=list)
    dataset_version: str | None = None
    methodology_version: str | None = None
    duration_ms: float | None = None
    note: str = ""

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status is CheckStatus.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.status is CheckStatus.FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for c in self.checks if c.status is CheckStatus.SKIP)

    @property
    def warnings(self) -> int:
        return sum(1 for c in self.checks if c.status is CheckStatus.WARN)

    @property
    def critical_failures(self) -> list[CheckResult]:
        return [
            c for c in self.checks if c.status is CheckStatus.FAIL and c.critical
        ]

    @property
    def ok(self) -> bool:
        """A suite is OK when nothing failed (skips and warnings are recorded)."""
        return self.failed == 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "title": self.title,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "warnings": self.warnings,
            "ok": self.ok,
            "dataset_version": self.dataset_version,
            "methodology_version": self.methodology_version,
            "duration_ms": self.duration_ms,
            "note": self.note,
            "checks": [c.to_payload() for c in self.checks],
        }


@dataclass
class EvaluationReport:
    """The whole run: what was evaluated, against what, and with what outcome."""

    metadata: Any  # RunMetadata, kept loosely typed to avoid a circular import
    suites: list[SuiteResult] = field(default_factory=list)
    gates: list[Any] = field(default_factory=list)  # GateOutcome
    release_blocked: bool = False
    release_blockers: list[str] = field(default_factory=list)

    @property
    def total_passed(self) -> int:
        return sum(s.passed for s in self.suites)

    @property
    def total_failed(self) -> int:
        return sum(s.failed for s in self.suites)

    @property
    def total_skipped(self) -> int:
        return sum(s.skipped for s in self.suites)

    @property
    def total_warnings(self) -> int:
        return sum(s.warnings for s in self.suites)

    @property
    def ok(self) -> bool:
        """A run is OK when every suite passed and no gate blocked the release."""
        return all(s.ok for s in self.suites) and not self.release_blocked

    def to_payload(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_payload() if self.metadata else {},
            "summary": {
                "suites": len(self.suites),
                "passed": self.total_passed,
                "failed": self.total_failed,
                "skipped": self.total_skipped,
                "warnings": self.total_warnings,
                "ok": self.ok,
                "release_blocked": self.release_blocked,
                "release_blockers": self.release_blockers,
            },
            "suites": [s.to_payload() for s in self.suites],
            "gates": [g.to_payload() for g in self.gates],
        }


__all__ = [
    "CheckResult",
    "CheckStatus",
    "EvaluationReport",
    "SuiteResult",
    "check",
    "skipped",
    "warning",
]
