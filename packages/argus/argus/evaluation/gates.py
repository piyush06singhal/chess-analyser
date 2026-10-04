"""Quality gates and release blockers (§41/§42).

A gate groups the suites that answer one question — "is chess handled
correctly?", "may a user be shown a number?", "is a game's state consistent?" —
and passes only when every suite in it passed. A **critical** gate failing blocks
a release.

Release blockers are deliberately narrow and enumerated: an illegal chess state, a
data-integrity failure, a cross-user access, a fabricated number, an invalid engine
perspective, a broken training solution, a prediction-leakage finding, a critical
security failure, or a live-game synchronization corruption. A busy but honest
warning never blocks a release; a correctness or privacy failure always does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from argus.evaluation.results import CheckStatus, SuiteResult


@dataclass(frozen=True)
class GateDefinition:
    """One quality gate: which suites decide it, and how much it matters."""

    name: str
    description: str
    suites: tuple[str, ...]
    critical: bool = True


#: The gate set. Order is the order they are reported in.
GATE_DEFINITIONS: tuple[GateDefinition, ...] = (
    GateDefinition(
        name="CHESS_GATE",
        description="Legal moves, FEN validity and PGN parsing are correct.",
        suites=("chess_rules", "fen_benchmark", "pgn_benchmark"),
    ),
    GateDefinition(
        name="ENGINE_GATE",
        description="The engine returns correct, perspective-normalised results.",
        suites=("engine",),
    ),
    GateDefinition(
        name="INTELLIGENCE_GATE",
        description="Game and player intelligence are rule- and sample-correct.",
        suites=("game_intelligence", "move_classification", "accuracy", "player_intelligence"),
    ),
    GateDefinition(
        name="ML_GATE",
        description="Metrics, calibration and gating are computed correctly.",
        suites=("ml_evaluation",),
    ),
    GateDefinition(
        name="AGENT_GATE",
        description="The agent selects tools, stays grounded and refuses to invent.",
        suites=("agent",),
    ),
    GateDefinition(
        name="TRAINING_GATE",
        description="Exercises are valid and the acceptance policy is fair.",
        suites=("training",),
    ),
    GateDefinition(
        name="OPPONENT_GATE",
        description="Opponent claims are evidenced, never predictions.",
        suites=("opponent",),
    ),
    GateDefinition(
        name="GRAPH_GATE",
        description="Graph nodes, edges, evidence and traversal are consistent.",
        suites=("graph",),
    ),
    GateDefinition(
        name="KNOWLEDGE_GATE",
        description="Knowledge is sourced, deterministic and refuses the absent.",
        suites=("knowledge",),
    ),
    GateDefinition(
        name="REALTIME_GATE",
        description="Live-game state and event ordering stay consistent.",
        suites=("realtime",),
    ),
    GateDefinition(
        name="SECURITY_GATE",
        description="Authorization, injection and secret handling hold.",
        suites=("security", "privacy"),
    ),
    GateDefinition(
        name="PERFORMANCE_GATE",
        description="Core operations stay within their measured budgets.",
        suites=("performance",),
    ),
)


@dataclass
class GateOutcome:
    """Whether one gate passed, and why."""

    name: str
    description: str
    critical: bool
    passed: bool
    reason: str = ""
    suites: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "critical": self.critical,
            "passed": self.passed,
            "reason": self.reason,
            "suites": self.suites,
            "failed_checks": self.failed_checks,
        }


def evaluate_gates(
    suites: list[SuiteResult], *, scoped: set[str] | None = None
) -> list[GateOutcome]:
    """Decide each gate from the suites that ran.

    ``scoped`` names the suites this run was asked to cover. When it is given, a
    gate whose suites are all outside the scope is omitted rather than reported as
    failed — a subset run does not silently claim to have certified a gate it did
    not exercise. Without ``scoped`` (a full run) an absent suite still fails its
    gate, because a full certification must cover every gate.
    """
    by_name = {suite.suite: suite for suite in suites}
    outcomes: list[GateOutcome] = []
    for definition in GATE_DEFINITIONS:
        if scoped is not None and not (set(definition.suites) & scoped):
            continue
        present = [name for name in definition.suites if name in by_name]
        if not present:
            outcomes.append(
                GateOutcome(
                    name=definition.name,
                    description=definition.description,
                    critical=definition.critical,
                    passed=False,
                    reason="no suite for this gate ran",
                )
            )
            continue
        failed_suites = [name for name in present if not by_name[name].ok]
        # A suite that ran but was entirely skipped (e.g. no engine) is not a pass.
        all_skipped = all(
            by_name[name].checks and by_name[name].skipped == len(by_name[name].checks)
            for name in present
        )
        failed_checks = [
            f"{name}:{c.name}"
            for name in present
            for c in by_name[name].checks
            if c.status is CheckStatus.FAIL
        ]
        if all_skipped:
            outcomes.append(
                GateOutcome(
                    name=definition.name,
                    description=definition.description,
                    critical=definition.critical,
                    passed=False,
                    reason="every suite for this gate was skipped",
                    suites=present,
                )
            )
            continue
        passed = not failed_suites
        outcomes.append(
            GateOutcome(
                name=definition.name,
                description=definition.description,
                critical=definition.critical,
                passed=passed,
                reason="" if passed else f"failing suites: {', '.join(failed_suites)}",
                suites=present,
                failed_checks=failed_checks,
            )
        )
    return outcomes


#: Which suites contain the checks that can block a release, and the block reason.
#: A check with ``critical=True`` in one of these suites is a release blocker.
_BLOCKING_SUITES: dict[str, str] = {
    "chess_rules": "illegal chess state",
    "fen_benchmark": "invalid FEN handling",
    "pgn_benchmark": "data integrity: PGN parsing",
    "engine": "invalid engine perspective",
    "training": "broken training solution",
    "agent": "fabricated numerical output",
    "security": "critical security issue",
    "privacy": "cross-user data access",
    "graph": "data integrity: graph provenance",
    "realtime": "live-game synchronization corruption",
    "ml_evaluation": "prediction leakage",
}


def release_decision(
    gates: list[GateOutcome], suites: list[SuiteResult]
) -> tuple[bool, list[str]]:
    """Whether a release is blocked, and the honest reasons."""
    blockers: list[str] = []
    for gate in gates:
        if gate.critical and not gate.passed:
            blockers.append(f"{gate.name}: {gate.reason or 'gate failed'}")

    for suite in suites:
        reason = _BLOCKING_SUITES.get(suite.suite)
        if reason is None:
            continue
        for result in suite.checks:
            if result.status is CheckStatus.FAIL and result.critical:
                blockers.append(f"{reason}: {suite.suite}:{result.name}")

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique = [b for b in blockers if not (b in seen or seen.add(b))]
    return bool(unique), unique


__all__ = [
    "GATE_DEFINITIONS",
    "GateDefinition",
    "GateOutcome",
    "evaluate_gates",
    "release_decision",
]
