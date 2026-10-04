"""Turning-point explorer: where a game could have gone differently.

This module is **engine-free on purpose**. It reads a stored analysis and lists
the moments worth branching from, together with the alternative moves the
analysis already recorded. That keeps the explorer instant (no engine call per
game) and keeps the alternatives honest: an alternative listed here is one that a
real search produced, not one this module inferred.

When an analysis predates MultiPV storage, a moment is still listed but reports
``what_if_available = False`` and says why, rather than offering a branch that
would have to be invented.
"""

from __future__ import annotations

from argus.scenarios.models import (
    CandidateAssessment,
    TurningPoint,
    TurningPointExplorer,
)
from argus.scenarios.policy import (
    DECISION_METHODOLOGY_VERSION,
    EXPLORER_MOMENT_LIMIT,
    MAX_CANDIDATE_MOVES,
    classify_move_quality,
)

#: Classifications that make a moment interesting regardless of its size.
_NOTABLE_CLASSIFICATIONS = {"inaccuracy", "mistake", "blunder", "missed_win"}

#: A swing at least this large (centipawns, White's perspective) makes a moment
#: a turning point even when the classification label is quiet.
_SWING_THRESHOLD_CP = 100


def _to_white(cp: int | None, color: str | None) -> int | None:
    if cp is None:
        return None
    return cp if (color or "white") == "white" else -cp


def _assessment_from_candidate(
    entry: dict, *, played_uci: str | None, best_uci: str | None, mover: str | None
) -> CandidateAssessment:
    """Convert one stored MultiPV entry into an assessment, without a new search."""
    uci = str(entry.get("uci") or "")
    cp = entry.get("cp")
    mate = entry.get("mate")
    # Centipawn loss needs the best line in the same search; the caller fills it
    # once the whole stored window for this ply is known.
    return CandidateAssessment(
        uci=uci,
        san=entry.get("san"),
        legal=True,
        rank=entry.get("rank"),
        cp=cp,
        mate=mate,
        cp_white=_to_white(cp, mover),
        centipawn_loss=None,
        quality=classify_move_quality(None, is_best=uci == best_uci),
        pv=list(entry.get("pv") or []),
        is_engine_best=bool(best_uci and uci == best_uci),
        is_played_move=bool(played_uci and uci == played_uci),
        eval_source="stored_analysis",
    )


def explore(
    *,
    game_id: str,
    rows: list[dict],
    criticals: list[dict] | None = None,
    moves_total: int | None = None,
    limit: int = EXPLORER_MOMENT_LIMIT,
    analysis_version: str | None = None,
) -> TurningPointExplorer:
    """List the branchable moments of a game from its stored analysis.

    ``rows`` are stored move analyses (the same row shape the training engine
    consumes); ``criticals`` are the stored critical positions, used only to mark
    which moments the intelligence layer already flagged.
    """
    critical_by_ply = {int(item.get("ply") or 0): item for item in (criticals or [])}
    evaluation_series: list[dict] = []
    candidates_all: list[TurningPoint] = []
    missing_candidates = 0

    for row in rows:
        ply = int(row.get("ply") or 0)
        mover = row.get("mover")
        before_white = _to_white(row.get("evaluation_before_cp"), mover)
        after_white = _to_white(row.get("evaluation_after_cp"), mover)
        critical = critical_by_ply.get(ply)
        swing = None
        if critical is not None and critical.get("swing_cp") is not None:
            swing = int(critical["swing_cp"])
        elif before_white is not None and after_white is not None:
            swing = after_white - before_white

        evaluation_series.append(
            {
                "ply": ply,
                "move_number": row.get("move_number"),
                "color": mover,
                "san": row.get("played_move_san"),
                "eval_white_cp": before_white,
                "eval_after_white_cp": after_white,
                "classification": row.get("classification"),
                "phase": row.get("phase"),
                "is_critical": critical is not None,
            }
        )

        raw_candidates = list(row.get("candidate_moves") or [])
        if not raw_candidates:
            missing_candidates += 1
        best_uci = row.get("best_move_uci")
        played_uci = row.get("played_move_uci")
        alternatives = [
            _assessment_from_candidate(
                entry, played_uci=played_uci, best_uci=best_uci, mover=mover
            )
            for entry in raw_candidates[:MAX_CANDIDATE_MOVES]
        ]
        best_cp = None
        if alternatives:
            best_cp = max(
                (entry.cp for entry in alternatives if entry.cp is not None), default=None
            )
        for entry in alternatives:
            if best_cp is not None and entry.cp is not None:
                entry.centipawn_loss = max(0, best_cp - entry.cp)
                entry.quality = classify_move_quality(
                    entry.centipawn_loss, is_best=entry.is_engine_best
                )

        notable = (row.get("classification") or "") in _NOTABLE_CLASSIFICATIONS
        big_swing = swing is not None and abs(swing) >= _SWING_THRESHOLD_CP
        if not (critical is not None or notable or big_swing):
            continue

        candidates_all.append(
            TurningPoint(
                ply=ply,
                move_number=int(row.get("move_number") or 0),
                color=str(mover or "white"),
                san=str(row.get("played_move_san") or ""),
                uci=str(played_uci or ""),
                fen_before=str(row.get("fen_before") or ""),
                classification=row.get("classification"),
                phase=row.get("phase"),
                evaluation_before_cp=row.get("evaluation_before_cp"),
                evaluation_after_cp=row.get("evaluation_after_cp"),
                swing_cp=swing,
                centipawn_loss=row.get("centipawn_loss"),
                best_move_uci=best_uci,
                best_move_san=row.get("best_move_san"),
                is_best_move=bool(best_uci and played_uci == best_uci),
                is_critical=critical is not None,
                critical_reason=(critical or {}).get("reason"),
                severity=(critical or {}).get("severity"),
                alternatives=alternatives,
                alternative_count=len([e for e in alternatives if not e.is_played_move]),
                what_if_available=len([e for e in alternatives if not e.is_played_move]) >= 1,
                branchable=bool(row.get("fen_before")),
            )
        )

    def _rank(moment: TurningPoint) -> tuple:
        return (
            0 if moment.is_critical else 1,
            0 if (moment.classification or "") in _NOTABLE_CLASSIFICATIONS else 1,
            -(abs(moment.swing_cp) if moment.swing_cp is not None else 0),
        )

    candidates_all.sort(key=_rank)
    selected = candidates_all[: max(1, limit)]
    notes: list[str] = []
    if missing_candidates:
        notes.append(
            f"{missing_candidates} analysed plies have no stored alternative moves (the "
            "analysis predates MultiPV storage), so no what-if branch can be offered there."
        )
    if len(candidates_all) > len(selected):
        notes.append(
            f"{len(candidates_all)} moments qualified; the {len(selected)} most decisive are "
            "shown. Every moment above is derived from stored engine facts."
        )
    if not rows:
        notes.append("This game has no stored analysis, so there are no moments to explore.")
    return TurningPointExplorer(
        game_id=game_id,
        analysis_version=analysis_version,
        plies_analyzed=len(rows),
        moves_total=moves_total,
        evaluation_series=evaluation_series,
        turning_points=selected,
        critical_moment_count=len(critical_by_ply),
        branchable_count=len([m for m in candidates_all if m.branchable]),
        notes=notes,
        methodology_version=DECISION_METHODOLOGY_VERSION,
    )


__all__ = ["explore"]
