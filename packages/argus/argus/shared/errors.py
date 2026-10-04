"""Domain error hierarchy shared across Caissa packages.

Every error carries a stable machine-readable ``code`` so the API layer can
map errors to HTTP responses centrally without leaking internals.
"""

from __future__ import annotations

from typing import Any


class ArgusError(Exception):
    """Base class for all Caissa domain errors."""

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


class UnsupportedSourceError(ValidationError):
    """Raised when an import source is unknown or not implemented yet."""

    code = "unsupported_source"


class UploadError(ValidationError):
    """Raised when an uploaded file is rejected (type, size, or content)."""

    code = "upload_error"


# --- External game sources (Chess.com, Lichess, …) ----------------------------


class SourceError(ArgusError):
    """Base class for failures talking to an external game source."""

    code = "source_error"


class SourcePlayerNotFoundError(SourceError):
    """Raised when a source has no such player (or publishes no games for them)."""

    code = "source_player_not_found"


class SourceUnavailableError(SourceError):
    """Raised when an external game source cannot be reached or errors out."""

    code = "source_unavailable"


class SourceRateLimitedError(SourceError):
    """Raised when an external game source refuses further requests for now."""

    code = "source_rate_limited"


class SourceResponseError(SourceError):
    """Raised when an external game source returns a payload Caissa cannot read."""

    code = "source_response"


# --- Chess engine -------------------------------------------------------------


class EngineError(ArgusError):
    """Base class for chess engine failures."""

    code = "engine_error"


class EngineNotFoundError(ArgusError):
    """Raised at startup when no engine binary can be located."""

    code = "engine_unavailable"


class AnalysisCancelledError(ArgusError):
    """Raised when an analysis run is cancelled by the user."""

    code = "analysis_cancelled"


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


class AnalysisRequiredError(ArgusError):
    """Raised when structured intelligence is requested without stored engine analysis.

    The game-intelligence layer reads the engine's stored output; it never runs
    Stockfish itself, so it refuses to invent sections instead of asking for a
    real analysis run first.
    """

    code = "analysis_required"


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


class ConflictError(ArgusError):
    """Raised when a request collides with an existing entity (e.g. a name).

    Distinct from a validation error: the request is well-formed, but the state
    it targets already exists. The API maps this to 409 so a client can tell a
    real conflict from a malformed payload.
    """

    code = "conflict"


# --- Operational (Phase 15) ----------------------------------------------------


class ServiceBusyError(ArgusError):
    """Raised when a bounded resource (engine slots, queue) is full.

    Mapped to HTTP 503 with a ``Retry-After`` so a caller backs off instead of
    piling more work onto a machine that is already at capacity. It is a real
    refusal with a reason, never a silent drop.
    """

    code = "service_busy"


class ConfigurationError(ArgusError):
    """Raised when a deployment's configuration is invalid for its environment.

    Distinct from a request validation error: this is a startup-time condition.
    ``validate_settings`` collects every problem and reports them together, so a
    misconfigured deployment fails with the whole list rather than one item at a
    time.
    """

    code = "configuration_error"


class FeatureDisabledError(ArgusError):
    """Raised when a route is gated behind a feature flag that is off.

    Mapped to HTTP 404: a feature that is disabled in this deployment is not
    announced, exactly as a game the caller may not read is not announced. The
    refusal is a first-class error with the flag name in its details, never a
    silent no-op.
    """

    code = "feature_disabled"
