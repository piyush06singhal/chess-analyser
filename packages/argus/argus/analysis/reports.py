"""Report architecture: data structures and deterministic report builder.

A report turns a :class:`GameAnalysis` into structured sections. Every value
is engine-derived or board-derived — the builder never invents insights.
Sections this per-game report object does not embed (narratives, player
tendencies, recommended training) are listed in ``pending_sections`` instead of
being faked. Player tendencies and training exist as their own surfaces (Phases 5
and 8); they are only outside this object.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from argus.analysis.classification import MoveClassification
from argus.analysis.game_analyzer import GameAnalysis, GameSummary
from argus.analysis.phase import GamePhase
from argus.chess_core.models import Color, Game

# Sections this report object does not embed. Listed explicitly so the UI can
# show a proper "not part of this report" state instead of fabricated content.
PENDING_SECTIONS = (
    "Opening narrative",
    "Tactical opportunities",
    "Positional issues",
    "Middlegame narrative",
    "Endgame narrative",
    "Key lessons",
    "Player tendencies",
    "Recommended training",
)

_DEFAULT_MAX_MOMENTS = 10


class CriticalMoment(BaseModel):
    """A move of interest with engine evidence and a factual description."""

    ply: int
    move_number: int
    color: Color
    san: str = Field(description="Played move (SAN)")
    best_move_san: str | None = Field(default=None, description="Engine best move (SAN)")
    fen_before: str
    evaluation_before_cp: int | None = None
    evaluation_change_cp: int | None = None
    centipawn_loss: int | None = None
    classification: MoveClassification | None = None
    phase: GamePhase
    description: str


class OpeningSection(BaseModel):
    """Opening metadata from the game (PGN headers when available)."""

    eco_code: str | None = None
    name: str | None = None
    variation: str | None = None
    from_headers: bool = Field(default=False, description="True when parsed from PGN headers")
    note: str | None = Field(
        default=None,
        description="Present when opening detection from the move list is not available yet",
    )


class GameReport(BaseModel):
    """Structured, explainable report for one game."""

    game_id: str | None = None
    generated_at: datetime
    engine: str
    engine_version: str | None = None
    depth: int
    result: str
    white_player: str
    black_player: str
    summary: GameSummary
    opening: OpeningSection
    critical_moments: list[CriticalMoment] = Field(default_factory=list)
    best_moves: list[CriticalMoment] = Field(default_factory=list)
    turning_point: CriticalMoment | None = None
    pending_sections: list[str] = Field(default_factory=lambda: list(PENDING_SECTIONS))


def _describe(move) -> str:  # noqa: ANN001 — AnalyzedMove; untyped to avoid an import cycle
    label = move.classification.value if move.classification else "unclassified"
    color = "White" if move.color == Color.WHITE else "Black"
    parts = [f"{color} played {move.san} ({label})"]
    if move.centipawn_loss is not None and move.centipawn_loss > 0:
        best = f", engine best was {move.best_move_san}" if move.best_move_san else ""
        parts.append(f"losing {move.centipawn_loss} centipawns{best}")
    elif move.classification == MoveClassification.BEST:
        parts.append("matching the engine's best line")
    return " — ".join(parts)


def _moment(move) -> CriticalMoment:  # noqa: ANN001 — AnalyzedMove
    return CriticalMoment(
        ply=move.ply,
        move_number=move.move_number,
        color=move.color,
        san=move.san,
        best_move_san=move.best_move_san,
        fen_before=move.fen_before,
        evaluation_before_cp=move.evaluation_before_cp,
        evaluation_change_cp=move.evaluation_change_cp,
        centipawn_loss=move.centipawn_loss,
        classification=move.classification,
        phase=move.phase,
        description=_describe(move),
    )


def build_report(
    analysis: GameAnalysis,
    game: Game,
    *,
    engine: str,
    engine_version: str | None,
    depth: int,
    max_moments: int = _DEFAULT_MAX_MOMENTS,
) -> GameReport:
    """Build a deterministic report from a completed game analysis."""
    moment_by_ply = {move.ply: move for move in analysis.moves}

    worst = sorted(
        (move for move in analysis.moves if move.classification is not None),
        key=lambda move: move.centipawn_loss or 0,
        reverse=True,
    )[:max_moments]
    critical_moments = [_moment(move) for move in worst if (move.centipawn_loss or 0) > 0]

    best = sorted(
        (
            move
            for move in analysis.moves
            if move.classification in (MoveClassification.BEST, MoveClassification.BRILLIANT)
        ),
        key=lambda move: move.centipawn_loss if move.centipawn_loss is not None else -1,
    )[:max_moments]
    best_moves = [_moment(move) for move in best]

    turning_point = None
    if analysis.turning_point_ply is not None and analysis.turning_point_ply in moment_by_ply:
        turning_point = _moment(moment_by_ply[analysis.turning_point_ply])

    opening_info = game.opening
    opening = OpeningSection(
        eco_code=opening_info.eco_code,
        name=opening_info.name,
        variation=opening_info.variation,
        from_headers=opening_info.source == "pgn_header",
        note=None
        if opening_info.source == "pgn_header"
        else "Opening detection from the move list is planned for a later phase",
    )

    return GameReport(
        game_id=analysis.game_id or game.id,
        generated_at=datetime.now(timezone.utc),
        engine=engine,
        engine_version=engine_version,
        depth=depth,
        result=game.result.value,
        white_player=game.white_player.name,
        black_player=game.black_player.name,
        summary=analysis.summary,
        opening=opening,
        critical_moments=critical_moments,
        best_moves=best_moves,
        turning_point=turning_point,
    )
