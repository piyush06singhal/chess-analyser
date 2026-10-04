"""Board facts and the position-comparison service.

Two positions can differ in two completely different ways: the engine may score
them differently, and the boards themselves may differ. This module keeps those
apart.

The structural side is pure board reading — it reuses
:func:`argus.analysis.features.extractor.extract_position_features_from_fen` and
:func:`argus.analysis.phase.classify_position_fen` from Phase 2/3 rather than
re-deriving material, pawn structure or phase for Phase 10. That reuse is the
point: if the feature definition ever changes, it changes once, and every phase
that reports it changes together.
"""

from __future__ import annotations

import chess
from argus.analysis.engine.base import AnalyzedPosition, ChessEngine, to_cp
from argus.analysis.features.extractor import extract_position_features_from_fen
from argus.analysis.phase import classify_position_fen

from argus.scenarios.models import (
    EngineConfig,
    EngineMetrics,
    FeatureDifference,
    PositionComparison,
    PositionFacts,
)
from argus.scenarios.policy import (
    DECISION_METHODOLOGY_VERSION,
    ComparisonDomain,
    clamp_depth,
    clamp_multipv,
)
from argus.shared.errors import InvalidFenError

#: Structural features compared between two positions, with the chess convention
#: that says which side benefits when the value is higher. ``None`` means the
#: feature has no side-benefit convention (a count of checkers, a total piece
#: count) and is reported without one.
_COMPARED_FEATURES: tuple[tuple[str, ComparisonDomain, str | None], ...] = (
    ("material_balance", ComparisonDomain.MATERIAL, "White"),
    ("material_white", ComparisonDomain.MATERIAL, "White"),
    ("material_black", ComparisonDomain.MATERIAL, "Black"),
    ("mobility_white", ComparisonDomain.ACTIVITY, "White"),
    ("mobility_black", ComparisonDomain.ACTIVITY, "Black"),
    ("king_safety_white", ComparisonDomain.KING_SAFETY, "White"),
    ("king_safety_black", ComparisonDomain.KING_SAFETY, "Black"),
    ("passed_pawns_white", ComparisonDomain.STRUCTURE, "White"),
    ("passed_pawns_black", ComparisonDomain.STRUCTURE, "Black"),
    ("isolated_pawns_white", ComparisonDomain.STRUCTURE, "Black"),
    ("isolated_pawns_black", ComparisonDomain.STRUCTURE, "White"),
    ("doubled_pawns_white", ComparisonDomain.STRUCTURE, "Black"),
    ("doubled_pawns_black", ComparisonDomain.STRUCTURE, "White"),
    ("backward_pawns_white", ComparisonDomain.STRUCTURE, "Black"),
    ("backward_pawns_black", ComparisonDomain.STRUCTURE, "White"),
    ("undeveloped_pieces_white", ComparisonDomain.ACTIVITY, "Black"),
    ("undeveloped_pieces_black", ComparisonDomain.ACTIVITY, "White"),
    ("center_occupied_white", ComparisonDomain.ACTIVITY, "White"),
    ("center_occupied_black", ComparisonDomain.ACTIVITY, "Black"),
    ("hanging_pieces_white", ComparisonDomain.TACTICS, "Black"),
    ("hanging_pieces_black", ComparisonDomain.TACTICS, "White"),
    ("checkers", ComparisonDomain.TACTICS, None),
    ("total_pieces", ComparisonDomain.STRUCTURE, None),
)


def _parse_fen(fen: str) -> chess.Board:
    """Parse a FEN, raising the domain error on bad input."""
    text = (fen or "").strip()
    if not text:
        raise InvalidFenError("A position FEN is required")
    try:
        return chess.Board(text)
    except ValueError as exc:
        raise InvalidFenError(f"Cannot parse FEN '{text}': {exc}") from exc


