"""Why-not-this-move and what-if-I-had analysis.

Both features answer a counterfactual question, and both are built here from the
*same* measured pieces: a candidate comparison for the played move and the
engine's alternatives, and a branch for the alternative line. The difference
between them is only which question is asked and how the result is framed.

There is deliberately no natural-language generation in this module. It produces
an :class:`ExplanationBundle`: a list of atomic, sourced facts plus the raw
numbers. A language model (or a template) may phrase them, but it cannot add a
claim that is not in the bundle, which is what stops "the knight becomes
dominant" from appearing when nothing measured it.
"""

from __future__ import annotations

from argus.analysis.engine.base import ChessEngine

from argus.scenarios.candidates import CandidateMoveComparison
from argus.scenarios.models import (
    CandidateAssessment,
    CandidateComparison,
    ExplanationBundle,
    ScenarioBranch,
)
from argus.scenarios.policy import MoveQuality

#: How many of the engine's own moves are offered as alternatives.
DEFAULT_ALTERNATIVES = 4


def _assessment_line(assessment: CandidateAssessment) -> str:
    score = "score unavailable"
    if assessment.mate is not None:
        score = f"mate in {abs(assessment.mate)}"
    elif assessment.cp is not None:
        score = f"{assessment.cp / 100:+.2f}"
    label = assessment.quality.value
    san = assessment.san or assessment.uci
    return f"{san} ({assessment.uci}) scores {score} — {label}"


def _branch_facts(branch: ScenarioBranch) -> list[str]:
    """Atomic, sourced facts about a branch — every one of them a measurement."""
    facts: list[str] = []
    alternative = branch.alternative_move_san or branch.alternative_move_uci
    actual = branch.actual_move_san or branch.actual_move_uci
    config = branch.engine_config.label()
    if branch.actual_eval_cp is not None:
        facts.append(
            f"Engine score after the played move {actual}: {branch.actual_eval_cp / 100:+.2f} "
            f"(mover's perspective, {branch.actual_eval_source})"
        )
    if branch.alternative_eval_cp is not None:
        facts.append(
            f"Engine score after the alternative {alternative}: "
            f"{branch.alternative_eval_cp / 100:+.2f} "
            f"(mover's perspective, {branch.alternative_eval_source})"
        )
    if branch.evaluation_change_cp is not None:
        direction = "better" if branch.evaluation_change_cp > 0 else "worse"
        facts.append(
            f"The alternative is {abs(branch.evaluation_change_cp) / 100:.2f} pawns {direction} "
            f"than the played move"
        )
    if branch.actual_continuation:
        line = " ".join(
            ply.san or ply.uci for ply in branch.actual_continuation[:6]
        )
        facts.append(f"Played-move continuation the engine chose: {line}")
    if branch.alternative_continuation:
        line = " ".join(
            ply.san or ply.uci for ply in branch.alternative_continuation[:6]
        )
        facts.append(f"Alternative continuation the engine chose: {line}")
        reply = branch.alternative_continuation[1] if len(branch.alternative_continuation) > 1 else None
        if reply is not None:
            facts.append(
                f"The opponent's strongest reply to {alternative} is {reply.san or reply.uci}"
            )
    if branch.comparison is not None:
        engine_diff = branch.comparison.engine_difference.get("delta_cp_white")
        if engine_diff is not None:
            facts.append(
                f"Resulting positions compared: {engine_diff / 100:+.2f} pawns for White "
                f"({alternative} versus {actual})"
            )
        for difference in branch.comparison.structural_differences[:6]:
            facts.append(
                f"Structural difference after the two moves: {difference.feature} "
                f"{difference.value_a} -> {difference.value_b}"
            )
        facts.extend(branch.comparison.notes)
    facts.append(f"All numbers come from one engine configuration: {config}")
    facts.extend(branch.notes)
    return facts


