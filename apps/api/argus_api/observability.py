"""Observability primitives: request context, metrics, and the audit log.

Three small, dependency-free pieces, so they work in every deployment and in the
test suite without a collector:

* **Request context** — a :class:`contextvars.ContextVar` carrying the request id
  and the caller id for the duration of a request. Background work started from a
  request inherits them (contextvars are copied into ``asyncio.to_thread``), so a
  log line emitted by an analysis job can be tied back to the request that asked
  for it. That is the correlation id the spec asks for (§18), without pretending
  to be full distributed tracing.
* **Metrics** — process-local counters and histograms rendered in the Prometheus
  text exposition format. They are real measurements of this process; they reset
  when it restarts, and that is stated where they are served. No fabricated
  uptime, no invented numbers.
* **Audit log** — a structured, append-only record of security-relevant events
  (login, data deletion, model promotion, config change). It is written through
  the logger under a dedicated name so an operator can route it separately.
"""

from __future__ import annotations

import contextvars
import json
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from argus.shared.logging import get_logger

logger = get_logger(__name__)

# --- request context ---------------------------------------------------------

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "argus_request_id", default=None
)
_caller_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "argus_caller_id", default=None
)


def set_request_id(value: str) -> None:
    """Bind the request id for the current context."""
    _request_id.set(value)


def current_request_id() -> str | None:
    """The request id bound to this context, if any."""
    return _request_id.get()


def set_caller_id(value: str | None) -> None:
    """Bind the caller id for the current context."""
    _caller_id.set(value)


def current_caller_id() -> str | None:
    """The caller id bound to this context, if any."""
    return _caller_id.get()


# --- metrics -----------------------------------------------------------------


@dataclass
class _Histogram:
    """A fixed-bucket histogram with running count and sum."""

    buckets: tuple[float, ...] = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    counts: list[int] = field(default_factory=list)
    total: int = 0
    sum_value: float = 0.0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * len(self.buckets)

    def observe(self, value: float) -> None:
        self.total += 1
        self.sum_value += value
        for index, bound in enumerate(self.buckets):
            if value <= bound:
                self.counts[index] += 1


class MetricsRegistry:
    """Process-local counters, gauges and histograms (Prometheus text format)."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], _Histogram] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _labels(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((labels or {}).items()))

    def inc(
        self, name: str, value: float = 1.0, *, labels: dict[str, str] | None = None
    ) -> None:
        with self._lock:
            self._counters[(name, self._labels(labels))] += value

    def set_gauge(
        self, name: str, value: float, *, labels: dict[str, str] | None = None
    ) -> None:
        with self._lock:
            self._gauges[(name, self._labels(labels))] = value

    def observe(
        self, name: str, value: float, *, labels: dict[str, str] | None = None
    ) -> None:
        with self._lock:
            key = (name, self._labels(labels))
            histogram = self._histograms.get(key)
            if histogram is None:
                histogram = _Histogram()
                self._histograms[key] = histogram
            histogram.observe(value)

    @staticmethod
    def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
        if not labels:
            return ""
        inner = ",".join(f'{key}="{value}"' for key, value in labels)
        return "{" + inner + "}"

    def render(self) -> str:
        """The registry as Prometheus text exposition format."""
        with self._lock:
            lines: list[str] = []
            for (name, labels), value in sorted(self._counters.items()):
                lines.append(f"{name}{self._render_labels(labels)} {value}")
            for (name, labels), value in sorted(self._gauges.items()):
                lines.append(f"{name}{self._render_labels(labels)} {value}")
            for (name, labels), histogram in sorted(self._histograms.items()):
                cumulative = 0
                for bound, count in zip(histogram.buckets, histogram.counts):
                    cumulative += count
                    bucket_labels = labels + (("le", str(bound)),)
                    lines.append(
                        f"{name}_bucket{self._render_labels(bucket_labels)} {cumulative}"
                    )
                inf_labels = labels + (("le", "+Inf"),)
                lines.append(
                    f"{name}_bucket{self._render_labels(inf_labels)} {histogram.total}"
                )
                lines.append(f"{name}_sum{self._render_labels(labels)} {histogram.sum_value}")
                lines.append(f"{name}_count{self._render_labels(labels)} {histogram.total}")
            return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


#: Process-wide registry, matching the process-wide engine and job registry.
METRICS = MetricsRegistry()


# --- audit log ---------------------------------------------------------------


def audit(event: str, **fields: Any) -> None:
    """Record a security-relevant event.

    Audit events are emitted under the ``argus.audit`` logger with a stable,
    JSON-encoded payload so an operator can route them to tamper-resistant
    storage. Secrets are never passed here; the caller is responsible for not
    putting a credential in ``fields``.
    """
    payload = {"event": event, "request_id": current_request_id(), "caller": current_caller_id()}
    payload.update(fields)
    logger.info("audit %s", json.dumps(payload, default=str, sort_keys=True))


# --- named audit events --------------------------------------------------------
#
# These are the security-relevant events Caissa records. They are named constants
# rather than free strings so a log query cannot miss an event because a call site
# spelled it differently, and so the set of audited actions is enumerable here.
#
# Every one of these is *called* from a real code path (see the call sites in
# `security.py` and the route modules). An audit event that is defined but never
# emitted is worse than none: it implies coverage that does not exist.

AUDIT_AUTH_FAILED = "auth.failed"
AUDIT_GAME_IMPORTED = "game.imported"
AUDIT_GAME_DELETED = "game.deleted"
AUDIT_ANALYSIS_STARTED = "analysis.started"
AUDIT_ANALYSIS_CANCELLED = "analysis.cancelled"
AUDIT_LIVE_GAME_CREATED = "live_game.created"
AUDIT_COLLECTION_DELETED = "collection.deleted"


__all__ = [
    "AUDIT_ANALYSIS_CANCELLED",
    "AUDIT_ANALYSIS_STARTED",
    "AUDIT_AUTH_FAILED",
    "AUDIT_COLLECTION_DELETED",
    "AUDIT_GAME_DELETED",
    "AUDIT_GAME_IMPORTED",
    "AUDIT_LIVE_GAME_CREATED",
    "METRICS",
    "MetricsRegistry",
    "audit",
    "current_caller_id",
    "current_request_id",
    "set_caller_id",
    "set_request_id",
]
