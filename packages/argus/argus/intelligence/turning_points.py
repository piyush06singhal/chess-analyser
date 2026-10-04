"""Turning-point detection.

A turning point is a position after which the game changed in a way that
mattered. The detector combines several independent signals instead of picking
"the biggest centipawn swing", because the biggest swing is often noise (a
forced recapture) while the decisive moment is a moderate, permanent shift.

Signals:

* ``evaluation_swing`` — a large loss for the mover that **persisted**
  (it was not immediately recovered inside ``persistence_plies``).
* ``advantage_lost`` — the mover went from a clear advantage to at most a slight
  one.
* ``missed_win`` — the mover was winning and is no longer winning.
* ``mate_change`` — a forced mate appeared against the mover, was no longer
  available to the mover, or the mover delivered it.
* ``material_transition`` — the mover's material balance moved by two or more
  pawns across the exchange sequence their move started.
* ``forced_sequence`` — a forced mate against the mover appeared right after the
  move.
* ``conversion_failure`` — the mover's winning evaluation never came back.

Several turning points may coexist; the layer reports them all (capped, with the
number dropped made explicit) rather than collapsing the game to one number.
"""

from __future__ import annotations

from enum import Enum

import chess
from pydantic import BaseModel, Field

from argus.analysis.classification import MoveClassification
from argus.analysis.perspective import format_evaluation
from argus.chess_core.models import Color
from argus.intelligence.advantage import band
from argus.intelligence.base import (
    Certainty,
    EvidenceSource,
    MoveFact,
    TurningPointPolicy,
    color_label,
    other,
    severity_from_magnitude,
)
from argus.intelligence.conversion import ConversionAnalysis, ConversionEventType
from argus.intelligence.material import material_points


class TurningPointType(str, Enum):
    """Why a position counts as a turning point."""

    EVALUATION_SWING = "evaluation_swing"
    ADVANTAGE_LOST = "advantage_lost"
    MISSED_WIN = "missed_win"
    MATE_CHANGE = "mate_change"
    MATERIAL_TRANSITION = "material_transition"
    FORCED_SEQUENCE = "forced_sequence"
    CONVERSION_FAILURE = "conversion_failure"


#: Types that are always kept when the report caps the list.
_ALWAYS_KEEP = {TurningPointType.MATE_CHANGE, TurningPointType.MISSED_WIN}

#: A loss this small may simply be repaired by the mover's next moves.
RECOVERY_TOLERANCE_CP = 200

#: Longest run of consecutive captures measured as one exchange sequence.
#: Bounded so an unusual chain of captures can never sweep the whole game.
MAX_EXCHANGE_SEQUENCE_PLIES = 6


class TurningPoint(BaseModel):
    """One detected turning point with its full evidence."""

    type: TurningPointType
    ply: int
    move_number: int
    side: Color = Field(description="The side the turning point went against")
    san: str
    evaluation_before_cp: int | None = Field(default=None, description="White perspective")
    evaluation_after_cp: int | None = Field(default=None, description="White perspective")
    swing_cp: int | None = Field(default=None, description="Mover perspective")
    classification: MoveClassification | None = None
    severity: str = "low"
    severity_score: int = 0
    persistent: bool | None = Field(
        default=None, description="None when persistence does not apply to this signal"
    )
    statement: str
    certainty: Certainty = Certainty.CONFIRMED
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE
    evidence: dict = Field(default_factory=dict)


class TurningPointAnalysis(BaseModel):
    """Turning points of a game plus transparency about what was dropped."""

    turning_points: list[TurningPoint] = Field(default_factory=list)
    candidates_considered: int = 0
    dropped_by_cap: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    largest_swing_ply: int | None = Field(
        default=None, description="Ply of the single largest loss for the mover"
    )
    note: str = (
        "Multiple turning points may exist. Candidates are ranked by severity; the number "
        "dropped by the report cap is reported so nothing is silently hidden."
    )


def _persisted(
    moves: list[MoveFact],
    index: int,
    *,
    mover: Color,
    after_value: int | None,
    window: int,
) -> bool | None:
    """True when the mover had not recovered within ``window`` plies.

    Compares the mover's evaluation right after the move with the nearest
    evaluated ply at least ``window`` plies later. A recovery of more than
    ``RECOVERY_TOLERANCE_CP`` means the loss did not stick.
    """
    if after_value is None:
        return None
    for offset in range(window, window * 3 + 1):
        candidate_index = index + offset
        if candidate_index >= len(moves):
            return True  # ran out of game: the loss was never repaired
        candidate = moves[candidate_index]
        value = candidate.mover_eval_after if candidate.mover == mover else candidate.mover_eval_before
        if value is None:
            continue
        return value <= after_value + RECOVERY_TOLERANCE_CP
    return None


