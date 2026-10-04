"""Phase 10 decision intelligence — package-level tests (no database, no Stockfish).

The scenario engine is only meaningful if its numbers are the engine's numbers and
its refusals are honest, so these tests drive it with a **recording stub engine**
that returns known values and records exactly what it was asked. That lets the
suite assert the properties that matter:

* every evaluation in a comparison or a branch traces to an engine call,
* a move outside the MultiPV window is marked ``resulting_position``,
* an illegal move is reported, never scored,
* the original game is never modified (a branch is a value object),
* the resource limits actually clamp,
* the turning-point explorer runs with no engine call at all.
"""

from __future__ import annotations

import chess

from argus.analysis.engine.base import AnalyzedPosition, ChessEngine, EngineLine
from argus.scenarios import (
    CounterfactualAnalyzer,
    MoveQuality,
    PositionComparisonService,
    ScenarioService,
    ScenarioType,
)

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
AFTER_D4 = "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1"
AFTER_E4_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"


class _StubEngine(ChessEngine):
    """A deterministic engine: fixed scores, recorded requests, zero process.

    The scores are chosen so a comparison has a clear best move and a clear
    runner-up, and so the second-best move is outside a narrow MultiPV window —
    the case that must be labelled ``resulting_position``.
    """

    def __init__(self, *, window: int = 2) -> None:
        self.window = window
        self.requests: list[dict] = []

    def info(self) -> dict:
        return {"available": True, "engine": "stub", "version": "stub-1.0"}

    def analyze_position(self, fen, *, depth=None, multipv=None, movetime_ms=None):  # noqa: ANN001
        self.requests.append({"fen": fen, "depth": depth, "multipv": multipv, "movetime_ms": movetime_ms})
        board = chess.Board(fen)
        width = max(1, min(int(multipv or 1), self.window))
        legal = list(board.legal_moves)
        if not legal:
            return AnalyzedPosition(
                fen=fen, depth=depth or 0, multipv=width, is_terminal=True, terminal_reason="checkmate"
            )
        # Scores: strictly decreasing by rank so "best" is unambiguous.
        lines = []
        for index, move in enumerate(legal[:width]):
            lines.append(
                EngineLine(
                    index=index + 1,
                    depth=depth or 12,
                    move_uci=move.uci(),
                    move_san=board.san(move),
                    cp=100 - index * 60,
                    pv=[move.uci()],
                )
            )
        return AnalyzedPosition(
            fen=fen,
            depth=depth or 12,
            multipv=width,
            best_move_uci=lines[0].move_uci,
            best_move_san=lines[0].move_san,
            lines=lines,
            engine="stub",
            engine_version="stub-1.0",
        )

    def compare_moves(self, fen, moves, *, depth=None, movetime_ms=None):  # noqa: ANN001
        return []

    def close(self) -> None:
        return None


class TestPositionFacts:
    def test_facts_come_from_the_board_with_no_engine_call(self) -> None:
        engine = _StubEngine()
        # No engine: the structural axis must still be complete, and the engine
        # axis must be reported as unavailable rather than guessed.
        comparison = PositionComparisonService(None).compare(AFTER_E4, AFTER_D4, depth=8)
        assert engine.requests == []
        assert comparison.facts_a is not None and comparison.facts_b is not None
        assert comparison.engine_difference["available"] is False
        assert comparison.facts_a.side_to_move == "black"

    def test_a_supplied_engine_is_used_for_the_engine_axis(self) -> None:
        engine = _StubEngine()
        comparison = PositionComparisonService(engine).compare(AFTER_E4, AFTER_D4, depth=8)
        assert engine.requests  # the engine axis was asked for a real result
        assert comparison.engine_difference["available"] is True

    def test_structured_differences_report_both_directions(self) -> None:
        engine = _StubEngine()
        comparison = PositionComparisonService(engine).compare(AFTER_E4, AFTER_D4, depth=8)
        # 1.e4 and 1.d4 are structurally different positions; the comparison must
        # find differences rather than an empty list.
        assert comparison.structural_differences
        assert all(item.basis == "board_feature" for item in comparison.structural_differences)

    def test_engine_axis_is_reported_when_results_are_supplied(self) -> None:
        engine = _StubEngine()
        service = PositionComparisonService(engine)
        a = engine.analyze_position(AFTER_E4, depth=8, multipv=1)
        b = engine.analyze_position(AFTER_E4_E5, depth=8, multipv=1)
        comparison = service.compare(AFTER_E4, AFTER_E4_E5, engine_a=a, engine_b=b)
        assert comparison.engine_difference["available"] is True
        assert comparison.engine_difference["cp_a_white"] is not None


