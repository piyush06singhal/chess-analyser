"""Tactical event detection.

Every event is derived from **board state** (plus the engine's stored evaluation
and principal variation as corroborating context). Nothing is claimed because a
move was classified as a blunder — the classification is only recorded as
context.

Honesty rules enforced here:

* A ``Confirmed`` event is a structurally verified board fact: a knight really
  does attack two pieces; a bishop really is aligned with a piece and the king
  behind it; a capture really is answered by a recapture.
* A ``Candidate`` event is a hypothesis Caissa cannot prove from one position
  alone (a hanging piece the opponent may simply not take, an overloaded
  defender that may never be exploited). Candidates are labelled as candidates
  everywhere they surface.
* ``engine_context`` records the mover's evaluation before/after, the centipawn
  loss and the classification, so a reviewer can see whether the engine agreed
  that the tactic mattered.
"""

from __future__ import annotations

from enum import Enum

import chess
from pydantic import BaseModel, Field

from argus.analysis.features.extractor import pawn_file_map
from argus.chess_core.models import Color
from argus.intelligence.activity import piece_value
from argus.intelligence.base import (
    Certainty,
    EvidenceSource,
    MoveFact,
    TacticPolicy,
    color_label,
    other,
    severity_from_magnitude,
)


class TacticType(str, Enum):
    """Tactical event types Caissa can label from board state."""

    FORK = "fork"
    DOUBLE_ATTACK = "double_attack"
    PIN = "pin"
    SKEWER = "skewer"
    DISCOVERED_ATTACK = "discovered_attack"
    HANGING_PIECE = "hanging_piece"
    BACK_RANK_WEAKNESS = "back_rank_weakness"
    MATING_THREAT = "mating_threat"
    FORCED_EXCHANGE = "forced_exchange"
    OVERLOADED_DEFENDER = "overloaded_defender"


#: Tactics whose exploitation by the opponent is not certain from one position.
_CANDIDATE_TYPES = {
    TacticType.HANGING_PIECE,
    TacticType.BACK_RANK_WEAKNESS,
    TacticType.OVERLOADED_DEFENDER,
    TacticType.DOUBLE_ATTACK,
}

_DIRECTIONS: dict[int, tuple[tuple[int, int], ...]] = {
    chess.ROOK: ((1, 0), (-1, 0), (0, 1), (0, -1)),
    chess.BISHOP: ((1, 1), (1, -1), (-1, 1), (-1, -1)),
    chess.QUEEN: (
        (1, 0),
        (-1, 0),
        (0, 1),
        (0, -1),
        (1, 1),
        (1, -1),
        (-1, 1),
        (-1, -1),
    ),
}


class TacticalEvent(BaseModel):
    """One detected tactical event."""

    type: TacticType
    ply: int
    move_number: int
    side: Color = Field(description="Side that created the event")
    san: str
    certainty: Certainty
    severity: str
    affected_pieces: list[str] = Field(default_factory=list)
    squares: list[str] = Field(default_factory=list)
    statement: str
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE
    engine_context: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)


class TacticalAnalysis(BaseModel):
    """All tactical events of a game plus simple evidence-derived counts."""

    events: list[TacticalEvent] = Field(default_factory=list)
    confirmed_count: int = 0
    candidate_count: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_side: dict[str, int] = Field(default_factory=dict)
    note: str = (
        "Confirmed events are verified board facts. Candidate events are hypotheses "
        "Caissa cannot prove from a single position and are labelled as candidates."
    )


def _label(piece: chess.Piece, square: int) -> str:
    return f"{piece.symbol()}{chess.square_name(square)}"


def _attacked_enemy_pieces(
    board: chess.Board, color: chess.Color, from_square: int
) -> list[tuple[int, chess.Piece]]:
    """Enemy pieces attacked by the piece standing on ``from_square``."""
    targets: list[tuple[int, chess.Piece]] = []
    for square in board.attacks(from_square):
        piece = board.piece_at(square)
        if piece is not None and piece.color != color:
            targets.append((square, piece))
    return targets


def _newly_attacked_enemy(
    before: chess.Board, after: chess.Board, color: chess.Color
) -> list[tuple[int, chess.Piece, str]]:
    """Enemy pieces attacked after the move but not before, with the attacker.

    Restricted to real attacks by ``color`` so a piece that merely moved into
    the line of fire is not reported as newly attacked by itself.
    """
    newly: list[tuple[int, chess.Piece, str]] = []
    for square in chess.SQUARES:
        piece = after.piece_at(square)
        if piece is None or piece.color == color:
            continue
        if piece.piece_type == chess.KING:
            continue
        was_attacked = bool(before.attackers(color, square))
        if was_attacked:
            continue
        attackers = after.attackers(color, square)
        if not attackers:
            continue
        attacker_square = min(attackers)
        newly.append((square, piece, chess.square_name(attacker_square)))
    return newly


