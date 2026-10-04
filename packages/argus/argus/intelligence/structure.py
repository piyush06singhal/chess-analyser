"""Pawn-structure analysis.

The module records **objective structural features** of both pawn chains at
every ply and the changes those features undergo. It deliberately does not
label anything "good" or "bad": an isolated pawn is a doubled-edged structural
fact, and whether it was actually a problem is decided elsewhere, from the
engine evaluation (see :mod:`argus.intelligence.positional`).

Reused, not re-derived: isolated/doubled/backward/passed counts come from the
Phase 1 feature extractor (``argus.analysis.features.extractor``), so the
intelligence layer and the stored position features cannot disagree.

New here (still pure board state):

* pawn islands
* connected passed pawns
* open and semi-open files
* pawn breaks (a pawn move that challenges an enemy pawn on an adjacent file,
  or creates a passed pawn)
* structural change events between consecutive plies
"""

from __future__ import annotations

from typing import Literal

import chess
from pydantic import BaseModel, Field

from argus.analysis.features.extractor import (
    extract_position_features,
    pawn_file_map,
)
from argus.chess_core.models import Color
from argus.intelligence.base import (
    Certainty,
    EvidenceSource,
    MoveFact,
    chess_color,
    color_label,
    severity_from_magnitude,
)

PawnEventType = Literal[
    "isolated_pawn_created",
    "isolated_pawn_removed",
    "doubled_pawns_created",
    "doubled_pawns_removed",
    "backward_pawn_created",
    "passed_pawn_created",
    "passed_pawn_lost",
    "pawn_island_change",
    "open_file_created",
    "semi_open_file_created",
    "pawn_break",
]


class PawnStructureSide(BaseModel):
    """Structural features of one side's pawn chain."""

    pawns: int
    islands: int
    isolated: int
    doubled: int
    backward: int
    passed: int
    connected_passed: int
    open_files: list[str] = Field(default_factory=list)
    semi_open_files: list[str] = Field(default_factory=list)
    pawn_breaks: list[str] = Field(default_factory=list, description="Squares a pawn can break on")


class PawnStructureSnapshot(BaseModel):
    """Both sides' pawn structure after one ply."""

    ply: int
    move_number: int
    white: PawnStructureSide
    black: PawnStructureSide


class PawnStructureEvent(BaseModel):
    """A structural change caused by one move (a feature, never a verdict)."""

    type: PawnEventType
    ply: int
    move_number: int
    side: Color = Field(description="Side whose structure changed")
    san: str
    severity: str
    delta: int
    certainty: Certainty = Certainty.CONFIRMED
    source: EvidenceSource = EvidenceSource.ARGUS_DERIVED_FEATURE
    statement: str
    evidence: dict = Field(default_factory=dict)


class PawnStructureAnalysis(BaseModel):
    """Complete pawn-structure history of a game."""

    snapshots: list[PawnStructureSnapshot] = Field(default_factory=list)
    events: list[PawnStructureEvent] = Field(default_factory=list)
    final_white: PawnStructureSide | None = None
    final_black: PawnStructureSide | None = None
    note: str = (
        "Structural features are objective board facts. A feature becomes an error "
        "candidate only where the engine evaluation supports it (see positional analysis)."
    )


def _islands(files: dict[int, list[int]]) -> int:
    """Count contiguous groups of files that contain at least one pawn."""
    if not files:
        return 0
    ordered = sorted(files)
    islands = 1
    for previous, current in zip(ordered, ordered[1:]):
        if current != previous + 1:
            islands += 1
    return islands


def _connected_passed(board: chess.Board, color: chess.Color, files: dict[int, list[int]]) -> int:
    """Passed pawns supported by (or supporting) another passed pawn on a neighbour file."""
    features = extract_position_features(board)
    passed = features.passed_pawns_white if color == chess.WHITE else features.passed_pawns_black
    if passed < 2:
        return 0
    opponent_pawns = chess.SquareSet(board.pawns & board.occupied_co[not color])
    direction = 1 if color == chess.WHITE else -1
    square_set = chess.SquareSet(board.pawns & board.occupied_co[color])
    connected = 0
    for square in square_set:
        file = chess.square_file(square)
        rank = chess.square_rank(square)
        is_passed = True
        for df in (-1, 0, 1):
            f = file + df
            if not 0 <= f <= 7:
                continue
            for step in range(1, 8):
                r = rank + direction * step
                if not 0 <= r <= 7:
                    break
                if chess.square(f, r) in opponent_pawns:
                    is_passed = False
                    break
            if not is_passed:
                break
        if not is_passed:
            continue
        for df in (-1, 1):
            neighbour_file = file + df
            if not 0 <= neighbour_file <= 7:
                continue
            if any(abs(neighbour_rank - rank) <= 1 for neighbour_rank in files.get(neighbour_file, [])):
                connected += 1
                break
    return connected


