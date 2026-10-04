"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator
from typing import Callable

from fastapi import Request
from sqlalchemy.orm import Session

from argus.shared.errors import RepositoryError
from argus_api.observability import current_caller_id
from argus_api.rate_limit import enforce
from argus_api.security import LOCAL_CALLER


def rate_limited(bucket: str) -> Callable[[Request], None]:
    """Build a dependency that enforces one rate-limit bucket for the caller.

    The caller id comes from the authenticated request (the middleware binds it),
    never from a client field, so one user cannot spend another's budget. The
    limit itself is configuration (``ARGUS_RATE_LIMIT_*``); a limit of 0 is
    unlimited. Exceeding it raises :class:`RateLimitedError` → HTTP 429 with a
    ``Retry-After``.
    """

    def _dependency(request: Request) -> None:
        caller = current_caller_id() or LOCAL_CALLER
        enforce(request.app.state.settings, bucket=bucket, caller=caller)

    return _dependency


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a database session for the request.

    Raises:
        RepositoryError: when persistence is not configured (honest 503).
    """
    factory = request.app.state.session_factory
    if not factory.enabled:
        raise RepositoryError(
            "Database is not configured; set ARGUS_DATABASE_URL to enable persistence"
        )
    yield from factory()
