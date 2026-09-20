"""Analysis package: deterministic chess analysis pipelines.

Exports the engine abstraction, Stockfish engine, feature extraction, phase
classification, move classification, game analyzer, and report builder.
"""

from argus.analysis.classification import (
    ClassificationThresholds,
    MoveClassification,
    MoveClassificationInput,
    classify_move,
)
from argus.analysis.engine.base import (
    AnalyzedPosition,
    ChessEngine,
    EngineLine,
    GameMoveEvaluation,
    MoveComparison,
    compute_cp_loss,
    to_cp,
)
from argus.analysis.engine.stockfish import StockfishEngine, StockfishSettings, locate_stockfish
from argus.analysis.features import RawPositionFeatures
from argus.analysis.features.extractor import (
    extract_position_features,
    extract_position_features_from_fen,
)
from argus.analysis.game_analyzer import (
    AnalyzedMove,
    GameAnalysis,
    GameAnalyzer,
    GameSummary,
    SideSummary,
)
from argus.analysis.phase import GamePhase, PhaseThresholds, classify_position_fen
from argus.analysis.reports import CriticalMoment, GameReport, build_report

__all__ = [
    "AnalyzedMove",
    "AnalyzedPosition",
    "ChessEngine",
    "ClassificationThresholds",
    "CriticalMoment",
    "EngineLine",
    "GameAnalysis",
    "GameAnalyzer",
    "GameMoveEvaluation",
    "GamePhase",
    "GameReport",
    "GameSummary",
    "MoveClassification",
    "MoveClassificationInput",
    "MoveComparison",
    "PhaseThresholds",
    "RawPositionFeatures",
    "SideSummary",
    "StockfishEngine",
    "StockfishSettings",
    "build_report",
    "classify_move",
    "classify_position_fen",
    "compute_cp_loss",
    "extract_position_features",
    "extract_position_features_from_fen",
    "locate_stockfish",
    "to_cp",
]