def _open_files(board: chess.Board, color: chess.Color) -> tuple[list[str], list[str]]:
    """Open files (no pawns at all) and semi-open files (no pawn of ``color``)."""
    own = pawn_file_map(board, color)
    enemy = pawn_file_map(board, not color)
    open_files: list[str] = []
    semi_open: list[str] = []
    for file in range(8):
        name = chess.FILE_NAMES[file]
        if file not in own and file not in enemy:
            open_files.append(name)
        elif file not in own and file in enemy:
            semi_open.append(name)
    return open_files, semi_open


def _pawn_breaks(board: chess.Board, color: chess.Color, files: dict[int, list[int]]) -> list[str]:
    """Forward squares where one of the side's pawns can challenge an enemy pawn.

    A break is a pawn advance whose destination attacks an enemy pawn on an
    adjacent file. Only the destination square is reported — this is a
    structural opportunity, not a recommendation.
    """
    direction = 1 if color == chess.WHITE else -1
    enemy_pawns = chess.SquareSet(board.pawns & board.occupied_co[not color])
    breaks: list[str] = []
    for file, ranks in files.items():
        for rank in ranks:
            forward = rank + direction
            if not 0 <= forward <= 7:
                continue
            destination = chess.square(file, forward)
            if board.piece_at(destination) is not None:
                continue
            attack_rank = forward + direction
            if not 0 <= attack_rank <= 7:
                continue
            attacks: list[int] = []
            for df in (-1, 1):
                f = file + df
                if not 0 <= f <= 7:
                    continue
                hit = chess.square(f, attack_rank)
                if hit in enemy_pawns:
                    attacks.append(hit)
            if attacks:
                breaks.append(chess.square_name(destination))
    return sorted(breaks)


def pawn_structure_for(board: chess.Board, color: chess.Color) -> PawnStructureSide:
    """Structural summary for one side in a position."""
    features = extract_position_features(board)
    files = pawn_file_map(board, color)
    open_files, semi_open = _open_files(board, color)
    return PawnStructureSide(
        pawns=sum(len(ranks) for ranks in files.values()),
        islands=_islands(files),
        isolated=features.isolated_pawns_white
        if color == chess.WHITE
        else features.isolated_pawns_black,
        doubled=features.doubled_pawns_white
        if color == chess.WHITE
        else features.doubled_pawns_black,
        backward=features.backward_pawns_white
        if color == chess.WHITE
        else features.backward_pawns_black,
        passed=features.passed_pawns_white
        if color == chess.WHITE
        else features.passed_pawns_black,
        connected_passed=_connected_passed(board, color, files),
        open_files=open_files,
        semi_open_files=semi_open,
        pawn_breaks=_pawn_breaks(board, color, files),
    )


def _event(
    fact: MoveFact,
    *,
    event_type: PawnEventType,
    side: Color,
    before,
    after,
    statement: str,
    delta: int,
    evidence: dict | None = None,
) -> PawnStructureEvent:
    return PawnStructureEvent(
        type=event_type,
        ply=fact.ply,
        move_number=fact.move_number,
        side=side,
        san=fact.san,
        severity=severity_from_magnitude(abs(delta), medium=1, high=2),
        delta=delta,
        statement=statement,
        evidence={
            "fen_before": fact.fen_before,
            "fen_after": fact.fen_after,
            "before": before,
            "after": after,
            **(evidence or {}),
        },
    )


