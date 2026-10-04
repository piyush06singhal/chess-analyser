"""Redis connectivity check.

A raw-socket PING keeps this dependency-free (no ``redis`` client required for a
liveness probe). The check reports honest status and never raises.

The Phase 15 hardening gives Redis a password, so the probe must authenticate
before it pings: a check that cannot send ``AUTH`` reports a healthy, password-
protected Redis as down, which is a false negative. Credentials come from the
URL (``redis://:password@host:port/db``), the same place the rest of the system
reads them.
"""

from __future__ import annotations

import socket
from urllib.parse import unquote, urlparse

#: Longest reply we read for a probe. AUTH/PING replies are one line; anything
#: longer is truncated rather than buffered.
_MAX_REPLY_BYTES = 256


def _read_line(sock: socket.socket) -> bytes:
    """Read one RESP reply line (up to the first CRLF)."""
    buffer = b""
    while b"\r\n" not in buffer and len(buffer) < _MAX_REPLY_BYTES:
        chunk = sock.recv(_MAX_REPLY_BYTES)
        if not chunk:
            break
        buffer += chunk
    return buffer.split(b"\r\n", 1)[0]


def check_redis(url: str) -> dict:
    """Ping a Redis instance; returns configuration/connection status.

    When the URL carries a password the probe authenticates first, so a
    password-protected instance reports ``connected: true`` instead of an
    ``-NOAUTH`` false negative.
    """
    if not (url or "").strip():
        return {"configured": False, "connected": False, "reason": "ARGUS_REDIS_URL is not set"}
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    password = unquote(parsed.password) if parsed.password else ""
    username = unquote(parsed.username) if parsed.username else ""

    def failure(reason: str) -> dict:
        return {
            "configured": True,
            "connected": False,
            "host": host,
            "port": port,
            "reason": reason[:200],
        }

    try:
        with socket.create_connection((host, port), timeout=1.5) as sock:
            if password:
                # RESP2 AUTH. Include the username only when the URL names one;
                # an empty username would be rejected as a malformed command.
                auth = (
                    f"*3\r\n$4\r\nAUTH\r\n${len(username)}\r\n{username}\r\n"
                    f"${len(password)}\r\n{password}\r\n"
                    if username
                    else f"*2\r\n$4\r\nAUTH\r\n${len(password)}\r\n{password}\r\n"
                )
                sock.sendall(auth.encode("utf-8"))
                auth_reply = _read_line(sock)
                if not auth_reply.startswith(b"+OK"):
                    return failure(f"authentication failed: {auth_reply[:32]!r}")
            sock.sendall(b"PING\r\n")
            reply = _read_line(sock)
    except OSError as exc:
        return failure(str(exc))

    if reply.startswith(b"+PONG"):
        return {"configured": True, "connected": True, "host": host, "port": port}
    return failure(f"unexpected reply: {reply[:32]!r}")
