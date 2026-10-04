"""Caissa operator CLI.

A small, dependency-free command line for the checks a deploy pipeline runs. It
does not start the server or touch the database; it validates *configuration*,
which is the thing that most often differs between a working local run and a
broken deployment.

    argus config validate --environment production
    argus config validate --environment staging --json

Exit code is 0 when the configuration is valid for the environment, 1 when it is
not, and 2 for a usage error — so CI can gate on it directly.
"""

from __future__ import annotations

import argparse
import json
import sys

from argus_api.config import ENVIRONMENTS, Settings, validate_settings


def _load_settings(environment: str) -> Settings:
    """Load settings for an environment, preferring ``.env.<environment>``.

    Reads, in order of precedence: real environment variables, then
    ``.env.<environment>`` (if present), then ``.env``. That mirrors how a
    deployment is configured — the environment file is a template, the real
    environment wins.
    """
    env_files = (f".env.{environment}", ".env")
    return Settings(_env_file=env_files)


def _cmd_config_validate(args: argparse.Namespace) -> int:
    environment = (args.environment or "development").strip().lower()
    settings = _load_settings(environment)
    problems = validate_settings(settings, environment=environment)

    if args.json:
        print(
            json.dumps(
                {
                    "environment": environment,
                    "valid": not problems,
                    "problems": problems,
                },
                indent=2,
            )
        )
    else:
        print(f"Caissa configuration — environment: {environment}")
        print("=" * 62)
        if not problems:
            print("VALID — no configuration problems found.")
        else:
            print(f"INVALID — {len(problems)} problem(s):")
            for index, problem in enumerate(problems, start=1):
                print(f"  {index}. {problem}")
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="argus", description="Caissa operator commands.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    config = subcommands.add_parser("config", help="configuration commands.")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    validate = config_sub.add_parser("validate", help="validate configuration for an environment.")
    validate.add_argument(
        "--environment",
        choices=ENVIRONMENTS,
        default="development",
        help="the environment whose rules to apply (default: development).",
    )
    validate.add_argument("--json", action="store_true", help="machine-readable output.")
    validate.set_defaults(func=_cmd_config_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
