"""Structured-report assembly, shared by the intelligence routes and Phase 5.

The intelligence layer is **engine-free and deterministic**: it consumes the
stored Phase 3 analysis and produces the structured ``GameReport`` that Phase 4
exposes and Phase 5 aggregates. This module owns that assembly so two callers can
share one code path:

* ``/api/intelligence/games/{id}/report`` — generate on demand for the reader.
* the player-profile service — materialise the report for any analyzed game that
  does not have one yet, so a profile is built from *analyses*, not from whether
  the user happened to open a report page first.

Nothing here runs Stockfish, and nothing here invents data: a game without stored
analysis raises :class:`AnalysisRequiredError` instead of producing a report.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from argus.analysis.classification import MoveClassification
from argus.analysis.phase import GamePhase
from argus.chess_core.models import Color
from argus.intelligence import (
    REPORT_VERSION,
    AnalysisMeta,
    CriticalFact,
    GameContext,
    GameIntelligence,
    MoveFact,
)
from argus.shared.errors import AnalysisRequiredError
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    get_critical_positions,
    get_game,
    get_game_report,
    get_latest_analysis_session,
    get_move_analyses,
    get_moves,
    save_game_report,
)
from argus_api.services.authorization import authorize_game

logger = get_logger(__name__)


def build_intelligence(db: Session, game_id: str) -> GameIntelligence:
    """Assemble the intelligence service from stored data (no engine calls).

    Authorization is enforced here — the single decision point — so every
    intelligence route that reads a game's report inherits it, rather than each
    route having to remember. A game outside the caller's allow-list is absent.
    """
    authorize_game(db, game_id)
    game = get_game(db, game_id)
    analysis_rows = get_move_analyses(db, game_id)
    critical_rows = get_critical_positions(db, game_id)
    session_row = get_latest_analysis_session(db, game_id)
    stored_moves = get_moves(db, game_id)

    context = GameContext(
        game_id=game.id,
        white_player=game.white_player_name,
        black_player=game.black_player_name,
        white_rating=game.white_rating,
        black_rating=game.black_rating,
        result=game.result,
        date=game.date,
        event=game.event,
        site=game.site,
        time_control=game.time_control,
        eco_code=game.eco_code,
        opening_name=game.opening_name,
        initial_position=game.initial_position,
        final_position=game.final_position,
        move_count=game.move_count,
    )

    facts: list[MoveFact] = []
    for row in analysis_rows:
        facts.append(
            MoveFact(
                ply=row.ply,
                move_number=row.move_number,
                mover=Color(row.mover),
                san=row.played_move_san,
                uci=row.played_move_uci,
                fen_before=row.fen_before,
                fen_after=row.fen_after,
                eval_before_cp=row.evaluation_before_cp,
                eval_before_mate=row.evaluation_before_mate,
                eval_after_cp=row.evaluation_after_cp,
                eval_after_mate=row.evaluation_after_mate,
                eval_change_cp=row.evaluation_change_cp,
                centipawn_loss=row.centipawn_loss,
                played_eval_cp=getattr(row, "played_eval_cp", None),
                played_eval_mate=getattr(row, "played_eval_mate", None),
                played_eval_source=getattr(row, "played_eval_source", None),
                classification=MoveClassification(row.classification)
                if row.classification
                else None,
                best_move_uci=row.best_move_uci,
                best_move_san=row.best_move_san,
                is_best_move=row.is_best_move,
                phase=GamePhase(row.phase) if row.phase else None,
                principal_variation=list(row.principal_variation or []),
                depth=row.depth,
            )
        )

    criticals = [
        CriticalFact(
            ply=row.ply,
            move_number=row.move_number,
            color=Color(row.color),
            san=row.san,
            reason=row.reason,
            severity=row.severity,
            severity_score=row.severity_score,
            classification=MoveClassification(row.classification) if row.classification else None,
            swing_cp=row.swing_cp,
            evaluation_before_white=row.evaluation_before_white,
            evaluation_after_white=row.evaluation_after_white,
            is_mate_related=row.is_mate_related,
            detail=row.detail,
        )
        for row in critical_rows
    ]

    meta = AnalysisMeta(
        analysis_version=session_row.analysis_version if session_row else None,
        engine=session_row.engine if session_row else None,
        engine_version=session_row.engine_version if session_row else None,
        depth=session_row.depth if session_row else None,
        multipv=session_row.multipv if session_row else None,
        movetime_ms=session_row.movetime_ms if session_row else None,
        profile=session_row.profile if session_row else None,
        positions_analyzed=len(analysis_rows),
    )

    intelligence = GameIntelligence(context, facts, criticals=criticals, analysis=meta)
    if not facts:
        raise AnalysisRequiredError(
            "This game has no stored engine analysis, so structured intelligence cannot be "
            "produced. Run the Stockfish analysis first.",
            details={
                "game_id": game_id,
                "moves_in_game": len(stored_moves),
                "required": "a completed analysis run (POST /api/analysis/games/{id})",
            },
        )
    return intelligence


def generate_and_store_report(db: Session, game_id: str) -> dict:
    """Build the report from stored analysis, persist the snapshot and return it."""
    report = build_intelligence(db, game_id).build_report()
    save_game_report(
        db,
        game_id,
        report.model_dump(mode="json"),
        report_version=report.report_version,
        analysis_version=report.provenance.analysis_version or "",
        engine=report.provenance.engine,
        engine_version=report.provenance.engine_version,
        depth=report.provenance.depth,
        moves_considered=report.provenance.moves_in_game,
        evaluated_moves=report.provenance.evaluated_moves,
        generated_at=report.generated_at,
    )
    logger.info(
        "Generated game report [game=%s report_version=%s moves=%d lessons=%d]",
        game_id,
        report.report_version,
        report.provenance.moves_in_game,
        len(report.key_lessons),
    )
    return report.model_dump(mode="json")


def report_is_current(stored) -> bool:
    """Whether a stored report was produced by the *current* intelligence code.

    A stored snapshot is only reused when its ``report_version`` matches
    :data:`REPORT_VERSION`. When the detector semantics change, the version moves
    and every stored report becomes stale — so it is rebuilt instead of being
    served with a superseded answer.
    """
    return stored is not None and bool(stored.payload) and stored.report_version == REPORT_VERSION


def ensure_report(db: Session, game_id: str) -> dict | None:
    """Make sure an analyzed game has a *current* stored report; ``None`` if it cannot.

    A stored snapshot is reused only when :func:`report_is_current` says it was
    built by this version of the intelligence layer. A stale snapshot is rebuilt
    from the stored analysis; if the analysis is gone and it cannot be rebuilt,
    the existing snapshot is served rather than nothing, so a profile never
    regresses to empty. A game that is imported but not analyzed (or whose
    analysis failed) has no report to build, so it is skipped rather than guessed
    at — the caller counts it as an analyzed-games gap.
    """
    stored = get_game_report(db, game_id)
    if report_is_current(stored):
        return stored.payload
    try:
        return generate_and_store_report(db, game_id)
    except AnalysisRequiredError:
        return stored.payload if stored is not None and stored.payload else None


__all__ = [
    "build_intelligence",
    "ensure_report",
    "generate_and_store_report",
    "report_is_current",
]
