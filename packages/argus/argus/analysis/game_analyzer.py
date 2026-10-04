"""Game intelligence: engine evaluation + raw features + phases + classification.

Combines the deterministic building blocks into a per-game analysis. This
module is synchronous and engine-facing only — no LLM, no database, no UI.
Every number in the output is derived from engine measurements or board
state; nothing is invented.
"""

from __future__ import annotations

import chess
from pydantic import BaseModel, Field

from argus.analysis.classification import (
    ClassificationThresholds,
    MoveClassification,
    MoveClassificationInput,
    classify_move,
)
from argus.analysis.engine.base import CandidateMove, ChessEngine, GameMoveEvaluation
from argus.analysis.features.extractor import extract_position_features_from_fen
from argus.analysis.features.models import RawPositionFeatures
from argus.analysis.phase import GamePhase, PhaseThresholds, classify_position_fen
from argus.chess_core.models import Color, Game, GameMove
from argus.shared.errors import AnalysisError

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


class AnalyzedMove(BaseModel):
    """A played move enriched with engine evidence, features, phase, and classification."""

    ply: int
    move_number: int
    color: Color
    san: str
    uci: str
    fen_before: str
    fen_after: str

    # Engine evidence (mover perspective)
    evaluation_before_cp: int | None = None
    evaluation_before_mate: int | None = None
    evaluation_after_cp: int | None = None
    evaluation_after_mate: int | None = None
    evaluation_change_cp: int | None = None
    centipawn_loss: int | None = None
    played_eval_cp: int | None = None
    played_eval_mate: int | None = None
    played_eval_source: str = "unavailable"
    best_move_uci: str | None = None
    best_move_san: str | None = None
    is_best_move: bool = False
    second_best_gap: int | None = Field(
        default=None, description="best_cp minus second-best cp; None when MultiPV < 2"
    )
    #: The MultiPV lines (root moves + evaluations) from the same search that
    #: produced the best line. Passed through to storage so alternative moves are
    #: evidence, not a guess.
    candidate_moves: list[CandidateMove] = Field(default_factory=list)
    depth: int
    principal_variation: list[str] = Field(default_factory=list)

    # Classification (None when the evaluation was unavailable)
    classification: MoveClassification | None = None

    # Phase after the move
    phase: GamePhase

    # Raw board features
    features_before: RawPositionFeatures
    features_after: RawPositionFeatures

    # Sacrifice flag (factual: moved a higher-value piece to a square attacked
    # by a lower-value attacker); False when the evaluation was unavailable
    is_sacrifice: bool = False


class SideSummary(BaseModel):
    """Per-color aggregates derived from engine measurements."""

    average_centipawn_loss: float | None = Field(
        default=None, description="Mean centipawn loss; None when no move was evaluated"
    )
    counts: dict[MoveClassification, int] = Field(default_factory=dict)


class GameSummary(BaseModel):
    """Deterministic aggregates over the analyzed game."""

    total_moves: int
    classified_moves: int
    unclassified_moves: int
    white: SideSummary
    black: SideSummary
    phase_move_counts: dict[GamePhase, int] = Field(default_factory=dict)


class GameAnalysis(BaseModel):
    """Complete deterministic analysis of one game."""

    game_id: str | None = None
    moves: list[AnalyzedMove]
    initial_position: str
    final_position: str
    final_phase: GamePhase
    summary: GameSummary
    turning_point_ply: int | None = Field(
        default=None,
        description="Ply of the move with the largest evaluation swing against the mover; "
        "None when no move was evaluated",
    )


def is_sacrifice(board_after: chess.Board, move_uci: str) -> bool:
    """Deterministic sacrifice heuristic.

    A move counts as a sacrifice when the moved piece ends up attacked by a
    strictly lower-valued enemy piece, or attacked with no defender. This is a
    documented Phase 1 heuristic to be refined with real data later.
    """
    move = chess.Move.from_uci(move_uci)
    piece = board_after.piece_at(move.to_square)
    if piece is None or piece.piece_type == chess.KING:
        return False
    enemy = not piece.color
    attackers = board_after.attackers(enemy, move.to_square)
    if not attackers:
        return False
    defenders = board_after.attackers(piece.color, move.to_square)
    if not defenders:
        return True
    piece_value = PIECE_VALUES[piece.piece_type]
    for attacker_square in attackers:
        attacker = board_after.piece_at(attacker_square)
        # King attackers are excluded: a king capture is legality-constrained
        # (cannot take a defended piece), so it is not a real threat.
        if (
            attacker is not None
            and attacker.piece_type != chess.KING
            and PIECE_VALUES[attacker.piece_type] < piece_value
        ):
            return True
    return False


