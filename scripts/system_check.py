#!/usr/bin/env python
"""Caissa system check: the deployment's real state, dependency by dependency.

Spec §52 asks for a command that verifies the database, Redis, Stockfish, the
backend, the frontend, the ML registry, the AI provider, the migrations and the
environment — and the result must come from real checks.

So this script does not have its own opinion about any of them. It asks the
*service*: ``/health`` and ``/ready`` already probe each dependency in-process
(a real SQL round-trip, a real Redis PING, a real Stockfish ``info`` call, a
schema inspection, the model registry), and this prints their answers as a table.
It adds two things the service cannot check about itself:

* the frontend, by fetching it, and
* the environment, by asserting that no secret is exposed by the probes.

Every line is PASS / FAIL / SKIP for a stated reason. Nothing is marked PASS
because the endpoint answered — a dependency that is down is reported as down.

Usage:
    python scripts/system_check.py [--api http://127.0.0.1:8002] [--web http://127.0.0.1:3100]
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

FAILURES: list[str] = []
WARNINGS: list[str] = []


def _get(url: str, timeout: int = 30) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode()
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            return exc.code, None
    except Exception as exc:  # noqa: BLE001 — an unreachable service is a FAIL line
        return 0, str(exc)


def line(label: str, status: str, detail: str = "") -> None:
    dots = "." * max(3, 26 - len(label))
    print(f"{label} {dots} {status}" + (f"  ({detail})" if detail else ""))
    if status == "FAIL":
        FAILURES.append(label)
    elif status == "WARN":
        WARNINGS.append(label)


def check_http(label: str, url: str, expect: int = 200) -> tuple[int, object]:
    status, body = _get(url)
    line(label, "PASS" if status == expect else "FAIL", f"HTTP {status}" if status else str(body)[:60])
    return status, body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8002")
    parser.add_argument("--web", default="http://127.0.0.1:3100")
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args()

    api = args.api.rstrip("/")
    web = args.web.rstrip("/")

    print("Caissa SYSTEM CHECK")
    print("=" * 62)

    # --- the service's own probes -------------------------------------------
    status, health = check_http("Backend reachable", f"{api}/health")
    if status != 200 or not isinstance(health, dict):
        print("\nThe API is not answering; nothing else can be checked.")
        return 1

    database = health.get("database", {})
    line(
        "Database",
        "PASS" if database.get("connected") else "FAIL",
        str(database.get("dialect") or database.get("detail", ""))[:60],
    )

    redis = health.get("redis", {})
    if redis.get("configured") is False:
        line("Redis", "SKIP", "not configured (the cache falls back in-process)")
    else:
        line(
            "Redis",
            "PASS" if redis.get("connected") else "FAIL",
            "connected" if redis.get("connected") else str(redis.get("detail", ""))[:60],
        )

    engine = health.get("engine", {})
    line(
        "Stockfish",
        "PASS" if engine.get("available") else "FAIL",
        f"{engine.get('version') or engine.get('engine') or engine.get('detail', '')}"[:60],
    )

    # --- readiness detail ----------------------------------------------------
    status, ready = check_http("Readiness probe", f"{api}/ready")
    checks = ready.get("checks", {}) if isinstance(ready, dict) else {}

    migrations = checks.get("migrations", {})
    line(
        "Migrations",
        "PASS" if migrations.get("ok") else "FAIL",
        f"{migrations.get('present', '?')}/{migrations.get('expected', '?')} tables"
        + (f", missing {migrations.get('missing')}" if migrations.get("missing") else ""),
    )

    registry = checks.get("ml_registry", {})
    production_models = registry.get("production_models") or []
    line(
        "ML registry",
        "PASS" if registry.get("ok") else "FAIL",
        (
            f"{len(production_models)} production model(s)"
            if production_models
            else f"{registry.get('registered', 0)} registered, none production-approved"
        ),
    )

    provider = checks.get("ai_provider", {})
    line(
        "AI provider",
        "PASS" if provider.get("ok") else "FAIL",
        (
            f"'{provider.get('provider')}' configured"
            if provider.get("configured")
            else "not configured — the coach answers from tools and says so"
        ),
    )

    overall = ready.get("status") if isinstance(ready, dict) else "unknown"
    line(
        "Readiness verdict",
        "PASS" if overall == "ready" else "WARN",
        f"status={overall}, blocking={ready.get('blocking') if isinstance(ready, dict) else '?'}",
    )

    # --- environment ---------------------------------------------------------
    status, env_body = _get(f"{api}/health")
    leaked = [
        key
        for key in ("api_key", "password", "secret", "token", "credential")
        if key in json.dumps(env_body).lower()
    ]
    line(
        "Secret exposure",
        "PASS" if not leaked else "FAIL",
        "no secret-like keys in the health payload" if not leaked else f"found {leaked}",
    )

    env_name = health.get("environment") if isinstance(health, dict) else None
    line("Environment", "PASS" if env_name else "WARN", f"env={env_name or 'unreported'}")

    # --- core API surfaces ---------------------------------------------------
    for label, path in (
        ("Scenario engine", "/api/scenarios/meta"),
        ("Coaching workspace", "/api/coaching/method"),
        ("Agent", "/api/coach/status"),
        ("Training engine", "/api/training/meta"),
        ("Predictions", "/api/predictions/tasks"),
        ("Live chess", "/api/live/method"),
    ):
        check_http(label, f"{api}{path}")

    # The fair-play rule is part of the contract, so the check asserts it is
    # actually stated rather than merely that the endpoint answered.
    status, live_method = _get(f"{api}/api/live/method")
    fair = live_method.get("fair_play") if isinstance(live_method, dict) else None
    fair_rule = fair.get("rule") if isinstance(fair, dict) else fair
    line(
        "Live fair play",
        "PASS" if isinstance(fair_rule, str) and fair_rule else "FAIL",
        fair_rule or "the fair-play rule is not stated",
    )

    # --- frontend ------------------------------------------------------------
    if args.skip_web:
        line("Frontend", "SKIP", "--skip-web")
    else:
        status, _ = _get(web)
        line(
            "Frontend",
            "PASS" if status == 200 else "WARN",
            f"HTTP {status}" if status else "not running (start it or pass --skip-web)",
        )

    print("=" * 62)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) — {', '.join(FAILURES)}")
        return 1
    if WARNINGS:
        print(f"OK with {len(WARNINGS)} warning(s): {', '.join(WARNINGS)}")
        return 0
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