def explanation_from_branch(branch: ScenarioBranch, *, question: str) -> ExplanationBundle:
    """The facts a phrasing of this branch is allowed to use."""
    facts = _branch_facts(branch)
    numbers = {
        "actual_eval_cp": branch.actual_eval_cp,
        "alternative_eval_cp": branch.alternative_eval_cp,
        "evaluation_change_cp": branch.evaluation_change_cp,
        "actual_eval_source": branch.actual_eval_source,
        "alternative_eval_source": branch.alternative_eval_source,
        "continuation_plies": len(branch.alternative_continuation),
        "engine": branch.engine_config.model_dump(),
    }
    insufficient = branch.alternative_eval_cp is None and not branch.alternative_continuation
    return ExplanationBundle(
        question=question,
        facts=facts,
        numbers=numbers,
        engine_config=branch.engine_config,
        evidence=branch.evidence,
        insufficient=insufficient,
        unavailable_reason=(
            "The engine returned no line for this move, so no difference can be stated."
            if insufficient
            else None
        ),
    )


def why_not_analysis(
    comparison: CandidateComparison,
    *,
    move_uci: str,
    depth: int | None = None,
) -> dict:
    """The measured components of "why is this move inferior?".

    Returns an honest refusal (``status == "move_is_best"`` or ``"not_found"``)
    when the requested move is not inferior, rather than manufacturing a reason.
    """
    target = next(
        (entry for entry in comparison.candidates if entry.uci == move_uci),
        None,
    )
    if target is None:
        return {
            "status": "not_found",
            "message": "That move is not among the compared moves for this position.",
            "engine_config": comparison.engine_config.model_dump(),
        }
    if not target.legal:
        return {
            "status": "illegal_move",
            "message": target.legality_note or "That move is not legal in this position.",
            "engine_config": comparison.engine_config.model_dump(),
        }

    alternatives = [
        entry
        for entry in comparison.candidates
        if entry.legal and entry.uci != target.uci
    ]
    better = [
        entry
        for entry in alternatives
        if (entry.centipawn_loss or 0) < (target.centipawn_loss or 0)
        or entry.is_engine_best
    ]
    better.sort(key=lambda entry: (entry.rank or 99))

    facts: list[str] = [f"Move under review: {_assessment_line(target)}"]
    if target.pv_san:
        facts.append(
            "Line the engine expects after this move: " + " ".join(target.pv_san[:8])
        )
    if target.material_consequence:
        facts.append(f"Material: {target.material_consequence}")
    for note in target.tactical_consequence:
        facts.append(f"Tactics: {note}")
    for difference in target.structural_deltas[:4]:
        facts.append(
            f"Board change: {difference.feature} {difference.value_a} -> {difference.value_b}"
        )
    for entry in better[:DEFAULT_ALTERNATIVES]:
        facts.append(f"Better alternative the engine found: {_assessment_line(entry)}")
    if target.position_type:
        facts.append(f"Resulting position type: {target.position_type}")

    status = "ok"
    message = None
    if not better and (target.centipawn_loss or 0) <= 0:
        status = "move_is_best"
        message = (
            "This move is the engine's first choice in this search, so there is no "
            "inferiority to explain."
        )
        facts.append(message)

    best_response = None
    if len(target.pv_san) > 1:
        best_response = target.pv_san[1]
    elif len(target.pv) > 1:
        best_response = target.pv[1]

    return {
        "status": status,
        "message": message,
        "move": target.model_dump(mode="json"),
        "best_response": best_response,
        "resulting_eval_cp": target.cp,
        "resulting_eval_mate": target.mate,
        "centipawn_loss": target.centipawn_loss,
        "quality": target.quality.value,
        "critical_issue": _critical_issue(target),
        "better_alternatives": [entry.model_dump(mode="json") for entry in better[:DEFAULT_ALTERNATIVES]],
        "engine_config": comparison.engine_config.model_dump(),
        "evidence": [ref.model_dump(mode="json") for ref in comparison.evidence],
        "explanation": ExplanationBundle(
            question="why not this move?",
            facts=facts,
            numbers={
                "centipawn_loss": target.centipawn_loss,
                "cp": target.cp,
                "mate": target.mate,
                "eval_source": target.eval_source,
                "engine": comparison.engine_config.model_dump(),
            },
            engine_config=comparison.engine_config,
            evidence=comparison.evidence,
            insufficient=target.cp is None and target.mate is None,
            unavailable_reason=(
                "The engine returned no score for this move."
                if target.cp is None and target.mate is None
                else None
            ),
        ).model_dump(mode="json"),
    }


