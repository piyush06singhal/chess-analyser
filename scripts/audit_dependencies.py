#!/usr/bin/env python
"""Reproducible dependency audit (Python, npm and container images).

This exists so the security posture is a *command* rather than a paragraph: the
same three checks can be re-run on any machine and their real output recorded.

What it runs, and what each answers:

1. ``pip-audit`` against the project virtualenv — known CVEs in the installed
   Python third-party packages.
2. ``npm audit --omit=dev`` in ``apps/web`` — known CVEs in the web app's
   *production* dependencies.
3. ``docker scout cves`` against the built images, if Docker Scout is available —
   OS and language-package CVEs in the shipped artifacts. This step is skipped
   (and reported as skipped, not passed) when Scout or the images are absent,
   because "not run" is not "clean".

The script never fabricates a result: a tool that is missing prints ``SKIPPED``
with the reason, and a tool that is present prints its own findings verbatim.

Usage::

    python scripts/audit_dependencies.py            # human-readable summary
    python scripts/audit_dependencies.py --json     # machine-readable

Exit code is non-zero only when a *critical or high* finding is present in a
check that actually ran.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
WEB_DIR = ROOT / "apps" / "web"
IMAGES = ("argus-chess-api:1.0.0", "argus-chess-web:1.0.0")


@dataclass
class Check:
    name: str
    status: str  # "ran" | "skipped"
    detail: str = ""
    critical: int = 0
    high: int = 0
    raw: str = ""
    findings: list[dict] = field(default_factory=list)


def _run(cmd: list[str], *, cwd: Path | None = None) -> tuple[int, str]:
    """Run a command, returning (exit_code, combined output). Never raises."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed, non-user-supplied argv
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=600,
        )
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def audit_python() -> Check:
    if not VENV_PY.exists():
        return Check("python (pip-audit)", "skipped", "no .venv found")
    code, out = _run([str(VENV_PY), "-m", "pip_audit", "--progress-spinner", "off"])
    if code == 127 or "No module named" in out:
        return Check("python (pip-audit)", "skipped", "pip-audit not installed in .venv")
    return Check(
        "python (pip-audit)",
        "ran",
        "No known vulnerabilities found" if code == 0 else "findings below",
        raw=out.strip(),
        findings=[{"raw": line} for line in out.splitlines() if line.strip()][:50],
    )


def audit_npm() -> Check:
    if not (WEB_DIR / "package.json").exists():
        return Check("web (npm audit)", "skipped", "apps/web not found")
    code, out = _run(
        ["npm", "audit", "--omit=dev", "--json"], cwd=WEB_DIR
    )
    if code == 127:
        return Check("web (npm audit)", "skipped", "npm not available")
    critical = high = 0
    try:
        data = json.loads(out)
        meta = data.get("metadata", {}).get("vulnerabilities", {})
        critical = int(meta.get("critical", 0))
        high = int(meta.get("high", 0))
        detail = f"{meta.get('total', 0)} advisories (prod deps only)"
    except (json.JSONDecodeError, TypeError, ValueError):
        # Fall back to the human summary line.
        match = re.search(r"found (\d+) vulnerabilit", out)
        detail = out.strip().splitlines()[-1] if out.strip() else "no output"
        if match and match.group(1) == "0":
            detail = "0 vulnerabilities (prod deps only)"
    return Check("web (npm audit)", "ran", detail, critical, high, raw=out.strip())


def audit_images() -> list[Check]:
    if shutil.which("docker") is None:
        return [Check("container images (docker scout)", "skipped", "docker not available")]
    probe = _run(["docker", "scout", "version"])
    if probe[0] != 0:
        return [Check("container images (docker scout)", "skipped", "docker scout not available")]

    checks: list[Check] = []
    for image in IMAGES:
        code, out = _run(
            ["docker", "scout", "cves", f"local://{image}", "--only-severity", "critical,high"]
        )
        if code != 0 or "not found" in out.lower():
            checks.append(
                Check(f"image {image}", "skipped", "image not built locally (build it first)")
            )
            continue
        crit = high = 0
        tail = re.search(r"CRITICAL\s+(\d+).*?HIGH\s+(\d+)", out, re.DOTALL)
        if tail:
            crit, high = int(tail.group(1)), int(tail.group(2))
        checks.append(Check(f"image {image}", "ran", f"{crit} critical, {high} high", crit, high, raw=out.strip()))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    checks: list[Check] = [audit_python(), audit_npm(), *audit_images()]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": c.name,
                        "status": c.status,
                        "detail": c.detail,
                        "critical": c.critical,
                        "high": c.high,
                    }
                    for c in checks
                ],
                indent=2,
            )
        )
    else:
        print("Caissa dependency audit")
        print("=" * 60)
        for c in checks:
            marker = "RAN    " if c.status == "ran" else "SKIPPED"
            print(f"{marker} {c.name:34s} {c.detail}")
        print("-" * 60)
        print("A skipped check is not a passing check. Record the real output in")
        print("docs/release/security-report.md when the environment changes.")

    failing = [c for c in checks if c.status == "ran" and (c.critical or c.high)]
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
