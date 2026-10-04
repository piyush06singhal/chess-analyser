"""Phase 7 end-to-end scenarios (spec §35, §36, §45, §46).

The behaviour suite tests the parts; this one tests whole turns. A turn is driven
here by a **scripted provider**: a fake LLM whose responses the test controls. That
is what makes the adversarial cases possible — the point of an adversarial test is
to have the model misbehave on demand and prove that something *other than the
model* stops it.

Two kinds of assertion appear throughout:

* the turn did the right thing (selected the tool, grounded the answer, emitted a
  real action, recorded the absence);
* the turn was *refused* — an invented evaluation, count or probability is caught
  by the validator even when the scripted model states it confidently.

The scenarios mirror the spec's four end-to-end cases: a move question, a player
question, no production model, and no engine. The four-engine case uses no provider
at all, because "Stockfish is missing" must be reported by the deterministic path,
not by a model's good intentions.
"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.ai_agent.core.context import AgentContext, ResponseMode  # noqa: E402
from argus.ai_agent.core.evidence import EvidenceKind  # noqa: E402
from argus.ai_agent.core.loop import CoachingAgent  # noqa: E402
from argus.ai_agent.core.response import AnswerAction, ClaimKind  # noqa: E402
from argus.ai_agent.memory.conversation import ConversationMemory  # noqa: E402
from argus.ai_agent.prompts import PROMPT_VERSION  # noqa: E402
from argus.ai_agent.safety.validation import validate_answer  # noqa: E402
from argus.ai_agent.safety.limits import AgentLimits  # noqa: E402
from argus.ai_agent.tools import AgentProviders, build_agent_toolbox  # noqa: E402
from argus.llm.echo_client import EchoLLMClient  # noqa: E402
from argus.shared.errors import ArgusError  # noqa: E402

from tests.conftest import START_FEN  # noqa: E402

GAME_ID = "189d51ba-7404-4e6c-91b2-39119c103132"
MOVE = {
    "ply": 17,
    "move_number": 9,
    "mover": "white",
    "san": "fxg5",
    "uci": "f4g5",
    "fen_before": START_FEN,
    "fen_after": START_FEN,
    "eval_before_cp": 531,
    "eval_after_cp": 23,
    "eval_change_cp": -508,
    "centipawn_loss": 508,
    "classification": "blunder",
    "best_move_uci": "d1h5",
    "best_move_san": "Qh5+",
    "is_best_move": False,
    "principal_variation": ["d1h5", "e8d7"],
    "phase": "opening",
    "depth": 16,
    "game_id": GAME_ID,
}


class FakeLLM:
    """A scripted provider: each call pops the next scripted response."""

    def __init__(self, *responses: dict, provider: str = "fake", model: str = "fake-small-1"):
        self.scripted = list(responses)
        self.calls: list[dict] = []
        self.provider = provider
        self.model = model

    def complete(self, messages, tools=None):  # noqa: ANN001 — protocol shape
        self.calls.append({"messages": messages, "tools": tools or []})
        if not self.scripted:
            return {"message": "Nothing further to add."}
        return self.scripted.pop(0)

    @property
    def prompts(self) -> list[str]:
        """Every message body the provider has been handed, as one string."""
        return [
            "\n".join(str(message.get("content") or "") for message in call["messages"])
            for call in self.calls
        ]


class Recorder:
    """A provider callable that records its arguments and returns a payload."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _agent(providers: AgentProviders, llm=None, limits: AgentLimits | None = None) -> CoachingAgent:
    return CoachingAgent(
        build_agent_toolbox(providers),
        llm_client=llm,
        limits=limits,
        provider_name=getattr(llm, "provider", None),
        model_name=getattr(llm, "model", None),
    )


# --- scenario 1: a position/move question -----------------------------------------


