"""Tests for Phase 3 core logic: critical positions, caching, and config."""

from __future__ import annotations

from types import SimpleNamespace

from argus.analysis.cache import CacheKey, PositionAnalysisCache
from argus.analysis.classification import MoveClassification, MoveClassificationPolicy
from argus.analysis.critical_positions import (
    CriticalPositionPolicy,
    CriticalReason,
    Severity,
    detect_critical_positions,
)
from argus.analysis.pipeline import AnalysisProfile, build_analysis_config
from argus.chess_core.models import Color

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_CAPTURE = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBN1 w kq - 0 1"  # a knight is gone


def move(**overrides):
    base = dict(
        ply=1,
        move_number=1,
        color=Color.WHITE,
        san="e4",
        fen_before=START,
        fen_after=START,
        evaluation_before_cp=20,
        evaluation_before_mate=None,
        evaluation_after_cp=20,
        evaluation_after_mate=None,
        evaluation_change_cp=0,
        classification=MoveClassification.BEST,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestCriticalPositions:
    def test_quiet_accurate_move_is_not_critical(self):
        assert detect_critical_positions([move()]) == []

    def test_blunder_is_flagged(self):
        candidates = detect_critical_positions(
            [move(classification=MoveClassification.BLUNDER, evaluation_change_cp=-400)]
        )
        reasons = {c.reason for c in candidates}
        assert CriticalReason.BLUNDER in reasons
        assert all(c.severity in Severity for c in candidates)

    def test_large_swing_is_flagged_even_without_a_label(self):
        candidates = detect_critical_positions(
            [move(classification=MoveClassification.GOOD, evaluation_change_cp=-250)]
        )
        assert any(c.reason is CriticalReason.EVALUATION_SWING for c in candidates)

    def test_missed_win_is_detected(self):
        candidates = detect_critical_positions(
            [move(evaluation_before_cp=400, evaluation_after_cp=50, evaluation_change_cp=-350)]
        )
        assert any(c.reason is CriticalReason.MISSED_WIN for c in candidates)

    def test_mate_change_is_flagged_and_marked(self):
        candidates = detect_critical_positions(
            [move(evaluation_before_mate=3, evaluation_after_mate=None, evaluation_change_cp=-300)]
        )
        mate_candidates = [c for c in candidates if c.reason is CriticalReason.MATE_CHANGE]
        assert mate_candidates and mate_candidates[0].is_mate_related

    def test_material_transition_is_detected(self):
        candidates = detect_critical_positions([move(fen_after=AFTER_CAPTURE)])
        assert any(c.reason is CriticalReason.MATERIAL_TRANSITION for c in candidates)

    def test_severity_ordering(self):
        high = detect_critical_positions(
            [move(classification=MoveClassification.BLUNDER, evaluation_change_cp=-600)]
        )
        low = detect_critical_positions(
            [move(classification=MoveClassification.BLUNDER, evaluation_change_cp=-120)]
        )
        assert high[0].severity is Severity.HIGH
        assert low[0].severity is Severity.LOW

    def test_no_swing_means_no_candidate(self):
        # An unclassified move with no evaluation change and no material shift.
        assert detect_critical_positions([move(evaluation_change_cp=None)]) == []

    def test_policy_thresholds_are_respected(self):
        strict = CriticalPositionPolicy(swing_medium_cp=50)
        assert detect_critical_positions(
            [move(evaluation_change_cp=-60)], policy=strict
        )


class TestCacheKey:
    def test_key_includes_engine_config(self):
        a = CacheKey.build("fen", engine_version="sf16", depth=12, movetime_ms=None, multipv=3)
        b = CacheKey.build("fen", engine_version="sf16", depth=14, movetime_ms=None, multipv=3)
        assert a != b  # different depth must not share a cached result

    def test_key_includes_engine_version(self):
        a = CacheKey.build("fen", engine_version="sf16", depth=12, movetime_ms=None, multipv=1)
        b = CacheKey.build("fen", engine_version="sf17", depth=12, movetime_ms=None, multipv=1)
        assert a != b

    def test_key_includes_multipv(self):
        a = CacheKey.build("fen", engine_version=None, depth=None, movetime_ms=500, multipv=1)
        b = CacheKey.build("fen", engine_version=None, depth=None, movetime_ms=500, multipv=3)
        assert a != b


class TestPositionCache:
    def test_hit_and_miss(self):
        cache = PositionAnalysisCache(max_entries=2)
        key = CacheKey.build("f", engine_version="v", depth=10, movetime_ms=None, multipv=1)
        assert cache.get(key) is None
        cache.put(key, "result")
        assert cache.get(key) == "result"
        assert cache.stats()["hits"] == 1
        assert cache.stats()["misses"] == 1

    def test_lru_eviction(self):
        cache = PositionAnalysisCache(max_entries=2)
        keys = [
            CacheKey.build(f"f{i}", engine_version=None, depth=1, movetime_ms=None, multipv=1)
            for i in range(3)
        ]
        for key in keys:
            cache.put(key, key.fen)
        assert cache.get(keys[0]) is None  # evicted (oldest)
        assert cache.get(keys[2]) is not None


class TestAnalysisConfig:
    def test_fast_profile_is_time_based(self):
        config = build_analysis_config("fast")
        assert config.movetime_ms and config.depth is None

    def test_standard_profile_is_depth_based(self):
        config = build_analysis_config("standard")
        assert config.depth and config.movetime_ms is None

    def test_explicit_overrides_win(self):
        config = build_analysis_config("deep", depth=10, multipv=4)
        assert config.depth == 10 and config.multipv == 4

    def test_explicit_time_clears_depth(self):
        config = build_analysis_config("standard", movetime_ms=750)
        assert config.movetime_ms == 750 and config.depth is None

    def test_policy_is_recorded(self):
        config = build_analysis_config("standard", policy=MoveClassificationPolicy())
        assert config.classification_policy["good_max_cp_loss"] == 50

    def test_profile_enum_roundtrip(self):
        assert build_analysis_config(AnalysisProfile.FAST).profile is AnalysisProfile.FAST
