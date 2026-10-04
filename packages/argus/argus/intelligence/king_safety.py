"""King-safety analysis.

For every ply the module measures both kings from the board — castling state,
pawn shield, open files near the king, enemy pressure on the king zone, checks,
king mobility — and emits **structured events** when a measurement changes.

The result is deliberately not prose: an event says "the white king's pawn
shield went from 3 to 1 after 12.exd5", together with the numbers behind it. A
later layer (or a human) decides what it means. Mating threats are reported only
when the **engine** says so (a mate score in the analysis), never guessed from
the board.
"""

from __future__ import annotations

from enum import Enum

import chess
from pydantic import BaseModel, Field

from argus.analysis.features.extractor import extract_position_features, pawn_file_map
from argus.chess_core.models import Color
from argus.intelligence.base import (
    Certainty,
    EvidenceSource,
    KingSafetyPolicy,
    MoveFact,
    color_label,
    other,
    severity_from_magnitude,
)


class KingSafetyEventType(str, Enum):
    """Structured king-safety events."""

    CASTLED = "castled"
    CASTLING_RIGHTS_LOST = "castling_rights_lost"
    PAWN_SHIELD_REDUCED = "pawn_shield_reduced"
    OPEN_FILE_NEAR_KING = "open_file_near_king"
    KING_EXPOSED = "king_exposed"
    PIECE_PRESSURE_NEAR_KING = "piece_pressure_near_king"
    CHECK_GIVEN = "check_given"
    KING_MOBILITY_LIMITED = "king_mobility_limited"
    MATING_NET = "mating_net"


class KingSafetySide(BaseModel):
    """King-safety measurements for one side in one position."""

    king_square: str
    on_home_rank: bool
    castled: bool
    castling_rights: bool
    pawn_shield: int
    shield_holes: int
    open_files_near_king: list[str] = Field(default_factory=list)
    semi_open_files_near_king: list[str] = Field(default_factory=list)
    enemy_attackers_near_king: int = 0
    king_mobility: int = 0
    in_check: bool = False
    mate_distance: int | None = Field(
        default=None, description="Engine mate distance against this side (None when no mate)"
    )
    score: int = Field(description="Caissa-derived weighted exposure score (higher = more exposed)")
    severity: str = "low"


class KingSafetySnapshot(BaseModel):
    """Both sides' king safety after one ply."""

    ply: int
    move_number: int
    white: KingSafetySide
    black: KingSafetySide


class KingSafetyEvent(BaseModel):
    """One structured king-safety event."""

    type: KingSafetyEventType
    ply: int
    move_number: int
    side: Color = Field(description="Side whose king safety the event is about")
    severity: str
    statement: str
    certainty: Certainty = Certainty.CONFIRMED
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE
    evidence: dict = Field(default_factory=dict)


class KingSafetyAnalysis(BaseModel):
    """King-safety history plus the most exposed moment per side."""

    snapshots: list[KingSafetySnapshot] = Field(default_factory=list)
    events: list[KingSafetyEvent] = Field(default_factory=list)
    worst_white: KingSafetySnapshot | None = None
    worst_black: KingSafetySnapshot | None = None
    note: str = (
        "King-safety events are board measurements. Mating threats are reported only "
        "when the engine evaluation contains a mate score."
    )


def king_zone(board: chess.Board, color: chess.Color) -> list[int]:
    """King square plus its up-to-eight neighbours."""
    king = board.king(color)
    if king is None:
        return []
    file, rank = chess.square_file(king), chess.square_rank(king)
    squares: list[int] = []
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            f, r = file + df, rank + dr
            if 0 <= f <= 7 and 0 <= r <= 7:
                squares.append(chess.square(f, r))
    return squares


def _king_files(board: chess.Board, color: chess.Color) -> list[str]:
    """File names of the king and its neighbours on the same rank."""
    king = board.king(color)
    if king is None:
        return []
    file = chess.square_file(king)
    return [
        chess.FILE_NAMES[f] for f in (file - 1, file, file + 1) if 0 <= f <= 7
    ]


def _files_near_king(board: chess.Board, color: chess.Color) -> tuple[list[str], list[str]]:
    own = pawn_file_map(board, color)
    enemy = pawn_file_map(board, not color)
    open_files: list[str] = []
    semi_open: list[str] = []
    for name in _king_files(board, color):
        file = chess.FILE_NAMES.index(name)
        if file not in own and file not in enemy:
            open_files.append(name)
        elif file not in own:
            semi_open.append(name)
    return open_files, semi_open


def _pressure(board: chess.Board, color: chess.Color) -> int:
    """Distinct enemy pieces attacking squares in ``color``'s king zone."""
    attackers: set[int] = set()
    for square in king_zone(board, color):
        attackers |= set(board.attackers(not color, square))
    return len(attackers)