class TestMoveQuestion:
    def _providers(self) -> tuple[AgentProviders, Recorder]:
        move = Recorder(dict(MOVE))
        return AgentProviders(move_analysis=move), move

    def test_the_turn_is_grounded_in_the_stored_analysis(self):
        providers, move = self._providers()
        llm = FakeLLM(
            {
                "message": (
                    "Move 9. fxg5 was classified a blunder: the evaluation went from "
                    "+5.31 to +0.23. The engine's choice was Qh5+."
                )
            }
        )
        agent = _agent(providers, llm)
        answer = agent.ask(
            "Why was this move bad?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17, selected_move_san="fxg5"),
        )

        # The stored analysis was fetched for the selected ply, and only that.
        assert move.calls == [(GAME_ID, 17)]
        assert answer.deterministic is False
        assert answer.validation.passed is True
        assert answer.prompt_version == PROMPT_VERSION
        assert answer.provider == "fake" and answer.model == "fake-small-1"

    def test_the_evidence_reaches_the_model_before_it_answers(self):
        providers, _move = self._providers()
        llm = FakeLLM({"message": "It was a blunder."})
        agent = _agent(providers, llm)
        agent.ask(
            "Why was this move bad?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        prompt = llm.prompts[0]
        assert "EVIDENCE ALREADY GATHERED THIS TURN" in prompt
        assert "fxg5" in prompt
        assert "+5.31" in prompt and "+0.23" in prompt
        assert "you may not add to them from memory" in prompt
        # The tool spec is offered, not the whole catalogue.
        offered = {spec["function"]["name"] for spec in llm.calls[0]["tools"]}
        assert "get_move_analysis" in offered
        assert "generate_training_position" not in offered

    def test_the_answer_carries_actions_backed_by_the_data(self):
        providers, _move = self._providers()
        agent = _agent(providers, FakeLLM({"message": "A blunder."}))
        answer = agent.ask(
            "Why was this move bad?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        by_action = {action.action: action for action in answer.actions}
        assert by_action[AnswerAction.SHOW_POSITION].href == f"/game/{GAME_ID}?ply=17"
        assert by_action[AnswerAction.SHOW_BEST_LINE].is_available
        assert by_action[AnswerAction.COMPARE_MOVES].params["best"] == "Qh5+"
        assert by_action[AnswerAction.OPEN_GAME].is_available

    def test_the_position_is_resolved_from_context_not_from_the_user(self):
        """Board awareness: no FEN is pasted, and none is asked for."""
        providers, move = self._providers()
        llm = FakeLLM({"message": "The board facts say White is to move."})
        agent = _agent(providers, llm)
        answer = agent.ask(
            "Is there an advantage here?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        prompt = llm.prompts[0]
        assert f"active_game_id: {GAME_ID}" in prompt
        assert "selected_ply: 17" in prompt
        # The position was read from the selected ply, not invented from a FEN the
        # user never supplied.
        assert move.calls[0] == (GAME_ID, 17)
        assert answer.evidence is not None
        positions = answer.evidence.of_kind(EvidenceKind.POSITION)
        assert positions and positions[0].data["ply"] == 17

    def test_a_follow_up_needs_no_repetition(self):
        providers, _move = self._providers()
        memory = ConversationMemory()
        # The client reports the board it has open, and the conversation carries it.
        memory.declare(game_id=GAME_ID, ply=17)
        agent = _agent(
            providers,
            FakeLLM({"message": "It was a blunder."}, {"message": "Qh5+ was better."}),
        )

        first = agent.ask(
            "Why was move 9 fxg5 bad?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
            memory=memory,
        )
        assert first.validation.passed is True
        memory.add_user("Why was move 9 fxg5 bad?")
        memory.add_assistant(first.message)

        second = agent.ask("What should I have played?", context=AgentContext(), memory=memory)
        # The second turn runs with no explicit context at all: the game and ply come
        # from the conversation, so the follow-up needed no repetition.
        assert second.trace["context"]["active_game_id"] == GAME_ID
        assert second.trace["context"]["selected_ply"] == 17
        assert second.evidence is not None
        assert second.evidence.of_kind(EvidenceKind.MOVE_ANALYSIS)

    def test_a_bare_move_reference_in_the_question_is_enough(self):
        providers, move = self._providers()
        agent = _agent(providers, FakeLLM({"message": "It was a blunder."}))
        agent.ask("Why was 9...Qd8 bad?", context=AgentContext(active_game_id=GAME_ID))
        # "9...Qd8" is the black move of move 9, so ply 18.
        assert move.calls == [(GAME_ID, 18)]

    def test_a_move_question_with_no_selection_says_so(self):
        providers, move = self._providers()
        agent = _agent(providers)
        answer = agent.ask("Why was this bad?", context=AgentContext(active_game_id=GAME_ID))
        # No ply in context and none in the question, so nothing was fetched and the
        # turn reports the gap rather than picking a move.
        assert move.calls == []
        assert answer.evidence is not None
        assert any("no move is selected" in entry.reason.lower() for entry in answer.evidence.missing)


# --- scenario 2: a game question ----------------------------------------------------


class TestGameQuestion:
    def _providers(self) -> AgentProviders:
        return AgentProviders(
            critical_moments=Recorder(
                {
                    "game_id": GAME_ID,
                    "total_moments": 2,
                    "returned": 2,
                    "selection": "ordered by absolute evaluation swing",
                    "moments": [
                        {
                            "ply": 17,
                            "move_number": 9,
                            "side": "white",
                            "statement": "A clearly winning position was not converted.",
                            "certainty": "confirmed",
                            "evidence": {"swing_cp": 508, "severity_score": 1.2},
                        },
                        {
                            "ply": 45,
                            "move_number": 23,
                            "side": "white",
                            "statement": "The evaluation moved back towards equality.",
                            "certainty": "confirmed",
                            "evidence": {"swing_cp": 210, "severity_score": 0.6},
                        },
                    ],
                }
            )
        )

    def test_where_did_i_lose_this_game_uses_the_stored_moments(self):
        llm = FakeLLM(
            {
                "message": (
                    "The game turned at move 9: a clearly winning position was not "
                    "converted, a 508 centipawn swing. A second moment at move 23 took "
                    "the evaluation back towards equality."
                )
            }
        )
        agent = _agent(self._providers(), llm)
        answer = agent.ask(
            "Where did I lose this game?",
            context=AgentContext(active_game_id=GAME_ID, mode=ResponseMode.GAME_REVIEW),
        )
        assert answer.validation.passed is True
        assert answer.evidence is not None
        moments = answer.evidence.of_kind(EvidenceKind.CRITICAL_MOMENT)
        assert [moment.ply for moment in moments] == [17, 45]
        assert moments[0].certainty.value == "confirmed"
        by_action = {action.action: action for action in answer.actions}
        assert by_action[AnswerAction.VIEW_CRITICAL_MOMENT].href == f"/game/{GAME_ID}?ply=17"
        assert answer.mode is ResponseMode.GAME_REVIEW

    def test_an_invented_swing_is_caught(self):
        """The moments are real; the invented 1.90-pawn figure is not."""
        agent = _agent(self._providers(), FakeLLM({"message": "You lost 1.90 pawns at move 9."}))
        answer = agent.ask(
            "Where did I lose this game?", context=AgentContext(active_game_id=GAME_ID)
        )
        assert answer.validation.passed is False
        assert any(finding.kind == "engine_eval_magnitude" for finding in answer.validation.failures)


# --- scenario 3: a player question ---------------------------------------------------


class TestPlayerQuestion:
    def _insights(self, *, games: int, coverage: str, claim_level: str) -> dict:
        return {
            "player_id": "8",
            "coverage": coverage,
            "sufficient_data": games >= 20,
            "insights": [
                {
                    "id": "phase-opening",
                    "claim_level": claim_level,
                    "metric": "error_category",
                    "category": "phase",
                    "statement": f"Opening-phase errors recur in 21 of {games} analysed games.",
                    "value": 21.0,
                    "games": games,
                    "occurrences": 21,
                    "evidence": [{"game_id": GAME_ID, "ply": 17, "label": "opening_error"}],
                }
            ],
        }

    def test_a_supported_recurring_weakness_is_reported_with_its_sample(self):
        providers = AgentProviders(
            player_insights=Recorder(self._insights(games=30, coverage="moderate", claim_level="tendency"))
        )
        llm = FakeLLM(
            {
                "message": (
                    "Opening-phase errors recur in 21 of 30 analysed games, so that is "
                    "your most supported recurring weakness."
                )
            }
        )
        agent = _agent(providers, llm)
        answer = agent.ask("What is my biggest weakness?", context=AgentContext(player_id="8"))
        assert answer.validation.passed is True
        assert answer.evidence is not None
        insights = answer.evidence.of_kind(EvidenceKind.PLAYER_INSIGHT)
        # The entry keeps its own fields, so the claim level and sample are citable.
        assert insights[0].data["claim_level"] == "tendency"
        assert insights[0].data["games"] == 30
        by_action = {action.action: action for action in answer.actions}
        assert by_action[AnswerAction.VIEW_PLAYER_PATTERN].href == "/players/8"

    def test_a_small_sample_forbids_calling_it_a_weakness(self):
        profile = Recorder(
            {
                "player_id": "8",
                "display_name": "Piyush1206",
                "coverage": "limited",
                "sufficient_data": False,
                "analyzed_games": 2,
                "imported_games": 2,
                "games": {"wins": 1, "draws": 0, "losses": 1},
            }
        )
        toolbox = build_agent_toolbox(AgentProviders(player_profile=profile))
        outcome = toolbox.call("get_player_profile", {}, AgentContext(player_id="8"))
        assert outcome.ok is True
        # The tool states the restriction itself, rather than trusting the model to
        # notice the sample size.
        assert "NOT enough to call anything a recurring weakness" in outcome.data["sample_note"]
        assert "Never say 'you always'" in outcome.data["sample_note"]

    def test_a_named_sample_is_verified_against_the_profile(self):
        providers = AgentProviders(
            player_insights=Recorder(self._insights(games=2, coverage="limited", claim_level="observation"))
        )
        agent = _agent(
            providers,
            FakeLLM({"message": "You always blunder: 14 blunders in 20 analysed games."}),
        )
        answer = agent.ask("What is my biggest weakness?", context=AgentContext(player_id="8"))
        # "14" and "20" appear nowhere in this player's evidence.
        assert answer.validation.passed is False
        assert {finding.kind for finding in answer.validation.failures} >= {"count"}
        assert answer.has_dissent is True

    def test_a_player_question_without_a_profile_is_refused(self):
        """The player tools are not even offered, and the gap is recorded."""
        agent = _agent(AgentProviders())
        answer = agent.ask("What is my biggest weakness?", context=AgentContext())
        assert answer.deterministic is True
        assert answer.evidence is not None
        assert any(entry.tool == "get_player_insights" for entry in answer.evidence.missing)
        assert any("no active player" in entry.reason for entry in answer.evidence.missing)
        assert "Unavailable" in answer.message


# --- scenario 4: openings -------------------------------------------------------------


class TestOpeningQuestion:
    def test_a_known_line_is_named_from_the_base(self):
        moves = Recorder([{"uci": "e2e4"}, {"uci": "c7c5"}])
        agent = _agent(
            AgentProviders(game_moves=moves),
            FakeLLM({"message": "You played the Sicilian Defence (B20)."}),
        )
        answer = agent.ask(
            "What opening did I play?", context=AgentContext(active_game_id=GAME_ID)
        )
        assert answer.validation.passed is True
        assert answer.evidence is not None
        opening = answer.evidence.of_kind(EvidenceKind.OPENING)[0]
        assert opening.data["name"] == "Sicilian Defence"
        assert opening.source.value == "argus_derived_feature"

    def test_an_unknown_line_forbids_naming_an_opening(self):
        moves = Recorder([{"uci": "a2a3"}, {"uci": "h7h6"}, {"uci": "a3a4"}])
        agent = _agent(
            AgentProviders(game_moves=moves),
            FakeLLM({"message": "This is the Caro-Kann Defence."}),
        )
        answer = agent.ask(
            "What opening did I play?", context=AgentContext(active_game_id=GAME_ID)
        )
        assert answer.evidence is not None
        assert answer.evidence.of_kind(EvidenceKind.OPENING)[0].data["matched"] is False
        assert answer.validation.passed is False
        assert any(finding.kind == "opening" for finding in answer.validation.failures)


# --- scenario 5: no production model --------------------------------------------------


class TestPredictionUnavailable:
    def test_a_probability_question_with_no_model_is_answered_without_the_llm(self):
        """The refusal is a stored fact, so it must not depend on a reachable LLM.

        With no production model the answer is exact: Caissa serves no probabilities.
        Answering it deterministically means a provider outage cannot turn an honest
        refusal into a degraded turn.
        """
        llm = FakeLLM({"message": "unused"})
        agent = _agent(AgentProviders(), llm)
        answer = agent.ask(
            "What is my win probability in this game?",
            context=AgentContext(active_game_id=GAME_ID),
        )
        assert answer.deterministic is True
        assert llm.calls == []
        assert "cannot" in answer.message.lower() or "no prediction model" in answer.message.lower()
        assert answer.evidence is not None
        assert answer.evidence.of_kind(EvidenceKind.PREDICTION)
        # Nothing in the evidence is a probability, and the answer invents none.
        assert not any(item.data.get("probabilities") for item in answer.evidence.items)

    def test_a_positive_status_defers_to_the_model_and_a_fabricated_figure_is_caught(self):
        # When a model is available the deterministic refusal must step aside so the
        # model's own output is presented; the validator then catches a number that
        # does not come from the gated tool.
        status = Recorder({"task": "game_outcome", "available": True, "reason": None})
        agent = _agent(
            AgentProviders(prediction_status=status),
            FakeLLM({"message": "White has a 62% win probability in this position."}),
        )
        answer = agent.ask(
            "What is my win probability in this game?",
            context=AgentContext(active_game_id=GAME_ID),
        )
        assert answer.deterministic is False
        assert answer.validation.passed is False
        assert any(finding.kind == "percentage" for finding in answer.validation.failures)

    def test_a_capability_question_is_answered_without_consulting_the_model(self):
        """Cost control (spec §37): a stored fact does not need generation."""
        llm = FakeLLM({"message": "unused"})
        agent = _agent(AgentProviders(), llm)
        answer = agent.ask("Can you predict my win probability?", context=AgentContext())
        assert answer.deterministic is True
        assert llm.calls == []
        assert "probability" in answer.message.lower()

    def test_a_served_prediction_is_usable_and_carries_its_coverage(self):
        status = Recorder({"task": "game_outcome", "available": True, "reason": None})
        prediction = Recorder(
            {
                "available": True,
                "model_id": "game_outcome-lr-20261001",
                "model_status": "PRODUCTION",
                "prediction": "white_win",
                "probabilities": {"white_win": 0.62, "draw": 0.23, "black_win": 0.15},
                "data_coverage": {"label": "moderate", "games": 24000},
                "disclaimer": "Estimate for the model's declared prediction setting.",
            }
        )
        providers = AgentProviders(prediction_status=status, prediction=prediction)
        agent = _agent(
            providers,
            FakeLLM(
                # The model must go through the gated route to obtain the number.
                {
                    "tool_calls": [
                        {
                            "name": "get_validated_prediction",
                            "arguments": {"task": "game_outcome"},
                        }
                    ]
                },
                {
                    "message": (
                        "The validated model estimates a 62% probability for a White win "
                        "under its defined prediction setting, on moderate data coverage."
                    )
                },
            ),
        )
        answer = agent.ask(
            "What is my win probability?", context=AgentContext(active_game_id=GAME_ID)
        )
        assert prediction.calls, "the validated model was never consulted"
        assert answer.validation.passed is True
        assert answer.evidence is not None
        served = answer.evidence.of_kind(EvidenceKind.PREDICTION)[-1]
        assert served.data["probabilities"]["white_win"] == 0.62
        assert served.data["data_coverage"]["label"] == "moderate"


# --- scenario 6: no engine --------------------------------------------------------------


class TestEngineUnavailable:
    def test_an_evaluation_request_reports_the_missing_engine(self):
        agent = _agent(AgentProviders())
        answer = agent.ask("What is the evaluation here?", context=AgentContext(current_fen=START_FEN))
        assert answer.deterministic is True
        assert answer.evidence is not None
        engine_gaps = [
            entry for entry in answer.evidence.missing if "engine" in entry.reason.lower()
        ]
        assert engine_gaps, "the missing engine was not recorded"
        assert "Unavailable" in answer.message

    def test_a_model_asked_for_an_evaluation_is_told_there_is_no_engine(self):
        llm = FakeLLM(
            {"tool_calls": [{"name": "analyze_position", "arguments": {"fen": START_FEN}}]},
            {
                "message": (
                    "Caissa cannot evaluate this position: no chess engine is reachable "
                    "in this deployment, so there is no evaluation to quote."
                )
            },
        )
        agent = _agent(AgentProviders(), llm)
        answer = agent.ask("What is the evaluation here?", context=AgentContext(current_fen=START_FEN))
        # The engine's refusal is fed back verbatim, so the model is told why rather
        # than left to infer it.
        assert "No chess engine is configured" in llm.prompts[1]
        assert answer.validation.passed is True

    def test_an_invented_evaluation_is_caught_when_the_engine_is_down(self):
        llm = FakeLLM(
            {"tool_calls": [{"name": "analyze_position", "arguments": {"fen": START_FEN}}]},
            {"message": "Stockfish evaluates this at +3.40, so White is winning."},
        )
        agent = _agent(AgentProviders(), llm)
        answer = agent.ask("What is the evaluation here?", context=AgentContext(current_fen=START_FEN))
        assert answer.validation.passed is False
        assert any(
            "no engine evidence" in finding.detail for finding in answer.validation.failures
        )


# --- the tool loop --------------------------------------------------------------------------


class TestToolLoop:
    def test_the_model_may_call_a_tool_itself_and_the_result_is_evidence(self):
        llm = FakeLLM(
            {"tool_calls": [{"name": "search_chess_knowledge", "arguments": {"query": "fork"}}]},
            {"message": "A fork attacks two pieces at once; the engine's best move exploited one."},
        )
        agent = _agent(AgentProviders(), llm)
        answer = agent.ask("What is a fork?", context=AgentContext())
        assert len(llm.calls) == 2
        assert answer.evidence is not None
        knowledge = answer.evidence.of_kind(EvidenceKind.KNOWLEDGE)
        assert knowledge and knowledge[0].data["found"] is True
        assert {record["tool"] for record in answer.trace["tool_calls"]} >= {"search_chess_knowledge"}

    def test_a_tool_error_is_fed_back_without_killing_the_turn(self):
        llm = FakeLLM(
            {"tool_calls": [{"name": "get_move_analysis", "arguments": {"ply": 17}}]},
            {"message": "Caissa has no stored analysis for that move, so I cannot explain it."},
        )
        agent = _agent(AgentProviders(), llm)
        answer = agent.ask("Why was ply 17 bad?", context=AgentContext(active_game_id=GAME_ID))
        # The failure reason reaches the model, which is how it can name the gap.
        assert "cannot reach stored move analysis" in llm.prompts[1]
        assert answer.validation.passed is True
        assert answer.trace["failed_tool_count"] >= 1

    def test_the_loop_is_bounded_when_the_model_never_answers(self):
        always_tools = {
            "tool_calls": [{"name": "search_chess_knowledge", "arguments": {"query": "fork"}}]
        }
        llm = FakeLLM(always_tools, always_tools, always_tools, always_tools, always_tools)
        agent = _agent(AgentProviders(), llm, limits=AgentLimits(max_iterations=2, max_tool_calls=6))
        answer = agent.ask("What is a fork?", context=AgentContext())
        assert len(llm.calls) == 2, "the iteration budget did not stop the loop"
        assert answer.trace["budget"]["reached"]
        assert "could not generate" in answer.message.lower()
        # It still says what it did manage to retrieve.
        assert "fork" in answer.message.lower()

    def test_a_provider_failure_returns_evidence_only(self):
        class Broken:
            provider = "broken"
            model = "broken-1"

            def complete(self, messages, tools=None):  # noqa: ANN001
                raise ArgusError("the provider is unavailable")

        providers = AgentProviders(move_analysis=Recorder(dict(MOVE)))
        agent = _agent(providers, Broken())
        answer = agent.ask(
            "Why was this move bad?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        assert answer.trace["status"] == "llm_error"
        assert answer.deterministic is True
        # It falls back to the evidence it already holds instead of inventing prose.
        assert "fxg5" in answer.message
        assert any("Generation failed" in limitation for limitation in answer.limitations)

    def test_the_existing_llm_layer_drives_the_agent(self):
        """Provider abstraction: an ``argus.llm`` client satisfies the agent's protocol."""
        echo = EchoLLMClient()
        providers = AgentProviders(move_analysis=Recorder(dict(MOVE)))
        agent = CoachingAgent(
            build_agent_toolbox(providers),
            llm_client=echo,
            provider_name="echo",
            model_name="echo-1",
        )
        answer = agent.ask(
            "What is the evaluation here?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        # The echo provider asks for the position on its first turn, so this proves
        # the tool-calling path runs against the real provider seam.
        assert {record["tool"] for record in answer.trace["tool_calls"]} >= {"get_current_position"}
        assert answer.provider == "echo"
        assert answer.prompt_version == PROMPT_VERSION
        assert "echo provider" in answer.message


# --- authorization --------------------------------------------------------------------------


class TestAuthorizationEndToEnd:
    def test_another_callers_game_is_refused_before_the_provider_runs(self):
        other = "71b2e6c4-74ee-4c4b-82c5-be0bcc9b1c1d"
        move = Recorder(dict(MOVE))
        agent = _agent(AgentProviders(move_analysis=move))
        answer = agent.ask(
            "Why was this move bad?",
            context=AgentContext(
                active_game_id=other,
                selected_ply=17,
                available_game_ids=[GAME_ID],
            ),
        )
        assert move.calls == [], "the tool read a game the caller does not own"
        assert answer.evidence is not None
        assert any("does not belong to this caller" in entry.reason for entry in answer.evidence.missing)

    def test_an_authorized_game_is_readable(self):
        move = Recorder(dict(MOVE))
        agent = _agent(AgentProviders(move_analysis=move))
        answer = agent.ask(
            "Why was this move bad?",
            context=AgentContext(
                active_game_id=GAME_ID,
                selected_ply=17,
                available_game_ids=[GAME_ID],
            ),
        )
        assert move.calls == [(GAME_ID, 17)]
        assert answer.evidence is not None
        assert answer.evidence.of_kind(EvidenceKind.MOVE_ANALYSIS)


# --- adversarial prompts (spec §36) ---------------------------------------------------------


class TestAdversarialPrompts:
    def _grounded_agent(self, message: str) -> CoachingAgent:
        return _agent(
            AgentProviders(move_analysis=Recorder(dict(MOVE)), game_moves=Recorder([])),
            FakeLLM({"message": message}),
        )

    def test_an_invented_engine_value_is_flagged(self):
        agent = self._grounded_agent(
            "The engine said +3.40, and the move was worth about 2 pawns."
        )
        answer = agent.ask(
            "What was the Stockfish evaluation of this move?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        assert answer.validation.passed is False
        kinds = {finding.kind for finding in answer.validation.failures}
        assert "engine_eval" in kinds or "engine_eval_magnitude" in kinds

    def test_assumed_history_is_flagged(self):
        agent = self._grounded_agent("Assuming you have played 100 games, your record is 40-60.")
        answer = agent.ask("How have I done across my games?", context=AgentContext(player_id="8"))
        assert answer.validation.passed is False
        assert any(finding.kind == "count" for finding in answer.validation.failures)

    def test_a_made_up_line_is_caught_through_its_evaluation(self):
        # The quoted evaluation must be clearly ungrounded: +2.75 is far outside the
        # tolerance of every stored value for this move (+5.31, +0.23, -5.08). A
        # value *near* a stored one (e.g. +0.35, within 12cp of +0.23) is not a
        # hallucination and is deliberately not flagged — the validator checks that
        # a number is traceable, not that it is bit-exact.
        agent = self._grounded_agent(
            "A likely line is 1. e4 e5 2. Nf3 Nc6 with an evaluation of about +2.75."
        )
        answer = agent.ask(
            "Make up a likely engine line.",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        assert answer.validation.passed is False
        # Stated plainly: the *numbers* in an invented line are checkable; the move
        # sequence itself is not, and no claim is made that it is.
        assert any("engine" in finding.kind for finding in answer.validation.failures)

    def test_an_invented_position_is_flagged(self):
        agent = self._grounded_agent(
            "The position was 8/8/8/8/8/8/8/8 w - - 0 1 after your move."
        )
        answer = agent.ask(
            "What is the position after my move?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        assert answer.validation.passed is False
        assert any(finding.kind == "fen" for finding in answer.validation.failures)

    def test_a_claim_may_not_reference_evidence_that_does_not_exist(self):
        from argus.ai_agent.core.response import Claim

        providers = AgentProviders(move_analysis=Recorder(dict(MOVE)))
        agent = _agent(providers, FakeLLM({"message": "The engine agrees with me."}))
        answer = agent.ask(
            "Why was this move bad?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        # Nothing checkable was claimed, so the generated answer passes as written.
        assert answer.validation.passed is True
        # But a claim pointing at evidence this turn never retrieved is refuted.
        fabricated = Claim(
            kind=ClaimKind.FACT,
            text="Stockfish evaluates this at +3.40.",
            evidence_refs=["game:some-other-game@ply=99"],
        )
        report = validate_answer("Stockfish evaluates this at +3.40.", answer.evidence, claims=[fabricated])
        assert report.passed is False
        assert {finding.kind for finding in report.failures} >= {"claim_reference", "engine_eval"}


# --- observability and cost ------------------------------------------------------------------


class TestObservabilityEndToEnd:
    def test_the_trace_records_tools_timings_and_counts(self):
        agent = _agent(AgentProviders(move_analysis=Recorder(dict(MOVE))), FakeLLM({"message": "A blunder."}))
        answer = agent.ask(
            "Why was this move bad?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        trace = answer.trace
        assert trace["tool_count"] == 1
        assert trace["tool_calls"][0]["tool"] == "get_move_analysis"
        assert trace["tool_calls"][0]["duration_ms"] >= 0.0
        assert trace["total_ms"] >= 0.0
        assert trace["provider"] == "fake"
        assert trace["validation"]["passed"] is True
        assert trace["evidence_items"] >= 1

    def test_the_trace_does_not_carry_the_question_or_a_key(self):
        agent = _agent(
            AgentProviders(move_analysis=Recorder(dict(MOVE))),
            FakeLLM({"message": "A blunder."}),
        )
        answer = agent.ask(
            "Why was this bad? my key is sk-live-abcdef123456",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        rendered = str(answer.trace)
        assert "sk-live-abcdef123456" not in rendered
        assert "question" not in answer.trace
        for record in answer.trace["tool_calls"]:
            assert "question" not in record["arguments"]

    def test_a_missing_provider_is_recorded_with_its_reason(self):
        agent = _agent(AgentProviders(), FakeLLM({"message": "I cannot check that."}))
        answer = agent.ask(
            "Why was this move bad?", context=AgentContext(active_game_id=GAME_ID, selected_ply=17)
        )
        assert answer.evidence is not None
        reason = next(
            entry.reason for entry in answer.evidence.missing if entry.tool == "get_move_analysis"
        )
        assert "stored move analysis" in reason

    def test_the_answer_reports_its_own_validation(self):
        agent = _agent(
            AgentProviders(move_analysis=Recorder(dict(MOVE))),
            FakeLLM({"message": "The move cost 508 centipawns."}),
        )
        answer = agent.ask(
            "Why was this move bad?",
            context=AgentContext(active_game_id=GAME_ID, selected_ply=17),
        )
        assert answer.validation.checked >= 1
        assert "agreed" in answer.validation.summary()
        assert answer.headline().startswith("answered by fake")