def detect_turning_points(
    moves: list[MoveFact],
    *,
    policy: TurningPointPolicy | None = None,
    conversion: ConversionAnalysis | None = None,
) -> TurningPointAnalysis:
    """Detect turning points from engine measurements and board facts."""
    limits = policy or TurningPointPolicy()
    candidates: list[TurningPoint] = []

    for index, fact in enumerate(moves):
        if not fact.evaluated:
            continue
        mover_band_before = band(_mover_state(fact, before=True))
        mover_band_after = band(_mover_state(fact, before=False))
        swing = fact.eval_change_cp
        severity_score = abs(swing) if swing is not None else 0

        def add(
            point_type: TurningPointType,
            *,
            statement: str,
            evidence: dict,
            persistent: bool | None = None,
            score: int | None = None,
            certainty: Certainty = Certainty.CONFIRMED,
        ) -> None:
            magnitude = score if score is not None else severity_score
            candidates.append(
                TurningPoint(
                    type=point_type,
                    ply=fact.ply,
                    move_number=fact.move_number,
                    side=fact.mover,
                    san=fact.san,
                    evaluation_before_cp=fact.eval_before_white,
                    evaluation_after_cp=fact.eval_after_white,
                    swing_cp=swing,
                    classification=fact.classification,
                    severity=severity_from_magnitude(
                        magnitude, medium=limits.min_swing_cp, high=limits.high_swing_cp
                    ),
                    severity_score=magnitude,
                    persistent=persistent,
                    statement=statement,
                    certainty=certainty,
                    evidence={
                        "fen_before": fact.fen_before,
                        "fen_after": fact.fen_after,
                        "centipawn_loss": fact.centipawn_loss,
                        "classification": fact.classification.value
                        if fact.classification
                        else None,
                        "evaluation_change_cp": swing,
                        **evidence,
                    },
                )
            )

        # 1. Large, persistent loss for the mover.
        if swing is not None and swing <= -limits.min_swing_cp:
            persistent = _persisted(
                moves,
                index,
                mover=fact.mover,
                after_value=fact.mover_eval_after,
                window=limits.persistence_plies,
            )
            if persistent:
                add(
                    TurningPointType.EVALUATION_SWING,
                    statement=(
                        f"{color_label(fact.mover)}'s evaluation fell "
                        f"{abs(swing)}cp after {fact.move_number}"
                        f"{'.' if fact.mover == Color.WHITE else '...'} {fact.san}."
                    ),
                    evidence={"persistence_plies": limits.persistence_plies, "persisted": True},
                    persistent=True,
                )

        # 2. Advantage lost / missed win.
        if mover_band_before is not None and mover_band_after is not None:
            if mover_band_before >= 3 and mover_band_after <= 1:
                add(
                    TurningPointType.MISSED_WIN,
                    statement=(
                        f"{color_label(fact.mover)} was winning before {fact.san} and the "
                        f"evaluation was {format_evaluation(fact.eval_after_white, fact.mate_after_white)} "
                        f"afterwards."
                    ),
                    evidence={
                        "band_before": mover_band_before,
                        "band_after": mover_band_after,
                    },
                    score=limits.high_swing_cp,
                )
            elif mover_band_before >= 2 and mover_band_after <= 1:
                add(
                    TurningPointType.ADVANTAGE_LOST,
                    statement=(
                        f"{color_label(fact.mover)}'s clear advantage was gone after "
                        f"{fact.san} (band {mover_band_before} to {mover_band_after})."
                    ),
                    evidence={
                        "band_before": mover_band_before,
                        "band_after": mover_band_after,
                    },
                    score=limits.min_swing_cp,
                )

        # 3. Mate being delivered / appearing / disappearing for the mover.
        mate_before = fact.mate_before_white
        mate_after = fact.mate_after_white
        mate_before_mover = None if mate_before is None else (mate_before if fact.mover == Color.WHITE else -mate_before)
        mate_after_mover = None if mate_after is None else (mate_after if fact.mover == Color.WHITE else -mate_after)
        # A terminal position is never evaluated, so a mating move is read from
        # the board rather than inferred from a missing mate score.
        after_evaluated = fact.eval_after_cp is not None or mate_after is not None
        if _delivered_checkmate(fact):
            add(
                TurningPointType.MATE_CHANGE,
                statement=f"{color_label(fact.mover)} delivered checkmate with {fact.san}.",
                evidence={"checkmate": True, "terminal": True},
                score=limits.high_swing_cp,
            )
        elif after_evaluated and mate_before_mover != mate_after_mover and (
            mate_before_mover is not None or mate_after_mover is not None
        ):
            lost_mate = mate_before_mover is not None and mate_before_mover > 0 and (
                mate_after_mover is None or mate_after_mover <= 0
            )
            conceded = mate_after_mover is not None and mate_after_mover < 0 and (
                mate_before_mover is None or mate_before_mover >= 0
            )
            if lost_mate or conceded:
                add(
                    TurningPointType.MATE_CHANGE,
                    statement=(
                        f"{color_label(fact.mover)}'s forced mate was no longer available "
                        f"after {fact.san}."
                        if lost_mate
                        else f"After {fact.san}, {color_label(other(fact.mover))} has a forced mate."
                    ),
                    evidence={
                        "mate_before_mover_perspective": mate_before_mover,
                        "mate_after_mover_perspective": mate_after_mover,
                    },
                    score=limits.high_swing_cp,
                )
                if conceded and mate_after_mover is not None and abs(mate_after_mover) <= 3:
                    add(
                        TurningPointType.FORCED_SEQUENCE,
                        statement=(
                            f"A forced mate in {abs(mate_after_mover)} appeared against "
                            f"{color_label(fact.mover)} after {fact.san}."
                        ),
                        evidence={"mate_distance": abs(mate_after_mover)},
                        score=limits.high_swing_cp,
                    )

        # 4. Material change for the mover (measured from the board, not the eval).
        #    The *balance* change is used, not the mover's own piece total: a
        #    capture never raises the capturer's own material, so an own-total
        #    metric could only ever report losses and would hide material wins.
        material_change = material_balance_change_for_mover(moves, index)
        if abs(material_change) >= limits.material_swing_pawns:
            direction = "gained" if material_change > 0 else "lost"
            add(
                TurningPointType.MATERIAL_TRANSITION,
                statement=(
                    f"{color_label(fact.mover)} {direction} {abs(material_change)} pawns of "
                    f"material in the sequence started by {fact.san} "
                    f"(board material, independent of the evaluation)."
                ),
                evidence={"material_balance_change_pawns": material_change},
                score=abs(material_change) * 100,
                certainty=Certainty.CONFIRMED,
            )

    # 5. Conversion failures from the conversion analysis.
    if conversion is not None:
        for event in conversion.events:
            if event.type is not ConversionEventType.ADVANTAGE_CONVERSION_CANDIDATE:
                continue
            candidates.append(
                TurningPoint(
                    type=TurningPointType.CONVERSION_FAILURE,
                    ply=event.ply,
                    move_number=event.move_number or event.ply,
                    side=event.side,
                    san=_san_at_ply(moves, event.ply),
                    evaluation_before_cp=event.peak_evaluation_white,
                    evaluation_after_cp=event.later_evaluation_white,
                    swing_cp=None,
                    classification=None,
                    severity="medium",
                    severity_score=limits.min_swing_cp,
                    persistent=None,
                    statement=event.statement,
                    certainty=Certainty.CANDIDATE,
                    source=EvidenceSource.ARGUS_INTERPRETATION,
                    evidence={"conversion": event.model_dump()},
                )
            )

    # One turning point per (ply, type): keep the most severe.
    unique: dict[tuple[int, TurningPointType], TurningPoint] = {}
    for candidate in candidates:
        key = (candidate.ply, candidate.type)
        existing = unique.get(key)
        if existing is None or candidate.severity_score > existing.severity_score:
            unique[key] = candidate
    ranked = sorted(
        unique.values(), key=lambda point: (point.severity_score, -point.ply), reverse=True
    )

    keep = [point for point in ranked if point.type in _ALWAYS_KEEP or point.severity == "high"]
    for point in ranked:
        if len(keep) >= limits.max_turning_points:
            break
        if point not in keep:
            keep.append(point)
    keep = keep[: limits.max_turning_points]
    selected = sorted(keep, key=lambda point: point.ply)

    by_type: dict[str, int] = {}
    for point in selected:
        by_type[point.type.value] = by_type.get(point.type.value, 0) + 1

    largest = min(
        (
            (fact.eval_change_cp, fact.ply)
            for fact in moves
            if fact.eval_change_cp is not None
        ),
        default=(None, None),
    )[1]

    return TurningPointAnalysis(
        turning_points=selected,
        candidates_considered=len(candidates),
        dropped_by_cap=max(0, len(unique) - len(selected)),
        by_type=by_type,
        largest_swing_ply=largest,
    )


