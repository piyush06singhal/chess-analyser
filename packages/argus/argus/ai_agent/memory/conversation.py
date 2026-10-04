"""Conversation memory: enough context for follow-ups, bounded and structured.

Two failure modes are avoided here.

**Repeating yourself.** After "why was 28...Nf6 bad?", a user asking "what should I
have played?" must not have to name the game and move again. So memory tracks the
*conversation focus* — the game, ply, move and player last under discussion — and
the agent resolves a follow-up against it (spec §17).

**Sending the transcript forever.** Stuffing every prior turn into every prompt is
the standard way an agent becomes slow, expensive and *worse* (the model starts
answering the history instead of the question). So memory keeps a bounded window of
full turns plus a compact digest of what came before, with an explicit character
budget. The digest is built deterministically — it is not an LLM summary — which
means it is reproducible, free, and cannot introduce a claim that was never said.

Memory is also explicitly **not** a source of player facts (spec §18). If a turn
once said "you are weak at tactics", that sentence lives here as conversational
text and nothing more. Player claims may only come from the player tools.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["user", "assistant", "tool"]

#: Full turns kept verbatim. Beyond this, turns survive only as digest lines.
DEFAULT_WINDOW = 6
#: Hard character budget for the rendered history block.
DEFAULT_MAX_CHARS = 4000
#: Characters kept per turn in the digest.
DIGEST_TURN_CHARS = 160


class Focus(BaseModel):
    """What the conversation is currently about."""

    game_id: str | None = None
    ply: int | None = None
    move_san: str | None = None
    player_id: str | None = None
    fen: str | None = None
    topic: str | None = None

    def is_empty(self) -> bool:
        return not any(
            (self.game_id, self.ply, self.move_san, self.player_id, self.fen, self.topic)
        )

    def merge(self, other: "Focus") -> "Focus":
        """Return a focus where ``other``'s known fields win over ``self``'s."""
        return Focus(
            game_id=other.game_id or self.game_id,
            ply=other.ply if other.ply is not None else self.ply,
            move_san=other.move_san or self.move_san,
            player_id=other.player_id or self.player_id,
            fen=other.fen or self.fen,
            topic=other.topic or self.topic,
        )

    def describe(self) -> str:
        parts: list[str] = []
        if self.game_id:
            parts.append(f"game {self.game_id}")
        if self.ply is not None:
            parts.append(f"ply {self.ply}" + (f" ({self.move_san})" if self.move_san else ""))
        if self.player_id:
            parts.append(f"player {self.player_id}")
        if self.fen:
            parts.append(f"position {self.fen}")
        if self.topic:
            parts.append(self.topic)
        return ", ".join(parts) if parts else "nothing in particular"


class ConversationTurn(BaseModel):
    """One exchange, with the focus it established."""

    role: Role
    text: str
    tool: str | None = None
    focus: Focus = Field(default_factory=Focus)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def line(self, limit: int = DIGEST_TURN_CHARS) -> str:
        speaker = {"user": "User", "assistant": "Coach", "tool": "Tool"}[self.role]
        text = " ".join(self.text.split())
        if len(text) > limit:
            text = text[: limit - 1].rstrip() + "…"
        return f"{speaker}: {text}"


class ConversationMemory(BaseModel):
    """Bounded, structured short-term memory."""

    conversation_id: str | None = None
    turns: list[ConversationTurn] = Field(default_factory=list)
    window: int = DEFAULT_WINDOW
    max_chars: int = DEFAULT_MAX_CHARS
    #: The resolved conversation focus (updated as turns arrive).
    focus: Focus = Field(default_factory=Focus)
    #: Facts the conversation has established about which game/position is open.
    #: Kept separate from `focus` because these are *context* assertions supplied
    #: by the client, not inferences from prose.
    declared: Focus = Field(default_factory=Focus)

    # --- writing -------------------------------------------------------------

    def add(
        self,
        role: Role,
        text: str,
        *,
        tool: str | None = None,
        focus: Focus | None = None,
    ) -> ConversationTurn:
        turn = ConversationTurn(role=role, text=text, tool=tool, focus=focus or Focus())
        self.turns.append(turn)
        if focus is not None:
            self.focus = self.focus.merge(focus)
        return turn

    def add_user(self, text: str, *, focus: Focus | None = None) -> ConversationTurn:
        return self.add("user", text, focus=focus)

    def add_assistant(
        self, text: str, *, tools: list[str] | None = None, focus: Focus | None = None
    ) -> ConversationTurn:
        turn = self.add("assistant", text, focus=focus)
        if tools:
            turn.tool = ", ".join(tools[:6])
        return turn

    def declare(self, **fields: Any) -> None:
        """Record a context fact asserted by the client (game/ply/player/fen)."""
        self.declared = self.declared.merge(Focus(**fields))

    # --- reading -------------------------------------------------------------

    def recent(self, count: int | None = None) -> list[ConversationTurn]:
        return self.turns[-(count or self.window) :]

    def older(self) -> list[ConversationTurn]:
        return self.turns[: -self.window] if len(self.turns) > self.window else []

    def digest(self) -> str:
        """A compact, deterministic summary of turns outside the window."""
        older = self.older()
        if not older:
            return ""
        lines = [turn.line() for turn in older]
        return "Earlier in this conversation:\n" + "\n".join(f"  {line}" for line in lines[-10:])

    def render(self, *, max_chars: int | None = None) -> str:
        """The history block for the prompt, bounded and newest-last."""
        budget = max_chars or self.max_chars
        parts: list[str] = []
        digest = self.digest()
        if digest:
            parts.append(digest)
        for turn in self.recent():
            line = turn.line()
            suffix = f" [tools: {turn.tool}]" if turn.tool else ""
            parts.append(f"{line}{suffix}")
        rendered = "\n".join(parts)
        if len(rendered) <= budget:
            return rendered
        # Keep the newest text: a bounded history must favour the present turn.
        trimmed = rendered[-budget:]
        return "(earlier history truncated)\n" + trimmed

    def resolved_focus(self) -> Focus:
        """The focus a follow-up question should be answered against."""
        return self.declared.merge(self.focus)

    def to_messages(self) -> list[dict[str, str]]:
        """Provider-neutral message list for the recent window."""
        return [
            {"role": "user" if turn.role == "user" else "assistant", "content": turn.text}
            for turn in self.recent()
            if turn.text
        ]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ConversationMemory":
        if not payload:
            return cls()
        return cls.model_validate(payload)

    @classmethod
    def from_messages(
        cls, messages: list[dict[str, Any]] | None
    ) -> "ConversationMemory | None":
        """Build memory from the provider-neutral ``{role, content}`` message list.

        The request carries history in the same shape the response is written in, so a
        client never has to know this module's internal turn model — and the route does
        not have to hand-translate, which is how a follow-up silently started failing.
        Unknown roles and empty entries are skipped rather than rejected: history is
        context, not data, and a stray entry must not fail an otherwise good turn.
        """
        if not messages:
            return None
        memory = cls()
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            text = str(message.get("content") or message.get("text") or "").strip()
            if not text or role not in ("user", "assistant", "tool"):
                continue
            memory.add(role, text)  # type: ignore[arg-type]
        return memory if memory.turns else None


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_WINDOW",
    "ConversationMemory",
    "ConversationTurn",
    "Focus",
    "Role",
]
