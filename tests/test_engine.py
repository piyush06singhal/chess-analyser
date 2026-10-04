"""Engine integration tests (require the Stockfish binary).

Run with ``pytest -m engine``; deselected with ``-m "not engine"``.
Expected values are verified through the engine itself: the starting position
is balanced (small positive score is normal for White), 1. e4 is a known best
move there, and scholar's-mate-style positions yield mate scores.
"""

from __future__ import annotations

import time

import pytest

from argus.analysis.engine.base import (
    MATE_SCORE_CEILING,
    PLAYED_EVAL_RESULTING_POSITION,
    PLAYED_EVAL_SAME_SEARCH,
    compute_cp_loss,
    to_cp,
)
from argus.analysis.engine.stockfish import StockfishEngine, StockfishSettings, locate_stockfish
from argus.shared.errors import InvalidFenError, InvalidMoveError


pytestmark = pytest.mark.engine

START_FEN_FORCED = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture(scope="module")
def engine():
    from argus.analysis.engine.stockfish import StockfishEngine

    if locate_stockfish() is None:
        pytest.skip("Stockfish binary not available")
    stockfish = StockfishEngine()
    yield stockfish
    stockfish.close()


class TestEngineDetection:
    def test_binary_is_located(self):
        assert locate_stockfish() is not None

    def test_info_reports_available(self, engine):
        info = engine.info()
        assert info["available"] is True
        assert info["path"]


class TestAnalyzePosition:
    def test_start_position_returns_structured_analysis(self, engine):
        analysis = engine.analyze_position(START_FEN_FORCED, depth=6, multipv=2)
        assert analysis.best_move_uci is not None
        assert analysis.best_move_san is not None
        assert len(analysis.lines) == 2
        assert analysis.lines[0].index == 1
        assert analysis.lines[0].pv
        assert analysis.depth >= 6
        assert analysis.is_terminal is False
        # Starting position is near-balanced: White's small edge is within 1 pawn.
        assert analysis.lines[0].cp is not None and abs(analysis.lines[0].cp) <= 100

    def test_mate_position_reports_mate_score(self, engine):
        # 1. f3 e5 2. g4 Qh4# is already on the board: Black has delivered mate.
        mate_fen = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
        analysis = engine.analyze_position(mate_fen, depth=6)
        assert analysis.is_terminal is True
        assert analysis.terminal_reason == "checkmate"
        assert analysis.best_move_uci is None

    def test_mate_in_one_is_found(self, engine):
        # Scholar's mate setup: Qxf7# is mate in one for White.
        mate_in_one = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K2R w KQkq - 0 1"
        analysis = engine.analyze_position(mate_in_one, depth=8)
        assert analysis.is_terminal is False
        assert analysis.best_move_uci == "f3f7"
        assert analysis.lines[0].mate is not None and analysis.lines[0].mate > 0

    def test_invalid_fen_is_rejected(self, engine):
        with pytest.raises(InvalidFenError):
            engine.analyze_position("not a fen")

    def test_illegal_kingless_position_is_rejected(self, engine):
        with pytest.raises(InvalidFenError):
            engine.analyze_position("rnbq1bnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQ1BNR w KQkq - 0 1")


class TestCompareMoves:
    def test_compare_moves_ranks_best_first(self, engine):
        comparisons = engine.compare_moves(START_FEN_FORCED, ["e2e4", "g1f3"], depth=6)
        assert len(comparisons) == 2
        by_move = {c.played_move_uci: c for c in comparisons}
        assert by_move["e2e4"].is_best_move is True
        assert by_move["e2e4"].centipawn_loss == 0
        # Both moves are reasonable openings; neither loses more than half a pawn.
        assert by_move["g1f3"].centipawn_loss is not None
        assert by_move["g1f3"].centipawn_loss <= 50

    def test_compare_moves_rejects_illegal_move(self, engine):
        with pytest.raises(InvalidMoveError):
            engine.compare_moves(START_FEN_FORCED, ["e2e5"])

    def test_compare_moves_rejects_empty_list(self, engine):
        with pytest.raises(InvalidMoveError):
            engine.compare_moves(START_FEN_FORCED, [])


