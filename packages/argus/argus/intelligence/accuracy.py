"""Caissa accuracy methodology.

**This is not Chess.com accuracy and not Lichess accuracy.** It is Caissa's own
documented metric, and the response always carries the methodology and a
disclaimer so the number can never be quoted as if it came from another site.

Methodology
-----------

1. **Win expectation.** An evaluation is mapped to an expectation in ``[0, 1]``
   with a logistic curve

   ``E = 1 / (1 + exp(-value / SCALE))``  with ``SCALE = 300`` centipawns
   (``AccuracyPolicy.scale_cp``).

   ``value`` is the mover's own evaluation, so ``E`` is "how much of the game
   the mover has". Mate is handled exactly, never through the curve:
   ``mate > 0 → 1.0`` (the mover is mating), ``mate < 0 → 0.0``.

   The scale is Caissa's choice: it is *not* calibrated against any other
   provider, and a different scale would produce different numbers.

2. **Per-move accuracy.** For each evaluated move,

   ``loss = max(0, E_before − E_after) / max(E_before, FLOOR)``  with
   ``FLOOR = 0.5`` (``AccuracyPolicy.denominator_floor``), and

   ``accuracy = 100 × (1 − loss)``.

   ``E_before`` comes from the best-line evaluation of the position and
   ``E_after`` from the played move's evaluation, both already stored by the
   engine pipeline.

   Dividing by the mover's remaining winning chances is what makes the metric
   discriminating: a raw expectation drop can never exceed 0.5 in an equal
   position (0.5 → 0), which would floor every blunder at 50 accuracy. Caissa
   normalizes the drop instead, so throwing away half of an equal game scores
   near zero while a small wobble in a balanced position barely moves the
   number. The floor keeps a position that is already lost (``E_before`` near 0)
   from inflating or collapsing the ratio.

   A move with an unknown evaluation is **not scored** — it is reported as
   unscored rather than guessed.

3. **Excluding decided positions.** Moves played in a position that is already
   decided (``E_before >= 0.95`` or ``<= 0.05``) are excluded from the average
   and counted separately. In a dead-won or dead-lost position the same move
   loses almost no win expectation, so including them would let an already
   decided game dominate the score. Arithmetically the exclusion is neutral, and
   the excluded count is always reported.

4. **Game accuracy** is the arithmetic mean of the per-move accuracies that
   survived 2 and 3. Raw centipawn loss is reported **separately** as its own
   average — the two are different quantities and are never conflated.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import chess
from pydantic import BaseModel, Field

from argus.analysis.classification import MoveClassification
from argus.analysis.phase import GamePhase
from argus.chess_core.models import Color
from argus.intelligence.base import (
    AccuracyPolicy,
    EvidenceSource,
    MoveFact,
    PIECE_POINTS,
    color_label,
)

#: The played move was scored inside the same search as the best line: the
#: comparison is exact (identical search, identical depth, identical tree).
EVAL_SOURCE_SAME_SEARCH = "same_search"
#: The played move's score had to come from a separate search of the position
#: after it. Honest, but it mixes two searches, so a few centipawns of the
#: difference is search noise rather than a real loss. Records say which moves
#: used it, and the side summary counts them.
EVAL_SOURCE_RESULTING_POSITION = "resulting_position"
#: No score for the played move at all — the move is not scored.
EVAL_SOURCE_UNAVAILABLE = "unavailable"

ACCURACY_METHODOLOGY = (
    "Caissa accuracy: logistic win expectation (scale 300cp, mate handled exactly); "
    "per-move loss = max(0, E_before - E_after) / max(E_before, 0.5); "
    "accuracy = 100*(1-loss); game accuracy = arithmetic mean over scored moves with "
    "already-decided positions (E_before >= 0.95 or <= 0.05) excluded and counted "
    "separately. E_before and E_after are taken from ONE engine search wherever "
    "possible — the best line and the move actually played, both scored by the same "
    "search — so a few centipawns of search noise is never reported as a real loss. "
    "Moves outside the MultiPV window fall back to a separate search of the resulting "
    "position, and every such move is counted and reported. Raw centipawn loss is "
    "reported as a separate, different quantity."
)
ACCURACY_DISCLAIMER = (
    "Caissa accuracy is Caissa's own documented metric. It is not Chess.com accuracy, "
    "not Lichess accuracy, and is not calibrated against either."
)

#: Short aliases kept for readability inside this module.
METHODOLOGY = ACCURACY_METHODOLOGY
DISCLAIMER = ACCURACY_DISCLAIMER


class MoveAccuracy(BaseModel):
    """Accuracy of one move, or an explicit record that it was not scored."""

    ply: int
    move_number: int
    side: Color
    san: str
    centipawn_loss: int | None = None
    win_expectation_before: float | None = None
    win_expectation_after: float | None = None
    loss: float | None = Field(
        default=None, description="Normalized 0..1 drop in win expectation"
    )
    accuracy: float | None = Field(default=None, description="0..100, None when not scored")
    scored: bool = False
    excluded: bool = False
    exclusion_reason: str | None = None
    evaluation_source: str = Field(
        default=EVAL_SOURCE_UNAVAILABLE,
        description=(
            "Where E_after came from: 'same_search' (exact) or 'resulting_position' "
            "(approximate — a separate search)."
        ),
    )
    classification: MoveClassification | None = None
    phase: GamePhase | None = None
    material_balance: int | None = Field(
        default=None,
        description="Material balance in pawns from the mover's perspective before the move",
    )
    evaluation_before_white: int | None = None
    evaluation_after_white: int | None = None


class AccuracyGroup(BaseModel):
    """Accuracy for one side within one slice of the game.

    A group only exists when it contains scored moves. Caissa never prints a
    group with no measurement behind it, and marks groups below the policy's
    minimum sample size so a claim can never be made from two moves.
    """

    key: str
    label: str
    scored_moves: int
    accuracy: float | None = None
    average_centipawn_loss: float | None = None
    share_of_loss: float | None = Field(
        default=None,
        description=(
            "This group's share (0..1) of the side's total normalized win-expectation "
            "loss. Always reported as one value per group of a dimension, adding to 1 — "
            "or ``None`` for every group when the side lost nothing at all, because "
            "there is then no loss to attribute."
        ),
    )
    small_sample: bool = False
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE


class SideBreakdown(BaseModel):
    """Where one side's accuracy was spent: by phase, error type and material state."""

    side: Color
    by_phase: list[AccuracyGroup] = Field(default_factory=list)
    by_classification: list[AccuracyGroup] = Field(default_factory=list)
    by_material_state: list[AccuracyGroup] = Field(default_factory=list)


