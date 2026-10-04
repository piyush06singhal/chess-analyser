#!/usr/bin/env python
"""Validate Caissa configuration for an environment (§43).

The configuration check a deploy pipeline runs before shipping. It applies the
environment's rules — production must not be open, must use PostgreSQL and JSON
logs, must not use the development LLM stub — and exits non-zero on any problem,
so a bad configuration is caught before it reaches a running deployment.

    python scripts/validate_config.py --environment production
    python scripts/validate_config.py --environment staging --json

This is the same check the installed ``argus config validate`` command runs; the
script form needs no console-script reinstall.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "argus"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from argus_api.cli import main  # noqa: E402


def run() -> int:
    argv = ["config", "validate", *sys.argv[1:]]
    return main(argv)


if __name__ == "__main__":
    sys.exit(run())
