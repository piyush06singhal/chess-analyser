"""The in-process broadcast hub for live games (§9, §10, §47).

Every event a client receives travels through here exactly once, in sequence
order, to exactly the connections watching that game. It is deliberately small:
a game id maps to a set of queues, publishing puts a copy of each event on every
queue, and a subscriber that cannot keep up is dropped rather than allowed to
stall the game.

Scope and honesty
-----------------
This hub is *per process*. With one API process (today's deployment) every
subscriber sees every event. With several workers a client connected to worker A
would not see an event published on worker B — so the system does not depend on
the hub for correctness: the event log in the database is the source of truth and
a reconnecting client replays the difference it missed (``GET /api/live/{id}/sync``).
The hub is an optimisation for latency, and `docs/realtime-protocol.md` states
that a Redis fan-out is the deployment step that makes it shared.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: How many unread events one connection may fall behind by.
QUEUE_SIZE = 256


@dataclass(eq=False)
class Subscriber:
    """One open connection watching a game.

    ``eq=False`` gives identity equality and, with it, hashability: a subscriber
    is a particular connection, not a value, so two connections that happen to
    carry the same fields are still distinct members of the watcher set.
    """

    game_id: str
    player_id: int | None
    role: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_SIZE))
    dropped: int = 0

    def offer(self, event: dict) -> bool:
        """Enqueue an event. ``False`` when the client has fallen too far behind."""
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False


class LiveHub:
    """Fan-out of live events to the connections watching a game."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Subscriber]] = {}
        self._published = 0
        self._dropped_connections = 0

    def subscribe(self, *, game_id: str, player_id: int | None, role: str) -> Subscriber:
        subscriber = Subscriber(game_id=game_id, player_id=player_id, role=role)
        self._subscribers.setdefault(game_id, set()).add(subscriber)
        logger.debug(
            "Live subscriber joined [game=%s player=%s role=%s watchers=%d]",
            game_id,
            player_id,
            role,
            self.watchers(game_id),
        )
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        watchers = self._subscribers.get(subscriber.game_id)
        if not watchers:
            return
        watchers.discard(subscriber)
        if not watchers:
            self._subscribers.pop(subscriber.game_id, None)

    def publish(self, game_id: str, events: list[dict]) -> int:
        """Send events to everyone watching ``game_id``. Returns how many got through."""
        watchers = self._subscribers.get(game_id)
        if not watchers:
            return 0
        delivered = 0
        for event in events:
            for subscriber in list(watchers):
                if subscriber.offer(event):
                    delivered += 1
                else:
                    # A connection that cannot keep up is closed rather than
                    # allowed to hold the game's latency hostage; it reconnects
                    # and syncs, which is the path that is actually correct.
                    self._dropped_connections += 1
                    watchers.discard(subscriber)
                    self.unsubscribe(subscriber)
        self._published += len(events)
        return delivered

    def watchers(self, game_id: str) -> int:
        return len(self._subscribers.get(game_id, ()))

    def session_counts(self, game_id: str) -> dict[int, int]:
        """How many open sockets each player has for this game.

        More than one for a seated player means multiple tabs (or devices) are
        watching the same game. The game state stays authoritative regardless —
        a stale action from any of them is refused — but the count is surfaced so
        the UI can warn instead of silently letting two tabs fight.
        """
        counts: dict[int, int] = {}
        for subscriber in self._subscribers.get(game_id, ()):
            if subscriber.player_id is None:
                continue
            counts[subscriber.player_id] = counts.get(subscriber.player_id, 0) + 1
        return counts

    def stats(self) -> dict[str, Any]:
        return {
            "games_with_watchers": len(self._subscribers),
            "watchers": sum(len(group) for group in self._subscribers.values()),
            "events_published": self._published,
            "slow_connections_dropped": self._dropped_connections,
        }

    def reset(self) -> None:
        self._subscribers.clear()
        self._published = 0
        self._dropped_connections = 0


#: Process-wide hub, matching the process-wide engine and job registry.
HUB = LiveHub()


__all__ = ["HUB", "QUEUE_SIZE", "LiveHub", "Subscriber"]