class AccuracyBreakdown(BaseModel):
    """Both sides' accuracy sliced three ways, all from the same scored moves."""

    white: SideBreakdown
    black: SideBreakdown
    note: str


class SideAccuracy(BaseModel):
    """Aggregate accuracy for one side."""

    side: Color
    accuracy: float | None = Field(default=None, description="Mean of scored, non-excluded moves")
    scored_moves: int = 0
    excluded_decided_moves: int = 0
    unscored_moves: int = Field(default=0, description="Moves with no engine evaluation")
    average_centipawn_loss: float | None = Field(
        default=None, description="Raw CPL mean over evaluated moves (a different quantity)"
    )
    best_moves: int = 0
    problem_moves: int = 0
    small_sample: bool = False
    exact_scores: int = Field(
        default=0,
        description="Scored moves compared inside one engine search (exact)",
    )
    approximate_scores: int = Field(
        default=0,
        description=(
            "Scored moves whose comparison used a separate search of the resulting "
            "position (approximate)"
        ),
    )
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE


class AccuracyAnalysis(BaseModel):
    """Both sides' accuracy plus the per-move record behind it."""

    white: SideAccuracy
    black: SideAccuracy
    moves: list[MoveAccuracy] = Field(default_factory=list)
    breakdown: AccuracyBreakdown | None = None
    methodology: str = METHODOLOGY
    disclaimer: str = DISCLAIMER
    scale_cp: int = 300
    note: str = (
        "Unscored moves are reported as unscored. Caissa never imputes an accuracy "
        "value for a move the engine did not evaluate."
    )


