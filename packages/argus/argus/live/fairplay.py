"""Fair play: what the coach may say *during* a live game, and what it may not.

This is the load-bearing safety module of Phase 12. During a competitive game
Caissa must never hand the player an engine move recommendation. That rule is
enforced in one place — here — and every caller (the live coach endpoint, the AI
agent, the frontend panel) asks this module before it is allowed to show engine
output.

The permission is a property of the game (its mode and analysis mode), chosen at
creation and clamped by :func:`argus.live.models.resolve_analysis_mode`. It is
never derived from the request, so a client cannot talk its way into analysis.

The levels, from the spec (§21, §54):

* ``OFF`` — the coach says nothing about the position.
* ``HINTS`` — non-engine heuristics only ("look for forcing moves first").
* ``CONCEPTUAL`` — positional concepts grounded in *board* facts, never a
  Stockfish evaluation.
* ``FULL_ANALYSIS`` — the real engine analysis, permitted **only** in an
  explicitly designated training or sandbox game.
"""

from __future__ import annotations

from argus.live.models import (
    AnalysisMode,
    CoachLevel,
    LiveGameState,
)

#: The refusal shown when a competitive player asks for an engine move.
COMPETITIVE_REFUSAL = (
    "This is a competitive game, so Caissa will not suggest a move. Engine "
    "assistance is disabled for games like this one. Play on — the full analysis "
    "is available the moment the game ends."
)

#: Non-engine hints that are always safe in competitive play.
SAFE_HINTS: tuple[str, ...] = (
    "Look for checks, captures and threats first.",
    "Ask what your opponent's last move attacks or threatens.",
    "Check whether any of your pieces are undefended.",
    "Before committing, look for your opponent's forcing replies.",
)


def coach_permissions(state: LiveGameState) -> dict:
    """Return exactly what the coach is allowed to do for this game.

    The shape is stable and is what the frontend renders into a set of affordances;
    every boolean here is a statement about the game, not about the user.
    """
    level = state.coach_level
    analysis_allowed = state.analysis_permitted()
    return {
        "mode": state.mode.value,
        "analysis_mode": state.analysis_mode.value,
        "coach_level": level.value,
        "competitive": state.is_competitive,
        "may_give_engine_moves": level is CoachLevel.FULL_ANALYSIS and analysis_allowed,
        "may_show_evaluation": level is CoachLevel.FULL_ANALYSIS and analysis_allowed,
        "may_show_engine_lines": level is CoachLevel.FULL_ANALYSIS and analysis_allowed,
        "may_give_hints": level in (CoachLevel.HINTS, CoachLevel.CONCEPTUAL, CoachLevel.FULL_ANALYSIS),
        "may_explain_concepts": level is not CoachLevel.OFF,
        "may_analyse": analysis_allowed,
        "refusal": COMPETITIVE_REFUSAL if state.is_competitive else None,
    }


def is_engine_request_permitted(state: LiveGameState) -> bool:
    """Whether an engine-backed answer may be produced for this game right now."""
    return (
        state.coach_level is CoachLevel.FULL_ANALYSIS
        and state.analysis_mode in (AnalysisMode.TRAINING_ANALYSIS, AnalysisMode.SANDBOX_ANALYSIS)
    )


def refuse_if_not_permitted(state: LiveGameState) -> None:
    """Raise the fair-play refusal when engine output is not permitted.

    Callers that would otherwise run Stockfish for an in-play question call this
    first, so no engine process is ever started for a competitive game.
    """
    if not is_engine_request_permitted(state):
        from argus.live.models import LiveGameError

        raise LiveGameError(
            "analysis_not_permitted",
            COMPETITIVE_REFUSAL,
            mode=state.mode.value,
            analysis_mode=state.analysis_mode.value,
        )


def coaching_reply(state: LiveGameState, *, question: str | None = None) -> dict:
    """The honest answer to an in-game coaching request.

    In a permitted game the caller replaces ``message`` with real analysis; this
    function only produces the *safe* answer for a game that may not receive one.
    It never invents a chess fact — it either refuses or offers a non-engine
    heuristic.
    """
    permissions = coach_permissions(state)
    if permissions["may_give_engine_moves"]:
        return {
            "kind": "analysis_permitted",
            "message": None,
            "permissions": permissions,
            "hint": None,
        }
    if state.coach_level is CoachLevel.OFF:
        return {
            "kind": "coach_off",
            "message": "The coach is turned off for this game.",
            "permissions": permissions,
            "hint": None,
        }
    # A safe, non-engine hint. The first one is used when no question is given.
    hint = SAFE_HINTS[0]
    return {
        "kind": "hint_only",
        "message": permissions["refusal"] or "Caissa can offer general guidance here, but not a move.",
        "permissions": permissions,
        "hint": hint,
    }


__all__ = [
    "COMPETITIVE_REFUSAL",
    "SAFE_HINTS",
    "coach_permissions",
    "coaching_reply",
    "is_engine_request_permitted",
    "refuse_if_not_permitted",
]
