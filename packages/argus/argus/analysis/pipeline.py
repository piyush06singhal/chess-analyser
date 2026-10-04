"""Game analysis pipeline: configuration, execution, and versioning.

This is the engine-facing pipeline that turns a parsed game into a
:class:`GameAnalysis`:

    Game → per-position Stockfish analysis → before/after evaluations →
    evaluation changes → centipawn loss → classification → critical candidates.

Configuration is explicit and reproducible: every run records the profile, the
actual depth/time/MultiPV used, the classification policy, and an
``ANALYSIS_VERSION``. Nothing is called "perfect" or "grandmaster" — only
measurable parameters.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from pydantic import BaseModel, Field

from argus.analysis.classification import MoveClassificationPolicy
from argus.analysis.game_analyzer import AnalyzedMove, GameAnalysis, GameAnalyzer
from argus.analysis.phase import PhaseThresholds
from argus.chess_core.models import Game

#: Bumped when the analysis semantics change (convention, CPL, classification).
#: 3.1 — each played move's own score from the same engine search that produced
#: the best line is now persisted (``played_eval_*``), so accuracy can compare
#: like with like instead of mixing two searches. Stored rows from 3.0 carry no
#: such score, so a re-analysis must actually re-run instead of resuming.
ANALYSIS_VERSION = "3.1"


class AnalysisProfile(str, Enum):
    """Named analysis presets. Names are plain and measurable on purpose."""

    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


_PROFILE_DEFAULTS: dict[AnalysisProfile, dict] = {
    AnalysisProfile.FAST: {"depth": None, "movetime_ms": 400, "multipv": 2},
    AnalysisProfile.STANDARD: {"depth": 16, "movetime_ms": None, "multipv": 3},
    AnalysisProfile.DEEP: {"depth": 22, "movetime_ms": None, "multipv": 3},
}


class AnalysisConfig(BaseModel):
    """The exact engine parameters used for a run (reproducible)."""

    profile: AnalysisProfile
    depth: int | None = None
    movetime_ms: int | None = None
    multipv: int = 1
    analysis_version: str = ANALYSIS_VERSION
    classification_policy: dict = Field(default_factory=dict)

    def describe(self) -> dict:
        return {
            "profile": self.profile.value,
            "depth": self.depth,
            "movetime_ms": self.movetime_ms,
            "multipv": self.multipv,
            "analysis_version": self.analysis_version,
            "classification_policy": self.classification_policy,
        }

    @property
    def label(self) -> str:
        if self.movetime_ms:
            return f"{self.profile.value} ({self.movetime_ms}ms, MultiPV {self.multipv})"
        return f"{self.profile.value} (depth {self.depth}, MultiPV {self.multipv})"


def build_analysis_config(
    profile: AnalysisProfile | str = AnalysisProfile.STANDARD,
    *,
    depth: int | None = None,
    movetime_ms: int | None = None,
    multipv: int | None = None,
    policy: MoveClassificationPolicy | None = None,
) -> AnalysisConfig:
    """Resolve a profile (plus explicit overrides) into a concrete config.

    Explicit ``depth``/``movetime_ms``/``multipv`` win over the profile
    defaults. Setting ``movetime_ms`` clears the depth so exactly one search
    limit governs the run.
    """
    resolved_profile = AnalysisProfile(profile)
    defaults = _PROFILE_DEFAULTS[resolved_profile]
    effective_time = movetime_ms if movetime_ms is not None else defaults["movetime_ms"]
    effective_depth = depth if depth is not None else defaults["depth"]
    if effective_time:
        effective_depth = None
    return AnalysisConfig(
        profile=resolved_profile,
        depth=effective_depth,
        movetime_ms=effective_time,
        multipv=max(1, multipv if multipv is not None else defaults["multipv"]),
        classification_policy=(policy or MoveClassificationPolicy()).describe(),
    )


class GameAnalysisPipeline:
    """Runs the deterministic analysis pipeline for a game."""

    def __init__(
        self,
        engine,  # argus.analysis.engine.base.ChessEngine
        *,
        policy: MoveClassificationPolicy | None = None,
        phase_thresholds: PhaseThresholds | None = None,
    ) -> None:
        self._engine = engine
        self._policy = policy
        self._phase_thresholds = phase_thresholds

    def analyze(
        self,
        game: Game,
        config: AnalysisConfig,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_move: Callable[[AnalyzedMove], None] | None = None,
        start_ply: int | None = None,
        end_ply: int | None = None,
    ) -> GameAnalysis:
        """Analyze a game and return the structured result.

        The analyzer is created per run with the configured policy so no shared
        mutable state leaks between concurrent analyses.
        """
        analyzer = GameAnalyzer(
            self._engine,
            classification_thresholds=self._policy,
            phase_thresholds=self._phase_thresholds,
        )
        return analyzer.analyze(
            game,
            depth=config.depth,
            multipv=config.multipv,
            movetime_ms=config.movetime_ms,
            on_progress=on_progress,
            should_cancel=should_cancel,
            on_move=on_move,
            start_ply=start_ply,
            end_ply=end_ply,
        )
