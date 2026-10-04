"""Agent context: what the agent knows about where the user is standing.

The single most valuable thing this phase adds is **board awareness**. If the user
is looking at move 27 and asks "why is this bad?", the agent must already know
which game, which ply and which move they mean — requiring a pasted FEN would make
the feature useless in practice.

The context is deliberately small. It carries identifiers and the *authorized*
game set, never rows: the tools fetch rows. It also carries the response mode and
skill hints, because the same evidence must be explainable to a beginner and to a
2100-rated player without changing what is true.

Two design rules:

* **Identifiers, not payloads.** ``active_game_id`` is a string. The whole
  database must never travel in a prompt (spec §6).
* **The authorized set is part of the context.** ``available_game_ids`` is what
  the server decided this caller may read. A tool can therefore refuse an id that
  was not authorized, without trusting the model to behave — see
  :mod:`argus.ai_agent.tools.base`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ResponseMode(str, Enum):
    """Presentation modes. The evidence is identical; only the telling changes."""

    COACH = "coach"
    ANALYST = "analyst"
    BEGINNER = "beginner"
    ADVANCED = "advanced"
    GAME_REVIEW = "game_review"
    PLAYER_COACH = "player_coach"

    @property
    def guidance(self) -> str:
        return _MODE_GUIDANCE[self]

    @property
    def target_audience(self) -> str:
        return _MODE_AUDIENCE[self]


_MODE_GUIDANCE: dict[ResponseMode, str] = {
    ResponseMode.COACH: (
        "Explain for learning: one main point, the reason behind it, and one "
        "concrete thing to do differently next time. Short paragraphs."
    ),
    ResponseMode.ANALYST: (
        "Explain the chess reasoning in full: candidate moves, the concrete line, "
        "the positional or tactical justification, and the numbers. Precise and "
        "unembellished."
    ),
    ResponseMode.BEGINNER: (
        "Assume the reader is new. Avoid jargon, and define any term you must use "
        "in plain words. One idea at a time. No long variations."
    ),
    ResponseMode.ADVANCED: (
        "Assume a strong reader. Concrete lines, exact evaluations, structural "
        "and tactical detail, and the nuance a stronger player cares about. "
        "Do not over-explain terminology."
    ),
    ResponseMode.GAME_REVIEW: (
        "Walk the game in order. Name the turning point, then the secondary "
        "moments, using move numbers and evaluations. Structured and factual."
    ),
    ResponseMode.PLAYER_COACH: (
        "Speak about the player across games: what recurs, how often, over how "
        "many analysed games, and what to practise. State sample sizes."
    ),
}

_MODE_AUDIENCE: dict[ResponseMode, str] = {
    ResponseMode.COACH: "an improving club player",
    ResponseMode.ANALYST: "a chess analyst reading a report",
    ResponseMode.BEGINNER: "a player new to chess",
    ResponseMode.ADVANCED: "an experienced tournament player",
    ResponseMode.GAME_REVIEW: "the player reviewing their own game",
    ResponseMode.PLAYER_COACH: "the player reviewing their own history",
}


class SkillContext(BaseModel):
    """What the agent may assume about the reader.

    Populated only from stored facts. When a rating is unknown this stays empty
    and the agent must use neutral language rather than guessing a level and
    pitching the explanation to it (spec §16).
    """

    rating: int | None = None
    rating_source: str | None = None
    preferred_mode: ResponseMode | None = None
    games_analyzed: int = 0

    @property
    def is_known(self) -> bool:
        return self.rating is not None

    def describe(self) -> str:
        if self.rating is None:
            return "rating unknown — use neutral language, assume nothing"
        return f"rating {self.rating} ({self.rating_source or 'stored'})"


class AgentContext(BaseModel):
    """Where the user is and what they are looking at."""

    #: Caller identity. Today Caissa is single-user; the field exists so that
    #: ownership checks are written once, against an identity, rather than
    #: retro-fitted when accounts arrive.
    user_id: str = "local"

    active_game_id: str | None = None
    #: The live game the user is *playing*, distinct from a stored game they are
    #: reviewing. A live game is a different namespace and carries fair-play
    #: permissions, so it is a separate field rather than overloading the above.
    active_live_game_id: str | None = None
    active_position_id: str | None = None
    #: The FEN the user is looking at, when it is known.
    current_fen: str | None = None
    #: The ply the user has selected. This is what makes "why was this bad?" work
    #: without the user pasting anything.
    selected_ply: int | None = None
    selected_move_san: str | None = None
    current_analysis_id: str | None = None

    player_id: str | None = None
    conversation_id: str | None = None

    #: Game ids this caller may read. Empty means "not restricted", which is the
    #: single-user behaviour; a tool still refuses ids that were checked and
    #: denied (see :mod:`argus.ai_agent.tools.base`).
    available_game_ids: list[str] = Field(default_factory=list)
    #: Live game ids this caller may read (owner or seated player).
    available_live_game_ids: list[str] = Field(default_factory=list)
    #: True when the caller is in a competitive live game, where no engine
    #: analysis may be produced during play. Set from the game's own fair-play
    #: clamp, never from the request, so an engine tool cannot be talked into
    #: running for a competitive game (spec §20, §53, §54).
    live_analysis_forbidden: bool = False

    mode: ResponseMode = ResponseMode.COACH
    skill: SkillContext = Field(default_factory=SkillContext)

    def has_game(self) -> bool:
        return bool(self.active_game_id)

    def has_position(self) -> bool:
        return bool(self.current_fen) or (
            bool(self.active_game_id) and self.selected_ply is not None
        )

    def has_live_game(self) -> bool:
        return bool(self.active_live_game_id)

    def is_authorized(self, game_id: str) -> bool:
        """True when the caller may read this game.

        An unrestricted context (no allow-list) permits everything the server
        already scoped earlier in the request path; a restricted context permits
        only its own set.
        """
        if not self.available_game_ids:
            return True
        return game_id in self.available_game_ids

    def is_live_authorized(self, live_game_id: str) -> bool:
        """True when the caller may read this live game.

        Unlike stored games, live games are *always* restricted: an empty set
        means "no live game authorized", never "all of them". A private live game
        must never be readable just because the caller omitted an identity.
        """
        return bool(live_game_id) and live_game_id in self.available_live_game_ids

    def compact(self) -> dict[str, Any]:
        """The context facts the response layer may reason about."""
        described: dict[str, Any] = {
            "mode": self.mode.value,
            "audience": self.mode.target_audience,
            "skill": self.skill.describe(),
        }
        if self.active_game_id:
            described["active_game_id"] = self.active_game_id
        if self.active_live_game_id:
            described["active_live_game_id"] = self.active_live_game_id
            described["live_analysis_forbidden"] = self.live_analysis_forbidden
        if self.selected_ply is not None:
            described["selected_ply"] = self.selected_ply
        if self.selected_move_san:
            described["selected_move"] = self.selected_move_san
        if self.current_fen:
            described["current_fen"] = self.current_fen
        if self.player_id:
            described["player_id"] = self.player_id
        return described

    def describe(self) -> str:
        """A one-line human summary of where the user is (safe to log)."""
        parts = [f"mode={self.mode.value}"]
        parts.append(f"game={self.active_game_id or 'none'}")
        if self.selected_ply is not None:
            move = f" {self.selected_move_san}" if self.selected_move_san else ""
            parts.append(f"ply={self.selected_ply}{move}")
        if self.player_id:
            parts.append(f"player={self.player_id}")
        return " ".join(parts)


__all__ = ["AgentContext", "ResponseMode", "SkillContext"]
