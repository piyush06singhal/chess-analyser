"""Authentication and per-caller ownership.

Caissa has no user accounts yet, and this module does not invent one. What it
adds is the *seam* every authenticated surface needs, made real and enforced:

* **An API key is a caller identity.** When ``ARGUS_API_KEYS`` is configured,
  each key is mapped to a caller id and a role; requests without a valid key are
  refused (401). When it is empty (today's local/single-user deployment) the
  deployment is explicitly **open**, and every request is the ``local`` caller.
  That is a configuration decision, not an accident, and it is reported by
  ``/ready`` as ``auth.mode``.
* **Ownership is data-driven, not caller-supplied.** A caller sees games they
  own plus public games, plus everything in open mode. The caller id comes from
  the authenticated key — never from a header or body field a client can set.
* **Fail closed.** An authenticated caller with no ownership row sees nothing;
  they are never granted the whole library.

When real accounts arrive, the game→owner link becomes a real foreign key and
this module is the only thing that changes.
"""

from __future__ import annotations

import hmac

from fastapi import Request

from argus.shared.errors import ArgusError
from argus_api.observability import AUDIT_AUTH_FAILED, audit

#: The caller id used when authentication is disabled (open deployment).
LOCAL_CALLER = "local"

#: The role granted to a valid API key that carries no explicit role.
DEFAULT_ROLE = "operator"


class AuthenticationError(ArgusError):
    """Raised when a request presents no valid credential on a protected deployment."""

    code = "unauthorized"


class RateLimitedError(ArgusError):
    """Raised when a caller exceeds a rate limit (mapped to HTTP 429)."""

    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: float, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


def parse_api_keys(raw: str) -> dict[str, dict[str, str]]:
    """Parse ``ARGUS_API_KEYS`` into ``{key: {caller, role}}``.

    Format: ``key:caller:role`` entries separated by commas, e.g.
    ``abc123:alice:operator,def456:bob:viewer``. A missing role defaults to
    ``operator``. Malformed entries are skipped rather than crashing startup —
    a typo in one key must not take the service down, and the key simply does not
    authenticate.
    """
    keys: dict[str, dict[str, str]] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        key = parts[0].strip()
        caller = parts[1].strip()
        role = parts[2].strip() if len(parts) > 2 and parts[2].strip() else DEFAULT_ROLE
        keys[key] = {"caller": caller, "role": role}
    return keys


def authenticate(request: Request) -> tuple[str, str]:
    """Resolve the caller id and role for a request.

    Returns ``(caller_id, role)``. In open mode returns ``(LOCAL_CALLER, role)``
    without requiring a key.

    Raises:
        AuthenticationError: when keys are configured and the request presents
            none, or a key that does not match any configured key.
    """
    settings = request.app.state.settings
    keys = parse_api_keys(getattr(settings, "api_keys", "") or "")
    if not keys:
        return LOCAL_CALLER, "operator"

    presented = _presented_key(request)
    if not presented:
        # A failed authentication is a security event and is audited: the caller
        # id is not known here, so only the request id and the reason are recorded.
        audit(AUDIT_AUTH_FAILED, reason="missing_key")
        raise AuthenticationError(
            "This deployment requires an API key; send it as the "
            "'X-API-Key' header (or 'Authorization: Bearer <key>').",
            details={"header": "X-API-Key"},
        )
    # Compare against every configured key with a constant-time compare so a
    # wrong key cannot be distinguished from a right-but-later one by timing.
    for candidate, identity in keys.items():
        if hmac.compare_digest(candidate, presented):
            return identity["caller"], identity["role"]
    audit(AUDIT_AUTH_FAILED, reason="invalid_key")
    raise AuthenticationError("The presented API key is not valid.", details={"header": "X-API-Key"})


def _presented_key(request: Request) -> str | None:
    header = request.headers.get("x-api-key")
    if header:
        return header.strip()
    authorization = request.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def is_open(settings) -> bool:  # noqa: ANN001 — argus_api.config.Settings
    """Whether authentication is disabled (no keys configured)."""
    return not parse_api_keys(getattr(settings, "api_keys", "") or "")


__all__ = [
    "DEFAULT_ROLE",
    "LOCAL_CALLER",
    "AuthenticationError",
    "RateLimitedError",
    "authenticate",
    "is_open",
    "parse_api_keys",
]