def _mover_state(fact: MoveFact, *, before: bool):
    """Advantage state for the mover before/after the move (uses stored evals)."""
    from argus.intelligence.advantage import state_for

    if before:
        cp, mate = fact.eval_before_cp, fact.eval_before_mate
    else:
        cp, mate = fact.eval_after_cp, fact.eval_after_mate
    if cp is None and mate is None:
        return None
    return state_for(cp, mate, fact.mover)


def _capture_target_square(moves: list[MoveFact], index: int) -> int | None:
    """Destination square of a capture on this ply, else ``None``."""
    fact = moves[index]
    try:
        move = chess.Move.from_uci(fact.uci)
    except ValueError:
        return None
    board = chess.Board(fact.fen_before)
    return move.to_square if board.is_capture(move) else None


def _is_promotion(fact: MoveFact) -> bool:
    return len(fact.uci) == 5 and fact.uci[4] in "qrbn"


def material_balance_change_for_mover(moves: list[MoveFact], index: int) -> int:
    """Pawns of material the mover **gained (+) / lost (-)** on this move.

    Three rules keep the attribution honest and non-duplicating:

    1. Only a move that captures or promotes can be a material event. A quiet
       move never changes material itself; whatever the reply wins or loses
       belongs to the reply.
    2. The mover's figure is the change in the material *balance* (White points
       minus Black points) across the move **and** the opponent's reply. Playing
       6.Nxf7 and being answered by 6...Kxf7 is a knight for a pawn, i.e. -2; a
       capture that is not answered is its own value. When the game ends on the
       move the direct change is used.
    3. A recapture on the square the opponent just captured on is attributed to
       the ply that initiated the exchange (value 0 here), so 4.dxe5 Bxf3 and
       4...Bxf3 5.Qxf3 are not counted twice.
    4. The window spans the *settled* exchange sequence the move starts (see
       :func:`_exchange_sequence_end`), bounded by
       ``MAX_EXCHANGE_SEQUENCE_PLIES``. One reply is too short (4.dxe5 looks
       like a two-pawn loss until 5.Qxf3 and 5...dxe5 settle it to zero) and the
       whole run of captures is too long (9.exd5 Nxd5 11.Nxf7 Kxf7 would blame
       the exd5 exchange for the piece sacrifice two plies later).

    The signed *balance* is used rather than the mover's own piece total: a
    capture never raises the capturer's own material, so an own-total metric
    could only ever report losses and would hide material wins.

    Material is measured from the board with python-chess and never from the
    engine, so it can contradict the evaluation (a side can be a pawn up and
    losing) and the two are reported separately.
    """
    fact = moves[index]
    target = _capture_target_square(moves, index)
    if target is None and not _is_promotion(fact):
        return 0
    if (
        target is not None
        and index > 0
        and _capture_target_square(moves, index - 1) == target
    ):
        return 0

    color = chess.WHITE if fact.mover == Color.WHITE else chess.BLACK
    before = chess.Board(fact.fen_before)
    after = chess.Board(moves[_exchange_sequence_end(moves, index)].fen_after)
    balance_before = material_points(before, chess.WHITE) - material_points(before, chess.BLACK)
    balance_after = material_points(after, chess.WHITE) - material_points(after, chess.BLACK)
    change = balance_after - balance_before
    return change if color == chess.WHITE else -change