def _undefended(board: chess.Board, square: int, *, attacker: chess.Color) -> bool:
    """True when the piece on ``square`` is not defended by its own side."""
    return not board.attackers(not attacker, square)


def _line_squares(start: int, end: int) -> set[int]:
    """Squares strictly between ``start`` and ``end`` when they share a line."""
    sf, sr = chess.square_file(start), chess.square_rank(start)
    ef, er = chess.square_file(end), chess.square_rank(end)
    df, dr = ef - sf, er - sr
    if df != 0 and dr != 0 and abs(df) != abs(dr):
        return set()
    step_f = 0 if df == 0 else (1 if df > 0 else -1)
    step_r = 0 if dr == 0 else (1 if dr > 0 else -1)
    squares: set[int] = set()
    f, r = sf + step_f, sr + step_r
    while (f, r) != (ef, er):
        if not (0 <= f <= 7 and 0 <= r <= 7):
            return set()
        squares.add(chess.square(f, r))
        f += step_f
        r += step_r
    return squares


def _pin_skewer_triples(board: chess.Board, color: chess.Color) -> set[tuple[int, int, int]]:
    """Aligned (slider, front, back) triples of the given side's sliders."""
    triples: set[tuple[int, int, int]] = set()
    for slider_square in chess.SquareSet(board.occupied_co[color]):
        slider = board.piece_at(slider_square)
        if slider is None or slider.piece_type not in _DIRECTIONS:
            continue
        for df, dr in _DIRECTIONS[slider.piece_type]:
            file = chess.square_file(slider_square) + df
            rank = chess.square_rank(slider_square) + dr
            front: int | None = None
            while 0 <= file <= 7 and 0 <= rank <= 7:
                square = chess.square(file, rank)
                occupant = board.piece_at(square)
                if occupant is not None:
                    if occupant.color == color:
                        break
                    if front is None:
                        front = square
                    else:
                        triples.add((slider_square, front, square))
                        break
                file += df
                rank += dr
    return triples


def _back_rank_weakness(board: chess.Board, victim: chess.Color, attacker: chess.Color) -> str | None:
    """A verified back-rank weakness of ``victim``, or ``None``.

    All of the following must hold, which is what makes this a real back-rank
    situation rather than "a king that has not moved yet":

    1. the victim's king stands on its home rank,
    2. the victim's pawn shield has at least one hole (an escape square is not
       covered by a pawn),
    3. every square next to the king on that rank is occupied or attacked by the
       attacker (the king cannot step along the rank), and
    4. the attacker has a rook or queen that attacks the victim's home rank
       (so the invasion square is real).
    """
    king_square = board.king(victim)
    if king_square is None:
        return None
    home_rank = 0 if victim == chess.WHITE else 7
    if chess.square_rank(king_square) != home_rank:
        return None
    if board.attackers(attacker, king_square):
        return None  # already check — a different situation

    king_file = chess.square_file(king_square)
    victim_pawns = pawn_file_map(board, victim)
    shield = sum(1 for f in (king_file - 1, king_file, king_file + 1) if victim_pawns.get(f))
    if shield >= 3:
        return None  # intact pawn shield: not a back-rank weakness

    for df in (-1, 1):
        file = king_file + df
        if not 0 <= file <= 7:
            continue
        neighbour = chess.square(file, home_rank)
        if board.piece_at(neighbour) is None and not board.attackers(attacker, neighbour):
            return None  # the king can step sideways

    heavy_attacks_rank = any(
        piece.piece_type in (chess.ROOK, chess.QUEEN)
        and any(chess.square_rank(square) == home_rank for square in board.attacks(square_index))
        for square_index, piece in board.piece_map().items()
        if piece.color == attacker
    )
    if not heavy_attacks_rank:
        return None
    return chess.square_name(king_square)


