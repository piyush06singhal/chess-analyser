"""The coaching context: what Caissa knows, and what it therefore may say.

Phase 11's premise is that the user should not have to re-explain where they are.
Every page already knows something — a game is open, a position is on the board,
a training session is running, an opponent was selected. This module turns those
facts into one structure, ``CoachContext``, and one decision: which *mode* the
coach should answer in.

The design rule is the same one every earlier phase used: **the context contains
only verified information**. A field is present because the caller measured it;
nothing is defaulted into place to make the workspace look full. If the player
has no profile yet, ``player_profile`` is ``None`` and ``gaps`` says why. A
context that silently invents "your rating is 1500" would poison every answer
built on top of it.

Modes are behaviour, not decoration. A mode decides how much engine information
is exposed, how deep the prose goes, and which tool families the coach may use —
so a beginner is not handed a principal variation, and an advanced review is not
denied one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Situation(str, Enum):
    """Where the user actually is — inferred from measured context, not asked."""

    LIVE_GAME = "live_game"
    GAME_REVIEW = "game_review"
    TRAINING = "training"
    OPPONENT_PREPARATION = "opponent_preparation"
    POSITION_ANALYSIS = "position_analysis"
    OPENING_STUDY = "opening_study"
    ENDGAME_STUDY = "endgame_study"
    GENERAL_COACHING = "general_coaching"


class CoachMode(str, Enum):
    """How the coach answers. Every mode is a real change in behaviour."""

    COACH = "coach"
    ANALYST = "analyst"
    GAME_REVIEW = "game_review"
    TRAINING = "training"
    OPPONENT_PREP = "opponent_prep"
    OPENING_COACH = "opening_coach"
    ENDGAME_COACH = "endgame_coach"
    BEGINNER = "beginner"
    ADVANCED = "advanced"


#: Tool families the coach may use, matching the agent's registered tools.
#: A mode lists the families it permits; the agent is filtered to them, so a
#: beginner mode cannot call a deep engine tool by accident.
TOOL_FAMILIES = (
    "position",
    "engine",
    "game",
    "player",
    "opponent",
    "training",
    "scenario",
    "knowledge",
    "prediction",
)


@dataclass(frozen=True)
class ModeProfile:
    """What a mode changes: exposure, depth, tooling, and context priority."""

    label: str
    summary: str
    explanation_depth: str  # brief | standard | deep
    expose_evaluation: bool
    expose_candidate_moves: bool
    expose_principal_variation: bool
    expose_statistics: bool
    suggest_training: bool
    allowed_tool_families: tuple[str, ...]
    #: Which context fields matter most here. Ordered: the first present wins
    #: when the coach has to choose what to lead with.
    context_priority: tuple[str, ...]


_ALL_TOOLS = TOOL_FAMILIES
_NO_ENGINE_DETAIL = ("position", "game", "player", "training", "knowledge")

MODE_PROFILES: dict[CoachMode, ModeProfile] = {
    CoachMode.COACH: ModeProfile(
        label="Coach",
        summary="Explains with tactics first, engine numbers only when they add something.",
        explanation_depth="standard",
        expose_evaluation=True,
        expose_candidate_moves=True,
        expose_principal_variation=False,
        expose_statistics=True,
        suggest_training=True,
        allowed_tool_families=_ALL_TOOLS,
        context_priority=("current_fen", "current_game", "player_profile", "training_history"),
    ),
    CoachMode.ANALYST: ModeProfile(
        label="Analyst",
        summary="Engine-first: evaluations, candidate moves and lines are all shown.",
        explanation_depth="deep",
        expose_evaluation=True,
        expose_candidate_moves=True,
        expose_principal_variation=True,
        expose_statistics=True,
        suggest_training=False,
        allowed_tool_families=_ALL_TOOLS,
        context_priority=("current_fen", "current_game", "player_profile"),
    ),
    CoachMode.GAME_REVIEW: ModeProfile(
        label="Game review",
        summary="Walks a finished game: critical moments, decisions, and what to train.",
        explanation_depth="standard",
        expose_evaluation=True,
        expose_candidate_moves=True,
        expose_principal_variation=True,
        expose_statistics=True,
        suggest_training=True,
        allowed_tool_families=_ALL_TOOLS,
        context_priority=("current_game", "player_profile", "training_history"),
    ),
    CoachMode.TRAINING: ModeProfile(
        label="Training",
        summary="Keeps to the exercise at hand and never hands over the solution early.",
        explanation_depth="standard",
        expose_evaluation=False,
        expose_candidate_moves=False,
        expose_principal_variation=False,
        expose_statistics=True,
        suggest_training=True,
        allowed_tool_families=("position", "training", "player", "knowledge"),
        context_priority=("training_position", "training_history", "player_profile"),
    ),
    CoachMode.OPPONENT_PREP: ModeProfile(
        label="Opponent preparation",
        summary="Evidence about one opponent, with observed and engine play kept apart.",
        explanation_depth="deep",
        expose_evaluation=True,
        expose_candidate_moves=True,
        expose_principal_variation=True,
        expose_statistics=True,
        suggest_training=True,
        allowed_tool_families=_ALL_TOOLS,
        context_priority=("opponent_profile", "current_game_id", "current_fen", "player_profile"),
    ),
    CoachMode.OPENING_COACH: ModeProfile(
        label="Opening coach",
        summary="Recurring lines, deviations, and what the user actually plays.",
        explanation_depth="standard",
        expose_evaluation=True,
        expose_candidate_moves=True,
        expose_principal_variation=True,
        expose_statistics=True,
        suggest_training=True,
        allowed_tool_families=_ALL_TOOLS,
        context_priority=("current_fen", "current_game", "player_profile"),
    ),
    CoachMode.ENDGAME_COACH: ModeProfile(
        label="Endgame coach",
        summary="Endgame technique, separating engine analysis from chess theory.",
        explanation_depth="deep",
        expose_evaluation=True,
        expose_candidate_moves=True,
        expose_principal_variation=True,
        expose_statistics=False,
        suggest_training=True,
        allowed_tool_families=_ALL_TOOLS,
        context_priority=("current_fen", "training_history"),
    ),
    CoachMode.BEGINNER: ModeProfile(
        label="Beginner",
        summary="One idea at a time: the tactic, the reason, the consequence.",
        explanation_depth="brief",
        expose_evaluation=False,
        expose_candidate_moves=False,
        expose_principal_variation=False,
        expose_statistics=False,
        suggest_training=True,
        allowed_tool_families=_NO_ENGINE_DETAIL,
        context_priority=("current_fen", "player_profile", "training_history"),
    ),
    CoachMode.ADVANCED: ModeProfile(
        label="Advanced",
        summary="Everything measured: evaluation, candidates, lines, positional features.",
        explanation_depth="deep",
        expose_evaluation=True,
        expose_candidate_moves=True,
        expose_principal_variation=True,
        expose_statistics=True,
        suggest_training=True,
        allowed_tool_families=_ALL_TOOLS,
        context_priority=("current_fen", "opponent_profile", "player_profile"),
    ),
}

#: The mode a situation defaults to. This is why the user never has to say where
#: they are: the context already knows.
DEFAULT_MODE_FOR_SITUATION: dict[Situation, CoachMode] = {
    Situation.LIVE_GAME: CoachMode.COACH,
    Situation.GAME_REVIEW: CoachMode.GAME_REVIEW,
    Situation.TRAINING: CoachMode.TRAINING,
    Situation.OPPONENT_PREPARATION: CoachMode.OPPONENT_PREP,
    Situation.POSITION_ANALYSIS: CoachMode.ANALYST,
    Situation.OPENING_STUDY: CoachMode.OPENING_COACH,
    Situation.ENDGAME_STUDY: CoachMode.ENDGAME_COACH,
    Situation.GENERAL_COACHING: CoachMode.COACH,
}


def mode_profile(mode: CoachMode) -> ModeProfile:
    return MODE_PROFILES[mode]


class CoachContext(BaseModel):
    """One user's verified coaching context.

    Every field is optional because every field is *measured*: a field is ``None``
    when Caissa does not have it, and ``gaps`` names what is missing so the coach
    can say "I don't have your profile yet" instead of guessing.
    """

    user_id: int | None = None
    situation: Situation = Situation.GENERAL_COACHING
    mode: CoachMode = CoachMode.COACH

    current_game_id: str | None = None
    current_game: dict | None = Field(
        default=None, description="Stored game header + status, as measured"
    )
    current_position_id: int | None = None
    current_fen: str | None = None
    side_to_move: str | None = None
    legal_move_count: int | None = None
    selected_move: str | None = None
    game_phase: str | None = None

    player_profile: dict | None = Field(
        default=None, description="Player Intelligence payload (Chess DNA), when computed"
    )
    recent_games: list[dict] = Field(default_factory=list)
    training_history: dict | None = Field(
        default=None, description="Training progress / attempts summary, measured"
    )
    training_position: dict | None = Field(
        default=None, description="The exercise in front of the user, solution withheld"
    )
    review_queue: dict | None = None

    opponent_id: int | None = None
    opponent_profile: dict | None = None

    current_scenario: dict | None = None
    available_predictions: dict | None = Field(
        default=None,
        description="Per-task prediction availability — a task with no production model says so",
    )

    gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    assembled_at: datetime | None = None
    methodology_version: str = "11.0"

    @property
    def profile(self) -> ModeProfile:
        return MODE_PROFILES[self.mode]

    def evidence_keys(self) -> list[str]:
        """Which context fields are actually populated (the traceable ones)."""
        keys: list[str] = []
        for name in (
            "current_game_id",
            "current_fen",
            "player_profile",
            "training_history",
            "opponent_profile",
            "current_scenario",
        ):
            if getattr(self, name):
                keys.append(name)
        return keys

    def lead_with(self) -> str | None:
        """The context field this mode should open with, or ``None``."""
        for name in self.profile.context_priority:
            value = getattr(self, name, None)
            if isinstance(value, list):
                if value:
                    return name
            elif value:
                return name
        return None


def infer_situation(
    *,
    game_id: str | None = None,
    game_is_analysed: bool | None = None,
    training_position_id: int | None = None,
    training_session_active: bool = False,
    opponent_id: int | None = None,
    fen: str | None = None,
    phase: str | None = None,
) -> tuple[Situation, list[str]]:
    """Work out where the user is from what is present, plus the reasoning.

    The order encodes product priority: something live or in progress beats a
    finished game, and a finished game beats a bare position. The returned notes
    say which evidence decided it, so the inference is auditable rather than
    magic.
    """
    notes: list[str] = []
    if training_position_id is not None or training_session_active:
        notes.append("a training session is active")
        return Situation.TRAINING, notes
    if game_id and game_is_analysed is False:
        notes.append("a game is open and has not been analysed yet")
        return Situation.LIVE_GAME, notes
    if opponent_id is not None:
        notes.append("an opponent is selected")
        return Situation.OPPONENT_PREPARATION, notes
    if game_id:
        notes.append("a stored, analysed game is open")
        return Situation.GAME_REVIEW, notes
    if fen:
        notes.append(f"a position is on the board (phase: {phase or 'unknown'})")
        if phase == "opening":
            return Situation.OPENING_STUDY, notes
        if phase == "endgame":
            return Situation.ENDGAME_STUDY, notes
        return Situation.POSITION_ANALYSIS, notes
    notes.append("no game, position or opponent context is present")
    return Situation.GENERAL_COACHING, notes


def resolve_mode(
    situation: Situation, *, requested: CoachMode | None = None
) -> tuple[CoachMode, str]:
    """The mode to answer in: an explicit request wins, else the situation's.

    A user who is mid-exercise cannot be switched out of training mode by the
    situation resolver; but a user who explicitly asks for the analyst view gets
    it, because that is their decision to make.
    """
    if requested is not None:
        return requested, f"mode explicitly requested ({requested.value})"
    mode = DEFAULT_MODE_FOR_SITUATION[situation]
    return mode, f"default for situation '{situation.value}'"


def assemble_context(
    *,
    user_id: int | None = None,
    requested_mode: CoachMode | None = None,
    game_id: str | None = None,
    game_is_analysed: bool | None = None,
    game: dict | None = None,
    fen: str | None = None,
    phase: str | None = None,
    side_to_move: str | None = None,
    legal_move_count: int | None = None,
    selected_move: str | None = None,
    training_position: dict | None = None,
    training_session_active: bool = False,
    training_history: dict | None = None,
    review_queue: dict | None = None,
    player_profile: dict | None = None,
    recent_games: list[dict] | None = None,
    opponent_id: int | None = None,
    opponent_profile: dict | None = None,
    current_scenario: dict | None = None,
    available_predictions: dict | None = None,
    now: datetime | None = None,
) -> CoachContext:
    """Build the context, recording what is missing instead of filling it in."""
    training_position_id = None
    if training_position is not None:
        training_position_id = training_position.get("id")
    situation, notes = infer_situation(
        game_id=game_id,
        game_is_analysed=game_is_analysed,
        training_position_id=training_position_id,
        training_session_active=training_session_active,
        opponent_id=opponent_id,
        fen=fen,
        phase=phase,
    )
    mode, mode_reason = resolve_mode(situation, requested=requested_mode)

    gaps: list[str] = []
    if player_profile is None:
        gaps.append(
            "No player profile is computed for this user, so no statement about "
            "their patterns or weaknesses can be made."
        )
    if situation in (Situation.GAME_REVIEW, Situation.LIVE_GAME) and game is None:
        gaps.append("The game is not readable, so only generic chess advice is possible.")
    if situation is Situation.TRAINING and training_position is None:
        gaps.append("No exercise is loaded, so training guidance has nothing to attach to.")
    if situation is Situation.OPPONENT_PREPARATION and opponent_profile is None:
        gaps.append(
            "An opponent is selected but no opponent profile has been computed, so "
            "nothing is claimed about their habits."
        )
    if fen is None and situation in (Situation.POSITION_ANALYSIS, Situation.OPENING_STUDY):
        gaps.append("No position is loaded, so no position-specific claim can be made.")

    return CoachContext(
        user_id=user_id,
        situation=situation,
        mode=mode,
        current_game_id=game_id,
        current_game=game,
        current_fen=fen,
        side_to_move=side_to_move,
        legal_move_count=legal_move_count,
        selected_move=selected_move,
        game_phase=phase,
        player_profile=player_profile,
        recent_games=list(recent_games or []),
        training_history=training_history,
        training_position=training_position,
        review_queue=review_queue,
        opponent_id=opponent_id,
        opponent_profile=opponent_profile,
        current_scenario=current_scenario,
        available_predictions=available_predictions,
        gaps=gaps,
        notes=[*notes, mode_reason],
        assembled_at=now,
    )


def context_brief(context: CoachContext) -> dict[str, Any]:
    """A compact, serialisable view of the context for prompts and the UI.

    Deliberately narrow: it carries what the coach may refer to and nothing else,
    so a prompt cannot accidentally be richer than the evidence.
    """
    profile = context.profile
    return {
        "situation": context.situation.value,
        "mode": context.mode.value,
        "mode_label": profile.label,
        "explanation_depth": profile.explanation_depth,
        "exposure": {
            "evaluation": profile.expose_evaluation,
            "candidate_moves": profile.expose_candidate_moves,
            "principal_variation": profile.expose_principal_variation,
            "statistics": profile.expose_statistics,
        },
        "suggest_training": profile.suggest_training,
        "allowed_tool_families": list(profile.allowed_tool_families),
        "lead_with": context.lead_with(),
        "evidence_available": context.evidence_keys(),
        "gaps": list(context.gaps),
        "notes": list(context.notes),
        "methodology_version": context.methodology_version,
    }


__all__ = [
    "DEFAULT_MODE_FOR_SITUATION",
    "MODE_PROFILES",
    "TOOL_FAMILIES",
    "CoachContext",
    "CoachMode",
    "ModeProfile",
    "Situation",
    "assemble_context",
    "context_brief",
    "infer_situation",
    "mode_profile",
    "resolve_mode",
]
