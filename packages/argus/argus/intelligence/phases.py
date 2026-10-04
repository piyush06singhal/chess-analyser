"""Game-phase detection from board state.

**Phase 4 refinement of the Phase 3 classifier.** The Phase 3 classifier
(``argus.analysis.phase.classify_position``) answers the question with two
board-state rules. That is enough for tagging stored moves, but a game report
needs to *explain* the answer and to be honest about how confident it is.

``GamePhaseDetector`` evaluates independent **indicators** over the board
(non-pawn material, development/castling, endgame character) plus the Phase 3
board-state rule, which is kept as a baseline/fallback. The substantive
indicators are combined first and the baseline only breaks ties:

    1. the phase with the most substantive votes wins;
    2. on a tie or when no substantive indicator voted, a documented precedence
       is applied (endgame evidence first, then opening evidence, then the
       Phase 3 baseline, then ``middlegame`` as the transitional default).

    reasons     = the indicators that voted for the winning phase
    confidence  = votes_for_winner / substantive_indicators_evaluated

``confidence`` is therefore a **documented agreement ratio**, not a probability
and not a calibrated statistical quantity. A precedence decision can leave it at
``0.0`` (no indicator confirmed the label); ``decision`` records which rule was
applied. The UI shows ``reasons``, not the number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import chess
from pydantic import BaseModel, Field

from argus.analysis.features.extractor import extract_position_features, phase_piece_material
from argus.analysis.phase import GamePhase, PhaseThresholds, classify_position
from argus.intelligence.base import GamePhasePolicy

IndicatorName = Literal[
    "phase3_baseline",
    "non_pawn_material",
    "development_and_castling",
    "endgame_character",
]


class PhaseIndicator(BaseModel):
    """One board-state indicator and the phase it voted for."""

    name: IndicatorName
    phase: GamePhase
    detail: str


class PhaseDetection(BaseModel):
    """Result of the detector for a single position."""

    phase: GamePhase
    reasons: list[str] = Field(default_factory=list)
    indicators: list[PhaseIndicator] = Field(default_factory=list)
    indicators_evaluated: int = 0
    votes: dict[str, int] = Field(default_factory=dict)
    decision: str = Field(
        default="indicator majority",
        description="Which documented rule produced the phase (majority or precedence)",
    )
    confidence: float = Field(
        default=0.0,
        description="Agreement ratio = winning votes / substantive indicators evaluated "
        "(documented Caissa metric, NOT a probability)",
    )

    @property
    def confidence_methodology(self) -> str:
        return (
            "agreement ratio: winning indicator votes / substantive indicators "
            "evaluated (0.0 when the label came from the precedence rules)"
        )


@dataclass(frozen=True)
class _Vote:
    name: IndicatorName
    phase: GamePhase | None
    detail: str


class GamePhaseDetector:
    """Detects the phase of a position from board characteristics.

    Usage::

        detector = GamePhaseDetector()
        detection = detector.detect(chess.Board(fen))
        detection.phase        # GamePhase.OPENING / MIDDLEGAME / ENDGAME
        detection.reasons      # human-readable board-state reasons
        detection.confidence   # documented agreement ratio
    """

    def __init__(
        self,
        policy: GamePhasePolicy | None = None,
        phase_thresholds: PhaseThresholds | None = None,
    ) -> None:
        self._policy = policy or GamePhasePolicy()
        self._phase_thresholds = phase_thresholds

    @property
    def policy(self) -> GamePhasePolicy:
        return self._policy

    # --- individual indicators -------------------------------------------------

    def _baseline_vote(self, board: chess.Board) -> _Vote:
        """The Phase 3 board-state rule, kept as one (authoritative-on-tie) vote."""
        phase = classify_position(board, self._phase_thresholds)
        return _Vote(
            name="phase3_baseline",
            phase=phase,
            detail=f"Phase 3 board-state rule classified this position as {phase.value}",
        )

    def _material_vote(self, board: chess.Board, phase_material: int) -> _Vote:
        if phase_material <= self._policy.endgame_max_phase_material:
            return _Vote(
                name="non_pawn_material",
                phase=GamePhase.ENDGAME,
                detail=(
                    f"{phase_material} points of non-pawn material on the board "
                    f"(<= {self._policy.endgame_max_phase_material})"
                ),
            )
        if phase_material >= self._policy.opening_min_phase_material:
            return _Vote(
                name="non_pawn_material",
                phase=GamePhase.OPENING,
                detail=(
                    f"{phase_material} points of non-pawn material on the board "
                    f"(>= {self._policy.opening_min_phase_material}), close to the starting material"
                ),
            )
        return _Vote(
            name="non_pawn_material",
            phase=None,
            detail=f"{phase_material} points of non-pawn material (transitional)",
        )

    def _development_vote(
        self, board: chess.Board, undeveloped: int, phase_material: int
    ) -> _Vote:
        active_castling = board.clean_castling_rights()
        if (
            undeveloped >= self._policy.opening_min_undeveloped_total
            and bool(active_castling)
            and phase_material >= self._policy.opening_development_min_phase_material
        ):
            return _Vote(
                name="development_and_castling",
                phase=GamePhase.OPENING,
                detail=(
                    f"{undeveloped} undeveloped pieces and castling rights still available"
                ),
            )
        if undeveloped == 0 and not active_castling:
            return _Vote(
                name="development_and_castling",
                phase=GamePhase.MIDDLEGAME,
                detail="every piece is developed and no castling rights remain",
            )
        if (
            undeveloped >= self._policy.opening_min_undeveloped_total
            and bool(active_castling)
        ):
            return _Vote(
                name="development_and_castling",
                phase=None,
                detail=(
                    f"{undeveloped} undeveloped pieces with castling rights still "
                    f"available, but only {phase_material} points of non-pawn material "
                    "left: too shattered to call an opening"
                ),
            )
        return _Vote(
            name="development_and_castling",
            phase=None,
            detail=f"{undeveloped} undeveloped pieces, castling rights {'present' if active_castling else 'gone'}",
        )

    def _endgame_vote(self, board: chess.Board, phase_material: int, queens: bool) -> _Vote:
        pieces = board.occupied_co[chess.WHITE].bit_count() + board.occupied_co[chess.BLACK].bit_count()
        if pieces <= self._policy.endgame_max_total_pieces:
            return _Vote(
                name="endgame_character",
                phase=GamePhase.ENDGAME,
                detail=f"{pieces} pieces left on the board (<= {self._policy.endgame_max_total_pieces})",
            )
        if (
            not queens
            and phase_material <= self._policy.queens_off_endgame_max_phase_material
        ):
            return _Vote(
                name="endgame_character",
                phase=GamePhase.ENDGAME,
                detail=(
                    "queens are off and only "
                    f"{phase_material} points of non-pawn material remain"
                ),
            )
        return _Vote(
            name="endgame_character",
            phase=None,
            detail="queens on the board or enough material left for a middlegame",
        )

    # --- public API ------------------------------------------------------------

    def detect(self, board: chess.Board) -> PhaseDetection:
        """Classify the phase of ``board`` with reasons and an agreement ratio."""
        features = extract_position_features(board)
        phase_material = phase_piece_material(board)
        undeveloped = (
            features.undeveloped_pieces_white + features.undeveloped_pieces_black
        )

        votes = [
            self._baseline_vote(board),
            self._material_vote(board, phase_material),
            self._development_vote(board, undeveloped, phase_material),
            self._endgame_vote(board, phase_material, features.queens_on_board),
        ]
        winner, decision = self._decide(votes, phase_material)

        substantive = [vote for vote in votes if vote.name != "phase3_baseline"]
        tallies: dict[GamePhase, int] = {}
        for vote in substantive:
            if vote.phase is not None:
                tallies[vote.phase] = tallies.get(vote.phase, 0) + 1

        reasons = [vote.detail for vote in votes if vote.phase == winner]
        return PhaseDetection(
            phase=winner,
            reasons=reasons,
            indicators=[
                PhaseIndicator(name=vote.name, phase=vote.phase or winner, detail=vote.detail)
                for vote in votes
            ],
            indicators_evaluated=len(substantive),
            votes={phase.value: count for phase, count in tallies.items()},
            decision=decision,
            confidence=round(tallies.get(winner, 0) / len(substantive), 3),
        )

    def _decide(
        self, votes: list[_Vote], phase_material: int
    ) -> tuple[GamePhase, str]:
        """Combine the indicator votes into one phase (documented precedence)."""
        by_name = {vote.name: vote.phase for vote in votes}
        baseline = by_name.get("phase3_baseline")
        material = by_name.get("non_pawn_material")
        development = by_name.get("development_and_castling")
        endgame = by_name.get("endgame_character")

        tallies: dict[GamePhase, int] = {}
        for name in ("non_pawn_material", "development_and_castling", "endgame_character"):
            phase = by_name.get(name)
            if phase is not None:
                tallies[phase] = tallies.get(phase, 0) + 1

        if tallies:
            best = max(tallies.values())
            leaders = [phase for phase, count in tallies.items() if count == best]
            if len(leaders) == 1:
                return leaders[0], "majority of the substantive board-state indicators"

        # No majority: documented precedence order.
        if material is GamePhase.ENDGAME and endgame is GamePhase.ENDGAME:
            return GamePhase.ENDGAME, "precedence: material and endgame character agree"
        if endgame is GamePhase.ENDGAME and material is not GamePhase.OPENING:
            return GamePhase.ENDGAME, "precedence: endgame character, no opening evidence"
        if development is GamePhase.OPENING and material is not GamePhase.ENDGAME:
            return GamePhase.OPENING, "precedence: development, no endgame evidence"
        if baseline is GamePhase.ENDGAME:
            return GamePhase.ENDGAME, "precedence: Phase 3 baseline endgame"
        if baseline is GamePhase.OPENING and material is GamePhase.OPENING:
            return GamePhase.OPENING, "precedence: Phase 3 baseline opening, material intact"
        if baseline is GamePhase.OPENING:
            return GamePhase.MIDDLEGAME, (
                "precedence: Phase 3 baseline says opening but non-pawn material "
                f"({phase_material}) is below the opening floor"
            )
        return GamePhase.MIDDLEGAME, "precedence: transitional default"

    def detect_fen(self, fen: str) -> PhaseDetection:
        """Classify the phase of a position given as a FEN string."""
        return self.detect(chess.Board(fen))

    def classify(self, fen: str) -> GamePhase:
        """Just the phase label for a FEN (convenience for bulk tagging)."""
        return self.detect_fen(fen).phase

    def detect_moves(self, moves) -> list[GamePhase]:  # noqa: ANN001 — list[MoveFact]
        """Phase after every move of a game (by ``fen_after``)."""
        return [self.classify(move.fen_after) for move in moves]
