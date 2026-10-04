# Observability

Metrics, request correlation, health, and the service-level indicators Caissa
actually measures. No fabricated numbers: every value here comes from a real
counter or probe.

## Metrics

`GET /metrics` serves the process's counters, gauges and histograms in Prometheus
text exposition format (`argus_api.observability.METRICS`). They are **this
process's** measurements and reset on restart; a multi-process deployment scrapes
each process (see [reliability-model.md](reliability-model.md)).

Recorded:

* `argus_http_requests_total{method,path,status}` — labelled by **route
  template** (`/api/games/{game_id}`), never the raw path, so cardinality stays
  bounded.
* `argus_http_request_duration_seconds{method,path}` — a fixed-bucket histogram.

The registry is dependency-free (no `prometheus_client`), so it works in every
deployment and in tests without a collector.

## Request correlation

`RequestContextMiddleware` binds a request id and a caller id for the duration of
a request via `contextvars`. Background work started from a request inherits them
(contextvars are copied into `asyncio.to_thread`), so a log line from an analysis
job ties back to the request that asked for it. A caller may *propose* a request
id via `X-Request-ID`; it is length-capped and echoed back, and a missing/invalid
one is generated. Every error response carries the request id, so a support
report maps to a log line.

## Logging

* `ARGUS_LOG_FORMAT=text` (development) or `json` (production/staging). JSON logs
  are aggregatable.
* Audit events go under the `argus.audit` logger with a stable JSON payload.
* Credentials are never logged; `/ready` reports provider *configuration*, never
  material.

## Health

| Endpoint | Use |
| --- | --- |
| `GET /health` | liveness — the process is up; dependencies reported per field |
| `GET /ready` | readiness — can this instance serve traffic right now? |
| `GET /metrics` | Prometheus scrape |

`/ready` is honest: `database` and `stockfish` block; everything else is
reported. Nothing is stubbed to return "ok".

## Service-level indicators (SLIs) and objectives (SLOs)

These are the indicators Caissa can measure today. The objectives are stated as
targets to alert on, not as achieved facts.

| SLI | How it is measured | Objective |
| --- | --- | --- |
| Availability | `/ready` returns `ready` | ≥ 99% of 1-minute samples |
| HTTP error rate | `argus_http_requests_total{status=~"5.."}` / total | < 1% |
| Request latency (p95) | `argus_http_request_duration_seconds` histogram | < 1s for read routes |
| Analysis queue depth | `/ready` → `checks.engine_capacity.queued` | alert if sustained > 80% of the limit |
| Rate-limit rejections | count of 429s | alert on a sustained spike (a client misbehaving or a limit too low) |

A green `/ready` does **not** mean Caissa plays good chess; it means the measured
availability properties held. The chess-correctness properties are covered by the
evaluation framework (`docs/evaluation/`), which is a different question.

## What is not here yet

* **No distributed tracing.** Request correlation (a shared request id in logs and
  error bodies) is present; span-level tracing across services is not, because
  Caissa is one service. It is a natural addition if a second service appears.
* **Metrics are per process** — stated above rather than pretended to be fleet-wide.