"""Phase 10 performance and resource controls.

Counterfactual analysis is the most expensive thing Caissa can be asked to do: a
single branch is several engine searches, and an unbounded request is a way to
make the service unusable. These tests measure rather than assume, and they assert
the *invariants* that make the budget real:

* an identical repeated request is served from cache and triggers **no** engine
  work — the counters prove it, the clock is reported for information;
* a different search configuration is never served a cached result;
* the cache is bounded and expires;
* the engine-free operations (board facts, turning-point exploration) run with
  zero engine calls, however large the input;
* the request limits clamp and disclose.

Timing is reported as a measurement, not asserted on: a wall-clock threshold in a
test suite is a flaky test, and a flaky test gets disabled.
"""

from __future__ import annotations

import threading
import time

import chess

from argus.analysis.engine.base import AnalyzedPosition, ChessEngine, EngineLine
from argus.scenarios import ScenarioService, position_facts
from argus.scenarios.service import ScenarioResultCache

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class _CountingEngine(ChessEngine):
    """Counts searches so a cache hit is provable, not assumed."""

    def __init__(self) -> None:
        self.searches = 0

    def info(self) -> dict:
        return {"available": True, "version": "stub-1.0"}

    def analyze_position(self, fen, *, depth=None, multipv=None, movetime_ms=None):  # noqa: ANN001
        self.searches += 1
        board = chess.Board(fen)
        legal = list(board.legal_moves)
        width = max(1, min(int(multipv or 1), len(legal) or 1))
        lines = [
            EngineLine(
                index=index + 1,
                depth=depth or 10,
                move_uci=move.uci(),
                move_san=board.san(move),
                cp=50 - index * 10,
                pv=[move.uci()],
            )
            for index, move in enumerate(legal[:width])
        ]
        return AnalyzedPosition(
            fen=fen,
            depth=depth or 10,
            multipv=width,
            best_move_uci=lines[0].move_uci if lines else None,
            lines=lines,
        )

    def compare_moves(self, fen, moves, *, depth=None, movetime_ms=None):  # noqa: ANN001
        return []

    def close(self) -> None:
        return None


class TestCaching:
    def test_a_repeated_request_costs_no_extra_engine_work(self) -> None:
        engine = _CountingEngine()
        service = ScenarioService(engine)
        service.compare_moves(START_FEN, ["e2e4", "d2d4"], depth=10, multipv=3)
        after_first = engine.searches
        assert after_first > 0
        service.compare_moves(START_FEN, ["e2e4", "d2d4"], depth=10, multipv=3)
        assert engine.searches == after_first, "a cache hit must not search again"
        assert service.cache.stats()["hits"] == 1

    def test_a_counterfactual_repeat_costs_no_extra_engine_work(self) -> None:
        engine = _CountingEngine()
        service = ScenarioService(engine)
        service.counterfactual(START_FEN, "e2e4", actual_move="d2d4", plies_ahead=3, depth=10)
        after_first = engine.searches
        service.counterfactual(START_FEN, "e2e4", actual_move="d2d4", plies_ahead=3, depth=10)
        assert engine.searches == after_first

    def test_depth_and_playout_are_part_of_the_cache_key(self) -> None:
        engine = _CountingEngine()
        service = ScenarioService(engine)
        service.counterfactual(START_FEN, "e2e4", plies_ahead=2, depth=8)
        first = engine.searches
        service.counterfactual(START_FEN, "e2e4", plies_ahead=2, depth=12)
        assert engine.searches > first, "a different search configuration is a different answer"
        service.counterfactual(START_FEN, "e2e4", plies_ahead=4, depth=8)
        assert engine.searches > first

    def test_the_cache_is_bounded(self) -> None:
        engine = _CountingEngine()
        service = ScenarioService(engine, cache_entries=4)
        for index in range(20):
            # Distinct positions (the move counters differ) so the cache sees 20
            # distinct keys and must start evicting.
            service.compare_positions(
                START_FEN,
                f"rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 {index + 1}",
                depth=6,
                multipv=1,
            )
        stats = service.cache.stats()
        assert stats["entries"] <= 4
        assert stats["evictions"] >= 1

    def test_the_cache_expires(self) -> None:
        cache = ScenarioResultCache(max_entries=4, ttl_seconds=0.05)
        cache.put("k", "v")
        assert cache.get("k") == "v"
        time.sleep(0.08)
        assert cache.get("k") is None
        assert cache.stats()["misses"] == 1

    def test_concurrent_readers_do_not_corrupt_the_counters(self) -> None:
        cache = ScenarioResultCache(max_entries=8, ttl_seconds=60)
        cache.put("shared", 1)
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(50):
                    assert cache.get("shared") == 1
            except Exception as exc:  # pragma: no cover - only on a real race
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        assert cache.stats()["hits"] == 400


class TestResourceLimits:
    def test_the_engine_free_operations_never_search(self) -> None:
        engine = _CountingEngine()
        service = ScenarioService(engine)
        started = time.perf_counter()
        # Board facts and the explorer are pure reads; they must scale with the
        # data, not with the engine.
        for _ in range(200):
            position_facts(START_FEN)
        rows = [
            {
                "ply": ply,
                "move_number": (ply + 1) // 2,
                "mover": "white" if ply % 2 else "black",
                "fen_before": START_FEN,
                "played_move_uci": "e2e4",
                "played_move_san": "e4",
                "best_move_uci": "d2d4",
                "evaluation_before_cp": 10,
                "evaluation_after_cp": -220,
                "centipawn_loss": 230,
                "classification": "blunder",
                "candidate_moves": [
                    {"rank": 1, "uci": "d2d4", "san": "d4", "cp": 10, "pv": ["d2d4"]},
                    {"rank": 2, "uci": "e2e4", "san": "e4", "cp": -220, "pv": ["e2e4"]},
                ],
            }
            for ply in range(1, 121)
        ]
        explorer = service.explore_game(game_id="g1", rows=rows, moves_total=120)
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(
            f"\n200 position-fact reads + explorer over {len(rows)} plies: "
            f"{elapsed_ms:.2f} ms, engine searches={engine.searches}"
        )
        assert engine.searches == 0
        assert explorer.plies_analyzed == 120

    def test_continuation_length_is_capped(self) -> None:
        engine = _CountingEngine()
        service = ScenarioService(engine)
        outcome = service.counterfactual(START_FEN, "e2e4", plies_ahead=100, depth=6)
        assert outcome.branch is not None
        assert outcome.branch.plies_requested == 12
        assert outcome.branch.moves_played <= 12

    def test_multi_pv_width_is_capped(self) -> None:
        engine = _CountingEngine()
        service = ScenarioService(engine)
        comparison = service.compare_moves(START_FEN, [], depth=6, multipv=99, include_top=99)
        assert comparison.truncated is True
        assert len(comparison.candidates) <= 8

    def test_measured_cost_of_a_branch_is_reported(self) -> None:
        engine = _CountingEngine()
        service = ScenarioService(engine)
        started = time.perf_counter()
        service.counterfactual(START_FEN, "e2e4", actual_move="d2d4", plies_ahead=6, depth=6)
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(
            f"\none branch (2 lines x 6 plies, stub engine): {elapsed_ms:.2f} ms, "
            f"searches={engine.searches}"
        )
        # A branch is bounded by the limits: two lines of at most 12 plies, each
        # ply one search, plus the root search.
        assert engine.searches <= 2 * 12 + 2
