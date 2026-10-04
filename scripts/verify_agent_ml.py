"""Verification for the AI agent foundation and ML scaffolding.

Checks (live):
- tool registry lists all tools with honest availability
- the agent refuses to run without an LLM provider (clear error, not fake chat)
- unavailable tools raise ToolUnavailableError
- ML validation refuses insufficient datasets
- dataset splitting enforces minimums

Exit codes: 0 = verified, 1 = failed.
"""

from __future__ import annotations

import sys

from argus.ai_agent import ChessCoachAgent, LLMNotConfiguredError, build_default_registry
from argus.analysis.engine.stockfish import StockfishEngine
from argus.analysis.game_analyzer import GameAnalyzer
from argus.chess_core.pgn import parse_first_game
from argus.ml import DatasetSpec, ProblemType, split_dataset, validate_dataset_strict
from argus.shared.errors import ToolUnavailableError

SMOKE_PGN = '[Event "t"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n'

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"{'OK  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


def main() -> int:
    engine = StockfishEngine()
    analyzer = GameAnalyzer(engine)
    game = parse_first_game(SMOKE_PGN)

    # Tool registry -------------------------------------------------------------
    registry = build_default_registry(engine, analyzer, game_provider=lambda: game)
    names = [tool.name for tool in registry.list()]
    expected = {
        "get_current_position",
        "analyze_position",
        "analyze_move",
        "get_game_analysis",
        "get_player_history",
        "get_player_statistics",
        "search_chess_knowledge",
        "generate_training_position",
    }
    check(set(names) == expected, f"registry contains all 8 spec tools ({len(names)})")
    check(len(registry.to_tool_specs()) == 4, "only the 4 working tools are exposed to an LLM")
    unavailable = [tool.name for tool in registry.list() if not tool.available]
    check(
        set(unavailable)
        == {"get_player_history", "get_player_statistics", "search_chess_knowledge",
            "generate_training_position"},
        "unavailable tools carry honest reasons",
    )

    position = registry.call("get_current_position")
    expected_final = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    check(
        position.get("fen") == expected_final and position.get("turn") == "white",
        "get_current_position returns the correct FEN and turn",
    )

    # Agent refuses without an LLM ----------------------------------------------
    agent = ChessCoachAgent(registry)
    try:
        agent.run("Hello coach")
        check(False, "agent refuses to run without an LLM provider")
    except LLMNotConfiguredError as exc:
        check(exc.code == "llm_not_configured", "agent refuses to run without an LLM provider")

    # Unavailable tool flagged ----------------------------------------------------
    try:
        registry.call("search_chess_knowledge")
        check(False, "unavailable tool raises ToolUnavailableError")
    except ToolUnavailableError:
        check(True, "unavailable tool raises ToolUnavailableError")

    # ML validation ---------------------------------------------------------------
    spec = DatasetSpec(
        name="smoke", problem_type=ProblemType.CLASSIFICATION, label_column="y", min_samples=100
    )
    rows = [{"y": "win", "feature_a": "1"}] * 50
    try:
        validate_dataset_strict(rows, spec)
        check(False, "insufficient dataset is refused")
    except Exception as exc:  # noqa: BLE001 — verify the domain error surfaces
        check("InsufficientData" in type(exc).__name__, "insufficient dataset is refused")

    good_rows = [{"y": "win" if i % 2 else "loss", "feature_a": str(i)} for i in range(160)]
    split_spec = DatasetSpec(
        name="smoke-split",
        problem_type=ProblemType.CLASSIFICATION,
        label_column="y",
        min_samples=100,
        min_validation_samples=20,
        min_test_samples=20,
    )
    try:
        splits = split_dataset(good_rows, split_spec, validation_share=0.15, test_share=0.15)
        check(
            len(splits["train"]) == 112
            and len(splits["validation"]) == 24
            and len(splits["test"]) == 24,
            f"dataset splits enforce spec minimums ({len(splits['train'])}/"
            f"{len(splits['validation'])}/{len(splits['test'])})",
        )
    except Exception as exc:  # noqa: BLE001
        check(False, f"dataset splits enforce spec minimums ({exc})")

    engine.close()

    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        return 1
    print("OK: agent foundation and ML scaffolding verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
