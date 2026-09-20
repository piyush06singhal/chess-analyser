"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from argus.shared.errors import RepositoryError


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
