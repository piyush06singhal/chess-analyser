"""Phase 7 evaluation suite, run for real (spec §35, §36, §45).

`argus.ai_agent.evaluation` is the runnable answer to "does the agent pick the right
tools, and does it refuse to invent?". It is only worth anything if it is actually
executed, so this test runs the whole case list — the ten spec questions plus the
adversarial set — against a toolbox wired to fixture providers, using the suite's own
scripted and hallucinating providers.

Two properties are being pinned:

* a faithful provider (one that never states a fact the evidence block did not
  contain) must pass every positive expectation, so a failure here is the agent's
  tool selection or permission logic, not the model's;
* a *hallucinating* provider must be caught by the validator on every adversarial
  case, because that is the only thing standing between a plausible sentence and a
  fabricated evaluation.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.ai_agent.core.context import AgentContext  # noqa: E402
from argus.ai_agent.core.loop import CoachingAgent  # noqa: E402
from argus.ai_agent.evaluation import (  # noqa: E402
    EVAL_CASES,
    EvalCase,
    provider_for,
    run_evaluation,
)
from argus.ai_agent.tools import AgentProviders, build_agent_toolbox  # noqa: E402

from tests.conftest import START_FEN  # noqa: E402

GAME_ID = "eval-game"


class Provider:
    """A provider callable that records its calls and returns a fixed payload."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.payload


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

MOMENT = {
    "ply": 17,
    "move_number": 9,
    "color": "white",
    "san": "fxg5",
    "best_move_san": "Qh5+",
    "fen_before": START_FEN,
    "evaluation_before_cp": 531,
    "evaluation_change_cp": -508,
    "centipawn_loss": 508,
    "classification": "blunder",
    "phase": "opening",
    "statement": "Move 9.fxg5 cost 508 centipawns.",
    "evidence": {"swing_cp": -508, "severity_score": 0.8},
}

PROFILE = {
    "player_id": "1",
    "display_name": "Evaluated Player",
    "profile_version": "5.0",
    "methodology_version": "5.0",
    "coverage": "limited",
    "sufficient_data": False,
    "imported_games": 2,
    "analyzed_games": 2,
    "games": {"wins": 1, "draws": 0, "losses": 1, "analyzed_games": 2},
    "insights": [
        {
            "id": "insight-1",
            "category": "weakness_candidate",
            "title": "Tactical oversights in the middlegame",
            "statement": "2 of 2 analysed games contain a blunder.",
            "claim_level": "observation",
            "games": 2,
            "occurrences": 2,
            "coverage": "limited",
            "evidence": [{"game_id": GAME_ID, "ply": 17, "san": "fxg5"}],
        }
    ],
}


def _providers() -> AgentProviders:
    return AgentProviders(
        move_analysis=Provider(MOVE),
        critical_moments=Provider(
            {
                "game_id": GAME_ID,
                "total_moments": 1,
                "returned": 1,
                "moments": [MOMENT],
                "engine_critical_moments": [MOMENT],
            }
        ),
        game_moves=Provider([{"uci": "e2e4"}, {"uci": "c7c5"}]),
        game_lookup=Provider(
            {
                "id": GAME_ID,
                "white_player": "White",
                "black_player": "Black",
                "result": "1-0",
                "opening_name": "Sicilian Defence",
                "eco_code": "B20",
                "analysis_status": "analyzed",
                "move_count": 40,
            }
        ),
        game_summary=Provider(
            {
                "white_player": "White",
                "black_player": "Black",
                "result": "1-0",
                "result_label": "White won",
                "opening_name": "Sicilian Defence",
                "eco_code": "B20",
                "moves": 40,
            }
        ),
        game_report=Provider(
            {"game_id": GAME_ID, "report_version": "4.0", "report": {"summary": {}, "phases": {}}}
        ),
        player_profile=Provider(PROFILE),
        player_insights=Provider(
            {
                "player_id": "1",
                "coverage": "limited",
                "sufficient_data": False,
                "total_insights": 1,
                "insights": PROFILE["insights"],
            }
        ),
        player_statistics=Provider(
            {
                "player_id": "1",
                "analyzed_games": 2,
                "imported_games": 2,
                "coverage": "limited",
                "sufficient_data": False,
                "games": {"wins": 1, "draws": 0, "losses": 1, "analyzed_games": 2},
                "insights": PROFILE["insights"],
            }
        ),
        player_evidence=Provider({"player_id": "1", "insights": PROFILE["insights"]}),
    )


def _factory(case: EvalCase) -> CoachingAgent:
    """One agent per case, wired to fixture providers and the case's own provider."""
    return CoachingAgent(build_agent_toolbox(_providers()), llm_client=provider_for(case))


def test_the_evaluation_suite_passes_end_to_end():
    report = run_evaluation(_factory)
    assert report.passed, "\n" + report.summary()
    assert report.cases == len(EVAL_CASES)


def test_the_suite_really_exercises_both_halves():
    """A suite that only ever asserts success is not a test of the validator."""
    report = run_evaluation(_factory)
    checks = {finding.check for finding in report.results}
    assert "validator agreed" in checks
    assert "hallucination detected" in checks
    assert any(finding.check.startswith("called one of") for finding in report.results)


def test_every_adversarial_case_is_actually_refuted():
    adversarial = [case for case in EVAL_CASES if case.provider == "hallucinating"]
    assert adversarial, "the suite must contain adversarial cases"
    report = run_evaluation(_factory, cases=adversarial)
    detected = [
        finding
        for finding in report.results
        if finding.check == "hallucination detected"
    ]
    assert len(detected) == len(adversarial)
    assert all(finding.ok for finding in detected), report.summary()


def test_the_faithful_provider_never_fails_validation():
    """The control: an answer written from the evidence must always pass."""
    positive = [
        case
        for case in EVAL_CASES
        if case.provider != "hallucinating" and case.expect_validation
    ]
    report = run_evaluation(_factory, cases=positive)
    assert report.passed, "\n" + report.summary()


def test_an_unrecognised_case_context_is_handled_without_crashing():
    """A case with neither game nor player must still produce a runnable turn."""
    case = EvalCase(
        name="context_free",
        question="Tell me about chess.",
        description="Nothing is established, so the agent must not invent anything.",
        with_game=False,
        with_ply=False,
    )
    report = run_evaluation(_factory, cases=[case])
    assert report.cases == 1
    assert all(
        finding.check != "validator agreed" or finding.ok for finding in report.results
    )


def test_the_default_context_matches_the_case_flags():
    """The suite's own context builder is part of what it tests."""
    case = EvalCase(name="x", question="q", description="d", with_game=False, with_ply=False)
    from argus.ai_agent.evaluation import _default_context

    context = _default_context(case)
    assert context == AgentContext()
