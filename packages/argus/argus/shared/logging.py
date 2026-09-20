"""Structured logging configuration for ARGUS Chess.

Supports human-readable (default) and JSON output selected via configuration.
All packages log through :func:`get_logger` so formatting stays consistent.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", log_format: str = "text") -> None:
    """Configure root logging once per process.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR).
        log_format: ``text`` for human-readable output, ``json`` for structured
            output suited to log aggregation.
    """
    global _CONFIGURED
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    if log_format.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s — %(message)s")
        )
    root.handlers = [handler]
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger, configuring defaults on first use."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