def win_expectation(
    cp: int | None, mate: int | None, *, policy: AccuracyPolicy | None = None
) -> float | None:
    """Logistic win expectation for the side the evaluation belongs to.

    ``cp``/``mate`` must already be from that side's perspective. Mate is exact
    (1.0 for the mating side, 0.0 for the side being mated); ``None`` means the
    expectation is unknown.
    """
    limits = policy or AccuracyPolicy()
    if mate is not None:
        return 1.0 if mate > 0 else 0.0
    if cp is None:
        return None
    return 1.0 / (1.0 + math.exp(-cp / limits.scale_cp))


def material_balance_for(fen: str, color: Color) -> int | None:
    """Material balance in pawns from ``color``'s point of view (kings excluded).

    Pure board state, read with the same piece values the rest of the layer uses
    (``PIECE_POINTS``). Returns ``None`` when the position cannot be read, rather
    than assuming level material.
    """
    try:
        board = chess.Board(fen)
    except ValueError:
        return None
    white = sum(
        PIECE_POINTS[piece.symbol().lower()]
        for piece in board.piece_map().values()
        if piece.color == chess.WHITE
    )
    black = sum(
        PIECE_POINTS[piece.symbol().lower()]
        for piece in board.piece_map().values()
        if piece.color == chess.BLACK
    )
    balance = white - black
    return balance if color == Color.WHITE else -balance


def played_expectation(
    fact: MoveFact, *, policy: AccuracyPolicy | None = None
) -> tuple[float | None, str]:
    """Win expectation after the played move, and where that score came from.

    Preference order — the point of the whole module:

    1. the played move's own score from the **same search** that produced the
       best line (exact comparison, no cross-search noise);
    2. otherwise the score of the position after the move from a separate search
       (honest, but approximate — the caller is told);
    3. otherwise nothing: the move is not scored.
    """
    limits = policy or AccuracyPolicy()
    same_search = win_expectation(fact.played_eval_cp, fact.played_eval_mate, policy=limits)
    if same_search is not None and fact.played_eval_source != EVAL_SOURCE_RESULTING_POSITION:
        return same_search, fact.played_eval_source or EVAL_SOURCE_SAME_SEARCH
    resulting = win_expectation(fact.eval_after_cp, fact.eval_after_mate, policy=limits)
    if resulting is not None:
        # A legacy row (analysed before the played score was persisted) has no
        # provenance to read, and its after-eval *is* the separate search.
        return resulting, EVAL_SOURCE_RESULTING_POSITION
    if same_search is not None:
        return same_search, fact.played_eval_source or EVAL_SOURCE_SAME_SEARCH
    return None, EVAL_SOURCE_UNAVAILABLE


def score_move(
    fact: MoveFact, *, policy: AccuracyPolicy | None = None
) -> MoveAccuracy:
    """Score one move (or record explicitly that it could not be scored)."""
    limits = policy or AccuracyPolicy()
    before = win_expectation(fact.eval_before_cp, fact.eval_before_mate, policy=limits)
    after, source = played_expectation(fact, policy=limits)
    record = MoveAccuracy(
        ply=fact.ply,
        move_number=fact.move_number,
        side=fact.mover,
        san=fact.san,
        centipawn_loss=fact.centipawn_loss,
        win_expectation_before=before,
        win_expectation_after=after,
        evaluation_source=source,
        classification=fact.classification,
        phase=fact.phase,
        material_balance=material_balance_for(fact.fen_before, fact.mover),
        evaluation_before_white=fact.eval_before_white,
        evaluation_after_white=fact.eval_after_white,
    )
    if before is None or after is None:
        return record

    denominator = max(before, limits.denominator_floor)
    loss = max(0.0, before - after) / denominator if denominator > 0 else 0.0
    loss = min(1.0, loss)
    record.loss = round(loss, 6)
    record.accuracy = round(max(0.0, min(100.0, 100.0 * (1.0 - loss))), 2)
    record.scored = True
    if before >= limits.decided_win_expectation:
        record.excluded = True
        record.exclusion_reason = "position was already decided for the mover"
    elif before <= 1.0 - limits.decided_win_expectation:
        record.excluded = True
        record.exclusion_reason = "position was already decided against the mover"
    return record