class TestCandidateComparison:
    def test_moves_inside_the_window_are_same_search(self) -> None:
        engine = _StubEngine()
        best = engine.analyze_position(START_FEN, depth=8, multipv=2).best_move_uci
        comparison = ScenarioService(engine).compare_moves(START_FEN, [best], depth=8, multipv=2)
        assessment = comparison.candidates[0]
        assert assessment.eval_source == "same_search"
        assert assessment.is_engine_best is True
        assert assessment.quality is MoveQuality.BEST
        assert assessment.centipawn_loss == 0

    def test_move_outside_the_window_is_marked_as_a_separate_search(self) -> None:
        engine = _StubEngine()
        # A move the narrow stub window never returns.
        comparison = ScenarioService(engine).compare_moves(
            START_FEN, ["g1f3"], depth=8, multipv=1
        )
        assessment = comparison.candidates[0]
        assert assessment.eval_source == "resulting_position"
        assert any("separate search" in note for note in comparison.notes)

    def test_illegal_move_is_reported_and_not_scored(self) -> None:
        engine = _StubEngine()
        before = len(engine.requests)
        comparison = ScenarioService(engine).compare_moves(START_FEN, ["e2e5"], depth=8)
        assessment = comparison.candidates[0]
        assert assessment.legal is False
        assert assessment.cp is None
        assert "not a legal move" in (assessment.legality_note or "")
        # No search was spent on a move that cannot be played.
        assert len(engine.requests) == before + 1  # only the root search

    def test_include_engine_top_adds_the_engines_own_moves(self) -> None:
        engine = _StubEngine(window=4)
        comparison = ScenarioService(engine).compare_moves(
            START_FEN, [], depth=8, multipv=3, include_top=3
        )
        assert len(comparison.candidates) == 3
        assert all(item.legal for item in comparison.candidates)
        assert all(item.eval_source == "same_search" for item in comparison.candidates)
        assert comparison.best_move_uci is not None

    def test_candidate_limit_is_enforced_and_disclosed(self) -> None:
        engine = _StubEngine()
        comparison = ScenarioService(engine).compare_moves(
            START_FEN, ["e2e4", "d2d4", "g1f3", "b1c3"], depth=8, multipv=9
        )
        # The MultiPV ceiling is 8, so the request is clamped and said to be.
        assert comparison.truncated is True

    def test_tactical_and_material_consequences_are_read_from_the_board(self) -> None:
        engine = _StubEngine()
        comparison = ScenarioService(engine).compare_moves(START_FEN, ["e2e4"], depth=8)
        assessment = comparison.candidates[0]
        assert assessment.material_consequence == "no material change"
        assert assessment.position_type and "opening" in assessment.position_type


class TestCounterfactualBranch:
    def test_branch_never_modifies_the_source_position(self) -> None:
        engine = _StubEngine()
        board = chess.Board(START_FEN)
        before = board.fen()
        analyzer = CounterfactualAnalyzer(engine)
        branch = analyzer.branch(START_FEN, "e2e4", actual_move="d2d4", plies_ahead=3, depth=8)
        assert board.fen() == before
        assert branch.source_fen == before
        assert branch.actual_move_uci == "d2d4"
        assert branch.alternative_move_uci == "e2e4"

    def test_continuation_plies_are_real_engine_moves(self) -> None:
        engine = _StubEngine()
        branch = CounterfactualAnalyzer(engine).branch(
            START_FEN, "e2e4", actual_move="d2d4", plies_ahead=4, depth=8
        )
        assert len(branch.alternative_continuation) == 3 or len(branch.alternative_continuation) == 4
        # Every recorded ply is legal in the position it claims to come from.
        for ply in branch.alternative_continuation:
            board = chess.Board(ply.fen_before)
            assert chess.Move.from_uci(ply.uci) in board.legal_moves
            assert board.san(chess.Move.from_uci(ply.uci)) == ply.san

    def test_resulting_positions_are_compared_and_the_axes_are_separate(self) -> None:
        engine = _StubEngine()
        branch = CounterfactualAnalyzer(engine).branch(
            START_FEN, "e2e4", actual_move="d2d4", plies_ahead=2, depth=8
        )
        assert branch.comparison is not None
        assert branch.comparison.fen_a and branch.comparison.fen_b
        # The structural axis is always populated; the engine axis is only
        # populated when a result exists, and says so either way.
        assert "available" in branch.comparison.engine_difference

    def test_illegal_alternative_raises_a_domain_error(self) -> None:
        from argus.shared.errors import InvalidMoveError

        engine = _StubEngine()
        try:
            CounterfactualAnalyzer(engine).branch(START_FEN, "e2e5", depth=8)
        except InvalidMoveError as exc:
            assert "not a legal move" in exc.message
        else:  # pragma: no cover - the call must raise
            raise AssertionError("an illegal alternative must not be evaluated")

    def test_continuation_length_is_clamped_and_disclosed(self) -> None:
        engine = _StubEngine()
        branch = CounterfactualAnalyzer(engine).branch(
            START_FEN, "e2e4", plies_ahead=999, depth=8
        )
        assert branch.plies_requested == 12
        assert any("clamped" in note for note in branch.notes)


