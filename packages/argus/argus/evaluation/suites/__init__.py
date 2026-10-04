"""The benchmark suites, in run order (§56 implementation order).

Each suite is registered with a stable ``name`` — the name the gates key on and
the name a report shows. Registration is one readable list so the entire
evaluation surface is auditable from one place.
"""

from __future__ import annotations

from argus.evaluation.framework import CaissaEvaluationFramework
from argus.evaluation.suites.agent import agent_suite
from argus.evaluation.suites.chess import (
    chess_rules_suite,
    fen_benchmark_suite,
    pgn_benchmark_suite,
)
from argus.evaluation.suites.engine import engine_suite
from argus.evaluation.suites.graph import graph_suite
from argus.evaluation.suites.intelligence import (
    accuracy_suite,
    game_intelligence_suite,
    move_classification_suite,
    opponent_suite,
    player_intelligence_suite,
)
from argus.evaluation.suites.knowledge import knowledge_suite
from argus.evaluation.suites.ml import ml_evaluation_suite
from argus.evaluation.suites.performance import performance_suite
from argus.evaluation.suites.realtime import realtime_suite
from argus.evaluation.suites.security import privacy_suite, security_suite
from argus.evaluation.suites.training import training_suite

#: ``(name, title, function, requires_engine, dataset_id)`` in run order.
DEFAULT_SUITES: tuple[tuple, ...] = (
    ("chess_rules", "Chess rule correctness", chess_rules_suite, False, "chess-positions"),
    ("fen_benchmark", "FEN validation", fen_benchmark_suite, False, "fen-cases"),
    ("pgn_benchmark", "PGN parsing", pgn_benchmark_suite, False, "pgn-corpus"),
    ("engine", "Engine correctness", engine_suite, True, "engine-positions"),
    (
        "move_classification",
        "Move classification",
        move_classification_suite,
        False,
        None,
    ),
    ("accuracy", "Accuracy methodology", accuracy_suite, False, None),
    # The game-intelligence suite builds its own inline positions, so it declares
    # no dataset rather than borrowing an unrelated one's identity.
    ("game_intelligence", "Game intelligence", game_intelligence_suite, False, None),
    (
        "player_intelligence",
        "Player intelligence guardrails",
        player_intelligence_suite,
        False,
        None,
    ),
    ("opponent", "Opponent intelligence guardrails", opponent_suite, False, None),
    ("training", "Training validation", training_suite, False, None),
    ("ml_evaluation", "ML evaluation", ml_evaluation_suite, False, None),
    ("agent", "AI agent grounding", agent_suite, False, "agent-questions"),
    ("knowledge", "Knowledge sourcing", knowledge_suite, False, None),
    ("graph", "Intelligence graph", graph_suite, False, None),
    ("realtime", "Live chess state", realtime_suite, False, None),
    ("security", "Security", security_suite, False, None),
    ("privacy", "Privacy isolation", privacy_suite, False, None),
    ("performance", "Performance", performance_suite, False, None),
)


def register_default_suites(framework: CaissaEvaluationFramework) -> CaissaEvaluationFramework:
    """Register every default suite, in order."""
    for name, title, function, requires_engine, dataset_id in DEFAULT_SUITES:
        framework.register_function(
            name,
            title,
            function,
            requires_engine=requires_engine,
            dataset_id=dataset_id,
        )
    return framework


__all__ = ["DEFAULT_SUITES", "register_default_suites"]
