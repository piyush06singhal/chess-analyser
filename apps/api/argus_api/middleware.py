"""Cross-cutting HTTP middleware: request ids, security headers, size limits, metrics.

These are ASGI middleware rather than route-level code so every request — including
one that fails before reaching a route — gets a request id, the security headers,
and a metrics observation. A request id on a 500 is the difference between a
report an operator can act on and one they cannot.
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from argus_api.observability import METRICS, set_caller_id, set_request_id
from argus_api.security import LOCAL_CALLER, authenticate, is_open

#: The header a caller may use to *propose* a request id (useful for correlating a
#: browser trace with the server log). It is never trusted as-is: a non-empty
#: value is length-capped, and a missing/invalid one is generated.
REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 128

#: Liveness and readiness paths that must answer without a credential.
#:
#: A healthcheck cannot present a key: the Docker ``HEALTHCHECK`` in
#: ``docker/Dockerfile.api``, the ``http_service.checks`` in ``fly.toml``, a
#: compose ``condition: service_healthy`` and an orchestrator's readiness probe
#: all issue a bare request. Requiring a key on these paths made every keyed
#: deployment report itself unhealthy and never pass a gate — the probes failed
#: while the service was fine. They reveal dependency *state*, never user data:
#: no caller, no game, no credential is named in the response.
#:
#: ``/metrics`` is deliberately NOT here. A scraper can send a header, and the
#: counters are operational detail rather than public information.
UNAUTHENTICATED_PATHS = frozenset({"/health", "/ready", "/health/ready"})


def _request_id_from(request: Request) -> str:
    proposed = request.headers.get(REQUEST_ID_HEADER)
    if proposed and 0 < len(proposed) <= MAX_REQUEST_ID_LENGTH and proposed.isascii():
        return proposed
    return uuid.uuid4().hex


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request id and caller id for the request, and echo the id back.

    Authentication is resolved here (not only in route dependencies) so the
    caller id is available to the rate limiter, the audit log and the metrics for
    every request. A request that fails authentication still gets a request id.
    """

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        request_id = _request_id_from(request)
        set_request_id(request_id)
        request.state.request_id = request_id

        # A CORS preflight carries no credentials by design, so authentication is
        # not attempted for it: the CORSMiddleware must be free to answer it. The
        # real request that follows is authenticated normally.
        if request.method == "OPTIONS":
            set_caller_id(None)
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response

        # Resolve the caller. In open mode this is always the local caller and
        # cannot fail; in keyed mode an invalid key is refused here, centrally,
        # rather than in every route.
        #
        # A liveness or readiness probe is answered without a credential (see
        # UNAUTHENTICATED_PATHS). It binds the same local identity an open
        # deployment would, so the probe is still metered, rate-limited and
        # audited, and the rest of the pipeline — security headers, size limits,
        # metrics — runs exactly as it does for an authenticated request.
        if request.url.path in UNAUTHENTICATED_PATHS:
            caller, role = LOCAL_CALLER, "operator"
        else:
            try:
                caller, role = authenticate(request)
            except Exception as exc:  # noqa: BLE001 — ArgusError subclasses; handled centrally
                from argus.shared.errors import ArgusError

                if isinstance(exc, ArgusError):
                    status = 401 if exc.code == "unauthorized" else 500
                    return JSONResponse(
                        status_code=status,
                        content={"error": {**exc.to_dict(), "request_id": request_id}},
                        headers={REQUEST_ID_HEADER: request_id},
                    )
                raise
        set_caller_id(caller)
        request.state.caller_id = caller
        request.state.caller_role = role

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach production HTTP security headers to every response.

    The CSP is deliberately conservative for a JSON API (``default-src 'none'``):
    the API returns data, not documents, so it needs no scripts, styles or frames.
    The web app sets its own CSP in ``next.config``.
    """

    #: Headers applied to every response. HSTS is only meaningful over HTTPS and
    #: is included because a production deployment terminates TLS in front of the
    #: app; browsers ignore it on plain HTTP.
    HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-site",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        response = await call_next(request)
        for name, value in self.HEADERS.items():
            response.headers.setdefault(name, value)
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject an oversized request body before it is read into memory.

    ``Content-Length`` is checked when present; a chunked body without it is
    checked again as it is read. The limit is configuration
    (``ARGUS_MAX_REQUEST_BYTES``) and 0 disables it.
    """

    def __init__(self, app, *, max_bytes: int) -> None:  # noqa: ANN001
        super().__init__(app)
        self.max_bytes = max_bytes

    def _limit(self, request: Request) -> int:
        """The effective limit: the live setting when present, else the constructor value."""
        settings = getattr(request.app.state, "settings", None)
        configured = getattr(settings, "max_request_bytes", None)
        return int(configured) if configured is not None else self.max_bytes

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        limit = self._limit(request)
        if limit > 0:
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                request_id = getattr(request.state, "request_id", None)
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "payload_too_large",
                            "message": f"Request body exceeds the {limit}-byte limit.",
                            "details": {"max_bytes": limit},
                            "request_id": request_id,
                        }
                    },
                    headers={REQUEST_ID_HEADER: request_id} if request_id else None,
                )
        return await call_next(request)


