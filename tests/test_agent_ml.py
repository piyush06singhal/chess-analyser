"""Tests for the AI agent tool layer and ML scaffolding contracts."""

from __future__ import annotations

import pytest

from argus.ai_agent.agent import ChessCoachAgent, LLMNotConfiguredError
from argus.ai_agent.tools import build_default_registry
from argus.analysis.engine.base import AnalyzedPosition, ChessEngine
from argus.analysis.game_analyzer import GameAnalyzer
from argus.chess_core.pgn import parse_first_game
from argus.ml.dataset import load_csv, split_dataset, validate_dataset, validate_dataset_strict
from argus.ml.models import DatasetSpec, ProblemType
from argus.shared.errors import (
    ArgusError,
    EngineUnavailableError,
    InsufficientDataError,
    ToolNotFoundError,
    ToolUnavailableError,
)

from tests.conftest import START_FEN


class _StubEngine(ChessEngine):
    """Engine stub for registry-construction tests (never starts a process)."""

    def info(self) -> dict:
        return {"available": False, "engine": "stub"}

    def analyze_position(self, fen, *, depth=None, multipv=None) -> AnalyzedPosition:
        raise EngineUnavailableError("stub engine cannot analyze")

    def compare_moves(self, fen, moves, *, depth=None):
        raise EngineUnavailableError("stub engine cannot compare")

    def close(self) -> None:
        return None


@pytest.fixture()
def loaded_game():
    return parse_first_game(
        "[Event \"Test\"]\n[Result \"1-0\"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0\n"
    )


@pytest.fixture()
def registry(loaded_game):
    analyzer = GameAnalyzer.__new__(GameAnalyzer)  # analyze() is not called here
    return build_default_registry(_StubEngine(), analyzer, game_provider=lambda: loaded_game)


# --- tool registry -----------------------------------------------------------------


class TestToolRegistry:
    def test_all_declared_tools_are_registered(self, registry):
        names = {tool.name for tool in registry.list()}
        assert {
            "get_current_position",
            "analyze_position",
            "analyze_move",
            "get_game_analysis",
            "get_player_history",
            "get_player_statistics",
            "search_chess_knowledge",
            "generate_training_position",
        } <= names

    def test_future_tools_are_honestly_unavailable(self, registry):
        for name in ("get_player_history", "search_chess_knowledge"):
            tool = registry.get(name)
            assert tool.available is False
            assert tool.reason

    def test_unknown_tool_raises(self, registry):
        with pytest.raises(ToolNotFoundError):
            registry.get("nonexistent_tool")

    def test_calling_unavailable_tool_raises(self, registry):
        with pytest.raises(ToolUnavailableError):
            registry.call("get_player_history")

    def test_tool_specs_only_include_available_tools(self, registry):
        specs = registry.to_tool_specs()
        spec_names = {spec["function"]["name"] for spec in specs}
        assert "analyze_position" in spec_names
        assert "get_player_history" not in spec_names
        for spec in specs:
            assert spec["type"] == "function"
            assert "parameters" in spec["function"]

    def test_current_position_returns_structured_data(self, registry, loaded_game):
        result = registry.call("get_current_position")
        assert result["fen"] == loaded_game.final_position
        assert result["turn"] in ("white", "black")
        assert result["move_count"] == loaded_game.move_count

    def test_current_position_without_game_raises(self):
        analyzer = GameAnalyzer.__new__(GameAnalyzer)
        empty_registry = build_default_registry(_StubEngine(), analyzer)
        with pytest.raises((ToolUnavailableError, ArgusError)):
            empty_registry.call("get_current_position")

    def test_analyze_move_rejects_illegal_move(self, registry):
        with pytest.raises(Exception):
            registry.call("analyze_move", {"fen": START_FEN, "move_uci": "e2e5"})

    def test_agent_never_registers_a_chess_calculator(self, registry):
        """The agent's available tools must not compute chess facts themselves."""
        names = {tool.name for tool in registry.list() if tool.available}
        assert all(
            name
            in ("get_current_position", "analyze_position", "analyze_move", "get_game_analysis")
            for name in names
        )


# --- agent shell --------------------------------------------------------------------