def analyse_accuracy(
    moves: list[MoveFact], *, policy: AccuracyPolicy | None = None
) -> AccuracyAnalysis:
    """Compute Caissa accuracy for both sides of a game."""
    limits = policy or AccuracyPolicy()
    records = [score_move(fact, policy=limits) for fact in moves]

    def counted_for(color: Color) -> list[MoveAccuracy]:
        return [
            record
            for record in records
            if record.side == color and record.scored and not record.excluded
        ]

    def side_summary(color: Color) -> SideAccuracy:
        own = [record for record in records if record.side == color]
        scored = [record for record in own if record.scored]
        counted = counted_for(color)
        accuracies = [record.accuracy for record in counted if record.accuracy is not None]
        losses = [
            record.centipawn_loss
            for record in own
            if record.centipawn_loss is not None
        ]
        return SideAccuracy(
            side=color,
            accuracy=round(sum(accuracies) / len(accuracies), 2) if accuracies else None,
            scored_moves=len(counted),
            excluded_decided_moves=len([r for r in scored if r.excluded]),
            unscored_moves=len([r for r in own if not r.scored]),
            average_centipawn_loss=round(sum(losses) / len(losses), 2) if losses else None,
            best_moves=len(
                [r for r in counted if r.accuracy is not None and r.accuracy >= 99.0]
            ),
            problem_moves=len(
                [
                    r
                    for r in own
                    if r.centipawn_loss is not None and r.centipawn_loss > 100
                ]
            ),
            small_sample=len(accuracies) < limits.minimum_scored_moves,
            exact_scores=len(
                [r for r in counted if r.evaluation_source == EVAL_SOURCE_SAME_SEARCH]
            ),
            approximate_scores=len(
                [
                    r
                    for r in counted
                    if r.evaluation_source == EVAL_SOURCE_RESULTING_POSITION
                ]
            ),
        )

    breakdown = AccuracyBreakdown(
        white=_side_breakdown(Color.WHITE, counted_for(Color.WHITE), limits),
        black=_side_breakdown(Color.BLACK, counted_for(Color.BLACK), limits),
        note=(
            "Each slice is computed from the same scored moves as the game total, so the "
            "slices always add up to it. A slice is reported only when it contains at "
            f"least one scored move, and is flagged when it holds fewer than "
            f"{limits.minimum_group_moves}."
        ),
    )

    return AccuracyAnalysis(
        white=side_summary(Color.WHITE),
        black=side_summary(Color.BLACK),
        moves=records,
        breakdown=breakdown,
        scale_cp=limits.scale_cp,
        note=(
            "Unscored moves are reported as unscored. Caissa never imputes an accuracy "
            f"value for a move the engine did not evaluate. Positions already decided "
            f"(win expectation >= {limits.decided_win_expectation}) are excluded from the mean."
        ),
    )


# --- breakdown --------------------------------------------------------------

_PHASE_ORDER: tuple[GamePhase, ...] = (
    GamePhase.OPENING,
    GamePhase.MIDDLEGAME,
    GamePhase.ENDGAME,
)

_CLASSIFICATION_ORDER: tuple[MoveClassification, ...] = (
    MoveClassification.BLUNDER,
    MoveClassification.MISTAKE,
    MoveClassification.INACCURATE,
    MoveClassification.GOOD,
    MoveClassification.EXCELLENT,
    MoveClassification.BEST,
    MoveClassification.BRILLIANT,
)