def king_safety_for(
    board: chess.Board,
    color: chess.Color,
    *,
    policy: KingSafetyPolicy | None = None,
    mate_distance: int | None = None,
) -> KingSafetySide:
    """King-safety measurements for one side in a position."""
    limits = policy or KingSafetyPolicy()
    features = extract_position_features(board)
    is_white = color == chess.WHITE
    king = board.king(color)
    king_square = chess.square_name(king) if king is not None else "—"
    home_rank = 0 if is_white else 7
    on_home_rank = king is not None and chess.square_rank(king) == home_rank
    castled = features.white_castled if is_white else features.black_castled
    rights = board.clean_castling_rights()
    house_rights = (
        bool(rights & (chess.BB_A1 | chess.BB_H1))
        if is_white
        else bool(rights & (chess.BB_A8 | chess.BB_H8))
    )
    shield = features.king_safety_white if is_white else features.king_safety_black
    open_files, semi_open = _files_near_king(board, color)
    pressure = _pressure(board, color)
    in_check = features.white_in_check if is_white else features.black_in_check

    probe = board
    if board.turn != color:
        probe = board.copy(stack=False)
        probe.turn = color
    mobility = len([move for move in probe.legal_moves if move.from_square == king]) if king is not None else 0

    score = (
        max(0, 3 - shield) * limits.missing_shield_pawn_weight
        + len(open_files) * limits.open_file_weight
        + pressure * limits.attacker_weight
        + (limits.check_weight if in_check else 0)
        + (limits.limited_mobility_weight if mobility <= 1 else 0)
    )
    severity = severity_from_magnitude(score, medium=limits.medium_score, high=limits.high_score)

    return KingSafetySide(
        king_square=king_square,
        on_home_rank=on_home_rank,
        castled=castled,
        castling_rights=house_rights,
        pawn_shield=shield,
        shield_holes=max(0, 3 - shield),
        open_files_near_king=open_files,
        semi_open_files_near_king=semi_open,
        enemy_attackers_near_king=pressure,
        king_mobility=mobility,
        in_check=in_check,
        mate_distance=mate_distance,
        score=score,
        severity=severity,
    )