class TestServiceRefusals:
    def test_no_engine_means_unavailable_not_a_guess(self) -> None:
        service = ScenarioService(None)
        assert service.engine_available() is False
        outcome = service.counterfactual_safely(START_FEN, "e2e4")
        assert outcome.status == "unavailable"
        assert outcome.branch is None
        meta = service.meta()
        assert meta["engine"]["available"] is False
        assert meta["capabilities"]["counterfactual_branch"] is False

    def test_illegal_move_is_a_result_not_an_exception(self) -> None:
        service = ScenarioService(_StubEngine())
        outcome = service.counterfactual_safely(START_FEN, "e2e5")
        assert outcome.status == "illegal_move"
        assert outcome.message and "not a legal move" in outcome.message

    def test_cached_results_are_returned_and_counted(self) -> None:
        service = ScenarioService(_StubEngine())
        service.compare_moves(START_FEN, ["e2e4"], depth=8)
        service.compare_moves(START_FEN, ["e2e4"], depth=8)
        stats = service.cache.stats()
        assert stats["hits"] == 1 and stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_a_different_search_configuration_is_not_a_cache_hit(self) -> None:
        service = ScenarioService(_StubEngine())
        service.compare_moves(START_FEN, ["e2e4"], depth=8)
        service.compare_moves(START_FEN, ["e2e4"], depth=12)
        assert service.cache.stats()["hits"] == 0

    def test_prediction_is_unavailable_rather_than_simulated(self) -> None:
        service = ScenarioService(_StubEngine())
        payload = service.attach_prediction("move_error_risk", [])
        assert payload["available"] is False
        assert payload["task"] == "move_error_risk"
        assert payload["reason"]


class TestWhyNotWhatIf:
    def test_why_not_reports_the_best_move_honestly(self) -> None:
        service = ScenarioService(_StubEngine())
        best = service.compare_moves(START_FEN, [], depth=8, multipv=3, include_top=3)
        result = service.why_not(START_FEN, best.candidates[0].uci, depth=8, multipv=3)
        assert result["status"] in ("ok", "move_is_best")
        if result["status"] == "move_is_best":
            assert "no inferiority" in result["message"]

    def test_why_not_offers_better_alternatives_for_an_inferior_move(self) -> None:
        service = ScenarioService(_StubEngine())
        top = service.compare_moves(START_FEN, [], depth=8, multipv=4, include_top=4)
        worst = sorted(
            (item for item in top.candidates if item.cp is not None),
            key=lambda item: item.cp or 0,
        )[0]
        result = service.why_not(START_FEN, worst.uci, depth=8, multipv=4)
        assert result["status"] == "ok"
        assert result["better_alternatives"]
        assert result["explanation"]["facts"]

    def test_what_if_reports_the_measured_change(self) -> None:
        service = ScenarioService(_StubEngine())
        result = service.what_if(START_FEN, "e2e4", actual_move="d2d4", plies_ahead=2, depth=8)
        assert result["status"] == "ok"
        assert result["branch"]["alternative_move_uci"] == "e2e4"
        assert result["explanation"]["facts"]

    def test_explanation_facts_carry_their_source(self) -> None:
        service = ScenarioService(_StubEngine())
        result = service.what_if(START_FEN, "e2e4", actual_move="d2d4", plies_ahead=2, depth=8)
        facts = " ".join(result["explanation"]["facts"])
        assert "mover's perspective" in facts
        assert "engine configuration" in facts or "engine_config" in result["explanation"]["numbers"]


