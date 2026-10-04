"""Engine-version baselines (§45): recording, loading and comparing.

These run without Stockfish — the engine is a deterministic stub — because the
point under test is the baseline machinery, not the engine.
"""

from __future__ import annotations


from argus.evaluation.engine_baseline import (
    EngineBaseline,
    EngineSample,
    baseline_path,
    build_baseline,
    compare_baseline,
    load_baseline,
    sample_position,
    save_baseline,
)

FEN_A = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
FEN_B = "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1"


class _Line:
    def __init__(self, cp: int | None = None, mate: int | None = None) -> None:
        self.cp = cp
        self.mate = mate


class _Analysis:
    def __init__(self, best: str | None, lines: list[_Line]) -> None:
        self.best_move_uci = best
        self.lines = lines


class _FakeEngine:
    """A deterministic stand-in for Stockfish."""

    def __init__(self, *, version: str = "Stockfish test 1", cp: int = 42, best: str = "e2e4"):
        self._version = version
        self._cp = cp
        self._best = best

    def info(self) -> dict:
        return {"version": self._version, "available": True}

    def analyze_position(self, fen, depth=8, multipv=1):  # noqa: ANN001
        return _Analysis(self._best, [_Line(cp=self._cp)])


class TestSampling:
    def test_sample_reads_the_top_line(self) -> None:
        sample = sample_position(_FakeEngine(cp=17, best="d2d4"), FEN_A, depth=8)
        assert sample == EngineSample(cp=17, mate=None, best_move="d2d4")

    def test_sample_clears_the_hash_when_the_engine_supports_it(self) -> None:
        # A sample must start from a clean transposition table, or the score can
        # drift between runs and the baseline becomes unreproducible.
        cleared = []

        class _EngineWithHash(_FakeEngine):
            def clear_hash(self) -> None:
                cleared.append(True)

        sample_position(_EngineWithHash(), FEN_A, depth=8)
        assert cleared == [True]

    def test_build_baseline_records_the_version_and_every_position(self) -> None:
        baseline = build_baseline(_FakeEngine(version="Stockfish 19"), (FEN_A, FEN_B), depth=8)
        assert baseline.engine_version == "Stockfish 19"
        assert baseline.depth == 8
        assert set(baseline.positions) == {FEN_A, FEN_B}


class TestStorage:
    def test_round_trip(self, tmp_path) -> None:
        baseline = EngineBaseline(
            engine_version="Stockfish 19",
            depth=8,
            positions={FEN_A: EngineSample(cp=12, best_move="e2e4")},
        )
        path = save_baseline(baseline, directory=tmp_path)
        assert path.exists()
        assert load_baseline("Stockfish 19", 8, directory=tmp_path) == baseline

    def test_missing_baseline_is_none(self, tmp_path) -> None:
        assert load_baseline("Stockfish never-recorded", 8, directory=tmp_path) is None

    def test_path_slug_is_filesystem_safe(self) -> None:
        path = baseline_path("Stockfish 17.1 (dev/nightly)", 8)
        assert "/" not in path.name
        assert path.name.startswith("engine-stockfish-17-1-dev-nightly-d8")


class TestComparison:
    def _baseline(self) -> EngineBaseline:
        return EngineBaseline(
            engine_version="Stockfish 19",
            depth=8,
            positions={FEN_A: EngineSample(cp=20, best_move="e2e4")},
        )

    def test_identical_output_has_no_differences(self) -> None:
        samples = {FEN_A: EngineSample(cp=20, best_move="e2e4")}
        assert compare_baseline(self._baseline(), samples) == []

    def test_changed_evaluation_is_reported(self) -> None:
        samples = {FEN_A: EngineSample(cp=35, best_move="e2e4")}
        differences = compare_baseline(self._baseline(), samples)
        assert len(differences) == 1
        assert "cp=20" in differences[0] and "cp=35" in differences[0]

    def test_changed_best_move_is_reported(self) -> None:
        samples = {FEN_A: EngineSample(cp=20, best_move="d2d4")}
        assert compare_baseline(self._baseline(), samples)

    def test_missing_sample_is_reported(self) -> None:
        assert compare_baseline(self._baseline(), {})
