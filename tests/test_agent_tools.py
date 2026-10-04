"""Phase 7 tool-layer tests: schema, permission, authorization, limits, failure.

The tool layer is where the agent's promises are actually kept, so these are the
tests that matter most. Each one pins a refusal: arguments that do not match the
schema, a tool that needs context it was not given, a game the caller may not read,
an engine that is not configured, a depth above the policy ceiling. A guardrail that
is not tested is a comment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.ai_agent.core.context import AgentContext  # noqa: E402
from argus.ai_agent.tools import (  # noqa: E402
    AgentProviders,
    Tool,
    ToolArgumentError,
    ToolNotPermittedError,
    ToolPermission,
    ToolSchema,
    Toolbox,
    build_agent_toolbox,
    validate_arguments,
)
from argus.ai_agent.tools.chess_engine import clamp_depth, clamp_multipv, normalize_move  # noqa: E402
from argus.ai_agent.tools.opening import OPENING_BASE_VERSION, match_opening  # noqa: E402
from argus.ai_agent.tools.position import board_facts, summarize_facts  # noqa: E402
from argus.analysis.engine.base import AnalyzedPosition, ChessEngine  # noqa: E402
from argus.shared.errors import (  # noqa: E402
    EngineUnavailableError,
    InvalidMoveError,
    NotFoundError,
    ToolNotFoundError,
)

from tests.conftest import START_FEN  # noqa: E402


class _StubEngine(ChessEngine):
    """Engine stub: records what it was asked for, never starts a process."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def info(self) -> dict:
        return {"available": True, "engine": "stub"}

    def analyze_position(self, fen, *, depth=None, multipv=None, movetime_ms=None) -> AnalyzedPosition:
        self.requests.append({"fen": fen, "depth": depth, "multipv": multipv})
        return AnalyzedPosition(
            fen=fen,
            depth=depth or 0,
            multipv=multipv or 1,
            best_move_uci="e2e4",
            best_move_san="e4",
            lines=[
                {
                    "index": 1,
                    "depth": depth or 17,
                    "multipv": 1,
                    "score_cp": 42,
                    "score_mate": None,
                    "pv": ["e2e4"],
                    "move_uci": "e2e4",
                    "move_san": "e4",
                }
            ],
        )

    def compare_moves(self, fen, moves, *, depth=None):
        from argus.analysis.engine.base import MoveComparison

        return [
            MoveComparison(
                fen=fen,
                played_move_uci=move,
                played_move_san=move,
                best_move_uci="e2e4",
                played_cp=0,
                best_cp=50,
                centipawn_loss=50,
                depth=depth or 0,
            )
            for move in moves
        ]

    def close(self) -> None:
        return None


# --- schema validation ---------------------------------------------------------