def _overloaded_defenders(board: chess.Board, color: chess.Color) -> list[tuple[int, int, list[int]]]:
    """Own pieces that are the only defender of two or more attacked own pieces."""
    enemy = not color
    attacked = [
        square
        for square in chess.SquareSet(board.occupied_co[color])
        if (piece := board.piece_at(square)) is not None
        and piece.piece_type != chess.KING
        and board.attackers(enemy, square)
    ]
    burdens: dict[int, list[int]] = {}
    for square in attacked:
        defenders = list(board.attackers(color, square))
        if len(defenders) == 1:
            burdens.setdefault(defenders[0], []).append(square)
    result: list[tuple[int, int, list[int]]] = []
    for defender, squares in burdens.items():
        if len(squares) < 2:
            continue
        # Require the burden to be materially meaningful, so a single defender
        # of two cheap pieces does not flood the report with candidates.
        total_value = sum(
            piece_value(board.piece_at(square)) for square in squares
        )
        if total_value >= 6:
            result.append((defender, len(squares), squares))
    return result


def detect_tactical_events(
    moves: list[MoveFact],
    *,
    policy: TacticPolicy | None = None,
) -> TacticalAnalysis:
    """Detect tactical events created by each played move."""
    limits = policy or TacticPolicy()
    events: list[TacticalEvent] = []

    for index, fact in enumerate(moves):
        board_before = chess.Board(fact.fen_before)
        board_after = chess.Board(fact.fen_after)
        try:
            move = chess.Move.from_uci(fact.uci)
        except ValueError:  # pragma: no cover — stored moves are validated
            continue
        mover = fact.mover.value if isinstance(fact.mover, Color) else fact.mover
        color = chess.WHITE if mover == "white" else chess.BLACK
        side = Color.WHITE if mover == "white" else Color.BLACK
        opponent = not color
        next_fact = moves[index + 1] if index + 1 < len(moves) else None

        eval_supported = fact.centipawn_loss is not None and fact.centipawn_loss <= 50
        engine_context = {
            "evaluation_before_cp": fact.eval_before_cp,
            "evaluation_before_mate": fact.eval_before_mate,
            "evaluation_after_cp": fact.eval_after_cp,
            "evaluation_after_mate": fact.eval_after_mate,
            "centipawn_loss": fact.centipawn_loss,
            "classification": fact.classification.value if fact.classification else None,
            "best_move_san": fact.best_move_san,
            "principal_variation": fact.principal_variation,
            "engine_supported": eval_supported,
        }

        def add(
            event_type: TacticType,
            *,
            statement: str,
            severity_magnitude: int,
            affected: list[str],
            squares: list[str],
            evidence: dict,
            certainty: Certainty | None = None,
        ) -> None:
            events.append(
                TacticalEvent(
                    type=event_type,
                    ply=fact.ply,
                    move_number=fact.move_number,
                    side=side,
                    san=fact.san,
                    certainty=certainty
                    or (Certainty.CANDIDATE if event_type in _CANDIDATE_TYPES else Certainty.CONFIRMED),
                    severity=severity_from_magnitude(severity_magnitude, medium=3, high=5),
                    affected_pieces=affected,
                    squares=squares,
                    statement=statement,
                    engine_context=engine_context,
                    evidence=evidence,
                )
            )

        landed = board_after.piece_at(move.to_square)
        moved_targets = _attacked_enemy_pieces(board_after, color, move.to_square)

        # --- fork / double attack by the piece that just moved ----------------
        if landed is not None and moved_targets:
            valuable = [
                (square, piece)
                for square, piece in moved_targets
                if piece_value(piece) >= limits.min_target_value
            ]
            king_attacked = any(
                piece.piece_type == chess.KING for _, piece in moved_targets
            )
            if len(valuable) + (1 if king_attacked else 0) >= limits.fork_min_targets:
                total = sum(piece_value(piece) for _, piece in valuable)
                add(
                    TacticType.FORK,
                    statement=(
                        f"{color_label(side)}'s {fact.san} forks "
                        + " and ".join(_label(p, s) for s, p in valuable)
                        + (" and the king" if king_attacked else "")
                        + "."
                    ),
                    severity_magnitude=total,
                    affected=[_label(p, s) for s, p in valuable],
                    squares=[chess.square_name(s) for s, _ in valuable],
                    evidence={
                        "attacked_squares": [chess.square_name(s) for s, _ in valuable],
                        "forking_piece": _label(landed, move.to_square),
                        "king_attacked": king_attacked,
                    },
                )

        # --- newly attacked enemy pieces (double attacks, discovered attacks) --
        for square, piece, attacker_square in _newly_attacked_enemy(
            board_before, board_after, color
        ):
            if piece_value(piece) < limits.min_target_value:
                continue
            if attacker_square != chess.square_name(move.to_square):
                between = _line_squares(move.from_square, square)
                vacated_on_line = move.from_square in between
                if vacated_on_line:
                    add(
                        TacticType.DISCOVERED_ATTACK,
                        statement=(
                            f"{fact.san} uncovers an attack by the piece on "
                            f"{attacker_square} on {_label(piece, square)}."
                        ),
                        severity_magnitude=piece_value(piece),
                        affected=[_label(piece, square)],
                        squares=[chess.square_name(square), attacker_square],
                        evidence={
                            "discoverer": attacker_square,
                            "target": _label(piece, square),
                            "vacated_square": chess.square_name(move.from_square),
                        },
                        certainty=Certainty.CONFIRMED,
                    )
                elif piece_value(piece) >= 5:
                    add(
                        TacticType.DOUBLE_ATTACK,
                        statement=(
                            f"After {fact.san}, the piece on {attacker_square} also "
                            f"attacks {_label(piece, square)}."
                        ),
                        severity_magnitude=piece_value(piece),
                        affected=[_label(piece, square)],
                        squares=[chess.square_name(square), attacker_square],
                        evidence={"attacker": attacker_square, "target": _label(piece, square)},
                    )
            if _undefended(board_after, square, attacker=color):
                add(
                    TacticType.HANGING_PIECE,
                    statement=(
                        f"{_label(piece, square)} is attacked and undefended after {fact.san}."
                    ),
                    severity_magnitude=piece_value(piece),
                    affected=[_label(piece, square)],
                    squares=[chess.square_name(square)],
                    evidence={
                        "target_value": piece_value(piece),
                        "attacker": attacker_square,
                        "defenders": [],
                    },
                )

        # --- pins and skewers created by the move ----------------------------
        before_triples = _pin_skewer_triples(board_before, color)
        for slider_square, front, back in _pin_skewer_triples(board_after, color):
            if (slider_square, front, back) in before_triples:
                continue
            front_piece = board_after.piece_at(front)
            back_piece = board_after.piece_at(back)
            if front_piece is None or back_piece is None:
                continue
            slider = board_after.piece_at(slider_square)
            if slider is None:
                continue
            if back_piece.piece_type == chess.KING:
                add(
                    TacticType.PIN,
                    statement=(
                        f"The piece on {chess.square_name(front)} is pinned to the king "
                        f"on {chess.square_name(back)} by the {slider.symbol()} on "
                        f"{chess.square_name(slider_square)}."
                    ),
                    severity_magnitude=piece_value(front_piece),
                    affected=[_label(front_piece, front), _label(back_piece, back)],
                    squares=[
                        chess.square_name(slider_square),
                        chess.square_name(front),
                        chess.square_name(back),
                    ],
                    evidence={"kind": "absolute_pin", "pinned": _label(front_piece, front)},
                )
            elif (
                piece_value(front_piece) < piece_value(back_piece)
                and piece_value(front_piece) >= limits.min_target_value
            ):
                # Relative pin. Requires a real piece in front: a pawn aligned
                # with a knight is geometry, not a pin worth reporting.
                add(
                    TacticType.PIN,
                    statement=(
                        f"{_label(front_piece, front)} is pinned against "
                        f"{_label(back_piece, back)} by the piece on "
                        f"{chess.square_name(slider_square)}."
                    ),
                    severity_magnitude=piece_value(back_piece) - piece_value(front_piece),
                    affected=[_label(front_piece, front), _label(back_piece, back)],
                    squares=[
                        chess.square_name(slider_square),
                        chess.square_name(front),
                        chess.square_name(back),
                    ],
                    evidence={"kind": "relative_pin"},
                )
            elif (
                piece_value(front_piece) > piece_value(back_piece)
                and piece_value(back_piece) >= limits.min_target_value
            ):
                # Skewer. Requires a real piece behind: winning a pawn behind a
                # rook is not a skewer worth reporting.
                add(
                    TacticType.SKEWER,
                    statement=(
                        f"{_label(front_piece, front)} is skewered to "
                        f"{_label(back_piece, back)} by the piece on "
                        f"{chess.square_name(slider_square)}."
                    ),
                    severity_magnitude=piece_value(front_piece) - piece_value(back_piece),
                    affected=[_label(front_piece, front), _label(back_piece, back)],
                    squares=[
                        chess.square_name(slider_square),
                        chess.square_name(front),
                        chess.square_name(back),
                    ],
                    evidence={"kind": "skewer"},
                )

        # --- forced exchange: a real capture recaptured on the same square -----
        # An exchange requires the move to have *taken* something: without this
        # check a quiet move onto a square the opponent then captures was
        # reported as a "recapture", which it never was. The traded material
        # must also be worth reporting — a routine pawn trade is not a tactic,
        # so it is left to the material timeline rather than surfaced here.
        captured = board_before.piece_at(move.to_square)
        if next_fact is not None and captured is not None:
            try:
                reply_move = chess.Move.from_uci(next_fact.uci)
            except ValueError:
                reply_move = None
            if reply_move is not None and reply_move.to_square == move.to_square:
                recaptured = board_after.piece_at(reply_move.to_square)
                if (
                    recaptured is not None
                    and recaptured.color == color
                    and piece_value(captured) >= limits.min_target_value
                ):
                    add(
                        TacticType.FORCED_EXCHANGE,
                        statement=(
                            f"{fact.san} won {_label(captured, move.to_square)} and was "
                            f"answered by the recapture {next_fact.san} on the same square."
                        ),
                        severity_magnitude=piece_value(captured),
                        affected=[_label(captured, move.to_square)],
                        squares=[chess.square_name(reply_move.to_square)],
                        evidence={
                            "recapture_ply": next_fact.ply,
                            "recapture_san": next_fact.san,
                            "square": chess.square_name(reply_move.to_square),
                            "traded_value": piece_value(captured),
                        },
                        certainty=Certainty.CONFIRMED,
                    )

        # --- back-rank weakness of the side that just moved into trouble -------
        weakness = _back_rank_weakness(board_after, opponent, color)
        if weakness is not None:
            add(
                TacticType.BACK_RANK_WEAKNESS,
                statement=(
                    f"After {fact.san}, {color_label(other(side))}'s king on {weakness} "
                    "has at most one escape square on its home rank."
                ),
                severity_magnitude=4,
                affected=[f"k{weakness}"],
                squares=[weakness],
                evidence={"king_square": weakness, "attacker": color_label(side)},
            )

        # --- overloaded defenders ---------------------------------------------
        for defender, count, squares in _overloaded_defenders(board_after, color):
            defender_piece = board_after.piece_at(defender)
            if defender_piece is None:
                continue
            add(
                TacticType.OVERLOADED_DEFENDER,
                statement=(
                    f"The piece on {chess.square_name(defender)} is the only defender of "
                    f"{count} attacked pieces."
                ),
                severity_magnitude=count,
                affected=[_label(defender_piece, defender)],
                squares=[chess.square_name(s) for s in squares],
                evidence={
                    "defender": _label(defender_piece, defender),
                    "burdened_squares": [chess.square_name(s) for s in squares],
                },
            )

        # --- mating threat (engine fact) --------------------------------------
        mate_after = fact.mate_after_white
        mate_after_mover = -mate_after if side == Color.BLACK and mate_after is not None else mate_after
        if mate_after_mover is not None and mate_after_mover > 0:
            delivered = board_after.is_checkmate()
            add(
                TacticType.MATING_THREAT,
                statement=(
                    f"{fact.san} delivers checkmate."
                    if delivered
                    else f"{fact.san} creates a forced mate in {mate_after_mover}."
                ),
                severity_magnitude=10,
                affected=[],
                squares=(
                    [chess.square_name(board_after.king(opponent))]
                    if board_after.king(opponent) is not None
                    else []
                ),
                evidence={
                    "mate_distance": mate_after_mover,
                    "delivered": delivered,
                    "source": "engine_evaluation",
                },
                certainty=Certainty.CONFIRMED,
            )

    by_type: dict[str, int] = {}
    by_side: dict[str, int] = {}
    for event in events:
        by_type[event.type.value] = by_type.get(event.type.value, 0) + 1
        by_side[event.side.value] = by_side.get(event.side.value, 0) + 1

    return TacticalAnalysis(
        events=events,
        confirmed_count=len([e for e in events if e.certainty == Certainty.CONFIRMED]),
        candidate_count=len([e for e in events if e.certainty == Certainty.CANDIDATE]),
        by_type=by_type,
        by_side=by_side,
    )


def tactics_by_ply(analysis: TacticalAnalysis) -> dict[int, list[TacticalEvent]]:
    """Group tactical events by ply (used by the category classifier)."""
    grouped: dict[int, list[TacticalEvent]] = {}
    for event in analysis.events:
        grouped.setdefault(event.ply, []).append(event)
    return grouped


__all__ = [
    "TacticType",
    "TacticalAnalysis",
    "TacticalEvent",
    "detect_tactical_events",
    "tactics_by_ply",
]