def material_delta_against_mover(moves: list[MoveFact], index: int) -> int:
    """Deprecated: pawns the mover lost, ``0`` when material was level or won.

    Kept for callers that only want the loss magnitude; use
    :func:`material_balance_change_for_mover` for the signed figure.
    """
    change = material_balance_change_for_mover(moves, index)
    return -change if change < 0 else 0


def _delivered_checkmate(fact: MoveFact) -> bool:
    """Whether the move delivers checkmate, read from the board after it."""
    try:
        board = chess.Board(fact.fen_after)
    except ValueError:
        return False
    return board.is_checkmate()


def _exchange_sequence_end(moves: list[MoveFact], index: int) -> int:
    """Last ply of the exchange sequence that the capture at ``index`` starts.

    A fixed one-reply window is not enough. Interleaved exchanges alternate
    squares (4.dxe5 Bxf3 5.Qxf3 dxe5), so measuring one reply reports a material
    loss for the side that wins a pawn, while measuring the whole run of captures
    swallows the *next*, unrelated skirmish (9.exd5 Nxd5 11.Nxf7 Kxf7 is two
    exchanges, and only the second one loses material).

    The sequence therefore ends at the first ply after which every square
    captured in it has been recaptured — except when the very next ply captures
    that same square again, which reopens it (10.Nxb5 cxb5 11.Bxb5+ is one
    flurry, so the run is netted as a whole).
    """
    run_end = index
    limit = min(len(moves) - 1, index + MAX_EXCHANGE_SEQUENCE_PLIES)
    while run_end < limit and _capture_target_square(moves, run_end + 1) is not None:
        run_end += 1

    pending: list[int] = []
    end = index
    while True:
        square = _capture_target_square(moves, end)
        if square is not None:
            if square in pending:
                pending.remove(square)
            else:
                pending.append(square)
        if not pending and not (
            square is not None
            and end < run_end
            and _capture_target_square(moves, end + 1) == square
        ):
            return end
        if end >= run_end:
            return run_end
        end += 1


def _san_at_ply(moves: list[MoveFact], ply: int) -> str:
    for fact in moves:
        if fact.ply == ply:
            return fact.san
    return f"ply {ply}"
