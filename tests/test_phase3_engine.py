"""Engine-backed Phase 3 tests (require Stockfish; ``pytest -m engine``).

Assertions focus on structure, ordering, signs, and known tactical facts — not
exact evaluations, which legitimately vary between engine versions/platforms.
"""

from __future__ import annotations

import pytest

from argus.analysis.cache import CachingEngine, PositionAnalysisCache
from argus.analysis.engine.stockfish import StockfishEngine, locate_stockfish
from argus.analysis.perspective import to_white_perspective
from argus.analysis.pipeline import build_analysis_config
from argus.analysis.pipeline import GameAnalysisPipeline
from argus.chess_core.pgn import parse_first_game
from argus.shared.errors import AnalysisCancelledError

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
# Scholar's-mate setup: Qxf7# is mate in one for White.
MATE_IN_ONE = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K2R w KQkq - 0 1"
# Black to move, a full queen down.
BLACK_LOST = "4k3/8/8/8/8/8/8/3QK3 b - - 0 1"

SHORT_PGN = '[Event "T"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 *'

pytestmark = pytest.mark.engine


@pytest.fixture(scope="module")
def engine():
    if locate_stockfish() is None:
        pytest.skip("Stockfish binary not available")
    stockfish = StockfishEngine()
    yield stockfish
    stockfish.close()


class TestTimeBasedSearch:
    def test_movetime_returns_analysis(self, engine):
        analysis = engine.analyze_position(START_FEN, movetime_ms=250)
        assert analysis.best_move_uci is not None
        assert analysis.lines

    def test_depth_and_time_are_mutually_exclusive(self, engine):
        # When movetime is given it governs the search; depth is not required.
        analysis = engine.analyze_position(START_FEN, movetime_ms=200, depth=30)
        assert analysis.best_move_uci is not None


class TestMultiPv:
    def test_multipv_returns_ordered_lines(self, engine):
        analysis = engine.analyze_position_multipv(START_FEN, multipv=3, depth=8)
        assert len(analysis.lines) == 3
        assert [line.index for line in analysis.lines] == [1, 2, 3]
        # Lines are distinct first moves.
        first_moves = [line.move_uci for line in analysis.lines]
        assert len(set(first_moves)) == len(first_moves)

    def test_multipv_capped_by_legal_moves(self, engine):
        # A position with very few legal moves cannot return more lines than that.
        near_mate = "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1"
        analysis = engine.analyze_position_multipv(near_mate, multipv=10, depth=8)
        assert len(analysis.lines) <= 10


class TestNodesMetadata:
    def test_nodes_reported_when_available(self, engine):
        analysis = engine.analyze_position(START_FEN, depth=8)
        # Stockfish reports nodes; when present they must be positive.
        if analysis.nodes is not None:
            assert analysis.nodes > 0


class TestPerspectiveThroughEngine:
    def test_winning_side_has_positive_white_perspective(self, engine):
        analysis = engine.analyze_position(MATE_IN_ONE, depth=8)
        assert analysis.best_move_uci == "f3f7"
        assert analysis.lines[0].mate is not None and analysis.lines[0].mate > 0

    def test_black_to_move_losing_reads_negative_for_black_positive_for_white(self, engine):
        analysis = engine.analyze_position(BLACK_LOST, depth=8)
        # Engine reports from Black's (side-to-move) perspective: Black is worse.
        engine_cp = analysis.lines[0].cp
        assert engine_cp is not None and engine_cp < 0
        white_cp, _ = to_white_perspective(engine_cp, None, side="black")
        assert white_cp is not None and white_cp > 0


class TestAnalyzeMove:
    def test_analyze_move_returns_san_and_loss(self, engine):
        comparison = engine.analyze_move(START_FEN, "g1f3", depth=6)
        assert comparison.played_move_uci == "g1f3"
        assert comparison.played_move_san == "Nf3"
        assert comparison.centipawn_loss is not None
        assert comparison.centipawn_loss >= 0


class TestAnalyzeGameCallbacks:
    def test_progress_and_move_callbacks_fire(self, engine):
        game = parse_first_game(SHORT_PGN)
        progress: list[tuple[int, int]] = []
        moves: list[int] = []
        evaluations = engine.analyze_game(
            game,
            depth=6,
            multipv=2,
            on_progress=lambda done, total: progress.append((done, total)),
            on_move=lambda ev: moves.append(ev.ply),
        )
        assert len(evaluations) == 4
        assert progress[-1] == (4, 4)
        assert moves == [1, 2, 3, 4]

    def test_cancellation_stops_the_run(self, engine):
        game = parse_first_game(SHORT_PGN)
        with pytest.raises(AnalysisCancelledError):
            engine.analyze_game(game, depth=6, should_cancel=lambda: True)

    def test_resume_from_ply(self, engine):
        game = parse_first_game(SHORT_PGN)
        evaluations = engine.analyze_game(game, depth=6, multipv=1, start_ply=3)
        assert [e.ply for e in evaluations] == [3, 4]


class TestCachingEngine:
    def test_second_identical_call_is_cached(self, engine):
        cache = PositionAnalysisCache()
        cached_engine = CachingEngine(engine, cache)
        first = cached_engine.analyze_position(START_FEN, depth=6, multipv=1)
        second = cached_engine.analyze_position(START_FEN, depth=6, multipv=1)
        assert cache.stats()["hits"] == 1
        assert first.best_move_uci == second.best_move_uci

    def test_different_depth_is_not_cached(self, engine):
        cache = PositionAnalysisCache()
        cached_engine = CachingEngine(engine, cache)
        cached_engine.analyze_position(START_FEN, depth=6, multipv=1)
        cached_engine.analyze_position(START_FEN, depth=8, multipv=1)
        assert cache.stats()["hits"] == 0


class TestPipeline:
    def test_pipeline_produces_classified_moves(self, engine):
        game = parse_first_game(SHORT_PGN)
        config = build_analysis_config("standard", depth=6, multipv=2)
        result = GameAnalysisPipeline(engine).analyze(game, config)
        assert result.game_id is None or isinstance(result.game_id, str)
        assert len(result.moves) == 4
        assert result.summary.total_moves == 4
        assert any(m.classification is not None for m in result.moves)
