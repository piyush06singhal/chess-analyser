"""Context resolution: turning "this" and "that" into identifiers.

A follow-up question is usually elliptical — "why was this bad?", "what about Qf3?",
"was that my biggest mistake?" — and the agent can only answer if "this" resolves to
a real game and ply. This module owns that resolution, and it does it in one order:

1. **Explicit request context** (the client said which game and ply are open).
2. **A literal reference in this question** ("and what about move 3?" names ply 5,
   and naming a move is a *current* instruction that outranks anything remembered).
3. **Declared context** (a previous turn in this conversation asserted it).
4. **Inferred focus** (a previous turn *discussed* it — "why was 28...Nf6 bad?"
   establishes the game and ply even if the client sent none).

Explicit beats a live mention, which beats declared, which beats inferred; each step
only fills fields the earlier step left empty. The ordering is deliberate: if the
conversation is parked on move 20 and the user asks about move 3, answering about
move 20 would be technically consistent and completely wrong. The result is a context
the tools can act on, produced without the model being asked to remember anything
(spec §6, §17).

Resolution never *guesses*. If nothing in the conversation establishes a game, the
resolved context simply has no game, and the tools raise their honest "no active
game" error — which the agent turns into a question for the user rather than an
invented answer.
"""

from __future__ import annotations

import re

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.memory.conversation import ConversationMemory, Focus

#: "28...Nf6", "28. Nf6", "move 28", "move 28 Nf6". Only the third pattern's SAN is
#: optional — "move 12" names a move by number alone, and requiring a move there
#: would make the most common phrasing unparseable.
_MOVE_PATTERNS = (
    re.compile(r"\b(\d{1,3})\s*\.{2,3}\s*([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?)"),
    re.compile(r"\b(\d{1,3})\s*\.\s*([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?)"),
    re.compile(
        r"\bmove\s+(\d{1,3})\b\s*([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?)?",
        re.IGNORECASE,
    ),
)
_PLY_PATTERN = re.compile(r"\bply\s+(\d{1,3})\b", re.IGNORECASE)
_GAME_ID_PATTERN = re.compile(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b")
_FEN_PATTERN = re.compile(
    r"\b([rnbqkpRNBQKP1-8]+(?:/[rnbqkpRNBQKP1-8]+){7}\s+[wb]\s+(?:-|[KQkq]{1,4})\s+(?:-|[a-h][1-8])\s+\d+\s+\d+)\b"
)


def extract_focus(text: str) -> Focus:
    """Pull any game/ply/move/fen references out of a user message.

    Only literal references are extracted. "The opening" or "my worst move" are not
    resolved here — that is the planner's job, and the tool layer will say when the
    request does not identify anything specific enough to act on.
    """
    focus = Focus()
    if not text:
        return focus
    game = _GAME_ID_PATTERN.search(text)
    if game:
        focus.game_id = game.group(1)
    fen = _FEN_PATTERN.search(text)
    if fen:
        focus.fen = " ".join(fen.group(1).split())
    ply = _PLY_PATTERN.search(text)
    if ply:
        focus.ply = int(ply.group(1))
    else:
        for pattern in _MOVE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            move_number = int(match.group(1))
            san = match.group(2) if match.re.groups > 1 else None
            # A move number plus "..." means the black move of that number, so ply
            # is 2n; otherwise the white move, ply 2n-1.
            ellipsis = "..." in match.group(0)
            focus.ply = move_number * 2 if ellipsis else move_number * 2 - 1
            if san:
                focus.move_san = san
            break
    return focus


def resolve_context(
    *,
    explicit: AgentContext | None = None,
    memory: ConversationMemory | None = None,
    question: str = "",
) -> AgentContext:
    """Merge explicit request context, conversation memory and the question itself."""
    base = (explicit or AgentContext()).model_copy(deep=True)
    inferred = extract_focus(question)

    # The question itself comes first among the fallbacks: a move named *now* beats a
    # ply merely remembered, so "and what about move 3?" is answered about move 3.
    chain: list[Focus] = [inferred]
    if memory is not None:
        chain.append(memory.resolved_focus())
        chain.append(memory.focus)

    for focus in chain:
        if base.active_game_id is None and focus.game_id:
            base.active_game_id = focus.game_id
        if base.selected_ply is None and focus.ply is not None:
            base.selected_ply = focus.ply
        if base.selected_move_san is None and focus.move_san:
            base.selected_move_san = focus.move_san
        if base.current_fen is None and focus.fen:
            base.current_fen = focus.fen
        if base.player_id is None and focus.player_id:
            base.player_id = focus.player_id
    return base


def focus_from_context(context: AgentContext) -> Focus:
    """The focus an answered turn establishes for the next one."""
    return Focus(
        game_id=context.active_game_id,
        ply=context.selected_ply,
        move_san=context.selected_move_san,
        player_id=context.player_id,
        fen=context.current_fen,
    )


__all__ = ["extract_focus", "focus_from_context", "resolve_context"]
