"""The one place the game-visibility decision is made.

Every surface that answers a question about a stored game — the agent's tools, the
training engine, opponent intelligence, the Phase 10 scenario engine, the graph —
asks *this* module whether the caller may read that game. Nothing else decides it.

It exists as its own module rather than living inside one service because several
services need it and none may import another (that would be a cycle), and because
a security decision with several call sites and several homes is a decision that
will eventually disagree with itself.

Ownership (Phase 15) is now real, not deferred:

* a game imported by an authenticated caller is **owned** by that caller and is
  visible only to them;
* a game with no owner belongs to the **shared library** and is visible to every
  caller (a single-user/open deployment, or data imported before ownership);
* an **open** deployment (no API keys) has no caller identity, so the allowed set
  is the whole library exactly as before.

The decision is per game and fail-closed: an owned game a caller may not read is
*absent*, not forbidden, so the API never confirms it exists.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from argus.shared.errors import NotFoundError

from argus_api.db.repository import game_visible_to, list_game_ids
from argus_api.observability import current_caller_id


def _resolve_caller(caller: str | None) -> str | None:
    """Resolve the caller id for a decision.

    An explicit ``caller`` wins; otherwise the id bound to the request context is
    used. Outside a request (a background task that lost its context) there is no
    caller, and the decision is the whole library — the pre-ownership behaviour,
    which is correct for internal work.
    """
    return caller if caller is not None else current_caller_id()


def authorized_game_ids(db: Session, caller: str | None = None) -> list[str]:
    """The ids of the games this caller may read.

    ``caller`` is the authenticated caller id; when omitted it is read from the
    request context, so every existing call site inherits ownership scoping
    without changing.
    """
    return list_game_ids(db, caller=_resolve_caller(caller))


def authorized_for_game(db: Session, game_id: str, caller: str | None = None) -> bool:
    """Whether the caller may read one game. A game they cannot see does not exist."""
    return game_visible_to(db, game_id, _resolve_caller(caller))


def authorize_game(db: Session, game_id: str, caller: str | None = None) -> None:
    """Raise :class:`NotFoundError` when the caller may not read ``game_id``.

    A game outside the caller's allow-list is reported as **absent**, not
    forbidden, so the API never confirms the existence of another caller's data.
    This is the single enforcement point every read/write route calls.
    """
    if not authorized_for_game(db, game_id, caller):
        raise NotFoundError(f"Game '{game_id}' not found")


#: Alias kept for the scenario/coaching services that already use this name.
require_authorized_game = authorize_game


__all__ = [
    "authorize_game",
    "authorized_for_game",
    "authorized_game_ids",
    "require_authorized_game",
]