class _FakeLLMClient:
    """Minimal LLMClient implementation for testing the tool-calling loop."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        return self._responses.pop(0)


class TestChessCoachAgent:
    def test_without_llm_client_raises_config_error(self, registry):
        agent = ChessCoachAgent(registry, llm_client=None)
        with pytest.raises(LLMNotConfiguredError):
            agent.run("What went wrong in my game?")

    def test_plain_answer_without_tool_calls(self, registry):
        client = _FakeLLMClient([{"message": "Play Rd1 in the Opera Game."}])
        agent = ChessCoachAgent(registry, llm_client=client)
        result = agent.run("Any advice?")
        assert result["message"] == "Play Rd1 in the Opera Game."
        assert result["tool_calls_used"] == 0

    def test_tool_call_loop_feeds_results_back(self, registry):
        client = _FakeLLMClient(
            [
                {"tool_calls": [{"name": "get_current_position", "arguments": {}}]},
                {"message": "Your final position is fine."},
            ]
        )
        agent = ChessCoachAgent(registry, llm_client=client)
        result = agent.run("Check my game")
        assert result["message"] == "Your final position is fine."
        tool_messages = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0]["content"]["status"] == "ok"

    def test_tool_error_is_reported_not_raised(self, registry):
        client = _FakeLLMClient(
            [
                {"tool_calls": [{"name": "get_player_history", "arguments": {}}]},
                {"message": "History is not available yet."},
            ]
        )
        agent = ChessCoachAgent(registry, llm_client=client)
        result = agent.run("Show my history")
        tool_messages = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
        assert tool_messages[0]["content"]["status"] == "error"
        assert result["message"] == "History is not available yet."


# --- ML dataset validation ------------------------------------------------------------


def _rows(count: int, *, label: str = "win") -> list[dict[str, str]]:
    return [
        {"material_balance": str(i % 8 - 4), "mobility": str(i % 5), "result": label}
        for i in range(count)
    ]


def _spec() -> DatasetSpec:
    return DatasetSpec(
        name="win_predictor_v0",
        description="Placeholder spec used to test the training gate",
        problem_type=ProblemType.CLASSIFICATION,
        label_column="result",
        min_samples=1000,
        min_validation_samples=200,
        min_test_samples=200,
    )


class TestDatasetValidation:
    def test_insufficient_dataset_is_invalid(self):
        result = validate_dataset(_rows(100), _spec())
        assert result.is_valid is False
        assert any("at least 1000" in error for error in result.errors)

    def test_sufficient_dataset_is_valid(self):
        result = validate_dataset(_rows(1500), _spec())
        assert result.is_valid is True

    def test_strict_validation_raises_insufficient_data(self):
        with pytest.raises(InsufficientDataError):
            validate_dataset_strict(_rows(50), _spec())

    def test_missing_label_column_is_reported(self):
        rows = [{"material_balance": "1", "mobility": "2"} for _ in range(1200)]
        result = validate_dataset(rows, _spec())
        assert result.is_valid is False
        assert any("result" in error for error in result.errors)

    def test_imbalanced_class_produces_warning_not_error(self):
        spec = DatasetSpec(
            name="imbalanced",
            problem_type=ProblemType.CLASSIFICATION,
            label_column="result",
            min_samples=100,
            min_class_balance_share=0.05,
        )
        rows = _rows(99) + _rows(1, label="draw")
        result = validate_dataset(rows, spec)
        assert result.is_valid is True  # warnings do not block training
        assert any("draw" in warning for warning in result.warnings)

    def test_split_enforces_split_minimums(self):
        with pytest.raises(InsufficientDataError):
            split_dataset(_rows(1500), _spec())  # 10% test = 150 < 200 required

    def test_split_is_deterministic(self):
        rows = _rows(5000)
        spec = DatasetSpec(
            name="d", label_column="result", problem_type=ProblemType.CLASSIFICATION, min_samples=1
        )
        first = split_dataset(rows, spec)
        second = split_dataset(rows, spec)
        assert [r["material_balance"] for r in first["train"][:10]] == [
            r["material_balance"] for r in second["train"][:10]
        ]

    def test_load_csv_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_csv("/nonexistent/dataset.csv")