class FeatureGateMiddleware(BaseHTTPMiddleware):
    """Return 404 for any request to a path prefix whose feature flag is off (§29).

    Enforced by path rather than a route dependency so both HTTP and WebSocket
    routes are covered uniformly (a WebSocket scope has no ``Request`` for a
    dependency to accept). A disabled feature is a 404 — not advertised — and the
    body names the flag so an operator can tell "off here" from "not built".
    """

    #: Path prefix → feature flag. Ordered longest-first so a nested prefix wins.
    GATES: tuple[tuple[str, str], ...] = (
        ("/api/predictions", "predictions"),
        ("/api/coach", "coach"),
        ("/api/training", "training"),
        ("/api/scenarios", "scenarios"),
        ("/api/live", "live_chess"),
        ("/api/graph", "graph"),
    )

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        from argus_api.feature_flags import KNOWN_FLAGS, is_enabled

        settings = getattr(request.app.state, "settings", None)
        if settings is not None:
            path = request.url.path
            for prefix, flag in self.GATES:
                if path == prefix or path.startswith(prefix + "/"):
                    if flag in KNOWN_FLAGS and not is_enabled(settings, flag):
                        request_id = getattr(request.state, "request_id", None)
                        return JSONResponse(
                            status_code=404,
                            content={
                                "error": {
                                    "code": "feature_disabled",
                                    "message": (
                                        f"The '{flag}' feature is disabled in this deployment."
                                    ),
                                    "details": {"flag": flag},
                                    "request_id": request_id,
                                }
                            },
                            headers={REQUEST_ID_HEADER: request_id} if request_id else None,
                        )
                    break
        return await call_next(request)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record request count, status and latency per route template."""

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        started = time.perf_counter()
        response: Response = await call_next(request)
        elapsed = time.perf_counter() - started
        # The route template (``/api/games/{game_id}``) is the useful label; the
        # raw path would create one series per game id and explode cardinality.
        route = request.scope.get("route")
        path = getattr(route, "path", None) or "unmatched"
        method = request.method
        METRICS.inc(
            "argus_http_requests_total",
            labels={"method": method, "path": path, "status": str(response.status_code)},
        )
        METRICS.observe(
            "argus_http_request_duration_seconds",
            elapsed,
            labels={"method": method, "path": path},
        )
        return response


__all__ = [
    "MAX_REQUEST_ID_LENGTH",
    "REQUEST_ID_HEADER",
    "BodySizeLimitMiddleware",
    "FeatureGateMiddleware",
    "MetricsMiddleware",
    "RequestContextMiddleware",
    "SecurityHeadersMiddleware",
    "LOCAL_CALLER",
    "is_open",
]