class TestTurningPointExplorer:
    def _row(self, ply: int, **overrides) -> dict:
        row = {
            "ply": ply,
            "move_number": (ply + 1) // 2,
            "mover": "white" if ply % 2 else "black",
            "fen_before": START_FEN,
            "fen_after": AFTER_E4,
            "played_move_uci": "e2e4",
            "played_move_san": "e4",
            "best_move_uci": "d2d4",
            "best_move_san": "d4",
            "evaluation_before_cp": 20,
            "evaluation_after_cp": -300,
            "centipawn_loss": 320,
            "classification": "blunder",
            "candidate_moves": [
                {"rank": 1, "uci": "d2d4", "san": "d4", "cp": 20, "mate": None, "pv": ["d2d4"]},
                {"rank": 2, "uci": "e2e4", "san": "e4", "cp": -300, "mate": None, "pv": ["e2e4"]},
            ],
        }
        row.update(overrides)
        return row

    def test_explorer_needs_no_engine_call(self) -> None:
        engine = _StubEngine()
        service = ScenarioService(engine)
        explorer = service.explore_game(
            game_id="g1", rows=[self._row(1)], moves_total=40
        )
        assert engine.requests == []
        assert explorer.plies_analyzed == 1
        assert explorer.turning_points

    def test_alternatives_come_from_stored_analysis_only(self) -> None:
        explorer = ScenarioService(_StubEngine()).explore_game(
            game_id="g1", rows=[self._row(1)]
        )
        moment = explorer.turning_points[0]
        assert moment.what_if_available is True
        assert {item.uci for item in moment.alternatives} == {"d2d4", "e2e4"}
        assert moment.alternatives[1].is_played_move is True

    def test_missing_stored_candidates_are_disclosed(self) -> None:
        explorer = ScenarioService(_StubEngine()).explore_game(
            game_id="g1",
            rows=[self._row(1, candidate_moves=[]), self._row(3, candidate_moves=[])],
        )
        assert any("no stored alternative moves" in note for note in explorer.notes)
        assert all(moment.what_if_available is False for moment in explorer.turning_points)

    def test_a_quiet_game_has_no_turning_points(self) -> None:
        quiet = self._row(1, classification="good", evaluation_before_cp=10, evaluation_after_cp=15)
        explorer = ScenarioService(_StubEngine()).explore_game(game_id="g1", rows=[quiet])
        assert explorer.turning_points == []


class TestScenarioTypes:
    def test_every_declared_scenario_type_is_accepted(self) -> None:
        engine = _StubEngine()
        analyzer = CounterfactualAnalyzer(engine)
        for scenario_type in ScenarioType:
            branch = analyzer.branch(
                START_FEN, "e2e4", scenario_type=scenario_type, plies_ahead=1, depth=6
            )
            assert branch.scenario_type is scenario_type


class TestScenarioTypeBehaviour:
    """A scenario type changes what the engine is asked and what is refused.

    Without these, the seven types would be labels on identical behaviour, which
    is exactly what the documentation must not imply.
    """

    def test_types_have_distinct_search_defaults(self) -> None:
        engine = _StubEngine()
        service = ScenarioService(engine)
        # No plies/multipv requested: each type supplies its own defaults.
        service.counterfactual(START_FEN, "e2e4", scenario_type=ScenarioType.COUNTERFACTUAL_MOVE)
        short = list(engine.requests)
        engine.requests.clear()
        service.counterfactual(START_FEN, "e2e4", scenario_type=ScenarioType.ALTERNATIVE_LINE)
        long = list(engine.requests)
        assert short and long
        assert len(long) > len(short)  # an alternative line looks further ahead

    def test_an_opening_deviation_outside_the_opening_is_refused(self) -> None:
        service = ScenarioService(_StubEngine())
        # A bare endgame: there is no opening to deviate from.
        endgame = "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"
        outcome = service.counterfactual(
            endgame, "e2e4", scenario_type=ScenarioType.OPENING_DEVIATION
        )
        assert outcome.status == "unavailable"
        assert outcome.branch is None
        assert "opening phase" in (outcome.message or "")

    def test_an_endgame_transition_needs_an_endgame_result(self) -> None:
        service = ScenarioService(_StubEngine())
        outcome = service.counterfactual(
            START_FEN, "e2e4", scenario_type=ScenarioType.ENDGAME_TRANSITION, plies_ahead=1
        )
        assert outcome.status == "unavailable"
        assert "endgame" in (outcome.message or "")

    def test_an_opponent_response_needs_the_opponents_games(self) -> None:
        service = ScenarioService(_StubEngine())
        refused = service.counterfactual(
            START_FEN, "e2e4", scenario_type=ScenarioType.OPPONENT_RESPONSE, plies_ahead=1
        )
        assert refused.status == "unavailable"
        assert "opponent" in (refused.message or "")

        supplied = service.counterfactual(
            START_FEN,
            "e2e4",
            scenario_type=ScenarioType.OPPONENT_RESPONSE,
            plies_ahead=1,
            opponent_historical={
                "observed": [{"uci": "e7e5", "games": 4}],
                "sample_size": 4,
            },
        )
        assert supplied.status == "ok"
        branch = supplied.branch
        assert branch is not None
        context = branch.type_context
        # History and engine line stay separate, and history is labelled as such.
        assert context["opponent_historical"]["sample_size"] == 4
        assert context["historical_source"].startswith("stored games")
        assert context["label"] == "Opponent response"

    def test_every_type_records_its_own_context(self) -> None:
        service = ScenarioService(_StubEngine())
        for scenario_type in ScenarioType:
            outcome = service.counterfactual(
                START_FEN,
                "e2e4",
                scenario_type=scenario_type,
                plies_ahead=1,
                opponent_historical={"observed": [], "sample_size": 0},
            )
            if outcome.status != "ok":
                # A refusal is a legitimate, documented outcome per type.
                assert outcome.message
                continue
            assert outcome.branch is not None
            context = outcome.branch.type_context
            assert context["scenario_type"] == scenario_type.value
            assert context["source_phase"] == "opening"
            assert context["search_defaults"]["plies_ahead"]