def position_facts(fen: str) -> PositionFacts:
    """Everything about a position that requires no engine call.

    Raises:
        InvalidFenError: when the FEN cannot be parsed.
    """
    board = _parse_fen(fen)
    features = extract_position_features_from_fen(board.fen())
    phase = classify_position_fen(board.fen())
    terminal_reason = None
    if board.is_checkmate():
        terminal_reason = "checkmate"
    elif board.is_stalemate():
        terminal_reason = "stalemate"
    elif board.is_insufficient_material():
        terminal_reason = "insufficient_material"
    return PositionFacts(
        fen=board.fen(),
        side_to_move="white" if board.turn == chess.WHITE else "black",
        phase=phase.value,
        material_white=features.material_white,
        material_black=features.material_black,
        material_balance=features.material_balance,
        move_number=board.fullmove_number,
        legal_move_count=board.legal_moves.count(),
        is_check=board.is_check(),
        is_terminal=bool(terminal_reason),
        terminal_reason=terminal_reason,
        castling_rights=board.castling_xfen(),
        features=features,
    )


def engine_metrics_from(
    fen: str,
    analysis: AnalyzedPosition | None,
    *,
    requested_move_uci: str | None = None,
    unavailable_reason: str | None = None,
) -> EngineMetrics:
    """Copy an engine result into the comparison shape.

    ``cp_white`` is the score normalised to White's perspective so two positions
    with different side-to-move can still be compared on one scale. That is an
    explicit normalisation, applied so the comparison is meaningful — not a
    reinterpretation of the engine's opinion.
    """
    if analysis is None:
        return EngineMetrics(
            fen=fen,
            depth=0,
            available=False,
            unavailable_reason=unavailable_reason or "no engine result",
        )
    line = analysis.lines[0] if analysis.lines else None
    cp: int | None = None
    mate: int | None = None
    rank: int | None = None
    pv: list[str] = []
    if requested_move_uci is not None:
        match = next(
            (entry for entry in analysis.lines if entry.move_uci == requested_move_uci), None
        )
        if match is not None:
            cp, mate, rank, pv = match.cp, match.mate, match.index, list(match.pv)
    if cp is None and mate is None and line is not None:
        cp, mate, pv = line.cp, line.mate, list(line.pv)
    board = _parse_fen(fen)
    sign = 1 if board.turn == chess.WHITE else -1
    value = to_cp(cp, mate)
    return EngineMetrics(
        fen=fen,
        depth=analysis.depth,
        multipv=analysis.multipv,
        cp=cp,
        mate=mate,
        cp_white=None if value is None else sign * value,
        best_move_uci=analysis.best_move_uci,
        best_move_san=analysis.best_move_san,
        pv=pv,
        multipv_rank=rank,
        nodes=analysis.nodes,
        engine=analysis.engine,
        engine_version=analysis.engine_version,
        available=True,
    )


def _direction(value_a: float | int | None, value_b: float | int | None) -> tuple[str, float | int | None]:
    if value_a is None or value_b is None:
        return "incomparable", None
    delta = value_b - value_a
    if delta > 0:
        return "b_higher", delta
    if delta < 0:
        return "a_higher", delta
    return "equal", 0


def structural_differences(
    facts_a: PositionFacts,
    facts_b: PositionFacts,
    *,
    domains: tuple[ComparisonDomain, ...] | None = None,
) -> list[FeatureDifference]:
    """Compare two positions field by field, with no interpretation attached.

    ``domains`` narrows the comparison to the axes a caller cares about. It is
    used when comparing a position with its successor after a move: mobility and
    king-safety counts change for *every* legal move simply because a piece has
    moved, so reporting them as a move's "consequence" would dress a tautology up
    as an insight. Material, pawn structure and tactics are the axes that carry
    information about a specific move.
    """
    differences: list[FeatureDifference] = []
    for field, domain, beneficiary in _COMPARED_FEATURES:
        if domains is not None and domain not in domains:
            continue
        value_a = getattr(facts_a.features, field, None)
        value_b = getattr(facts_b.features, field, None)
        direction, delta = _direction(value_a, value_b)
        if direction == "equal":
            continue
        # ``higher_is`` is recorded only where a count has an unambiguous chess
        # convention. It states a convention, not a verdict on the position.
        higher_is = beneficiary if direction == "b_higher" else (beneficiary if beneficiary else None)
        if beneficiary is not None and direction == "a_higher":
            higher_is = None  # the *other* side is higher here; reported via direction
        differences.append(
            FeatureDifference(
                domain=domain,
                feature=field,
                value_a=value_a,
                value_b=value_b,
                delta=delta,
                direction=direction,
                higher_is=higher_is,
                basis="board_feature",
            )
        )
    return differences


