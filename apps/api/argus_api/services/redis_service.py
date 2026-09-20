"""Redis connectivity check.

Phase 1 uses a raw-socket PING so no extra client dependency is required;
the ``redis`` client and caching layer arrive in Phase 2. The check reports
honest status and never raises.
"""

from __future__ import annotations

import socket
from urllib.parse import urlparse


def check_redis(url: str) -> dict:
    """Ping a Redis instance; returns configuration/connection status."""
    if not (url or "").strip():
        return {"configured": False, "connected": False, "reason": "ARGUS_REDIS_URL is not set"}
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=1.5) as sock:
            sock.sendall(b"PING\r\n")
            reply = sock.recv(64)
    except OSError as exc:
        return {
            "configured": True,
            "connected": False,
            "host": host,
            "port": port,
            "reason": str(exc)[:200],
        }
    if reply.startswith(b"+PONG"):
        return {"configured": True, "connected": True, "host": host, "port": port}
    return {
        "configured": True,
        "connected": False,
        "host": host,
        "port": port,
        "reason": f"unexpected reply: {reply[:32]!r}",
    }