class TestPredictionAttachmentIsGatedNotHardcoded:
    """The scenario layer serves a prediction when — and only when — a model
    has passed its production gate.

    The end state today is "no production model", so every check would pass if
    ``attach_prediction`` simply returned ``available: false``. This test rules
    that out by promoting a real model into a temporary registry and driving the
    real ``PredictionService``: the same code path must then serve a prediction,
    with its model metadata and its coverage caveat attached.
    """

    def test_a_production_model_is_served_through_the_scenario_layer(self, tmp_path) -> None:
        from argus.ml.models import ModelStatus
        from argus.ml.registry import ModelRegistry, RegisteredModel, model_id_for
        from argus.ml.service import PredictionService

        task = "game_outcome"
        classes = ["white_win", "draw", "black_win"]
        models_dir = tmp_path / "models"
        model = _MajorityModel(classes=classes)
        model.fit([{"rating_diff": 0.0}] * 6, classes * 2, feature_names=["rating_diff"])
        saved = model.save(models_dir / task)

        registry = ModelRegistry.load(tmp_path)
        registry.register(
            RegisteredModel(
                model_id=model_id_for(task, "majority_class", "9"),
                task=task,
                model_type="majority_class",
                version="9",
                status=ModelStatus.PRODUCTION,
                artifact=str(saved),
                split_strategy="temporal",
                trained_rows=6,
                metrics={"macro_f1": 0.5, "balanced_accuracy": 0.4, "log_loss": 1.0},
                calibration_metrics={
                    "expected_calibration_error": 0.03,
                    "calibrated": True,
                },
            )
        )
        service = ScenarioService(
            _StubEngine(),
            prediction_service=PredictionService(registry, models_dir=models_dir),
        )

        served = service.attach_prediction(task, [{"rating_diff": 0.0}])
        assert served["available"] is True, served
        assert served["task"] == task
        assert served["model_id"]
        assert served["prediction"] in classes
        # The calibration caveat travels with the number, never inside it.
        assert served["data_coverage"] is not None

    def test_without_a_registered_model_the_same_call_refuses(self, tmp_path) -> None:
        from argus.ml.registry import ModelRegistry
        from argus.ml.service import PredictionService

        service = ScenarioService(
            _StubEngine(),
            prediction_service=PredictionService(ModelRegistry.load(tmp_path), models_dir=tmp_path),
        )
        payload = service.attach_prediction("game_outcome", [{"rating_diff": 0.0}])
        assert payload["available"] is False
        assert payload["reason"]


class _MajorityModel:
    """The registry's own baseline model type, used only as a serving fixture."""

    def __init__(self, *, classes: list[str]) -> None:
        from argus.ml.baselines import MajorityClassModel

        self._inner = MajorityClassModel(classes=classes)

    def fit(self, rows, labels, *, feature_names):  # noqa: ANN001
        self._inner.fit(rows, labels, feature_names=feature_names)
        return self

    def save(self, path):  # noqa: ANN001
        return self._inner.save(path)