class TestScoreHelpers:
    def test_to_cp_prefers_cp(self):
        assert to_cp(35, 5) == 35

    def test_to_cp_converts_mate(self):
        assert to_cp(None, 3) == MATE_SCORE_CEILING - 3
        assert to_cp(None, -3) == -(MATE_SCORE_CEILING - 3)

    def test_compute_cp_loss(self):
        assert compute_cp_loss(100, None, 80, None) == 20
        assert compute_cp_loss(100, None, 120, None) == 0  # cannot be negative
        assert compute_cp_loss(None, None, None, None) is None
        assert compute_cp_loss(500, None, None, 1) == 0  # delivering mate


class TestAnalyzeGame:
    def test_short_game_analyzed(self, engine):
        from argus.chess_core.pgn import parse_first_game

        pgn = '[Event "Test"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 *'
        game = parse_first_game(pgn)
        evaluations = engine.analyze_game(game, depth=6, multipv=1)
        assert len(evaluations) == 4
        first = evaluations[0]
        assert first.san == "e4"
        assert first.evaluation_before_cp is not None
        assert first.is_best_move is True
        # evaluation_after is the flipped perspective of the position after e4.
        assert first.evaluation_after_cp is not None

    def test_played_move_inside_the_window_is_scored_by_the_same_search(self, engine):
        from argus.chess_core.pgn import parse_first_game

        # 1. e4 is the best move, so it is inside any MultiPV window and its
        # score comes from the same search as the best line.
        game = parse_first_game('[Event "Test"]\n[Result "*"]\n\n1. e4 e5 *')
        first, second = engine.analyze_game(game, depth=6, multipv=2)
        assert first.played_eval_source == PLAYED_EVAL_SAME_SEARCH
        assert first.played_eval_cp is not None
        # Its score is the best line's score, so there is nothing to lose.
        assert first.centipawn_loss == 0
        # 1...e5 is also a top-two reply, so it is exact as well.
        assert second.played_eval_source == PLAYED_EVAL_SAME_SEARCH

    def test_played_move_outside_the_window_falls_back_and_says_so(self, engine):
        from argus.chess_core.pgn import parse_first_game

        # 1. a4 is never a top-two move in the starting position.
        game = parse_first_game('[Event "Test"]\n[Result "*"]\n\n1. a4 e5 *')
        first = engine.analyze_game(game, depth=6, multipv=2)[0]
        assert first.played_eval_source == PLAYED_EVAL_RESULTING_POSITION
        assert first.played_eval_cp is not None
        # The fallback score is the flip of the position after the move, which
        # is what evaluation_after_cp already holds.
        assert first.played_eval_cp == first.evaluation_after_cp

    def test_every_played_move_records_where_its_score_came_from(self, engine):
        from argus.chess_core.pgn import parse_first_game

        game = parse_first_game(
            '[Event "Test"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *'
        )
        evaluations = engine.analyze_game(game, depth=6, multipv=2)
        recorded = [e.played_eval_source for e in evaluations]
        assert all(
            source in (PLAYED_EVAL_SAME_SEARCH, PLAYED_EVAL_RESULTING_POSITION)
            for source in recorded
        )
        assert recorded.count(PLAYED_EVAL_SAME_SEARCH) >= 1


class TestEngineLatency:
    """Guard the per-search latency floor the flush nudge imposes.

    Stockfish block-buffers stdout on a pipe, so Caissa nudges the engine with a
    harmless ``isready`` to flush pending output. The nudge interval is
    therefore a floor on *every* search: while it was 0.5 s, a 33-move game paid
    ~33 s of pure waiting. These tests keep the floor small.
    """

    def test_default_nudge_interval_is_small(self):
        settings = StockfishSettings()
        assert 0.0 < settings.flush_nudge_seconds <= 0.1
        assert settings.describe()["flush_nudge_seconds"] == settings.flush_nudge_seconds

    def test_shallow_search_does_not_pay_a_fixed_half_second(self):
        if locate_stockfish() is None:
            pytest.skip("Stockfish binary not available")
        stockfish = StockfishEngine(
            StockfishSettings(depth=4, multipv=1, timeout_seconds=30.0)
        )
        try:
            stockfish.analyze_position(START_FEN_FORCED, depth=4, multipv=1)  # warm up
            durations = []
            for _ in range(5):
                started = time.monotonic()
                stockfish.analyze_position(START_FEN_FORCED, depth=4, multipv=1)
                durations.append(time.monotonic() - started)
        finally:
            stockfish.close()
        # A depth-4 search is milliseconds of work; anything near the old 0.5 s
        # floor means the nudge interval regressed.
        assert min(durations) < 0.25, durations