def build_pawn_structure(moves: list[MoveFact], *, initial_position: str) -> PawnStructureAnalysis:
    """Track pawn structure across a game and record the structural changes."""
    if not moves:
        return PawnStructureAnalysis()

    snapshots: list[PawnStructureSnapshot] = []
    events: list[PawnStructureEvent] = []
    previous: dict[Color, PawnStructureSide] = {
        Color.WHITE: pawn_structure_for(chess.Board(initial_position), chess.WHITE),
        Color.BLACK: pawn_structure_for(chess.Board(initial_position), chess.BLACK),
    }

    _SIDE_FIELDS = (
        ("islands", "pawn_island_change"),
        ("isolated", "isolated_pawn_created"),
        ("doubled", "doubled_pawns_created"),
        ("backward", "backward_pawn_created"),
        ("passed", "passed_pawn_created"),
    )

    for fact in moves:
        board_after = chess.Board(fact.fen_after)
        white = pawn_structure_for(board_after, chess.WHITE)
        black = pawn_structure_for(board_after, chess.BLACK)
        snapshots.append(
            PawnStructureSnapshot(
                ply=fact.ply, move_number=fact.move_number, white=white, black=black
            )
        )

        # Only the side that moved can change its own pawn structure materially,
        # but a capture by the mover can remove an enemy pawn too, so both sides
        # are compared and attributed to the mover's move.
        for color, current in ((Color.WHITE, white), (Color.BLACK, black)):
            prior = previous[color]
            for field, positive_type in _SIDE_FIELDS:
                old_value = getattr(prior, field)
                new_value = getattr(current, field)
                if new_value == old_value:
                    continue
                if field == "islands" and new_value < old_value:
                    continue  # fewer islands is a structural simplification
                event_type: PawnEventType = positive_type
                if field == "isolated" and new_value < old_value:
                    event_type = "isolated_pawn_removed"
                elif field == "doubled" and new_value < old_value:
                    event_type = "doubled_pawns_removed"
                elif field == "passed" and new_value < old_value:
                    event_type = "passed_pawn_lost"
                events.append(
                    _event(
                        fact,
                        event_type=event_type,
                        side=color,
                        before=old_value,
                        after=new_value,
                        delta=new_value - old_value,
                        statement=(
                            f"{color_label(color)}'s {field.replace('_', ' ')} went from "
                            f"{old_value} to {new_value} after {fact.san}."
                        ),
                    )
                )

            new_open = [f for f in current.open_files if f not in prior.open_files]
            if new_open:
                events.append(
                    _event(
                        fact,
                        event_type="open_file_created",
                        side=color,
                        before=prior.open_files,
                        after=current.open_files,
                        delta=len(new_open),
                        statement=(
                            f"File(s) {', '.join(new_open)} became fully open to "
                            f"{color_label(color)} after {fact.san}."
                        ),
                    )
                )
            new_semi = [f for f in current.semi_open_files if f not in prior.semi_open_files]
            if new_semi:
                events.append(
                    _event(
                        fact,
                        event_type="semi_open_file_created",
                        side=color,
                        before=prior.semi_open_files,
                        after=current.semi_open_files,
                        delta=len(new_semi),
                        statement=(
                            f"File(s) {', '.join(new_semi)} became semi-open for "
                            f"{color_label(color)} after {fact.san}."
                        ),
                    )
                )

        # A pawn break is reported only for the move just played, and only when
        # the played move really was a pawn advance.
        board_before = chess.Board(fact.fen_before)
        try:
            move = chess.Move.from_uci(fact.uci)
        except ValueError:  # pragma: no cover
            move = None
        if move is not None:
            piece = board_before.piece_at(move.from_square)
            if piece is not None and piece.piece_type == chess.PAWN:
                destination = chess.square_name(move.to_square)
                mover_color = chess_color(fact.mover)
                pawn_breaks = _pawn_breaks(
                    board_after, mover_color, pawn_file_map(board_after, mover_color)
                )
                if destination in pawn_breaks:
                    events.append(
                        _event(
                            fact,
                            event_type="pawn_break",
                            side=fact.mover,
                            before=None,
                            after=destination,
                            delta=1,
                            statement=(
                                f"{color_label(fact.mover)}'s {fact.san} is a pawn break, "
                                f"challenging an enemy pawn from {destination}."
                            ),
                            evidence={"break_square": destination},
                        )
                    )

        previous[Color.WHITE] = white
        previous[Color.BLACK] = black

    return PawnStructureAnalysis(
        snapshots=snapshots,
        events=events,
        final_white=snapshots[-1].white,
        final_black=snapshots[-1].black,
    )
