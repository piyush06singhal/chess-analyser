"""Candidate move comparison: several moves in one position, one search.

This is the direct answer to "compare 18.Bxf6, 18.Nd5, 18.Rd1, 18.Qe2". Every
row in the result is measured, not described:

* the evaluation and principal variation come from a **single MultiPV search**,
  so the moves are compared under identical conditions (comparing two searches at
  different depths is how a "difference" gets invented);
* a move outside the MultiPV window gets a **separate search of the resulting
  position**, and that is recorded as ``resulting_position`` so nobody reads it
  as the same-search value;
* an **illegal** move is reported as illegal and is never evaluated;
* material and tactical consequences are read off the resulting board.
"""

from __future__ import annotations

import chess
from argus.analysis.engine.base import ChessEngine, calculate_centipawn_loss, to_cp
from argus.analysis.features.extractor import extract_position_features_from_fen
from argus.analysis.phase import classify_position_fen

from argus.scenarios.models import (
    CandidateAssessment,
    CandidateComparison,
    EvidenceRef,
)
from argus.scenarios.policy import (
    DEFAULT_MULTIPV,
    MAX_CANDIDATE_MOVES,
    ComparisonDomain,
    clamp_depth,
    clamp_multipv,
    classify_move_quality,
)
from argus.scenarios.positions import (
    PositionComparisonService,
    engine_metrics_from,
    position_facts,
    structural_differences,
)
from argus.shared.errors import InvalidFenError

#: Domains that carry information about a *specific move*. Mobility and king
#: safety change for every legal move, so reporting them per move would be noise
#: presented as insight; the full board comparison keeps them.
_MOVE_CONSEQUENCE_DOMAINS: tuple[ComparisonDomain, ...] = (
    ComparisonDomain.MATERIAL,
    ComparisonDomain.STRUCTURE,
    ComparisonDomain.TACTICS,
)


def resolve_move(board: chess.Board, text: str) -> tuple[chess.Move | None, str | None]:
    """Accept UCI or SAN and return the move, or ``(None, reason)`` when illegal.

    Returning a reason instead of raising lets a caller report "that move is not
    legal in this position" as a normal result rather than as a failure.
    """
    candidate = (text or "").strip()
    if not candidate:
        return None, "A move is required"
    try:
        parsed = chess.Move.from_uci(candidate)
        if parsed in board.legal_moves:
            return parsed, None
    except ValueError:
        pass
    try:
        parsed = board.parse_san(candidate)
    except ValueError:
        return None, f"'{candidate}' is not a legal move in this position"
    return parsed, None


def pv_to_san(board: chess.Board, pv: list[str]) -> list[str]:
    """Render a UCI principal variation as SAN, stopping at the first bad move.

    A PV is engine output, not a guarantee that every move is legal on arrival —
    if one is not, the conversion stops rather than inventing notation.
    """
    working = board.copy(stack=False)
    rendered: list[str] = []
    for uci in pv:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in working.legal_moves:
            break
        rendered.append(working.san(move))
        working.push(move)
    return rendered


def _material_and_captures(before: chess.Board, move: chess.Move) -> tuple[list[str], int]:
    """Captured material and the material swing this move causes, in centipawns."""
    notes: list[str] = []
    mover_is_white = before.turn == chess.WHITE
    captured = before.piece_at(move.to_square) if not before.is_en_passant(move) else None
    if before.is_en_passant(move):
        pawn_square = chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))
        captured = before.piece_at(pawn_square)
    if captured is not None:
        notes.append(f"captures the {chess.piece_name(captured.piece_type)} on {chess.square_name(move.to_square)}")
    if move.promotion:
        notes.append(f"promotes to a {chess.piece_name(move.promotion)}")
    after = before.copy(stack=False)
    after.push(move)
    before_features = extract_position_features_from_fen(before.fen())
    after_features = extract_position_features_from_fen(after.fen())
    swing = after_features.material_balance - before_features.material_balance
    # Report the swing from the mover's point of view so a positive number always
    # means "this move won material for the player who made it".
    swing = swing if mover_is_white else -swing
    return notes, swing


