"""The live clock sweeper (§13, §12).

A server-authoritative clock has to be *checked* by the server. If the only thing
that ever noticed a flag fall were an incoming request, a player who closed their
browser would leave a game running forever, and an opponent would have no way to
win it. So a small loop, started with the application, looks at every active
game and applies the timeout itself — through the same validated transition a
move would use, so the result, the events and the sequence are identical.

It is deliberately modest: a fixed interval, a bounded page of games, and a
failure that logs and continues rather than killing the loop. It holds no state
between passes, so a restart loses nothing — the clock is derived from stored
timestamps, not from a counter this process keeps.
"""

from __future__ import annotations

import asyncio

from argus.shared.logging import get_logger

from argus_api.db.repository import list_live_games
from argus_api.services import live_service
from argus_api.services.live_hub import HUB

logger = get_logger(__name__)

#: How often the sweeper looks at active games. Fine enough that a flag fall is
#: announced promptly, coarse enough to be free.
DEFAULT_INTERVAL_SECONDS = 2.0
#: The most games one pass inspects.
MAX_GAMES_PER_PASS = 200


def sweep_once(session_factory) -> list[str]:
    """Check every active game for a flag fall. Returns the games that timed out."""
    finished: list[str] = []
    with session_factory.session_scope() as session:
        active = list_live_games(session, status="active", limit=MAX_GAMES_PER_PASS)
        for row in active:
            try:
                result = live_service.check_timeout(session, live_game_id=row.id)
            except Exception as exc:  # noqa: BLE001 — one bad game must not stop the pass
                logger.warning("Timeout check failed [live_game=%s]: %s", row.id, exc)
                continue
            if result:
                finished.append(row.id)
                HUB.publish(row.id, result.get("events") or [])
                # A finished game has to reach the library too; the sweeper owns
                # no engine, so it hands the pipeline to the same service the
                # routes use and lets the next poll pick it up. The route-level
                # scheduler covers the common case; this covers a game that ended
                # while nobody was connected.
                logger.info("Clock fell [live_game=%s]", row.id)
    return finished


async def sweep_loop(
    session_factory, *, interval_seconds: float = DEFAULT_INTERVAL_SECONDS
) -> None:
    """Run :func:`sweep_once` forever until cancelled."""
    logger.info("Live clock sweeper started [interval=%.1fs]", interval_seconds)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await asyncio.to_thread(sweep_once, session_factory)
            except Exception as exc:  # noqa: BLE001 — the loop outlives its failures
                logger.warning("Live sweep pass failed: %s", exc)
    except asyncio.CancelledError:
        logger.info("Live clock sweeper stopped")
        raise


__all__ = ["DEFAULT_INTERVAL_SECONDS", "MAX_GAMES_PER_PASS", "sweep_loop", "sweep_once"]