def _critical_issue(assessment: CandidateAssessment) -> str | None:
    """The most concrete measured problem with a move, or ``None``.

    Ordered from the hardest evidence to the softest: a forced reply that changes
    the evaluation beats a structural count. If nothing was measured, this returns
    ``None`` and the caller reports no issue rather than inventing one.
    """
    if assessment.quality in (MoveQuality.BLUNDER, MoveQuality.MISTAKE, MoveQuality.INACCURATE):
        loss = assessment.centipawn_loss
        if loss is not None:
            return f"it loses {loss / 100:.2f} pawns against the engine's best move"
    for note in assessment.tactical_consequence:
        if "opponent piece(s) attacked" in note or "checkmate" in note:
            continue
        if "own piece(s) attacked" in note:
            return note
    for difference in assessment.structural_deltas:
        if difference.domain.value == "material" and (difference.delta or 0) < 0:
            return f"material: {difference.feature} {difference.value_a} -> {difference.value_b}"
    if assessment.tactical_consequence:
        return assessment.tactical_consequence[0]
    if assessment.material_consequence and assessment.material_consequence != "no material change":
        return assessment.material_consequence
    return None


def what_if_analysis(branch: ScenarioBranch) -> dict:
    """The measured components of "what if I had played this?"."""
    alternative = branch.alternative_move_san or branch.alternative_move_uci
    facts: list[str] = []
    if branch.actual_move_uci:
        facts.append(
            f"The game continued {branch.actual_move_san or branch.actual_move_uci}"
        )
    facts.append(f"The alternative analysed is {alternative} ({branch.alternative_move_uci})")
    facts.extend(_branch_facts(branch))
    return {
        "status": "ok",
        "alternative_move": alternative,
        "actual_move": branch.actual_move_san,
        "evaluation_change_cp": branch.evaluation_change_cp,
        "branch": branch.model_dump(mode="json"),
        "explanation": explanation_from_branch(
            branch, question="what if this move had been played?"
        ).model_dump(mode="json"),
    }


def legal_move_refusal(move: str, *, reason: str | None = None) -> dict:
    """The honest answer when a requested move cannot be played."""
    return {
        "status": "illegal_move",
        "message": reason or "That move is not legal in this position.",
        "requested_move": move,
        "engine_config": None,
        "explanation": ExplanationBundle(
            question="what if this move had been played?",
            facts=[],
            insufficient=True,
            unavailable_reason=reason or "That move is not legal in this position.",
        ).model_dump(mode="json"),
    }


def build_comparison(
    engine: ChessEngine,
    fen: str,
    moves: list[str],
    *,
    depth: int | None = None,
    multipv: int | None = None,
    movetime_ms: int | None = None,
    played_move_uci: str | None = None,
    include_top: int = 0,
) -> CandidateComparison:
    """One search, several moves — the shared entry point for both features."""
    return CandidateMoveComparison(engine).compare(
        fen,
        moves,
        depth=depth,
        multipv=multipv,
        movetime_ms=movetime_ms,
        played_move_uci=played_move_uci,
        include_top=include_top,
    )


__all__ = [
    "build_comparison",
    "explanation_from_branch",
    "legal_move_refusal",
    "what_if_analysis",
    "why_not_analysis",
]
