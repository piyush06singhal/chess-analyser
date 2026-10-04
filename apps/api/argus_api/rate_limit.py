"""Rate limiting for expensive operations (§14).

A fixed-window counter, keyed by ``(caller, bucket)``. It is deliberately small
and dependency-free so it works in every deployment:

* **Per caller.** The caller id comes from the authenticated key (or ``local`` in
  open mode), never from a client-supplied field — so one user cannot exhaust
  another's budget by naming them.
* **Bounded memory.** Expired windows are evicted on access, so the table cannot
  grow without bound even under a flood of distinct callers.
* **Honest limits.** Limits are configuration (``ARGUS_RATE_LIMIT_*``); a bucket
  with a limit of 0 is unlimited. When a limit is hit the response is a real 429
  with a ``Retry-After`` value.

The limiter is per process. A multi-process deployment should back it with Redis
(the interface is the two methods below), which is stated in
``docs/production/reliability-model.md`` rather than pretended to be shared today.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from argus_api.security import RateLimitedError


@dataclass
class _Window:
    count: int
    reset_at: float


class RateLimiter:
    """A fixed-window limiter: ``limit`` requests per ``window_seconds`` per key."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._windows: dict[tuple[str, str], _Window] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def check(self, *, bucket: str, key: str, limit: int, window_seconds: float) -> None:
        """Record one request, raising when the limit is exceeded.

        Raises:
            RateLimitedError: when ``limit`` > 0 and the window is full.
        """
        if limit <= 0:
            return
        now = self._clock()
        identity = (bucket, key)
        with self._lock:
            window = self._windows.get(identity)
            if window is None or now >= window.reset_at:
                self._windows[identity] = _Window(count=1, reset_at=now + window_seconds)
                self._evict(now)
                return
            if window.count >= limit:
                raise RateLimitedError(
                    f"Rate limit exceeded for '{bucket}': {limit} request(s) per "
                    f"{window_seconds:g}s.",
                    retry_after=max(0.0, window.reset_at - now),
                    details={"bucket": bucket, "limit": limit, "window_seconds": window_seconds},
                )
            window.count += 1

    def _evict(self, now: float) -> None:
        """Drop expired windows (called while holding the lock, on a new window)."""
        expired = [key for key, window in self._windows.items() if now >= window.reset_at]
        for key in expired:
            self._windows.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


#: Process-wide limiter.
LIMITER = RateLimiter()


#: Bucket name → (limit attribute on Settings, window attribute). Keeping the
#: mapping here means a route names a bucket and the limit lives in configuration.
BUCKETS: dict[str, tuple[str, str]] = {
    "analysis": ("rate_limit_analysis_per_minute", "rate_limit_window_seconds"),
    "import": ("rate_limit_import_per_minute", "rate_limit_window_seconds"),
    "coach": ("rate_limit_coach_per_minute", "rate_limit_window_seconds"),
    "upload": ("rate_limit_upload_per_minute", "rate_limit_window_seconds"),
    "search": ("rate_limit_search_per_minute", "rate_limit_window_seconds"),
}


def enforce(settings, *, bucket: str, caller: str) -> None:  # noqa: ANN001
    """Enforce the configured limit for ``bucket`` and ``caller``."""
    spec = BUCKETS.get(bucket)
    if spec is None:
        return
    limit = int(getattr(settings, spec[0], 0) or 0)
    window = float(getattr(settings, spec[1], 60.0) or 60.0)
    LIMITER.check(bucket=bucket, key=caller, limit=limit, window_seconds=window)


__all__ = ["BUCKETS", "LIMITER", "RateLimiter", "enforce"]
