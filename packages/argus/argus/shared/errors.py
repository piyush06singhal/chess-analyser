"""Domain error hierarchy shared across ARGUS Chess packages.

Every error carries a stable machine-readable ``code`` so the API layer can
map errors to HTTP responses centrally without leaking internals.
"""

from __future__ import annotations

from typing import Any


class ArgusError(Exception):
    """Base class for all ARGUS Chess domain errors."""

    code = "argus_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details or None}


# --- Validation (user-provided chess data) -----------------------------------


class ValidationError(ArgusError):
    """Raised when user-provided data fails validation."""

    code = "validation_error"


class InvalidFenError(ValidationError):
    code = "invalid_fen"


class InvalidPgnError(ValidationError):
    code = "invalid_pgn"


class InvalidMoveError(ValidationError):
    code = "invalid_move"


# --- Chess engine -------------------------------------------------------------


class EngineError(ArgusError):
    """Base class for chess engine failures."""

    code = "engine_error"


class EngineUnavailableError(EngineError):
    """Raised when no chess engine can be located or started."""

    code = "engine_unavailable"


class EngineTimeoutError(EngineError):
    """Raised when the engine does not answer within the configured timeout."""

    code = "engine_timeout"


class EngineResponseError(EngineError):
    """Raised when the engine returns output that cannot be parsed."""

    code = "engine_response"


# --- Analysis -----------------------------------------------------------------


class AnalysisError(ArgusError):
    """Raised when an analysis pipeline cannot be completed."""

    code = "analysis_error"


# --- ML ------------------------------------------------------------------------


class InsufficientDataError(ArgusError):
    """Raised when a dataset is too small or unreliable for model training."""

    code = "insufficient_data"


# --- AI agent tools ------------------------------------------------------------


class ToolNotFoundError(ArgusError):
    """Raised when an unknown tool is requested from the registry."""

    code = "tool_not_found"


class ToolUnavailableError(ArgusError):
    """Raised when a tool exists but its backing service is not built yet."""

    code = "tool_unavailable"


# --- Persistence / lookup ------------------------------------------------------


class RepositoryError(ArgusError):
    """Raised when the persistence layer fails."""

    code = "repository_error"


class NotFoundError(ArgusError):
    """Raised when a requested entity does not exist."""

    code = "not_found"
