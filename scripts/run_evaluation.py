#!/usr/bin/env python
"""Run the Caissa evaluation framework and write its report (Phase 14 §18/§39).

Usage:

    python scripts/run_evaluation.py                 # all suites, both reports
    python scripts/run_evaluation.py --no-engine     # skip engine-required suites
    python scripts/run_evaluation.py --only chess_rules fen_benchmark
    python scripts/run_evaluation.py --report-dir evaluation/reports

The command exits non-zero when a release blocker is present, so it can be used
as a CI gate directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "argus"))

from argus.evaluation import build_framework  # noqa: E402
from argus.evaluation.report import (  # noqa: E402
    render_text_report,
    write_json_report,
    write_text_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Caissa evaluation framework.")
    parser.add_argument("--only", nargs="*", default=None, help="run only these suites")
    parser.add_argument(
        "--no-engine",
        action="store_true",
        help="do not start Stockfish; engine suites are skipped with a reason",
    )
    parser.add_argument(
        "--report-dir",
        default=str(REPO_ROOT / "evaluation" / "reports"),
        help="where to write the JSON and text reports",
    )
    parser.add_argument("--json", default=None, help="explicit JSON report path")
    parser.add_argument("--text", default=None, help="explicit text report path")
    parser.add_argument(
        "--quiet", action="store_true", help="write reports without printing the summary"
    )
    args = parser.parse_args()

    framework = build_framework(repo_root=REPO_ROOT)
    if args.no_engine:
        # A null engine is asked for explicitly, so engine suites skip with a reason.
        framework = build_framework(engine=None, repo_root=REPO_ROOT)

    report = framework.run(only=args.only)

    report_dir = Path(args.report_dir)
    json_path = Path(args.json) if args.json else report_dir / "argus-evaluation.json"
    text_path = Path(args.text) if args.text else report_dir / "argus-evaluation.txt"
    write_json_report(report, json_path)
    write_text_report(report, text_path)

    if not args.quiet:
        print(render_text_report(report))
    print(f"\nreports written:\n  {json_path}\n  {text_path}")

    if report.release_blocked:
        print(
            json.dumps(
                {"release_blocked": True, "blockers": report.release_blockers}, indent=2
            )
        )
        return 1
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