def build_king_safety(
    moves: list[MoveFact],
    *,
    initial_position: str,
    policy: KingSafetyPolicy | None = None,
) -> KingSafetyAnalysis:
    """Measure king safety across a game and emit structured events."""
    limits = policy or KingSafetyPolicy()
    if not moves:
        return KingSafetyAnalysis()

    snapshots: list[KingSafetySnapshot] = []
    events: list[KingSafetyEvent] = []

    initial = chess.Board(initial_position)
    previous: dict[Color, KingSafetySide] = {
        Color.WHITE: king_safety_for(initial, chess.WHITE, policy=limits),
        Color.BLACK: king_safety_for(initial, chess.BLACK, policy=limits),
    }

    for fact in moves:
        board = chess.Board(fact.fen_after)
        # Mate distances come from the engine evaluation, never from the board.
        mate_white = fact.mate_after_white
        white = king_safety_for(
            board,
            chess.WHITE,
            policy=limits,
            mate_distance=None if mate_white is None or mate_white > 0 else mate_white,
        )
        black = king_safety_for(
            board,
            chess.BLACK,
            policy=limits,
            mate_distance=None if mate_white is None or mate_white < 0 else -mate_white,
        )
        snapshots.append(
            KingSafetySnapshot(ply=fact.ply, move_number=fact.move_number, white=white, black=black)
        )

        def add(
            event_type: KingSafetyEventType,
            side: Color,
            statement: str,
            evidence: dict,
            severity: str,
            certainty: Certainty = Certainty.CONFIRMED,
        ) -> None:
            events.append(
                KingSafetyEvent(
                    type=event_type,
                    ply=fact.ply,
                    move_number=fact.move_number,
                    side=side,
                    severity=severity,
                    statement=statement,
                    certainty=certainty,
                    evidence={
                        "fen_before": fact.fen_before,
                        "fen_after": fact.fen_after,
                        "uci": fact.uci,
                        **evidence,
                    },
                )
            )

        for color, current in ((Color.WHITE, white), (Color.BLACK, black)):
            prior = previous[color]
            if current.castled and not prior.castled:
                add(
                    KingSafetyEventType.CASTLED,
                    color,
                    f"{color_label(color)} castled ({fact.san}); the king is on {current.king_square}.",
                    {"king_square": current.king_square},
                    "low",
                )
            if prior.castling_rights and not current.castling_rights and not current.castled:
                add(
                    KingSafetyEventType.CASTLING_RIGHTS_LOST,
                    color,
                    f"{color_label(color)} lost castling rights after {fact.san}.",
                    {"king_square": current.king_square},
                    "low",
                )
            if current.pawn_shield < prior.pawn_shield:
                add(
                    KingSafetyEventType.PAWN_SHIELD_REDUCED,
                    color,
                    (
                        f"{color_label(color)}'s pawn shield around {current.king_square} went "
                        f"from {prior.pawn_shield} to {current.pawn_shield} after {fact.san}."
                    ),
                    {
                        "before": prior.pawn_shield,
                        "after": current.pawn_shield,
                        "king_square": current.king_square,
                    },
                    severity_from_magnitude(prior.pawn_shield - current.pawn_shield, medium=1, high=2),
                )
            new_open = [f for f in current.open_files_near_king if f not in prior.open_files_near_king]
            if new_open:
                add(
                    KingSafetyEventType.OPEN_FILE_NEAR_KING,
                    color,
                    (
                        f"File(s) {', '.join(new_open)} next to {color_label(color)}'s king "
                        f"on {current.king_square} became fully open after {fact.san}."
                    ),
                    {"files": new_open, "king_square": current.king_square},
                    "medium",
                )
            new_semi = [
                f
                for f in current.semi_open_files_near_king
                if f not in prior.semi_open_files_near_king
            ]
            if new_semi:
                add(
                    KingSafetyEventType.OPEN_FILE_NEAR_KING,
                    color,
                    (
                        f"File(s) {', '.join(new_semi)} next to {color_label(color)}'s king "
                        f"became semi-open after {fact.san}."
                    ),
                    {"files": new_semi, "semi_open": True, "king_square": current.king_square},
                    "low",
                )

        # Events about the opponent: pressure applied by the mover, checks given.
        victim = other(fact.mover)
        victim_now = white if victim == Color.WHITE else black
        victim_before = previous[victim]
        victim_severity = severity_from_magnitude(
            victim_now.score, medium=limits.medium_score, high=limits.high_score
        )
        if victim_now.enemy_attackers_near_king > victim_before.enemy_attackers_near_king:
            add(
                KingSafetyEventType.PIECE_PRESSURE_NEAR_KING,
                victim,
                (
                    f"After {fact.san}, {victim_now.enemy_attackers_near_king} enemy pieces "
                    f"attack {color_label(victim)}'s king zone around "
                    f"{victim_now.king_square} (was "
                    f"{victim_before.enemy_attackers_near_king})."
                ),
                {
                    "before": victim_before.enemy_attackers_near_king,
                    "after": victim_now.enemy_attackers_near_king,
                    "attacker": fact.mover.value,
                    "king_zone": [
                        chess.square_name(s) for s in king_zone(board, chess.WHITE if victim == Color.WHITE else chess.BLACK)
                    ],
                },
                victim_severity,
            )
        if victim_now.in_check:
            add(
                KingSafetyEventType.CHECK_GIVEN,
                victim,
                f"{fact.san} gives check to the {color_label(victim).lower()} king on {victim_now.king_square}.",
                {"king_square": victim_now.king_square, "checker_ply": fact.ply},
                "medium",
            )
        if victim_now.king_mobility <= 1 and victim_now.score >= limits.medium_score:
            add(
                KingSafetyEventType.KING_MOBILITY_LIMITED,
                victim,
                (
                    f"{color_label(victim)}'s king on {victim_now.king_square} has "
                    f"{victim_now.king_mobility} legal move(s) after {fact.san}."
                ),
                {"king_mobility": victim_now.king_mobility, "score": victim_now.score},
                victim_severity,
            )
        if not victim_now.on_home_rank and victim_now.score >= limits.medium_score:
            add(
                KingSafetyEventType.KING_EXPOSED,
                victim,
                (
                    f"{color_label(victim)}'s king left its home rank and stands on "
                    f"{victim_now.king_square} with exposure score {victim_now.score}."
                ),
                {"king_square": victim_now.king_square, "score": victim_now.score},
                victim_severity,
            )

        opponent_mate = black if fact.mover == Color.WHITE else white
        if opponent_mate.mate_distance is not None and opponent_mate.mate_distance < 0:
            add(
                KingSafetyEventType.MATING_NET,
                victim,
                (
                    f"The engine evaluation after {fact.san} contains a forced mate against "
                    f"{color_label(victim)} in {abs(opponent_mate.mate_distance)}."
                ),
                {"mate_distance": opponent_mate.mate_distance, "source": "engine_evaluation"},
                "high",
            )

        previous[Color.WHITE] = white
        previous[Color.BLACK] = black

    worst_white = max(snapshots, key=lambda snap: snap.white.score)
    worst_black = max(snapshots, key=lambda snap: snap.black.score)
    return KingSafetyAnalysis(
        snapshots=snapshots, events=events, worst_white=worst_white, worst_black=worst_black
    )


def events_by_ply(analysis: KingSafetyAnalysis) -> dict[int, list[KingSafetyEvent]]:
    """Group king-safety events by ply (used by the category classifier)."""
    grouped: dict[int, list[KingSafetyEvent]] = {}
    for event in analysis.events:
        grouped.setdefault(event.ply, []).append(event)
    return grouped