def tactical_consequences(before: chess.Board, move: chess.Move) -> list[str]:
    """Tactical facts about the resulting position, read off the board."""
    after = before.copy(stack=False)
    after.push(move)
    mover_is_white = before.turn == chess.WHITE
    facts: list[str] = []
    if after.is_checkmate():
        facts.append("delivers checkmate")
        return facts
    if after.is_stalemate():
        facts.append("leaves the opponent stalemated")
    if after.is_check():
        facts.append("gives check")
    features = extract_position_features_from_fen(after.fen())
    opponent_hanging = (
        features.hanging_pieces_black if mover_is_white else features.hanging_pieces_white
    )
    own_hanging = (
        features.hanging_pieces_white if mover_is_white else features.hanging_pieces_black
    )
    if opponent_hanging:
        facts.append(
            f"leaves {opponent_hanging} opponent piece(s) attacked and not defended"
        )
    if own_hanging:
        facts.append(f"leaves {own_hanging} of the mover's own piece(s) attacked and not defended")
    if after.is_insufficient_material():
        facts.append("leaves insufficient material to mate")
    return facts


def _position_type(after: chess.Board) -> str:
    """A short, board-derived descriptor: phase plus pawn-structure shape."""
    phase = classify_position_fen(after.fen())
    features = extract_position_features_from_fen(after.fen())
    doubled = features.doubled_pawns_white + features.doubled_pawns_black
    isolated = features.isolated_pawns_white + features.isolated_pawns_black
    passed = features.passed_pawns_white + features.passed_pawns_black
    shape: list[str] = []
    if doubled:
        shape.append(f"{doubled} doubled pawn(s)")
    if isolated:
        shape.append(f"{isolated} isolated pawn(s)")
    if passed:
        shape.append(f"{passed} passed pawn(s)")
    suffix = ", ".join(shape) if shape else "no notable pawn weaknesses"
    return f"{phase.value} ({suffix})"


