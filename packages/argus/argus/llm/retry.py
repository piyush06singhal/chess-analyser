"""A brief, capped retry for provider calls that come back rate-limited.

Free-tier providers (Groq, OpenAI, Anthropic) throttle with HTTP 429, and a
short, backoff-spaced wait almost always clears it. Without a retry a single
throttled call degrades the whole turn to "I can only show you the stored
evidence" — which is an honest answer, but a worse product than the same answer
one second later.

Deliberately narrow:

* **Only 429 is retried.** A bad key, a malformed request or a 500 will not
  clear by waiting; retrying those would hide the real error behind extra
  latency, so they propagate immediately.
* **Capped.** Three attempts maximum, with exponential backoff and a ceiling on
  any single wait, so a hard quota limit still fails fast rather than turning a
  request into a hang.
* **Observable.** Every retry is logged with its delay and attempt number, so a
  deployment can see when it is living on retries.

The caller keeps its own error handling: after the last attempt the response is
returned as-is, and the client raises its usual honest ``LLMRequestError``.
"""

from __future__ import annotations

import time
from typing import Callable

import httpx

from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: Extra attempts after the first.
MAX_ATTEMPTS = 3
#: Delay before the first retry; doubles each time.
BASE_DELAY_SECONDS = 0.75
#: A ``Retry-After`` header longer than this is not honoured — the request must
#: fail rather than block a request thread indefinitely.
MAX_DELAY_SECONDS = 5.0


def _retry_delay(response: httpx.Response, attempt: int) -> float | None:
    """The wait before attempt ``attempt+1``, or ``None`` to give up now.

    A short ``Retry-After`` is honoured exactly. A longer one means the provider
    is asking for a wait this request must not make, so the caller stops
    retrying and surfaces the 429 immediately rather than stalling the request.
    """
    header = response.headers.get("retry-after", "")
    try:
        offered = float(header)
    except (TypeError, ValueError):
        offered = 0.0
    if offered > MAX_DELAY_SECONDS:
        return None
    if offered > 0:
        return offered
    return min(BASE_DELAY_SECONDS * (2 ** (attempt - 1)), MAX_DELAY_SECONDS)


def post_with_rate_limit_retry(
    post: Callable[[], httpx.Response],
    *,
    provider: str,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
) -> httpx.Response:
    """Call ``post()``, retrying only while the provider answers 429.

    Returns the final response either way, so the caller's existing status-code
    handling is unchanged — after the last attempt the caller raises the same
    rate-limit error it always did.

    ``sleep`` is resolved at call time rather than defaulted at import, so a
    test can substitute it without waiting out the real backoff.
    """
    pause = sleep or time.sleep
    attempt = 0
    while True:
        response = post()
        if response.status_code != 429:
            return response
        attempt += 1
        if attempt >= max_attempts:
            return response
        delay = _retry_delay(response, attempt)
        if delay is None:
            logger.warning(
                "%s asked for a wait longer than %.0fs (429); not retrying",
                provider,
                MAX_DELAY_SECONDS,
            )
            return response
        logger.warning(
            "%s rate limited (429); retry %d/%d in %.2fs",
            provider,
            attempt + 1,
            max_attempts,
            delay,
        )
        pause(delay)


__all__ = ["post_with_rate_limit_retry"]