def _material_band(balance: int, limits: AccuracyPolicy) -> tuple[str, str]:
    """Caissa material-state band for a balance in pawns (mover perspective)."""
    if balance <= -limits.material_band_major:
        return "behind_major", f"Behind by {limits.material_band_major}+ pawns"
    if balance < 0:
        return "behind_minor", f"Behind by {abs(balance)} pawn(s)"
    if balance == 0:
        return "level", "Material level"
    if balance < limits.material_band_major:
        return "ahead_minor", f"Ahead by {balance} pawn(s)"
    return "ahead_major", f"Ahead by {limits.material_band_major}+ pawns"


def _group(
    key: str,
    label: str,
    records: list[MoveAccuracy],
    total_loss: float,
    limits: AccuracyPolicy,
) -> AccuracyGroup | None:
    """Build one group, or ``None`` when there is nothing measured in it."""
    if not records:
        return None
    accuracies = [r.accuracy for r in records if r.accuracy is not None]
    if not accuracies:
        return None
    losses = [r.centipawn_loss for r in records if r.centipawn_loss is not None]
    loss_sum = sum(r.loss for r in records if r.loss is not None)
    return AccuracyGroup(
        key=key,
        label=label,
        scored_moves=len(accuracies),
        accuracy=round(sum(accuracies) / len(accuracies), 2),
        average_centipawn_loss=round(sum(losses) / len(losses), 2) if losses else None,
        share_of_loss=round(loss_sum / total_loss, 4) if total_loss > 0 else None,
        small_sample=len(accuracies) < limits.minimum_group_moves,
    )


def _side_breakdown(
    color: Color, records: list[MoveAccuracy], limits: AccuracyPolicy
) -> SideBreakdown:
    total_loss = sum(r.loss for r in records if r.loss is not None)
    by_phase = _groups(
        records,
        total_loss,
        limits,
        order=[(phase.value, phase.value.title()) for phase in _PHASE_ORDER],
        key_of=lambda record: record.phase.value if record.phase else None,
    )
    by_classification = _groups(
        records,
        total_loss,
        limits,
        order=[
            (item.value, _class_label(item)) for item in _CLASSIFICATION_ORDER
        ],
        key_of=lambda record: record.classification.value if record.classification else None,
    )
    by_material = _groups(
        records,
        total_loss,
        limits,
        order=[
            ("behind_major", f"Behind by {limits.material_band_major}+ pawns"),
            ("behind_minor", "Behind by 1+ pawn(s)"),
            ("level", "Material level"),
            ("ahead_minor", "Ahead by 1+ pawn(s)"),
            ("ahead_major", f"Ahead by {limits.material_band_major}+ pawns"),
        ],
        key_of=lambda record: (
            _material_band(record.material_balance, limits)[0]
            if record.material_balance is not None
            else None
        ),
    )
    return SideBreakdown(
        side=color,
        by_phase=by_phase,
        by_classification=by_classification,
        by_material_state=by_material,
    )


def _groups(
    records: list[MoveAccuracy],
    total_loss: float,
    limits: AccuracyPolicy,
    *,
    order: list[tuple[str, str]],
    key_of: Callable[[MoveAccuracy], str | None],
) -> list[AccuracyGroup]:
    """Group records onto a fixed, documented order; skip empty groups."""
    buckets: dict[str, list[MoveAccuracy]] = {}
    for record in records:
        key = key_of(record)
        if key is None:
            continue
        buckets.setdefault(key, []).append(record)
    groups: list[AccuracyGroup] = []
    for key, label in order:
        group = _group(key, label, buckets.get(key, []), total_loss, limits)
        if group is not None:
            groups.append(group)
    return groups


def _class_label(classification: MoveClassification) -> str:
    return classification.value.replace("_", " ").title()


def accuracy_statement(side: SideAccuracy) -> str:
    """A factual sentence about one side's accuracy (no commentary)."""
    if side.accuracy is None:
        return f"{color_label(side.side)} has no scored moves to compute accuracy from."
    sample = " (small sample)" if side.small_sample else ""
    return (
        f"{color_label(side.side)} accuracy {side.accuracy:.2f} over "
        f"{side.scored_moves} scored moves{sample}, with "
        f"{side.excluded_decided_moves} already-decided move(s) excluded."
    )
