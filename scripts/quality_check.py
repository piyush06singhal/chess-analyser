#!/usr/bin/env python
"""``python scripts/quality_check.py`` — the single quality command (Phase 14 §53).

Runs, in order, the checks that must hold before a change ships:

1. the evaluation framework's offline suites (correctness, security, graph, …);
2. the static correctness check (ruff: undefined names, dead locals, syntax);
3. the core pytest subset (evaluation harness, chess core, security, API smoke);
4. optionally the frontend type-check and lint, when ``--with-frontend`` is given
   and the Node toolchain is installed.

It exits non-zero if any step fails, so it can back a CI job or a git hook. It is
deliberately not the whole test suite: the full suite belongs in CI, not in the
inner loop. ``python scripts/run_evaluation.py`` is the heavier certification run.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "argus"))

PYTEST_SUBSET = [
    "tests/test_evaluation_framework.py",
    "tests/test_chess_core.py",
    "tests/test_security.py",
    "tests/test_intelligence_graph_core.py",
]


def _run(label: str, command: list[str], cwd: Path | None = None) -> bool:
    print(f"\n=== {label} ===")
    print("$ " + " ".join(command))
    result = subprocess.run(command, cwd=str(cwd or REPO_ROOT), check=False)
    ok = result.returncode == 0
    print(f"--- {label}: {'PASS' if ok else 'FAIL'} ---")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Caissa quality checks.")
    parser.add_argument(
        "--with-frontend",
        action="store_true",
        help="also type-check and lint the web app (needs node_modules)",
    )
    parser.add_argument(
        "--only-evaluation",
        action="store_true",
        help="run only the evaluation framework",
    )
    args = parser.parse_args()

    results: dict[str, bool] = {}

    # 1. The evaluation framework, offline.
    from argus.evaluation import build_framework

    print("\n=== evaluation framework (offline suites) ===")
    report = build_framework(engine=None).run()
    failures = [
        f"{suite.suite}:{result.name} — {result.detail}"
        for suite in report.suites
        for result in suite.checks
        if result.status.value == "fail"
    ]
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
    results["evaluation"] = not failures
    print(f"--- evaluation: {'PASS' if results['evaluation'] else 'FAIL'} "
          f"({report.total_passed} passed, {report.total_failed} failed, {report.total_skipped} skipped) ---")

    if not args.only_evaluation:
        # 2. Static correctness. Ruff's F rules are what caught four real
        #    undefined names when first run; skipping silently here would hide
        #    that class of defect again.
        import importlib.util

        if importlib.util.find_spec("ruff") is not None:
            results["ruff"] = _run(
                "ruff (F, E9 correctness)",
                [sys.executable, "-m", "ruff", "check", "packages", "apps", "scripts", "tests"],
            )
        else:
            print("\n=== ruff: SKIP (ruff is not installed; .venv/bin/pip install ruff) ===")

        # 3. The core pytest subset.
        results["pytest"] = _run(
            "pytest subset",
            [sys.executable, "-m", "pytest", "-p", "no:warnings", "-o", "addopts=", "-q", *PYTEST_SUBSET],
        )

    if args.with_frontend:
        web = REPO_ROOT / "apps" / "web"
        if (web / "node_modules").exists():
            results["typecheck"] = _run("frontend type-check", ["npx", "tsc", "--noEmit"], cwd=web)
            results["lint"] = _run("frontend lint", ["npm", "run", "lint"], cwd=web)
        else:
            print("\n=== frontend: SKIP (node_modules not installed) ===")

    print("\n=== summary ===")
    for label, ok in results.items():
        print(f"  {label:<12} {'PASS' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
