"""Database session management.

Sync SQLAlchemy 2.0 engine: FastAPI route handlers defined with ``def`` run
in the threadpool automatically, which also suits the blocking engine calls.
The URL comes from configuration (PostgreSQL in production, SQLite supported
for local development and tests). When no URL is configured, the repository
reports persistence as unavailable instead of failing obscurely.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from argus_api.config import Settings
from argus.shared.logging import get_logger

logger = get_logger(__name__)


def build_engine(settings: Settings) -> Engine | None:
    """Build a SQLAlchemy engine from settings; ``None`` when not configured."""
    url = settings.database_url.strip()
    if not url:
        return None
    engine = create_engine(url, pool_pre_ping=True, future=True)
    logger.info("Database engine created [url=%s]", _safe_url(url))
    return engine


def _safe_url(url: str) -> str:
    """Mask credentials in a database URL for logging."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    credentials = rest.rsplit("@", 1)[0]
    return f"{scheme}://***@{rest[len(credentials) + 1:]}"


class SessionFactory:
    """Produces short-lived sessions; ``None`` when persistence is disabled."""

    def __init__(self, engine: Engine | None) -> None:
        self._engine = engine
        self._maker = sessionmaker(bind=engine, expire_on_commit=False) if engine else None

    @property
    def enabled(self) -> bool:
        return self._maker is not None

    def __call__(self) -> Generator[Session, None, None]:
        if self._maker is None:
            raise RuntimeError("Database is not configured")
        session = self._maker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def check_connection(engine: Engine | None) -> dict:
    """Ping the database; returns honest status without raising."""
    if engine is None:
        return {"configured": False, "connected": False, "reason": "ARGUS_DATABASE_URL is not set"}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — report any driver failure honestly
        return {"configured": True, "connected": False, "reason": str(exc)[:200]}
    return {"configured": True, "connected": True, "dialect": engine.dialect.name}