class CandidateMoveComparison:
    """Compare several candidate moves in one position."""

    def __init__(self, engine: ChessEngine, comparisons: PositionComparisonService | None = None) -> None:
        self.engine = engine
        self.comparisons = comparisons or PositionComparisonService(engine)

    def compare(
        self,
        fen: str,
        moves: list[str],
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
        played_move_uci: str | None = None,
        include_top: int = 0,
    ) -> CandidateComparison:
        """Measure each requested move in ``fen``.

        ``include_top`` appends the engine's own top-N moves to the comparison, so
        a caller can ask "is this move as good as the engine's best options?" in a
        single search instead of comparing two different searches to each other.

        Raises:
            InvalidFenError: when the FEN cannot be parsed.
        """
        facts = position_facts(fen)
        try:
            board = chess.Board(facts.fen)
        except ValueError as exc:  # pragma: no cover - facts already parsed it
            raise InvalidFenError(f"Cannot parse FEN '{fen}': {exc}") from exc

        effective_depth, depth_clamped = clamp_depth(depth)

        resolved: list[tuple[str, chess.Move | None, str | None]] = []
        seen: set[str] = set()
        for raw in moves:
            move, reason = resolve_move(board, raw)
            key = move.uci() if move is not None else (raw or "").strip()
            if key in seen:
                continue
            seen.add(key)
            resolved.append((key, move, reason))

        # The MultiPV window is wide enough to score every requested move in the
        # same search; a wider explicit request is honoured, and the hard ceiling
        # is never exceeded. ``include_top`` widens the window too, because the
        # engine's top moves have to come from the same search to be comparable.
        top_slots = max(0, min(include_top, MAX_CANDIDATE_MOVES))
        requested = len(resolved)
        truncated = False
        if multipv is not None:
            width, width_clamped = clamp_multipv(multipv)
            truncated = truncated or width_clamped
        else:
            width = max(min(requested, MAX_CANDIDATE_MOVES), DEFAULT_MULTIPV)
        width = min(max(width, top_slots, 1), MAX_CANDIDATE_MOVES)

        search = self.engine.analyze_position(
            facts.fen, depth=effective_depth, multipv=max(width, 1), movetime_ms=movetime_ms
        )

        seen_uci = {move.uci() for _, move, _ in resolved if move is not None}
        for entry in search.lines[:top_slots]:
            if entry.move_uci in seen_uci:
                continue
            try:
                extra = chess.Move.from_uci(entry.move_uci)
            except ValueError:
                continue
            if extra not in board.legal_moves:
                continue
            seen_uci.add(entry.move_uci)
            resolved.append((entry.move_uci, extra, None))

        requested = min(len(resolved), MAX_CANDIDATE_MOVES)
        truncated = truncated or len(resolved) > MAX_CANDIDATE_MOVES
        config = self.comparisons.engine_config(
            depth=effective_depth, multipv=max(width, 1), movetime_ms=movetime_ms
        )
        best_metrics = engine_metrics_from(facts.fen, search)
        best_move_uci = search.best_move_uci

        candidates: list[CandidateAssessment] = []
        for index, (key, move, reason) in enumerate(resolved[:requested], start=1):
            if move is None:
                candidates.append(
                    CandidateAssessment(
                        uci=key,
                        san=None,
                        legal=False,
                        legality_note=reason,
                        quality=classify_move_quality(None),
                    )
                )
                continue

            line = next((entry for entry in search.lines if entry.move_uci == move.uci()), None)
            if line is not None:
                cp, mate, pv, rank = line.cp, line.mate, list(line.pv), line.index
                eval_source = "same_search"
            else:
                # Outside the MultiPV window: score the resulting position in its
                # own search and flip it back to the mover. Honest, but recorded
                # as a different source so no one reads it as like-for-like.
                after = board.copy(stack=False)
                after.push(move)
                after_analysis = self.engine.analyze_position(
                    after.fen(), depth=effective_depth, multipv=1, movetime_ms=movetime_ms
                )
                after_metrics = engine_metrics_from(after.fen(), after_analysis)
                cp = None if after_metrics.cp_white is None else -after_metrics.cp_white
                mate = (
                    None if after_metrics.mate is None else -after_metrics.mate
                )
                pv = list(after_metrics.pv)
                rank = None
                eval_source = "resulting_position"

            value = to_cp(cp, mate)
            loss = calculate_centipawn_loss(best_metrics.cp, best_metrics.mate, cp, mate)
            after = board.copy(stack=False)
            after.push(move)
            after_facts = position_facts(after.fen())
            capture_notes, material_swing = _material_and_captures(board, move)
            san = board.san(move)
            candidates.append(
                CandidateAssessment(
                    uci=move.uci(),
                    san=san,
                    legal=True,
                    rank=rank if rank is not None else index,
                    cp=cp,
                    mate=mate,
                    cp_white=None if value is None else (value if board.turn == chess.WHITE else -value),
                    centipawn_loss=loss,
                    quality=classify_move_quality(loss, is_best=move.uci() == best_move_uci),
                    depth=effective_depth,
                    pv=pv,
                    pv_san=pv_to_san(board, pv),
                    is_engine_best=move.uci() == best_move_uci,
                    is_played_move=played_move_uci is not None and move.uci() == played_move_uci,
                    material_after={
                        "balance_before": facts.features.material_balance,
                        "balance_after": after_facts.features.material_balance,
                        "swing_cp_mover": material_swing,
                    },
                    material_consequence=(
                        "; ".join(capture_notes)
                        if capture_notes
                        else "no material change"
                    ),
                    tactical_consequence=tactical_consequences(board, move),
                    resulting_phase=after_facts.phase,
                    position_type=_position_type(after),
                    structural_deltas=structural_differences(
                        facts, after_facts, domains=_MOVE_CONSEQUENCE_DOMAINS
                    ),
                    eval_source=eval_source,
                )
            )

        notes: list[str] = []
        if truncated:
            notes.append(
                f"Only the first {requested} moves were compared "
                f"(limit {requested}); the rest were not searched."
            )
        if depth_clamped:
            notes.append(f"Requested depth was clamped to {effective_depth}.")
        if not search.lines:
            notes.append("The engine returned no lines for this position.")
        if any(candidate.eval_source == "resulting_position" for candidate in candidates):
            notes.append(
                "Some moves were outside the MultiPV window; their scores come from a "
                "separate search of the resulting position, so a small difference "
                "against a same-search score is not meaningful."
            )
        return CandidateComparison(
            fen=facts.fen,
            side_to_move=facts.side_to_move,
            phase=facts.phase,
            engine_config=config,
            candidates=candidates,
            best_cp=best_metrics.cp,
            best_move_uci=best_move_uci,
            requested=len(resolved),
            truncated=truncated,
            notes=notes,
            evidence=[
                EvidenceRef(
                    kind="engine",
                    detail=f"MultiPV search of {facts.fen} at {config.label()}",
                    uci=best_move_uci,
                ),
                EvidenceRef(kind="board", detail="material, structure and tactics read from the resulting board"),
            ],
        )


__all__ = [
    "CandidateMoveComparison",
    "pv_to_san",
    "resolve_move",
    "tactical_consequences",
]