class GameAnalyzer:
    """Runs the deterministic game analysis pipeline for one game.

    Pipeline: Stockfish evaluation (via :class:`ChessEngine`) → raw feature
    extraction → phase classification → move classification → aggregates and
    turning point.
    """

    def __init__(
        self,
        engine: ChessEngine,
        *,
        classification_thresholds: ClassificationThresholds | None = None,
        phase_thresholds: PhaseThresholds | None = None,
    ) -> None:
        self._engine = engine
        self._classification_thresholds = classification_thresholds
        self._phase_thresholds = phase_thresholds

    @property
    def classification_policy(self) -> ClassificationThresholds:
        return self._classification_thresholds or ClassificationThresholds()

    def analyze(
        self,
        game: Game,
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
        on_progress=None,
        should_cancel=None,
        on_move=None,
        start_ply: int | None = None,
        end_ply: int | None = None,
    ) -> GameAnalysis:
        """Analyze every move of the game and aggregate the results.

        Requires ``multipv >= 2`` for second-best-gap evidence (brilliant
        detection); falls back gracefully when the engine returns one line.

        ``on_progress(completed, total)`` and ``should_cancel()`` are forwarded
        to the engine so long analyses report progress and can be stopped.
        """
        analyzed: list[AnalyzedMove] = []
        expected = len(game.moves) - (max(0, (start_ply or 1) - 1))
        if end_ply is not None:
            expected -= max(0, len(game.moves) - end_ply)

        def _enrich(evaluation: GameMoveEvaluation) -> None:
            # ``ply`` is 1-based and contiguous, so it indexes straight into moves.
            move = game.moves[evaluation.ply - 1]
            enriched = self._analyze_move(move, evaluation)
            analyzed.append(enriched)
            if on_move is not None:
                on_move(enriched)

        self._engine.analyze_game(
            game,
            depth=depth,
            multipv=multipv,
            movetime_ms=movetime_ms,
            on_progress=on_progress,
            should_cancel=should_cancel,
            on_move=_enrich,
            start_ply=start_ply,
            end_ply=end_ply,
        )
        if len(analyzed) != expected:
            raise AnalysisError(
                "Engine returned an incomplete evaluation of the game",
                details={"expected": expected, "received": len(analyzed)},
            )
        return GameAnalysis(
            game_id=game.id,
            moves=analyzed,
            initial_position=game.initial_position,
            final_position=game.final_position,
            final_phase=classify_position_fen(game.final_position, self._phase_thresholds),
            summary=self._summarize(analyzed),
            turning_point_ply=self._turning_point(analyzed),
        )

    def _analyze_move(
        self, move: GameMove, evaluation: GameMoveEvaluation
    ) -> AnalyzedMove:
        gap = (
            evaluation.second_best_cp - evaluation.evaluation_before_cp
            if evaluation.second_best_cp is not None
            and evaluation.evaluation_before_cp is not None
            else None
        )
        sacrifice = (
            is_sacrifice(chess.Board(move.fen_after), move.uci)
            if evaluation.centipawn_loss is not None
            else None
        )
        classification = classify_move(
            MoveClassificationInput(
                centipawn_loss=evaluation.centipawn_loss,
                is_best_move=evaluation.is_best_move,
                second_best_gap=gap,
                sacrifices_material=sacrifice,
            ),
            self._classification_thresholds,
        )
        return AnalyzedMove(
            ply=move.ply,
            move_number=move.move_number,
            color=move.color,
            san=move.san,
            uci=move.uci,
            fen_before=move.fen_before,
            fen_after=move.fen_after,
            evaluation_before_cp=evaluation.evaluation_before_cp,
            evaluation_before_mate=evaluation.evaluation_before_mate,
            evaluation_after_cp=evaluation.evaluation_after_cp,
            evaluation_after_mate=evaluation.evaluation_after_mate,
            evaluation_change_cp=evaluation.evaluation_change_cp,
            centipawn_loss=evaluation.centipawn_loss,
            played_eval_cp=evaluation.played_eval_cp,
            played_eval_mate=evaluation.played_eval_mate,
            played_eval_source=evaluation.played_eval_source,
            best_move_uci=evaluation.best_move_uci,
            best_move_san=evaluation.best_move_san,
            is_best_move=evaluation.is_best_move,
            second_best_gap=gap,
            candidate_moves=list(evaluation.candidate_moves),
            depth=evaluation.depth,
            principal_variation=evaluation.principal_variation,
            classification=classification,
            phase=classify_position_fen(move.fen_after, self._phase_thresholds),
            features_before=extract_position_features_from_fen(move.fen_before),
            features_after=extract_position_features_from_fen(move.fen_after),
            is_sacrifice=bool(sacrifice),
        )

    @staticmethod
    def _summarize(analyzed: list[AnalyzedMove]) -> GameSummary:
        def side(color: Color) -> SideSummary:
            losses = [
                move.centipawn_loss
                for move in analyzed
                if move.color == color and move.centipawn_loss is not None
            ]
            counts: dict[MoveClassification, int] = {}
            for move in analyzed:
                if move.color == color and move.classification is not None:
                    counts[move.classification] = counts.get(move.classification, 0) + 1
            average = round(sum(losses) / len(losses), 1) if losses else None
            return SideSummary(average_centipawn_loss=average, counts=counts)

        phase_counts: dict[GamePhase, int] = {}
        for move in analyzed:
            phase_counts[move.phase] = phase_counts.get(move.phase, 0) + 1
        classified = sum(1 for move in analyzed if move.classification is not None)
        return GameSummary(
            total_moves=len(analyzed),
            classified_moves=classified,
            unclassified_moves=len(analyzed) - classified,
            white=side(Color.WHITE),
            black=side(Color.BLACK),
            phase_move_counts=phase_counts,
        )

    @staticmethod
    def _turning_point(analyzed: list[AnalyzedMove]) -> int | None:
        """Ply of the move with the largest evaluation swing against the mover.

        Deterministic definition: the minimum ``evaluation_change_cp`` (mover
        perspective) among evaluated moves. ``None`` when no move was evaluated.
        """
        candidates = [
            (move.evaluation_change_cp, move.ply)
            for move in analyzed
            if move.evaluation_change_cp is not None
        ]
        if not candidates:
            return None
        return min(candidates)[1]