class TestArgumentValidation:
    SCHEMA = {
        "type": "object",
        "properties": {
            "fen": {"type": "string", "minLength": 1},
            "depth": {"type": "integer", "minimum": 1, "maximum": 60},
            "moves": {"type": "array", "items": {"type": "string"}},
            "task": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["fen"],
    }

    def test_valid_arguments_pass_through(self):
        cleaned = validate_arguments(self.SCHEMA, {"fen": START_FEN, "depth": 18})
        assert cleaned == {"fen": START_FEN, "depth": 18}

    def test_missing_required_argument_is_rejected(self):
        with pytest.raises(ToolArgumentError, match="Missing required"):
            validate_arguments(self.SCHEMA, {"depth": 18})

    def test_unknown_argument_is_rejected_not_ignored(self):
        with pytest.raises(ToolArgumentError, match="Unknown argument"):
            validate_arguments(self.SCHEMA, {"fen": START_FEN, "depht": 18})

    def test_wrong_type_is_rejected(self):
        with pytest.raises(ToolArgumentError, match="must be an integer"):
            validate_arguments(self.SCHEMA, {"fen": START_FEN, "depth": "deep"})

    def test_boolean_is_not_accepted_as_an_integer(self):
        with pytest.raises(ToolArgumentError, match="must be an integer"):
            validate_arguments(self.SCHEMA, {"fen": START_FEN, "depth": True})

    def test_bounds_are_enforced(self):
        with pytest.raises(ToolArgumentError, match=">= 1"):
            validate_arguments(self.SCHEMA, {"fen": START_FEN, "depth": 0})
        with pytest.raises(ToolArgumentError, match="<= 60"):
            validate_arguments(self.SCHEMA, {"fen": START_FEN, "depth": 200})

    def test_enum_is_enforced(self):
        with pytest.raises(ToolArgumentError, match="must be one of"):
            validate_arguments(self.SCHEMA, {"fen": START_FEN, "task": "c"})

    def test_array_items_are_validated(self):
        cleaned = validate_arguments(self.SCHEMA, {"fen": START_FEN, "moves": ["e2e4", "d2d4"]})
        assert cleaned["moves"] == ["e2e4", "d2d4"]
        with pytest.raises(ToolArgumentError):
            validate_arguments(self.SCHEMA, {"fen": START_FEN, "moves": [1, 2]})

    def test_empty_string_is_rejected_when_a_length_is_required(self):
        with pytest.raises(ToolArgumentError):
            validate_arguments(self.SCHEMA, {"fen": ""})


# --- permissions ---------------------------------------------------------------


def _tool(name: str, permission: ToolPermission, *, available: bool = True) -> Tool:
    return Tool(
        name=name,
        description=name,
        schema=ToolSchema(parameters={"type": "object", "properties": {}, "required": []}),
        permission=permission,
        handler=lambda _context: {"ok": True},
        available=available,
        reason=None if available else "declared but unavailable",
    )


class TestToolPermissions:
    def _toolbox(self) -> Toolbox:
        box = Toolbox()
        box.register(_tool("needs_game", ToolPermission.GAME_CONTEXT))
        box.register(_tool("needs_player", ToolPermission.PLAYER_CONTEXT))
        box.register(_tool("any", ToolPermission.ANY))
        box.register(_tool("declared_only", ToolPermission.ANY, available=False))
        return box

    def test_game_tool_refuses_without_a_game(self):
        outcome = self._toolbox().call("needs_game", {}, AgentContext())
        assert outcome.ok is False
        assert outcome.error_code == "tool_not_permitted"

    def test_player_tool_refuses_without_a_player(self):
        outcome = self._toolbox().call("needs_player", {}, AgentContext(active_game_id="g"))
        assert outcome.ok is False
        assert outcome.error_code == "tool_not_permitted"

    def test_context_free_tool_runs_anywhere(self):
        outcome = self._toolbox().call("any", {}, AgentContext())
        assert outcome.ok is True

    def test_unavailable_tool_refuses_with_its_reason(self):
        outcome = self._toolbox().call("declared_only", {}, AgentContext())
        assert outcome.ok is False
        assert "declared but unavailable" in outcome.error_message

    def test_callable_tools_excludes_the_impossible(self):
        box = self._toolbox()
        assert box.names(AgentContext()) == {"any"}
        assert box.names(AgentContext(active_game_id="g")) == {"any", "needs_game"}
        assert box.names(AgentContext(active_game_id="g", player_id="p")) == {
            "any",
            "needs_game",
            "needs_player",
        }

    def test_tool_specs_only_offer_callable_tools(self):
        specs = self._toolbox().to_tool_specs(AgentContext())
        assert {spec["function"]["name"] for spec in specs} == {"any"}

    def test_unknown_tool_is_a_domain_error(self):
        with pytest.raises(ToolNotFoundError):
            self._toolbox().get("nope")


class TestAuthorization:
    """A game the caller does not own must be refused by the backend."""

    def _toolbox(self) -> Toolbox:
        box = Toolbox()
        box.register(
            Tool(
                name="read_game",
                description="read",
                schema=ToolSchema(
                    parameters={
                        "type": "object",
                        "properties": {"game_id": {"type": "string", "minLength": 1}},
                        "required": ["game_id"],
                    }
                ),
                handler=lambda _context, game_id: {"game_id": game_id},
            )
        )
        return box

    def test_an_authorized_game_is_readable(self):
        context = AgentContext(available_game_ids=["mine"])
        outcome = self._toolbox().call("read_game", {"game_id": "mine"}, context)
        assert outcome.ok is True

    def test_another_callers_game_is_refused(self):
        context = AgentContext(available_game_ids=["mine"])
        outcome = self._toolbox().call("read_game", {"game_id": "theirs"}, context)
        assert outcome.ok is False
        assert outcome.error_code == "tool_not_permitted"
        assert "does not belong to this caller" in outcome.error_message

    def test_an_unrestricted_context_reads_anything(self):
        outcome = self._toolbox().call("read_game", {"game_id": "anything"}, AgentContext())
        assert outcome.ok is True

    def test_the_active_game_is_also_checked(self):
        context = AgentContext(active_game_id="theirs", available_game_ids=["mine"])
        outcome = self._toolbox().call("read_game", {}, context)
        assert outcome.ok is False


# --- the assembled catalogue ----------------------------------------------------


class TestAgentToolbox:
    def test_every_declared_tool_is_registered(self):
        names = {tool.name for tool in build_agent_toolbox(AgentProviders()).list()}
        assert {
            "get_current_position",
            "inspect_position",
            "analyze_position",
            "analyze_position_multipv",
            "compare_moves",
            "get_game",
            "get_game_moves",
            "get_move_analysis",
            "get_game_analysis",
            "get_game_summary",
            "get_critical_moments",
            "get_game_trajectory",
            "get_player_profile",
            "get_player_statistics",
            "get_player_insights",
            "get_player_evidence",
            "get_opening_information",
            "search_chess_knowledge",
            "list_chess_concepts",
            "get_prediction_status",
            "get_validated_prediction",
            "generate_training_position",
            "get_training_recommendations",
            "get_training_requirements",
        } <= names

    def test_data_tools_are_unavailable_without_providers(self):
        box = build_agent_toolbox(AgentProviders())
        for name in ("get_game", "get_move_analysis", "get_player_profile", "get_validated_prediction"):
            tool = box.get(name)
            assert tool.available is False
            assert tool.reason

    def test_engine_tools_are_available_without_providers_but_fail_honestly(self):
        box = build_agent_toolbox(AgentProviders())
        outcome = box.call("analyze_position", {"fen": START_FEN}, AgentContext())
        assert outcome.ok is False
        assert "No chess engine is configured" in outcome.error_message

    def test_training_tools_are_declared_and_refuse(self):
        box = build_agent_toolbox(AgentProviders())
        # With no active player the refusal names what the user can fix.
        outcome = box.call("generate_training_position", {}, AgentContext())
        assert outcome.ok is False
        assert "player" in outcome.error_message.lower()
        # With a player but no training provider, the gap is reported honestly.
        outcome = box.call("generate_training_position", {}, AgentContext(player_id="1"))
        assert outcome.ok is False
        assert "training" in outcome.error_message.lower()
        # The meta tool is always answerable, so the agent can explain the contract.
        meta = box.call("get_training_requirements", {}, AgentContext())
        assert meta.ok is True
        assert meta.data["requirements"]

    def test_catalogue_is_machine_readable(self):
        catalogue = build_agent_toolbox(AgentProviders()).catalogue(AgentContext())
        assert len(catalogue) >= 20
        for entry in catalogue:
            assert {"name", "description", "permission", "available", "outputs"} <= set(entry)
        by_name = {entry["name"]: entry for entry in catalogue}
        assert by_name["inspect_position"]["permission"] == "any"
        assert by_name["get_move_analysis"]["permission"] == "game_context"
        assert by_name["get_player_insights"]["permission"] == "player_context"


# --- engine tools ---------------------------------------------------------------


class TestEngineTools:
    def test_the_declared_maximum_teaches_the_policy_ceiling(self):
        """An over-deep request is refused by name, not silently downgraded.

        The tool spec declares the policy maximum, so a model that asks for depth 99
        is told the limit and can ask again properly. The handler's clamp remains as
        a backstop for callers that bypass the schema.
        """
        engine = _StubEngine()
        box = build_agent_toolbox(AgentProviders(engine=engine))
        schema = box.get("analyze_position").schema
        assert schema.parameters["properties"]["depth"]["maximum"] == 24
        assert schema.parameters["properties"]["depth"]["minimum"] == 1

        outcome = box.call("analyze_position", {"fen": START_FEN, "depth": 99}, AgentContext())
        assert outcome.ok is False
        assert outcome.error_code == "tool_argument_error"
        assert "<= 24" in outcome.error_message
        assert engine.requests == []

    def test_a_depth_within_policy_is_passed_through_unchanged(self):
        engine = _StubEngine()
        box = build_agent_toolbox(AgentProviders(engine=engine))
        outcome = box.call("analyze_position", {"fen": START_FEN, "depth": 18}, AgentContext())
        assert outcome.ok is True
        assert outcome.data["depth"] == 18
        assert outcome.data["depth_was_clamped"] is False
        assert engine.requests[0]["depth"] == 18

    def test_the_clamp_backstop_still_exists_for_direct_calls(self):
        """Clamping is tested at the helper, since the schema stops it upstream."""
        assert clamp_depth(99)[0] == 24
        assert clamp_depth(99)[1] is True
        assert clamp_multipv(40)[0] == 5

    def test_the_multipv_ceiling_is_declared_too(self):
        box = build_agent_toolbox(AgentProviders(engine=_StubEngine()))
        schema = box.get("analyze_position_multipv").schema
        assert schema.parameters["properties"]["multipv"]["maximum"] == 5
        outcome = box.call(
            "analyze_position_multipv",
            {"fen": START_FEN, "multipv": 40},
            AgentContext(),
        )
        assert outcome.ok is False
        assert "<= 5" in outcome.error_message

    def test_clamp_helpers(self):
        assert clamp_depth(None) == (16, False)
        assert clamp_depth(20) == (20, False)
        assert clamp_depth(90) == (24, True)
        assert clamp_depth(1) == (6, True)
        assert clamp_multipv(None) == (1, False)
        assert clamp_multipv(3) == (3, False)
        assert clamp_multipv(99) == (5, True)
        assert clamp_multipv(0) == (1, True)

    def test_compare_moves_accepts_san_and_uci(self):
        engine = _StubEngine()
        box = build_agent_toolbox(AgentProviders(engine=engine))
        outcome = box.call(
            "compare_moves", {"fen": START_FEN, "moves": ["e4", "e2e4"]}, AgentContext()
        )
        assert outcome.ok is True
        assert outcome.data["resolved_uci"] == ["e2e4", "e2e4"]

    def test_an_illegal_move_is_refused_before_the_engine(self):
        box = build_agent_toolbox(AgentProviders(engine=_StubEngine()))
        outcome = box.call(
            "compare_moves", {"fen": START_FEN, "moves": ["e2e5"]}, AgentContext()
        )
        assert outcome.ok is False
        assert "not a legal move" in outcome.error_message

    def test_normalize_move_rejects_nonsense(self):
        with pytest.raises(InvalidMoveError):
            normalize_move(START_FEN, "Qz9")
        with pytest.raises(InvalidMoveError):
            normalize_move(START_FEN, "")

    def test_missing_engine_is_reported_not_faked(self):
        box = build_agent_toolbox(AgentProviders(engine=None))
        outcome = box.call("analyze_position", {"fen": START_FEN}, AgentContext())
        assert outcome.ok is False
        assert outcome.error_code == EngineUnavailableError.code


# --- position tools --------------------------------------------------------------


class TestPositionFacts:
    def test_start_position_facts(self):
        facts = board_facts(START_FEN)
        assert facts["side_to_move"] == "white"
        assert facts["legal_move_count"] == 20
        assert facts["material_balance_cp_white_minus_black"] == 0
        assert facts["is_check"] is False
        assert facts["piece_counts"]["white"]["pawn"] == 8

    def test_summary_never_claims_an_advantage(self):
        text = summarize_facts(board_facts(START_FEN))
        assert "material level" in text
        assert "winning" not in text.lower()
        assert "better" not in text.lower()

    def test_move_analysis_summary_names_the_mover(self):
        """The stored evaluations are from the mover's perspective, so the summary
        must say *whose* perspective it is. Otherwise a model reads "+1.80 for the
        mover" and attributes the advantage to the wrong side (a real failure seen
        live against Groq).
        """
        from argus.ai_agent.core.collection import summarize

        black = summarize(
            "get_move_analysis",
            {
                "move_number": 20,
                "mover": "black",
                "san": "Na6",
                "classification": "blunder",
                "eval_before_cp": 180,
                "eval_after_cp": -873,
                "centipawn_loss": 1053,
                "best_move_san": "Nf6",
            },
        )
        assert "Black" in black
        assert "from Black's perspective" in black

        white = summarize(
            "get_move_analysis",
            {
                "move_number": 17,
                "mover": "white",
                "san": "fxg5",
                "classification": "blunder",
                "eval_before_cp": 531,
                "eval_after_cp": 23,
                "centipawn_loss": 508,
                "best_move_san": "Qh5+",
            },
        )
        assert "White" in white
        assert "from White's perspective" in white

    def test_an_illegal_fen_is_a_domain_error(self):
        outcome = build_agent_toolbox(AgentProviders()).call(
            "inspect_position", {"fen": "not-a-fen"}, AgentContext()
        )
        assert outcome.ok is False
        assert outcome.error_code == "invalid_fen"

    def test_current_position_needs_a_context(self):
        outcome = build_agent_toolbox(AgentProviders()).call(
            "get_current_position", {}, AgentContext()
        )
        assert outcome.ok is False


# --- opening and knowledge bases --------------------------------------------------


class TestOpeningBase:
    def test_a_known_line_matches_the_most_specific_entry(self):
        matched = match_opening(["e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4", "f3d4", "g8f6", "b1c3", "a7a6"])
        assert matched["matched"] is True
        assert matched["name"] == "Sicilian Defence, Najdorf Variation"
        assert matched["eco"] == "B90"

    def test_a_prefix_matches_the_shorter_entry(self):
        matched = match_opening(["e2e4", "c7c5"])
        assert matched["name"] == "Sicilian Defence"

    def test_an_unknown_line_returns_unmatched_not_a_guess(self):
        matched = match_opening(["a2a3", "h7h6", "a3a4"])
        assert matched["matched"] is False
        assert matched["name"] is None
        assert matched["base_version"] == OPENING_BASE_VERSION

    def test_the_tool_reports_unknown_rather_than_inventing(self):
        box = build_agent_toolbox(AgentProviders())
        outcome = box.call(
            "get_opening_information", {"moves": "a2a3 h7h6 a3a4"}, AgentContext()
        )
        assert outcome.ok is True
        assert outcome.data["matched"] is False
        assert "do not name it" in outcome.data["note"]


class TestKnowledgeBase:
    def test_a_concept_round_trips(self):
        box = build_agent_toolbox(AgentProviders())
        outcome = box.call("search_chess_knowledge", {"query": "fork"}, AgentContext())
        assert outcome.ok is True
        assert outcome.data["found"] is True
        assert outcome.data["concept"] == "fork"
        assert outcome.data["definition"]

    def test_plural_tolerance(self):
        box = build_agent_toolbox(AgentProviders())
        outcome = box.call("search_chess_knowledge", {"query": "forks"}, AgentContext())
        assert outcome.data["found"] is True

    def test_an_unknown_concept_is_reported_not_invented(self):
        box = build_agent_toolbox(AgentProviders())
        outcome = box.call(
            "search_chess_knowledge", {"query": "the Hypermodern Baltic Gambit"}, AgentContext()
        )
        assert outcome.data["found"] is False
        assert outcome.data["available_concepts"]

    def test_category_filter_rejects_a_mismatch(self):
        box = build_agent_toolbox(AgentProviders())
        outcome = box.call(
            "search_chess_knowledge", {"query": "fork", "category": "endgame"}, AgentContext()
        )
        assert outcome.data["found"] is False


# --- tool handlers that fail -------------------------------------------------------


class TestToolFailureHandling:
    def test_a_raising_handler_becomes_an_outcome_not_an_exception(self):
        box = Toolbox()
        box.register(
            Tool(
                name="explodes",
                description="explodes",
                schema=ToolSchema(parameters={"type": "object", "properties": {}, "required": []}),
                handler=lambda _context: (_ for _ in ()).throw(RuntimeError("boom")),
            )
        )
        outcome = box.call("explodes", {}, AgentContext())
        assert outcome.ok is False
        assert "boom" in outcome.error_message

    def test_a_domain_error_is_preserved_with_its_code(self):
        box = Toolbox()
        box.register(
            Tool(
                name="missing",
                description="missing",
                schema=ToolSchema(parameters={"type": "object", "properties": {}, "required": []}),
                handler=lambda _context: (_ for _ in ()).throw(NotFoundError("nothing here")),
            )
        )
        outcome = box.call("missing", {}, AgentContext())
        assert outcome.ok is False
        assert outcome.error_code == NotFoundError.code

    def test_a_failed_call_records_its_duration(self):
        box = Toolbox()
        box.register(
            Tool(
                name="slow",
                description="slow",
                schema=ToolSchema(parameters={"type": "object", "properties": {}, "required": []}),
                handler=lambda _context: (_ for _ in ()).throw(NotFoundError("no")),
            )
        )
        outcome = box.call("slow", {}, AgentContext())
        assert outcome.duration_ms >= 0.0


class TestPermissionEnums:
    def test_permission_values_are_stable(self):
        assert ToolPermission.ANY.value == "any"
        assert ToolPermission.GAME_CONTEXT.value == "game_context"
        assert ToolPermission.PLAYER_CONTEXT.value == "player_context"
        assert ToolPermission.PRODUCTION_MODEL.value == "production_model"
        assert ToolPermission.VALIDATED_POSITION.value == "validated_position"

    def test_the_verification_error_code_exists(self):
        assert ToolNotPermittedError("x").code == "tool_not_permitted"