class PositionComparisonService:
    """Compare two positions, keeping the engine axis and the board axis apart.

    The service is stateless. It either uses engine results handed to it or asks
    the engine it was given — it never reads a stored analysis, so a comparison
    is always explicit about what produced each number.
    """

    def __init__(self, engine: ChessEngine | None = None) -> None:
        self.engine = engine

    def compare(
        self,
        fen_a: str,
        fen_b: str,
        *,
        engine_a: AnalyzedPosition | None = None,
        engine_b: AnalyzedPosition | None = None,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
    ) -> PositionComparison:
        """Compare two positions, running the engine only when a result is missing.

        When no engine is available and no results were supplied, the structural
        axis is still returned in full and the engine axis is marked unavailable.
        The comparison never fills the gap with a guess.
        """
        facts_a = position_facts(fen_a)
        facts_b = position_facts(fen_b)
        effective_depth, depth_clamped = clamp_depth(depth)
        effective_multipv, _ = clamp_multipv(multipv)

        if engine_a is None or engine_b is None:
            if self.engine is not None:
                if engine_a is None:
                    engine_a = self.engine.analyze_position(
                        facts_a.fen,
                        depth=effective_depth,
                        multipv=effective_multipv,
                        movetime_ms=movetime_ms,
                    )
                if engine_b is None:
                    engine_b = self.engine.analyze_position(
                        facts_b.fen,
                        depth=effective_depth,
                        multipv=effective_multipv,
                        movetime_ms=movetime_ms,
                    )

        metrics_a = engine_metrics_from(
            facts_a.fen, engine_a, unavailable_reason="no engine result for position A"
        )
        metrics_b = engine_metrics_from(
            facts_b.fen, engine_b, unavailable_reason="no engine result for position B"
        )
        engine_difference: dict = {
            "cp_a_white": metrics_a.cp_white,
            "cp_b_white": metrics_b.cp_white,
            "delta_cp_white": (
                None
                if metrics_a.cp_white is None or metrics_b.cp_white is None
                else metrics_b.cp_white - metrics_a.cp_white
            ),
            "available": metrics_a.available and metrics_b.available,
            "perspective": "white",
            "note": (
                "A search result, not a board fact. Scores are normalised to White's "
                "perspective; a difference smaller than the engine's own stability is "
                "not a real difference."
            ),
            **({"depth_clamped_to": effective_depth} if depth_clamped else {}),
        }
        notes: list[str] = []
        if facts_a.side_to_move != facts_b.side_to_move:
            notes.append(
                "The two positions have different side to move; engine scores were "
                "normalised to White for comparability, which does not remove the "
                "tempo difference."
            )
        if not (metrics_a.available and metrics_b.available):
            notes.append(
                "No engine comparison is available for these positions; only board facts "
                "are reported."
            )
        return PositionComparison(
            fen_a=facts_a.fen,
            fen_b=facts_b.fen,
            facts_a=facts_a,
            facts_b=facts_b,
            engine_a=metrics_a,
            engine_b=metrics_b,
            engine_difference=engine_difference,
            structural_differences=structural_differences(facts_a, facts_b),
            phase_change=(
                None if facts_a.phase == facts_b.phase else f"{facts_a.phase} -> {facts_b.phase}"
            ),
            material_change_cp=facts_b.material_balance - facts_a.material_balance,
            notes=notes,
            methodology_version=DECISION_METHODOLOGY_VERSION,
        )

    def engine_config(
        self, *, depth: int | None = None, multipv: int | None = None, movetime_ms: int | None = None
    ) -> EngineConfig:
        effective_depth, _ = clamp_depth(depth)
        effective_multipv, _ = clamp_multipv(multipv)
        version = None
        if self.engine is not None:
            version = self.engine.info().get("version")
        return EngineConfig(
            depth=None if movetime_ms else effective_depth,
            multipv=effective_multipv,
            movetime_ms=movetime_ms,
            engine_version=version,
        )


__all__ = [
    "PositionComparisonService",
    "engine_metrics_from",
    "position_facts",
    "structural_differences",
]
