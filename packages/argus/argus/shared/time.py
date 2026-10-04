"""UTC time helpers shared by every Caissa layer.

There is exactly one definition of "now" and of "coerce a stored timestamp to
UTC" in Caissa. Before this module existed, the DB models, the graph models, the
live game, the graph store, the live service and the training service each
carried their own copy of the same one-liner — and they did not all agree (some
returned an aware non-UTC value unchanged, some normalised it). Everything Caissa
writes is UTC, so a stored timestamp is only ever *interpreted* as UTC, never as
the server's local time.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """The current time, timezone-aware and in UTC."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Coerce ``value`` to a timezone-aware UTC datetime, or ``None``.

    A naive value is *interpreted* as UTC. That is the correct reading, not a
    guess: Postgres round-trips a timezone but SQLite returns
    ``DateTime(timezone=True)`` columns naive, and treating a stored naive value
    as local time would shift every comparison by the operator's offset.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utcnow_or(value: datetime | None = None) -> datetime:
    """``value`` coerced to aware UTC, or the current time when it is ``None``.

    One definition for the widespread ``now = value or datetime.now(utc)``
    idiom, so a caller cannot accidentally keep a naive value.
    """
    coerced = as_utc(value)
    return coerced if coerced is not None else utcnow()


__all__ = ["as_utc", "utcnow", "utcnow_or"]
