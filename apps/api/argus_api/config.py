"""Application configuration via environment variables (no secrets in code).

Every setting is prefixed with ``ARGUS_`` and can be overridden per
environment (shell, Docker Compose, .env).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(env_prefix="ARGUS_", env_file=".env", extra="ignore")

    # Application
    env: str = "development"
    log_level: str = "INFO"
    log_format: str = "text"  # "text" | "json"

    # Chess engine (empty path triggers auto-detection)
    stockfish_path: str = ""
    engine_depth: int = 12
    engine_multipv: int = 3
    engine_timeout_seconds: float = 30.0

    # Database (empty URL disables persistence features; SQLite/PostgreSQL supported)
    database_url: str = ""

    # Cache (prepared for Phase 2; Phase 1 only reports configuration status)
    redis_url: str = ""

    # CORS
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return cached settings (one instance per process)."""
    return Settings()
